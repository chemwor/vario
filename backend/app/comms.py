"""Comms: every utterance, the confirm hold before it's acted on, and what came of it.

One path for the mic, push-to-talk, the dev harness and re-runs from the Comms pane:
  utterance -> comms row (what was heard, whisper's confidence)
            -> plan (classification, action, target; no side effects)
            -> read back: Athena says what she understood ("Scouting competitors and their features."),
               unless CONFIRM_READBACK=off
            -> hold: a confirm card for CONFIRM_HOLD_MS, e.g. send "yes, run it" -> Billing.
               "no" / "cancel" / Esc cancels it, silence dispatches it. Speaking during the hold keeps it
               open (the browser says so) until what you said is transcribed, up to CONFIRM_MAX_MS.
               One-word commands in CONFIRM_FAST skip the hold.
            -> run -> outcome on the row
Confidence gate (anything heard: mic/ptt, or any utterance carrying an STT confidence, e.g. a harness replay;
typed input from the harness/panel/palette carries none and isn't gated):
  below MIN_DISPATCH_CONF (default 40%) an utterance is logged "rejected: low confidence" and goes nowhere,
  not to the command parser, the classifier or the briefing. Tiered verbs (approve/deny/skip/retry/
  interrupt, and anything routed to a session) below STRICT_CONF (default 70%) get an explicit hold: the
  card waits for a click or a spoken "confirm" (itself >= STRICT_CONF); silence does NOT send it, and it
  expires after EXPLICIT_HOLD_S (default 60).
"no, I said ..." cancels a pending hold (or takes the last utterance) and re-runs the corrected words.
Corrections teach: a word you've corrected in 3+ times is proposed for VOCAB in voice.conf.
Settings live in voice.conf and are read on every utterance.
"""
import asyncio
import difflib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import db, voice
from .config import set_voice_conf, voice_conf
from .hub import hub

log = logging.getLogger("comms")

DEFAULT_FAST = "yes,no,approve,deny,skip,stop,repeat,details,stats"
CANCEL_WORDS = {"no", "nope", "cancel", "cancel that", "stop", "wait", "hold on", "never mind", "nevermind", "abort",
                "don't", "dont", "no no", "scratch that"}
CORRECTION_RE = re.compile(r"^\s*(?:no[,.!]?\s+)?(?:i said|i meant|i mean|correction[:,]?)\s+(.+?)\s*$", re.I)
SUGGEST_AT = 3
_STOP = {"the", "and", "a", "an", "to", "of", "in", "on", "for", "it", "is", "that", "this", "with", "i", "you", "yes",
         "no", "please", "can", "be", "at", "or", "so", "do", "my", "me", "we", "us", "are", "was", "said", "meant"}


def _norm(t: str) -> str:
    return re.sub(r"[^a-z' ]", "", t.lower()).strip()


def hold_ms() -> int:
    try:
        return max(0, int(voice_conf("CONFIRM_HOLD_MS", "2000")))
    except ValueError:
        return 2000


def max_hold_ms() -> int:
    try:
        return max(hold_ms(), int(voice_conf("CONFIRM_MAX_MS", "8000")))
    except ValueError:
        return 8000


def fast_words() -> set[str]:
    return {_norm(w) for w in voice_conf("CONFIRM_FAST", DEFAULT_FAST).split(",") if w.strip()}


SPOKEN = {"mic", "ptt", "correction"}
TIERED = {"approve", "deny", "skip", "retry", "interrupt", "reply", "spawn"}
CONFIRM_WORDS = {"yes", "confirm", "confirmed", "yes confirm", "send it", "yes send it", "send", "do it", "go ahead",
                 "yes go ahead"}


def _pct(key: str, default: float) -> float:
    """A 0-1 threshold from voice.conf; accepts 0.4, 40 or 40%."""
    try:
        v = float(voice_conf(key, str(default)).rstrip("%"))
    except ValueError:
        return default
    return v / 100 if v > 1 else v


def min_dispatch_conf() -> float:
    return _pct("MIN_DISPATCH_CONF", 0.40)


def strict_conf() -> float:
    return _pct("STRICT_CONF", 0.70)


def explicit_hold_s() -> float:
    try:
        return max(5.0, float(voice_conf("EXPLICIT_HOLD_S", "60")))
    except ValueError:
        return 60.0


def heard(source: str | None, confidence: float | None) -> bool:
    """Came through speech-to-text: a spoken source, or anything carrying an STT confidence (a harness replay)."""
    return source in SPOKEN or confidence is not None


def below_floor(source: str | None, confidence: float | None) -> bool:
    return confidence is not None and confidence < min_dispatch_conf()


def clear_enough(source: str | None, confidence: float | None) -> bool:
    """Typed input, or speech at >= STRICT_CONF. Speech with no confidence reported doesn't count."""
    return not heard(source, confidence) or (confidence is not None and confidence >= strict_conf())


