"""Session events -> reports. Shared by SDK (dashboard) sessions and terminal transcript sessions."""
import logging

from . import voice
from .alerts import final_question, permission_ask, truncate
from .reports import reports

log = logging.getLogger("session_reports")


async def blocked_on_tool(sid: str, project: str, tool: str, inp: dict, *, key: str, t0: float | None = None):
    ask, needs_llm = permission_ask(tool, inp)
    await reports.create(source="session", project=project, type="decision_needed", urgency=4,
                         summary="" if needs_llm else ask, body=ask, session_id=sid, key=f"block:{key}",
                         wait_triage=needs_llm, t0=t0)


async def blocked_generic(sid: str, project: str, message: str, *, key: str, t0: float | None = None):
    await reports.create(source="session", project=project, type="decision_needed", urgency=4,
                         summary=message, body=message, session_id=sid, key=f"block:{key}", t0=t0)


async def finished(sid: str, project: str, final_text: str | None, *, key: str, t0: float | None = None):
    await reports.resolve_session(sid, "continued in session")
    text = final_text or ""
    q = final_question(text)
    if q:   # the turn ended by asking something: that's a decision, spoken like a block
        await reports.create(source="session", project=project, type="decision_needed", urgency=4,
                             summary=q, body=text, session_id=sid, key=f"final:{key}", t0=t0)
        return
    spoken = voice.clean(text) if text else ""
    if not spoken:      # interrupted / empty turn: nothing worth reporting
        return
    await reports.create(source="session", project=project, type="fyi", urgency=2,
                         summary=f"done. {truncate(spoken)}", body=text,
                         session_id=sid, key=f"final:{key}", t0=t0)


async def errored(sid: str, project: str, reason: str | None, *, key: str, t0: float | None = None):
    await reports.error(project=project, summary=f"error, {truncate(voice.clean(reason or '') or 'the session stopped', 100)}",
                        body=reason or "", session_id=sid, key=f"error:{key}", t0=t0)


async def moved_on(sid: str, decision_text: str, key: str | None = None):
    await reports.resolve_session(sid, decision_text, only_key=f"block:{key}" if key else None)
