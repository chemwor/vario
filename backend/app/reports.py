"""Report inbox: every alert path creates a report; delivery policy decides what gets spoken.

Lifecycle: queued -> delivered (spoken) -> answered | skipped.
Delivery outside briefing mode: decision_needed reports with urgency >= SPEAK_URGENCY are spoken at
once as one-off alerts; everything else waits in the queue. Unanswered spoken decisions re-alert every
REMIND_S ("Still waiting: Billing. ...") up to MAX_REMINDERS times.
"""
import asyncio
import json
import logging
import os
import re
import time

from . import db, host, voice
from .alerts import LONG_CHARS, compressor, truncate
from .config import voice_conf
from .hub import hub
from .music import music

log = logging.getLogger("reports")

SPEAK_URGENCY = int(os.environ.get("SPEAK_URGENCY", "4"))
ERROR_WINDOW_S = 3600          # identical errors from one session this close together collapse into one report
ESCALATE_AT = 3                # ...and the third one becomes a decision: retry, or kill it?
REMIND_S = int(os.environ.get("ALERT_REMIND_S", "180"))
MAX_REMINDERS = int(os.environ.get("ALERT_MAX_REMINDERS", "5"))
TYPES = ("decision_needed", "research", "fyi")

TRIAGE_PROMPT = """Triage this report for a busy developer's voice inbox. Respond with ONLY a JSON object:
{"type": "decision_needed" | "research" | "fyi", "urgency": 1-5, "summary": "<one sentence to be spoken aloud, max 20 words>"}
- decision_needed: the developer must approve, choose, or answer something before work continues.
- research: findings, analysis, or a recommendation to read.
- fyi: status updates, completed work, nothing to decide.
- urgency: 5 = blocking work right now, 4 = blocking soon / needs a decision, 3 = should see today, 2 = normal, 1 = low.
- summary: plain spoken words (no markdown, paths, or code); for decisions, phrase it as the question.
Project: {project}
Source: {source}
Hint (may be wrong): type={type}, urgency={urgency}"""

# appended for end-of-turn reports: one triage call also labels how the turn went
OUTCOME_PROMPT = """
This is the session's final message for its turn. Also add to the JSON:
"outcome": "ok" | "failed" | "partial", "failure": "<the line or step that failed, in plain words; empty if ok>"
- ok: the work it set out to do succeeded (or it only answered a question).
- failed: the main thing it tried did not work (tests fail, deploy failed, couldn't fix the bug, blocked by an error).
- partial: some of it worked but something is left broken, skipped, or unverified.
When the outcome is failed or partial, the summary must say so plainly, e.g. "finished but the deploy failed at
the last step." Don't repeat the project name in the summary."""


def err_sig(text: str) -> str:
    """Error text with ids, numbers and paths blurred, so the same failure matches itself."""
    t = voice.clean(text or "") or (text or "")
    t = re.sub(r"/[\w./-]+|\b[0-9a-f]{8,}\b|\b\w*\d[\w.-]*", "#", t.lower())   # paths, hashes, anything with a digit
    return re.sub(r"\s+", " ", t).strip()[:300]


def _public(r: dict) -> dict:
    out = {k: r.get(k) for k in ("id", "source", "project", "type", "urgency", "summary", "body", "created_at", "status",
                                 "decision_text", "decision_routed_to", "session_id", "triaged", "delivered_at",
                                 "outcome", "outcome_line", "last_seen")}
    out["repeat"] = r.get("repeat") or 1
    out["kind"] = (r.get("dedupe_key") or "").split(":", 1)[0] or None    # block | final | error | fail | stuck ...
    return out


async def _emit(r: dict):
    if r["status"] not in ("queued", "delivered") or (r.get("snoozed_until") or 0) > time.time():
        music.answered(r["id"])
    await hub.emit("report", report=_public(r))


