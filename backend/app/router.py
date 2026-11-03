"""One pipeline for every utterance, whether it came from the mic, push-to-talk, or the dev harness.

classify(text)  local rules first (yes/no/approve/deny/skip/next/later/mute/stop briefing),
                otherwise one haiku call -> {action, text, project}
execute(...)    performs the action against the report it answers and returns what to say back.

Actions: approve, deny, reply, spawn, note, later, skip, next, mute, stop
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field

from . import db, demo, reach
from .music import music
from .alerts import compressor
from .commands import find_session

log = logging.getLogger("router")

_N = lambda s: re.sub(r"[^a-z' ]", "", s.lower()).strip()
LOCAL = {
    "approve": {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "approve", "approved", "allow", "go ahead",
                "yes go ahead", "do it", "go for it", "sounds good", "yes please", "ship it", "confirm", "confirmed"},
    "deny": {"no", "nope", "deny", "denied", "don't", "do not", "reject", "no thanks", "nah", "cancel"},
    "skip": {"skip", "skip it", "skip this", "skip that", "dismiss"},
    "next": {"next", "next one", "move on", "pass", "keep going"},
    "later": {"later", "snooze", "not now", "remind me later", "come back to this", "ask me later"},
    "mute": {"mute", "mute this", "mute that", "mute this project", "shut up about this"},
    "stop": {"stop briefing", "end briefing", "stop the briefing", "that's all", "thats all", "done for now",
             "briefing off", "exit briefing", "disarm", "stop"},
}

ROUTE_PROMPT = """You route a developer's spoken reply during a voice briefing. Respond with ONLY JSON:
{"action": "approve"|"deny"|"reply"|"spawn"|"scout"|"note"|"later"|"skip"|"unintelligible", "text": "<string>", "project": "<project name or null>"}
- unintelligible: the words are garbled, fragmentary, unrelated chatter, or don't make sense as a reply to this
  report (a speech-to-text mishear like "MIKE lipimmerless q"). NEVER guess an action for these; when in doubt,
  choose unintelligible.