def needs_explicit(plan: "Plan", source: str | None, confidence: float | None) -> bool:
    """A tiered verb that wasn't clearly heard."""
    return (plan.action in TIERED or plan.mode == "reply") and not clear_enough(source, confidence)


# unmistakable confirms: a closed set matched exactly, so the floor (not STRICT_CONF) is enough. Whisper scores
# one-word utterances low, and "confirm" at 55% is still "confirm". Bare "yes" / "go ahead" are easier to
# mishear out of background talk, so they still need STRICT_CONF.
STRONG_CONFIRM = {"confirm", "confirmed", "send", "send it", "yes confirm", "yes send it", "confirm it", "send that"}


def is_confirm(text: str) -> bool:
    return _norm(text) in CONFIRM_WORDS | STRONG_CONFIRM


def confirm_heard(text: str, source: str | None, confidence: float | None) -> bool:
    """Is this confirm clear enough to send a card that's waiting for one?"""
    return _norm(text) in STRONG_CONFIRM or clear_enough(source, confidence)


def reason(row: dict) -> str:
    """The Comms chip: dispatched / rejected-low-conf / ignored-speaking / ignored-muted / ..."""
    st, out = row.get("status") or "", (row.get("outcome") or "").lower()
    if st == "rejected":
        return ("rejected-low-conf" if "low confidence" in out else "rejected-unintelligible" if "unintelligible" in out
                else "rejected-unknown" if "unknown command" in out else "rejected")
    if st == "ignored":
        return ("ignored-speaking" if "speaking" in out else "ignored-muted" if "muted" in out
                else "ignored-busy" if "phase=" in out else "ignored")
    return st or "received"


def flag_below() -> float:
    try:
        return float(voice_conf("STT_FLAG_BELOW", "0.65"))
    except ValueError:
        return 0.65


def read_back() -> bool:
    return voice_conf("CONFIRM_READBACK", "on").lower() not in ("off", "0", "no", "false")


def is_fast(text: str) -> bool:
    t = _norm(text)
    return " " not in t and t in fast_words()


def is_cancel(text: str) -> bool:
    return _norm(text) in CANCEL_WORDS


def correction(text: str) -> str | None:
    m = CORRECTION_RE.match(text)
    return m.group(1).strip(" .") if m else None


@dataclass
class Plan:
    """What an utterance will do, worked out before anything happens."""
    mode: str                                 # command | reply | briefing
    action: str
    target: str | None
    desc: str                                 # the confirm card line
    run: Callable[[], Awaitable[dict]]
    via: str | None = None
    holdable: bool = True                     # False: nothing would be dispatched (e.g. an unknown command)
    spoken: str | None = None                 # read back before the hold: "Scouting competitors and their features."


@dataclass
class Pending:
    id: int
    started: float
    deadline: float
    plan: Plan
    decided: asyncio.Event = field(default_factory=asyncio.Event)
    outcome: str | None = None                # dispatch | cancel
    why: str = ""
    reading: bool = False                     # Athena is reading it back; the countdown starts after
    explicit: bool = False                    # low confidence on a tiered verb: only a click or "confirm" sends it


def public(row: dict | None) -> dict | None:
    if row is None:
        return None
    c = row.get("confidence")
    return {**row, "flagged": c is not None and c < flag_below(), "reason": reason(row)}


