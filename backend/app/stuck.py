"""Stuck detection: a working session with no tool calls or results for STUCK_MINUTES (vario.conf, default 10)
gets info.stuck (the "possibly stuck" badge) and one low-urgency report per turn, with its last 3 transcript
lines as evidence. When tool activity resumes or the turn ends, the badge clears and the report is closed."""
import asyncio
import logging
import time

from . import db
from .config import setting
from .evidence import last_lines
from .hub import hub
from .reports import reports

log = logging.getLogger("stuck")
STUCK_S = float(setting("STUCK_MINUTES", "10")) * 60
CHECK_S = 20


def _key(s) -> str:
    return f"stuck:{s.id}:{int(s.turn_started_at or 0)}"


async def check(s, events_of, now: float):
    quiet_since = max(s.turn_started_at or 0, s.info.last_tool_at or 0)
    stuck = s.state == "working" and quiet_since > 0 and now - quiet_since >= STUCK_S
    if stuck == s.info.stuck:
        return
    s.info.stuck = stuck
    await hub.emit("session_state", session=s.to_dict())
    if stuck:
        mins = int((now - quiet_since) // 60)
        log.info("%s possibly stuck (%d min without tool activity)", s.name, mins)
        await reports.create(source="session", project=s.name, type="fyi", urgency=2,
                             summary=f"{s.name} may be stuck: no tool activity for {mins} minutes.",
                             body="\n".join(last_lines(events_of(s))), session_id=s.id, key=_key(s), triage=False)
    else:
        r = db.find_report(_key(s))
        if r and r["status"] in ("queued", "delivered"):
            await reports.answer(r["id"], "activity resumed", s.id, status="skipped")


async def loop(sessions, events_of):
    """sessions(): live session objects (terminal and dashboard); events_of(s): its recent transcript events."""
    while True:
        await asyncio.sleep(CHECK_S)
        now = time.time()
        for s in list(sessions()):
            try:
                await check(s, events_of, now)
            except Exception:
                log.exception("stuck check for %s", getattr(s, "name", "?"))