class Reports:
    def __init__(self):
        self.triage_q: asyncio.Queue[int] = asyncio.Queue()
        self.briefing = False           # Phase B: when armed, the briefing loop owns delivery
        self.last_alerted: str | None = None
        self.muted_check = lambda r: False   # set by main: per-session mute lookup
        self.is_dashboard_session = lambda sid: False    # set by main
        self.on_created = lambda r: None     # set by main: wakes the briefing loop
        self.on_outcome = lambda sid, outcome: None    # set by main: the session row's done/failed badge

    # --- creation ---------------------------------------------------------------
    async def create(self, *, source: str, project: str, type: str, urgency: int, summary: str, body: str = "",
                     session_id: str | None = None, key: str | None = None, triage: bool = True,
                     wait_triage: bool = False, t0: float | None = None) -> dict | None:
        r = db.create_report(source=source, project=project, type=type if type in TYPES else "fyi",
                             urgency=max(1, min(5, int(urgency))), summary=summary.strip() or truncate(body),
                             body=body, session_id=session_id, dedupe_key=key)
        if not r:
            return None                  # duplicate event
        log.info("report #%s %s/%s u%s %s: %s", r["id"], r["source"], r["type"], r["urgency"], r["project"], r["summary"])
        await _emit(r)
        self.on_created(r)
        if triage:
            # a long provisional summary would be awkward to speak: let triage write it first
            if self._should_speak(r) and (wait_triage or len(r["summary"]) > LONG_CHARS):
                r = await self.triage(r["id"])
            else:
                self.triage_q.put_nowait(r["id"])
        await self.deliver_if_urgent(r, t0)
        return r

    # --- errors: identical ones collapse; the third is a decision ---------------------------------
    async def error(self, *, project: str, summary: str, body: str, session_id: str, key: str,
                    t0: float | None = None) -> dict | None:
        sig, now = err_sig(body or summary), time.time()
        prev = db.find_error(session_id, sig, now - ERROR_WINDOW_S)
        if not prev:
            r = await self.create(source="session", project=project, type="decision_needed", urgency=4,
                                  summary=summary, body=body, session_id=session_id, key=key, t0=t0)
            return db.update_report(r["id"], err_sig=sig, last_seen=now) if r else None
        # a repeat: keep the event (its dedupe key stops a rescan counting it twice) folded into the first one
        dup = db.create_report(source="session", project=project, type="fyi", urgency=1, summary=summary, body=body,
                               session_id=session_id, dedupe_key=key, err_sig=sig, triaged=1, status="skipped",
                               decision_text=f"collapsed into #{prev['id']}")
        if not dup:
            return prev
        return await self._repeat(prev, now)

    async def _repeat(self, prev: dict, at: float) -> dict:
        n = (prev.get("repeat") or 1) + 1
        fields: dict = {"repeat": n, "last_seen": at}
        escalate = n >= ESCALATE_AT
        if escalate:
            fields.update(type="decision_needed", urgency=4, triaged=1, status="queued",
                          summary=f"{prev['project']} has failed the same way {n} times. Retry, or kill it?")
        elif prev["status"] not in ("queued", "delivered"):
            fields["status"] = "queued"          # it came back: reopen it
        r = db.update_report(prev["id"], **fields)
        log.info("report #%s: same error again (x%d)%s", r["id"], n, ", escalated" if n == ESCALATE_AT else "")
        await _emit(r)
        if n == ESCALATE_AT:
            self.on_created(r)
            await self.deliver_if_urgent(r)
        return r

    def collapse_existing(self):
        """Startup: fold identical errors filed before dedupe existed (same session, within the window)."""
        groups: dict[tuple, dict] = {}
        changed = []
        for r in db.error_reports():
            if (r.get("decision_text") or "").startswith("collapsed into #"):
                continue
            sig = r.get("err_sig") or err_sig(r["body"] or r["summary"])
            k = (r["session_id"], sig)
            head = groups.get(k)
            seen = (head or {}).get("last_seen") or (head or {}).get("created_at") or 0
            if head and r["created_at"] - seen <= ERROR_WINDOW_S and head["status"] in ("queued", "delivered") \
                    and r["status"] in ("queued", "delivered"):
                db.update_report(r["id"], status="skipped", err_sig=sig, decision_text=f"collapsed into #{head['id']}")
                n = (head.get("repeat") or 1) + 1
                fields: dict = {"repeat": n, "last_seen": r["created_at"], "err_sig": sig}
                if n >= ESCALATE_AT:
                    fields.update(type="decision_needed", urgency=4, triaged=1, status="queued",
                                  summary=f"{head['project']} has failed the same way {n} times. Retry, or kill it?")
                groups[k] = db.update_report(head["id"], **fields)
                changed.append(head["id"])
            else:
                groups[k] = db.update_report(r["id"], err_sig=sig, last_seen=r.get("last_seen") or r["created_at"])
        if changed:
            log.info("collapsed repeated errors into %s", sorted(set(changed)))

    # --- triage -------------------------------------------------------------------
    async def triage(self, rid: int) -> dict:
        r = db.get_report(rid)
        if not r or r["triaged"]:
            return r
        final = (r.get("dedupe_key") or "").startswith("final:")
        prompt = (TRIAGE_PROMPT.replace("{project}", r["project"]).replace("{source}", r["source"])
                  .replace("{type}", r["type"]).replace("{urgency}", str(r["urgency"]))) + (OUTCOME_PROMPT if final else "")
        # background work, so a slow (cold) call can take longer than a spoken alert could
        raw = await compressor.compress(prompt, f"{r['summary']}\n\n{r['body']}".strip(), timeout=25)
        fields = parse_triage(raw, outcome=final)
        via = "sdk"
        if fields is None:
            via = "rules" if compressor.auth["path"] == "rules" else "rules (sdk failed)"
            if r["source"] in ("app_drop", "scout"):
                fields = rule_triage(r)
            else:                               # session events come with a deterministic classification
                fields = {}
        log.info("triage #%s via=%s -> %s/%s", rid, via, fields.get("type", r["type"]), fields.get("urgency", r["urgency"]))
        if r["source"] == "scout":
            fields["type"] = "research"          # scouts always file research, whatever triage thinks
            if r["summary"] and not r["summary"].lower().startswith("scout"):
                fields.pop("summary", None)      # keep the scout's own one-line answer
        if fields.get("outcome") in ("failed", "partial"):
            # a turn that didn't fully work is worth hearing about: at the threshold for a failure
            fields["urgency"] = max(fields.get("urgency", r["urgency"]), SPEAK_URGENCY if fields["outcome"] == "failed"
                                    else SPEAK_URGENCY - 1)
        r = db.update_report(rid, triaged=1, **fields)
        await _emit(r)
        if r.get("outcome") and r["session_id"]:
            self.on_outcome(r["session_id"], {"label": r["outcome"], "line": r.get("outcome_line")})
            await self.deliver_if_urgent(r)
        return r

    async def triage_worker(self):
        while True:
            rid = await self.triage_q.get()
            try:
                await self.triage(rid)
            except Exception:
                log.exception("triage #%s", rid)

    # --- delivery -------------------------------------------------------------------
    def _should_speak(self, r: dict) -> bool:
        # decisions, and end-of-turn reports whose outcome wasn't ok (the summary says what failed)
        return (not self.briefing and r["status"] == "queued" and r["urgency"] >= SPEAK_URGENCY
                and (r["type"] == "decision_needed" or r.get("outcome") in ("failed", "partial")))

    async def deliver_if_urgent(self, r: dict, t0: float | None = None):
        if not self._should_speak(r):
            return
        await self.speak(r, t0=t0)

    async def speak(self, r: dict, prefix: str = "", t0: float | None = None) -> bool:
        summ, proj = r["summary"] or "", r["project"]
        if summ.lower().startswith(proj.lower()):
            line = f"{prefix}{summ}"                       # "Claude Speak has failed the same way 3 times..."
        elif r.get("outcome") in ("failed", "partial") and re.match(r"(finished|done|stopped|completed)\b", summ, re.I):
            line = f"{prefix}{proj} {summ[0].lower()}{summ[1:]}"   # "Storefront finished but the deploy failed at the last step."
        else:
            line = f"{prefix}{proj}: {summ}"
        muted = self.muted_check(r) or voice.global_muted()
        # NOTIFY (voice.conf): fallback = a desktop notification when the voice can't speak it (default),
        # also = always notify as well, only = notify instead of speaking, off = never notify
        mode = voice_conf("NOTIFY", "fallback").lower()
        ok = False if mode == "only" else await voice.speak(line, session_id=r["session_id"], muted=muted, kind="alert")
        if not muted and (mode in ("also", "only") or (mode == "fallback" and not ok)):
            if await host.notify(f"Vario: {r['project']}", r["summary"] or line):
                ok = ok or mode == "only"
        if ok and r["type"] == "decision_needed" and self.is_dashboard_session(r["session_id"]):
            music.await_answer(r["id"])        # dashboard sessions wait for your answer; terminal ones don't
        if t0:
            log.info("report #%s spoken %.0f ms after the event", r["id"], (time.time() - t0) * 1000)
        if r["session_id"]:
            self.last_alerted = r["session_id"]
        r = db.update_report(r["id"], status="delivered" if r["status"] == "queued" else r["status"],
                             delivered_at=r["delivered_at"] or time.time())
        await _emit(r)
        await hub.emit("alert", report_id=r["id"], session_id=r["session_id"], text=line, delivered=ok)
        return ok

    async def reminder_loop(self):
        while True:
            await asyncio.sleep(15)
            if self.briefing:
                continue
            now = time.time()
            for r in db.open_reports(type_="decision_needed"):
                if (r["status"] == "delivered" and r["urgency"] >= SPEAK_URGENCY and r["reminders"] < MAX_REMINDERS
                        and now - (r["delivered_at"] or now) >= REMIND_S * (r["reminders"] + 1)):
                    db.update_report(r["id"], reminders=r["reminders"] + 1)
                    await self.speak(r, prefix="Still waiting: ")

    # --- resolution -------------------------------------------------------------------
    async def answer(self, rid: int, decision_text: str, routed_to: str | None, status: str = "answered"):
        r = db.update_report(rid, status=status, decision_text=decision_text, decision_routed_to=routed_to,
                             answered_at=time.time())
        if r:
            await _emit(r)
        return r

    async def resolve_session(self, session_id: str, decision_text: str, routed_to: str | None = None,
                              only_key: str | None = None):
        """The session moved on (permission answered, new prompt...): close its open decisions."""
        for r in db.open_reports(session_id=session_id, type_="decision_needed"):
            if only_key is None or r["dedupe_key"] == only_key:
                await self.answer(r["id"], decision_text, routed_to or session_id)