- approve / deny: a yes or no to the decision; put any conditions or instructions in text.
- reply: a message for the session behind the report (an answer, instruction, or question); text = the message, cleaned up.
- scout: research something on the web and report back ("look into", "find out", "research"); text = the research brief.
- spawn: start a NEW worker/session ("spin up", "have a worker", "start a session to ..."); text = the task as a prompt.
- note: record something ("note that ...", "remember ...", "log that ..."); text = the note.
- later: postpone. skip: dismiss without acting.
- project: only if the user names a different project than the report's; else null.
Current report: project={project}, type={type}, summary={summary}
Known projects: {projects}"""


@dataclass
class Intent:
    action: str
    text: str = ""
    project: str | None = None
    via: str = "local"          # local | haiku | fallback


@dataclass
class Result:
    action: str
    say: str
    routed_to: str | None = None
    detail: dict = field(default_factory=dict)
    answered: bool = True        # mark the report answered


def split_project_prefix(ctx, text: str):
    """'billing, yes go ahead' -> (billing session dict, 'yes go ahead')."""
    m = re.match(r"^\s*([\w .&'-]{2,40}?)\s*[,:]\s+(.+)$", text)
    if m:
        s = find_session(ctx, m.group(1))
        if s:
            return s, m.group(2)
    return None, text


# "scout: ...", "start scouting for ...", "go research ...", "send a scout to look into ..."
SCOUT_RE = re.compile(r"^(?:(?:start|go|begin|please)\s+)?(?:scout(?:ing)?|send a scout|have a scout|research(?:ing)?)"
                      r"(?:\s+(?:to\s+)?(?:on|for|into|out|about|look into|find out))?[:,]?\s+(?!(?:is|was|are|were)\b)(.{4,})$", re.I)


# Whisper sometimes writes a leading "no" as "know"/"now" ("Smith & Co, know, deploy to staging only").
# Only a bare word followed by a comma or full stop is rewritten; "now deploy it" is left alone.
_NO_HOMOPHONE = re.compile(r"^\s*(?:know|now)\s*[,.]\s*", re.I)


async def classify(text: str, report: dict | None, projects: list[str]) -> Intent:
    if report and report.get("type") == "decision_needed" and _NO_HOMOPHONE.match(text):
        text = _NO_HOMOPHONE.sub("no, ", text, count=1)
    t = _N(text)
    for action, phrases in LOCAL.items():
        if t in phrases:
            return Intent(action, via="local")
    m = SCOUT_RE.match(text.strip())
    if m:   # "scout: cheapest way to verify contractor licenses in GA"
        return Intent("scout", m.group(1).strip(), via="local")
    m = re.match(r"^(yes|yeah|yep|sure|ok|okay|approve|approved|go ahead)[,.!]?\s+(.{3,})$", text.strip(), re.I)
    if m:   # "yes, but use the staging bucket" -> approve with instructions
        return Intent("approve", m.group(2).strip(), via="local")
    m = re.match(r"^(no|nope|deny|denied|don't|do not)[,.!]?\s+(.{3,})$", text.strip(), re.I)
    if m:   # "no, deploy to staging only" -> deny + tell Claude what to do instead
        return Intent("deny", m.group(2).strip(), via="local")
    m = re.match(r"^(?:note|note that|remember|log that|make a note)\s*(?:that\s+)?(.+)$", text.strip(), re.I)
    if m:
        return Intent("note", m.group(1).strip(), via="local")
    r = report or {}
    prompt = (ROUTE_PROMPT.replace("{project}", r.get("project", "none")).replace("{type}", r.get("type", "none"))
              .replace("{summary}", r.get("summary", "none")).replace("{projects}", ", ".join(projects) or "none"))
    raw = await compressor.compress(prompt, text)
    try:
        d = json.loads(re.search(r"\{.*\}", raw or "", re.S).group(0))
        if d.get("action") == "unintelligible":
            return Intent("unintelligible", via="haiku")
        if d.get("action") in ("approve", "deny", "reply", "spawn", "scout", "note", "later", "skip"):
            return Intent(d["action"], str(d.get("text") or ""), d.get("project") or None, via="haiku")
    except Exception:
        pass
    # haiku unavailable: treat free text as a reply to the session (or a note if there is none)
    return Intent("reply" if r.get("session_id") else "note", text, via="fallback")


@dataclass
class RoutePlan:
    utterance: str
    report: dict | None
    intent: Intent

    def spoken(self) -> str:
        """What Athena reads back before the hold, e.g. "Scouting the competitors and their features." """
        it, r = self.intent, self.report or {}
        who = (it.project if it.action == "spawn" and it.project else r.get("project")) or "it"
        t = (it.text or "").strip().rstrip(".")
        line = {
            "approve": f"Approving {who}" + (f", with: {t}" if t else ""),
            "deny": f"Denying {who}" + (f", and saying: {t}" if t else ""),
            "reply": f"Telling {who}: {t or self.utterance}",
            "note": f"Noting: {t}",
            "scout": f"Scouting {t}",
            "spawn": f"Starting a worker in {who}: {t}",
            "later": f"Snoozing {who} for 30 minutes",
            "skip": f"Skipping {who}",
            "next": "Next report",
            "mute": f"Muting {who}",
            "stop": "Stopping the briefing",
        }.get(it.action, self.utterance)
        return line.rstrip(".") + "."

    def describe(self) -> tuple[str | None, str]:
        """(target, confirm-card line), e.g. ("Billing", 'send "use staging" -> Billing')."""
        it, r = self.intent, self.report or {}
        who = it.project if it.action == "spawn" and it.project else r.get("project")
        q = lambda t: f"\u201c{t}\u201d"
        to = f" \u2192 {who}" if who else ""
        line = {
            "approve": f"approve {q(self.utterance)}{to}",
            "deny": f"deny {q(self.utterance)}{to}",
            "reply": f"send {q(it.text or self.utterance)}{to}",
            "note": f"note {q(it.text)}",
            "scout": f"scout {q(it.text)}",
            "spawn": f"start a worker{' in ' + who if who else ''}: {q(it.text)}",
            "later": f"snooze{' ' + who if who else ''} for 30 minutes",
            "skip": f"skip{' ' + who if who else ''}",
            "next": "next report",
            "mute": f"mute {who or 'this project'}",
            "stop": "stop briefing",
        }.get(it.action, f"{it.action} {q(self.utterance)}")
        return who, line


class Router:
    def __init__(self, manager, terminal, ctx, reports):
        self.manager, self.terminal, self.ctx, self.reports = manager, terminal, ctx, reports

    def projects(self) -> list[str]:
        return sorted({s["name"] for s in self.ctx.all_sessions()})

    async def plan(self, utterance: str, report: dict | None) -> "RoutePlan":
        """Classify and pick the target. No side effects, so the confirm hold can show it and still cancel."""
        override, text = split_project_prefix(self.ctx, utterance)
        if override and (not report or override["id"] != report.get("session_id")):
            # "billing, ..." -> answer billing's most urgent open decision instead
            report = next(iter(db.open_reports(session_id=override["id"], type_="decision_needed")), None) or \
                next(iter(db.open_reports(session_id=override["id"])), None) or {"project": override["name"],
                                                                              "session_id": override["id"], "type": "fyi"}
        intent = await classify(text, report, self.projects())
        if intent.project and (not report or intent.project.lower() != (report.get("project") or "").lower()):
            s = find_session(self.ctx, intent.project)
            if s and intent.action in ("reply", "approve", "deny"):
                report = {"project": s["name"], "session_id": s["id"], "type": "fyi"}
        return RoutePlan(utterance, report, intent)

    async def run(self, p: "RoutePlan", source: str, comms_id: int | None = None, heard: str | None = None) -> Result:
        """Carry out a plan. The decision log keeps the words verbatim (and the mishear, if it was corrected)."""
        intent, report, utterance = p.intent, p.report, p.utterance
        res = await self.execute(intent, report)
        db.log_decision(report_id=(report or {}).get("id"), project=(report or {}).get("project"), utterance=utterance,
                        action=intent.action, routed_to=res.routed_to, result=res.say, comms_id=comms_id, heard=heard)
        if report and report.get("id") and res.answered:
            await self.reports.answer(report["id"], f"{intent.action}: {intent.text or utterance}"[:500], res.routed_to,
                                      status="skipped" if intent.action == "skip" else "answered")
        res.detail.update({"intent": intent.action, "via": intent.via, "source": source})
        log.info("utterance %r [%s/%s] -> %s (%s)", utterance, intent.action, intent.via, res.say, res.routed_to)
        return res

    async def handle(self, utterance: str, report: dict | None, source: str) -> Result:
        return await self.run(await self.plan(utterance, report), source)

    # --- actions ----------------------------------------------------------------------
    async def execute(self, it: Intent, r: dict | None) -> Result:
        r = r or {}
        name = r.get("project") or "that"
        sid = r.get("session_id")
        if it.action == "stop":
            from .briefing import voice_name
            return Result("stop", f"{voice_name()} standing by.", answered=False)
        if it.action == "next":
            return Result("next", "Next.", answered=False)
        if it.action == "skip":
            return Result("skip", "Skipped.")
        if it.action == "later":
            if r.get("id"):
                db.update_report(r["id"], snoozed_until=time.time() + 1800, status="queued")
                music.answered(r["id"])
            return Result("later", "Snoozed for 30 minutes.", answered=False)
        if it.action == "mute":
            if sid:
                await self.ctx.set_muted(sid, True)
            return Result("mute", f"Muted {name}.", answered=False)
        if it.action == "note":      # handle() writes the decision-log entry
            return Result("note", "Noted.", routed_to="decision_log")
        if it.action == "scout":
            from .scouts import SCOUTS_ENABLED, SCOUTS_OFF_SAY, scouts
            if not SCOUTS_ENABLED:
                return Result("scout", SCOUTS_OFF_SAY, answered=False)
            linked = r.get("id") if r.get("type") == "decision_needed" and r.get("status") in ("queued", "delivered") else None
            sc = await scouts.launch(it.text, project=it.project or r.get("project"), linked_report_id=linked)
            tail = " I'll bring it back when the research lands." if linked else ""
            return Result("scout", f"Scout {sc['id']} is on it.{tail}", routed_to=f"scout:{sc['id']}", answered=False)
        if it.action == "spawn":
            cwd = self._cwd_for(it.project, r)
            if not cwd:
                return Result("spawn", "I don't know which project to start that in.", answered=False)
            s = await self.manager.create(cwd, it.text)
            return Result("spawn", f"Starting a worker in {s.name}.", routed_to=s.id)
        if it.action in ("approve", "deny", "reply"):
            return await self._answer_session(it, r)
        return Result(it.action, "Sorry, I didn't catch that.", answered=False)

    def _cwd_for(self, project: str | None, r: dict) -> str | None:
        if project:
            s = find_session(self.ctx, project)
            if s and s.get("cwd"):
                return s["cwd"]
        sid = r.get("session_id")
        for s in self.ctx.all_sessions():
            if s["id"] == sid and s.get("cwd"):
                return s["cwd"]
        return None

    async def _answer_session(self, it: Intent, r: dict) -> Result:
        sid, name = r.get("session_id"), r.get("project") or "the session"
        key = r.get("dedupe_key") or ""
        allow = it.action == "approve"
        if not sid:   # inbox drop or system report: a yes/no just closes it; a reply is kept as a note
            if it.action == "reply":
                db.log_decision(report_id=r.get("id"), project=r.get("project"), utterance=it.text, action="note",
                                routed_to="decision_log", result="noted (no session to send to)")
                return Result("reply", "There's no session behind that, so I noted it.", routed_to="decision_log")
            return Result(it.action, "Got it." if allow else "Dismissed.", routed_to="dashboard")

        s = self.manager.sessions.get(sid)
        if s:                                               # dashboard-owned SDK session
            pending = key[6:] if key.startswith("block:") else None
            if pending and pending in s.permissions:
                if it.action == "reply":                    # free text answers a permission by telling Claude
                    await s.resolve_permission(pending, False, it.text)
                    return Result("reply", f"Sent to {name}.", routed_to=sid)
                await s.resolve_permission(pending, allow, it.text or None)
                return Result(it.action, f"{'Approved' if allow else 'Denied'}. Sent to {name}.", routed_to=sid)
            msg = it.text or ("Yes, go ahead." if allow else "No, don't do that.")
            if it.action != "reply" and it.text:
                msg = ("Yes. " if allow else "No. ") + it.text
            await s.send(msg)
            return Result(it.action, f"Sent to {name}.", routed_to=sid)

        t = self.terminal.get(sid)                          # terminal session: tmux, then cmux
        if demo.is_demo_session(t):                         # `vario demo`: made-up session, fake transcript
            await demo.reply(t, it.text or ("Yes, go ahead." if allow else "No."))
            return Result(it.action, f"Sent to {name}.", routed_to="demo")
        pane = await reach.tmux_pane_for(t.cwd) if t else None
        if not pane:
            cm = await reach.cmux_surface_for(sid)
            if cm:
                return await self._answer_cmux(it, cm, name, key)
            return Result(it.action, f"{name} needs you in its terminal; I can't reach it from here.",
                          routed_to=None, answered=False)
        if key.startswith("block:"):                       # a permission dialog: only if it's still on screen
            if not reach.permission_dialog_showing(await reach.tmux_screen(pane)):
                return Result(it.action, f"{name}'s permission prompt isn't showing anymore, so I sent nothing.",
                              routed_to=f"tmux:{pane}", answered=False)
            if it.action == "approve" and not it.text:
                ok = await reach.tmux_send(pane, "Enter")   # default option is "Yes"
            else:
                ok = await reach.tmux_send(pane, "Escape")
                if ok and (it.text or it.action == "reply"):
                    ok = await reach.tmux_send(pane, "-l", it.text or "No.") and await reach.tmux_send(pane, "Enter")
        else:
            msg = it.text or ("Yes, go ahead." if allow else "No.")
            ok = await reach.tmux_send(pane, "-l", msg) and await reach.tmux_send(pane, "Enter")
        if not ok:
            return Result(it.action, f"Couldn't reach {name}'s terminal.", answered=False)
        return Result(it.action, f"Sent to {name}.", routed_to=f"tmux:{pane}")

    # --- free-form messages to terminal sessions (the dashboard's message box / dictation) --------------
    async def terminal_route(self, sid: str) -> dict | None:
        """How a terminal session can be reached: {"via": "tmux", "pane"} or {"via": "cmux", "target"}."""
        t = self.terminal.get(sid)
        pane = await reach.tmux_pane_for(t.cwd) if t else None
        if pane:
            return {"via": "tmux", "pane": pane}
        cm = await reach.cmux_surface_for(sid)
        return {"via": "cmux", "target": cm} if cm else None

    async def send_to_terminal(self, sid: str, text: str) -> Result:
        """Type a message into a terminal session's prompt and press Enter. Refuses (sends nothing) while a
        permission prompt is on screen, since Enter would answer it."""
        import asyncio
        t = self.terminal.get(sid)
        name = t.name if t else "that session"
        if demo.is_demo_session(t):
            await demo.reply(t, text)
            return Result("message", f"Sent to {name}.", routed_to="demo")
        route = await self.terminal_route(sid)
        if not route:
            return Result("message", f"I can't reach {name}'s terminal (not in tmux or a cmux pane).", answered=False)
        if route["via"] == "tmux":
            pane = route["pane"]
            where = f"tmux:{pane}"
            if reach.permission_dialog_showing(await reach.tmux_screen(pane)):     # Enter would answer it
                return Result("message", f"{name} is showing a permission prompt; answer that first.",
                              routed_to=where, answered=False)
            ok = await reach.tmux_send(pane, "-l", text) and await reach.tmux_send(pane, "Enter")
        else:
            cm = route["target"]
            where = f"cmux:{cm['surface'][:8]}"
            if reach.permission_dialog_showing(await reach.cmux_screen(cm)):
                return Result("message", f"{name} is showing a permission prompt; answer that first.",
                              routed_to=where, answered=False)
            ok = await reach.cmux_text(cm, text)
            if ok:
                await asyncio.sleep(0.2)
                ok = await reach.cmux_key(cm, "enter")
        log.info("message to %s via %s: %d chars, %s", sid[:8], where, len(text), "sent" if ok else "failed")
        if not ok:
            return Result("message", f"Couldn't type into {name}'s terminal.", routed_to=where, answered=False)
        return Result("message", f"Sent to {name}.", routed_to=where)

    async def _answer_cmux(self, it: Intent, cm: dict, name: str, key: str) -> Result:
        """Type into the cmux pane running the session - only after confirming what's on screen."""
        import asyncio
        screen = await reach.cmux_screen(cm)
        dialog = reach.permission_dialog_showing(screen)
        target = f"cmux:{cm['surface'][:8]}"
        allow = it.action == "approve"
        if key.startswith("block:"):
            if not dialog:
                return Result(it.action, f"{name}'s permission prompt isn't showing anymore, so I sent nothing.",
                              routed_to=target, answered=False)
            if allow and not it.text:
                ok = await reach.cmux_key(cm, "enter")                 # option 1, "Yes", is preselected
            else:
                ok = await reach.cmux_key(cm, "escape")                # "No, and tell Claude what to do"
                if ok and (it.text or it.action == "reply"):
                    await asyncio.sleep(0.4)
                    ok = await reach.cmux_text(cm, it.text or "No.") and await reach.cmux_key(cm, "enter")
        else:
            if dialog:
                return Result(it.action, f"{name} is showing a permission prompt; answer that first.",
                              routed_to=target, answered=False)
            msg = it.text or ("Yes, go ahead." if allow else "No.")
            ok = await reach.cmux_text(cm, msg)
            if ok:
                await asyncio.sleep(0.2)
                ok = await reach.cmux_key(cm, "enter")
        if not ok:
            return Result(it.action, f"Couldn't type into {name}'s terminal.", routed_to=target, answered=False)
        verb = {"approve": "Approved", "deny": "Denied"}.get(it.action, "Sent")
        return Result(it.action, f"{verb}. Sent to {name}." if verb != "Sent" else f"Sent to {name}.", routed_to=target)