class Comms:
    def __init__(self):
        self.pending: Pending | None = None

    # --- the log -------------------------------------------------------------------------------
    async def record(self, **d) -> dict:
        row = db.add_comms(**d)
        await hub.emit("comms", entry=public(row))
        return row

    async def update(self, cid: int, **fields) -> dict | None:
        row = db.update_comms(cid, **fields)
        await hub.emit("comms", entry=public(row))
        return row

    async def classified(self, cid: int, plan: Plan):
        await self.update(cid, mode=plan.mode, action=plan.action, via=plan.via, target=plan.target, descr=plan.desc)

    async def finish(self, cid: int, res: dict | None = None, status: str = "dispatched", outcome: str | None = None):
        res = res or {}
        await self.update(cid, status=status, outcome=outcome if outcome is not None else res.get("say") or res.get("reason"),
                          routed_to=res.get("routed_to") or res.get("session_id"))

    # --- the confirm hold ----------------------------------------------------------------------
    def pending_state(self) -> dict | None:
        p = self.pending
        if not p:
            return None
        row = db.get_comms(p.id) or {}
        return {"id": p.id, "text": row.get("text"), "source": row.get("source"), "action": p.plan.action,
                "target": p.plan.target, "desc": p.plan.desc, "mode": p.plan.mode, "spoken": p.plan.spoken,
                "reading": p.reading, "explicit": p.explicit,
                "confidence": row.get("confidence"), "deadline": p.deadline, "hold_ms": int((p.deadline - p.started) * 1000)}

    async def hold(self, cid: int, plan: Plan, text: str) -> bool:
        """Show the confirm card and wait. True: dispatch it. False: it was cancelled (or, for an explicit
        hold, never confirmed)."""
        row = db.get_comms(cid) or {}
        explicit = needs_explicit(plan, row.get("source"), row.get("confidence"))
        ms = explicit_hold_s() * 1000 if explicit else hold_ms()
        if not explicit and (ms <= 0 or not plan.holdable or is_fast(text)):
            return True
        if self.pending:                              # a newer utterance replaces the one on screen
            await self.cancel(self.pending.id, "replaced by a newer utterance")
        now = time.time()
        p = self.pending = Pending(cid, now, now + ms / 1000, plan, explicit=explicit)
        await self.update(cid, status="held")
        if explicit:
            log.info("comms #%s: explicit hold (%s at %s confidence)", cid, plan.action,
                     "unknown" if row.get("confidence") is None else f"{row['confidence']:.0%}")
        if plan.spoken and read_back():
            # read it back first; the countdown (and the mic listening for "no") starts once she's done,
            # so the mic doesn't hear her and the hold isn't spent talking over you
            p.reading = True
            await hub.emit("pending", pending=self.pending_state())
            spoken = plan.spoken + (" Say confirm to send it." if explicit else "")
            await voice.speak_and_wait(spoken, kind="command", max_s=15)
            p.reading = False
            now = time.time()
            p.started, p.deadline = now, now + ms / 1000
        await hub.emit("pending", pending=self.pending_state())
        while not p.decided.is_set():
            left = p.deadline - time.time()
            if left <= 0:
                # silence sends an ordinary hold; an explicit one expires unsent
                p.outcome, p.why = ("cancel", "not confirmed: low confidence needs a click or \u201cconfirm\u201d") \
                    if explicit else ("dispatch", "")
                break
            try:
                await asyncio.wait_for(p.decided.wait(), timeout=left)
            except asyncio.TimeoutError:
                pass                                  # the deadline may have moved: look again
        if self.pending is p:
            self.pending = None
        await hub.emit("pending_done", id=cid, outcome=p.outcome, why=p.why)
        if p.outcome == "cancel":
            await self.update(cid, status="cancelled", outcome=p.why or "cancelled")
            return False
        return True

    async def cancel(self, cid: int | None, why: str = "cancelled") -> bool:
        p = self.pending
        if not p or (cid is not None and p.id != cid):
            return False
        p.outcome, p.why = "cancel", why
        self.pending = None
        p.decided.set()
        log.info("comms #%s cancelled: %s", p.id, why)
        return True

    def dispatch_now(self, cid: int) -> bool:
        p = self.pending
        if not p or p.id != cid:
            return False
        p.outcome = "dispatch"
        p.decided.set()
        return True

    async def extend(self, cid: int, ms: int = 2500) -> dict | None:
        """You started talking during the hold: keep it open until that's transcribed (capped)."""
        p = self.pending
        if not p or p.id != cid:
            return None
        if p.explicit:                                # it waits for a confirm anyway
            return self.pending_state()
        p.deadline = min(p.started + max_hold_ms() / 1000, max(p.deadline, time.time() + ms / 1000))
        await hub.emit("pending", pending=self.pending_state())
        return self.pending_state()

    # --- learning from corrections -------------------------------------------------------------
    def learn(self, heard: str, said: str) -> list[str]:
        """Words in the corrected text that whisper got wrong; each one counts toward a VOCAB suggestion."""
        a, b = heard.split(), said.split()
        clean = lambda w: re.sub(r"^[^\w&]+|[^\w&]+$", "", w)
        sm = difflib.SequenceMatcher(None, [clean(w).lower() for w in a], [clean(w).lower() for w in b])
        learned = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag not in ("replace", "insert"):
                continue
            was = " ".join(a[i1:i2])
            for w in b[j1:j2]:
                w = clean(w)
                if len(w) >= 3 and w.lower() not in _STOP and not w.isdigit():
                    db.count_mishear(w.lower(), w, was or None)
                    learned.append(w)
        if learned:
            log.info("corrections: %d word(s) counted toward VOCAB suggestions", len(learned))
        return learned

    def vocab(self) -> list[str]:
        return [w.strip() for w in voice_conf("VOCAB", "").split(",") if w.strip()]

    def suggestions(self) -> list[dict]:
        have = {w.lower() for w in self.vocab()}
        return [m for m in db.mishears(SUGGEST_AT) if m["word"] not in have]

    def add_vocab(self, word: str) -> list[str]:
        word = word.strip().strip(",")
        words = self.vocab()
        if word and word.lower() not in {w.lower() for w in words}:
            words.append(word)
            set_voice_conf("VOCAB", ",".join(words), "Words whisper should expect (comma-separated): project names, jargon")
        return words


comms = Comms()