_DECIDE = re.compile(r"\b(approve|approval|permission|decide|decision|choose|pick one|confirm|sign off|go ahead|"
                     r"should (?:i|we)|which (?:one|option)|ok to|waiting (?:on|for) (?:you|your))\b", re.I)
_RESEARCH = re.compile(r"\b(findings|research|recommend(?:ation)?s?|analysis|compar(?:e|ison)|sources|benchmark)\b", re.I)
_URGENT = re.compile(r"\b(urgent|asap|blocking|blocked|outage|failing|failed|broken|deadline|today)\b", re.I)


def rule_triage(r: dict) -> dict:
    """No model available (or not allowed): classify with keywords. Deterministic and instant."""
    text = f"{r['summary']}\n{r['body']}"
    summary = truncate(first_line(r["summary"]) or first_line(r["body"]) or r["project"], 120)
    question = re.search(r"[^.!?\n#>*]*\?", voice.clean(text) or text)
    if _DECIDE.search(text) or question:
        kind, urgency = "decision_needed", 4
        if question:                                   # speak the actual question, not a heading
            summary = truncate(question.group(0).strip(), 120)
    elif _RESEARCH.search(text):
        kind, urgency = "research", 3
    else:
        kind, urgency = "fyi", 2
    if _URGENT.search(text):
        urgency = min(5, urgency + 1)
    return {"type": kind, "urgency": urgency, "summary": summary}


def first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if line:
            return line
    return ""


def parse_triage(raw: str | None, outcome: bool = False) -> dict | None:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("type") not in TYPES:
        return None
    try:
        urgency = max(1, min(5, int(d.get("urgency", 2))))
    except (TypeError, ValueError):
        urgency = 2
    summary = re.sub(r"\s+", " ", str(d.get("summary", ""))).strip()
    out = {"type": d["type"], "urgency": urgency, **({"summary": summary} if summary else {})}
    if outcome and d.get("outcome") in ("ok", "failed", "partial"):
        out["outcome"] = d["outcome"]
        line = re.sub(r"\s+", " ", str(d.get("failure") or "")).strip()
        out["outcome_line"] = line[:240] if line and d["outcome"] != "ok" else None
    return out


reports = Reports()
