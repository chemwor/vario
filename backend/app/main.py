"""Vario backend: REST for actions, one websocket for live typed events."""
import asyncio
import logging
import os
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import claude_agent_sdk
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import commands, db, evidence, firstrun, inbox, research, stuck, voice
from . import music as music_mod
from .alerts import compressor
from .config import CONFIG_FILE, PROFILE, SCOUTS_ENABLED, STT_MODE, VERSION, WEB_DIR, WORK, set_voice_conf, setting
from .config import voice_conf as voice_conf_get
from .stt import stt, vocab_prompt
from .reports import _public as report_public, reports
from .hub import hub
from .sessions import manager
from .terminal import TerminalWatcher
from .briefing import Briefing
from .router import SCOUT_RE, Router, split_project_prefix
from .scouts import SCOUTS_OFF_SAY, scouts
from .awake import Awake
from .comms import (Plan, below_floor, comms, confirm_heard, correction, flag_below, is_cancel, is_confirm,
                    min_dispatch_conf, public as comms_public, strict_conf)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")
terminal = TerminalWatcher(owned_ids=manager.owned_ids)


def all_sessions() -> list[dict]:
    return [s.to_dict() for s in manager.sessions.values()] + [s.to_dict() for s in terminal.sessions.values()]


async def voice_state() -> dict:
    return {"global_muted": voice.global_muted(), "tts_online": await voice.tts_online()}


def session_muted(sid: str | None) -> bool:
    if not sid:
        return False
    if sid in manager.sessions:
        return manager.sessions[sid].muted
    return sid in set(db.get_pref("terminal_muted", []))


async def voice_changed():
    await hub.emit("tts_status", voice=await voice_state())


async def tts_monitor():
    last = None
    while True:
        state = await voice_state()
        if state != last:
            await hub.emit("tts_status", voice=state)
            last = state
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app):
    manager.load()
    reports.muted_check = lambda r: session_muted(r.get("session_id"))
    reports.is_dashboard_session = lambda sid: bool(sid) and sid in manager.sessions
    reports.on_outcome = lambda sid, outcome: asyncio.create_task(outcome_changed(sid, outcome))
    reports.collapse_existing()
    tasks = [asyncio.create_task(c) for c in (terminal.run(), tts_monitor(), inbox.watch(), reports.triage_worker(),
                                              reports.reminder_loop(), compressor.warm(), awake.loop(),
                                              stuck.loop(live_sessions, live_events), housekeeping())]
    if STT_MODE == "local":
        tasks.append(asyncio.create_task(stt.warm()))
    log.info("profile: %s (config %s; stt=%s, scouts=%s)", PROFILE, CONFIG_FILE, STT_MODE,
             "on" if SCOUTS_ENABLED else "off")
    for r in db.list_reports(("queued",)):          # re-triage anything left unclassified by a restart
        if not r["triaged"]:
            reports.triage_q.put_nowait(r["id"])
    log.info("Agent SDK %s; %d dashboard sessions restored", claude_agent_sdk.__version__, len(manager.sessions))
    yield
    for t in tasks:
        t.cancel()
    await stt.close()
    await manager.shutdown()


app = FastAPI(title="Vario", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
                   allow_methods=["*"], allow_headers=["*"])


def owned(sid: str):
    s = manager.sessions.get(sid)
    if not s:
        raise HTTPException(404, "No dashboard session with that id")
    return s


# --- models ---------------------------------------------------------------------
class NewSession(BaseModel):
    cwd: str
    prompt: str | None = None
    name: str | None = None


class Turn(BaseModel):
    text: str


class Decision(BaseModel):
    allow: bool
    message: str | None = None


class Flag(BaseModel):
    value: bool


class Rename(BaseModel):
    name: str


class Adopt(BaseModel):
    id: str
    cwd: str


# --- sessions -------------------------------------------------------------------
@app.get("/api/sessions")
async def list_sessions():
    return all_sessions()


@app.post("/api/sessions")
async def new_session(body: NewSession):
    try:
        s = await manager.create(body.cwd, body.prompt, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return s.to_dict()


@app.get("/api/sessions/{sid}/events")
async def session_events(sid: str):
    if sid in manager.sessions:
        return db.events(sid)
    t = terminal.get(sid)
    if t:
        return list(t.events)
    raise HTTPException(404)


@app.get("/api/sessions/{sid}/tests")
async def session_tests(sid: str):
    """Test runs this session made (newest first), with counts, failing names and the output's tail."""
    s = _session_obj(sid)
    if not s:
        raise HTTPException(404)
    return {"runs": s.info.tests.all()}


@app.post("/api/sessions/{sid}/messages")
async def send_turn(sid: str, body: Turn):
    s = owned(sid)
    if not body.text.strip():
        raise HTTPException(400, "Empty message")
    asyncio.create_task(s.send(body.text.strip()))
    return {"ok": True}


@app.get("/api/terminal/{sid}/reach")
async def terminal_reach(sid: str):
    """Whether the dashboard can type into this terminal session (tmux / cmux), for the message box."""
    if not terminal.get(sid):
        raise HTTPException(404)
    route = await router.terminal_route(sid)
    return {"via": route["via"] if route else None}


@app.post("/api/terminal/{sid}/messages")
async def terminal_message(sid: str, body: Turn):
    """Type a message into a terminal session (refused while a permission prompt is showing)."""
    if not terminal.get(sid):
        raise HTTPException(404)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Empty message")
    res = await router.send_to_terminal(sid, text)
    return {"ok": res.answered, "say": res.say, "routed_to": res.routed_to}


@app.post("/api/sessions/{sid}/interrupt")
async def interrupt(sid: str):
    await owned(sid).interrupt()
    return {"ok": True}


@app.post("/api/sessions/{sid}/permissions/{rid}")
async def decide(sid: str, rid: str, body: Decision):
    if not await owned(sid).resolve_permission(rid, body.allow, body.message):
        raise HTTPException(404, "No pending request")
    return {"ok": True}


async def set_muted(sid: str, muted: bool):
    if sid in manager.sessions:
        s = manager.sessions[sid]
        s.muted = muted
        await s.publish()
    elif terminal.get(sid):
        ids = set(db.get_pref("terminal_muted", []))
        ids.add(sid) if muted else ids.discard(sid)
        db.set_pref("terminal_muted", sorted(ids))
        await hub.emit("session_state", session=terminal.get(sid).to_dict())
    else:
        raise HTTPException(404)


@app.post("/api/sessions/{sid}/mute")
async def mute_session(sid: str, body: Flag):
    await set_muted(sid, body.value)
    return {"ok": True}


@app.patch("/api/sessions/{sid}")
async def rename(sid: str, body: Rename):
    s = owned(sid)
    s.name = body.name.strip() or s.name
    await s.publish()
    return s.to_dict()


@app.delete("/api/sessions/{sid}")
async def remove(sid: str):
    owned(sid)
    await manager.remove(sid)
    return {"ok": True}


@app.get("/api/recent")
async def recent(limit: int = 40):
    infos = await asyncio.to_thread(claude_agent_sdk.list_sessions, None, limit)
    mine = manager.owned_ids()
    now_ms = time.time() * 1000
    return [{"id": i.session_id, "cwd": i.cwd, "name": os.path.basename((i.cwd or "").rstrip("/")),
             "title": i.custom_title or i.summary or (i.first_prompt or "")[:80],
             "last_modified": int(i.last_modified) / 1000, "owned": i.session_id in mine,
             "idle": now_ms - int(i.last_modified) > 10 * 60 * 1000} for i in infos]


@app.post("/api/sessions/adopt")
async def adopt(body: Adopt):
    """Bring an idle (10+ min) transcript under dashboard control so it can be resumed via the SDK."""
    if body.id not in manager.sessions:
        info = next((i for i in await asyncio.to_thread(claude_agent_sdk.list_sessions, None, 200)
                     if i.session_id == body.id), None)
        if not info:
            raise HTTPException(404, "Unknown session")
        if time.time() * 1000 - int(info.last_modified) < 10 * 60 * 1000:
            raise HTTPException(409, "Session is still active in a terminal")
        terminal.sessions.pop(body.id, None)
        await hub.emit("session_removed", session_id=body.id)
        await manager.create(body.cwd, None, sid=body.id)
    return manager.sessions[body.id].to_dict()


# --- directories -------------------------------------------------------------------
@app.get("/api/projects")
async def projects():
    infos = await asyncio.to_thread(claude_agent_sdk.list_sessions, None, 300)
    seen, out = set(), []
    for i in infos:
        if i.cwd and i.cwd not in seen and os.path.isdir(i.cwd):
            seen.add(i.cwd)
            out.append({"cwd": i.cwd, "name": os.path.basename(i.cwd.rstrip("/"))})
    return out


@app.get("/api/dirs")
async def dirs(path: str = "~"):
    p = Path(os.path.expanduser(path))
    if not p.is_dir():
        p = p.parent
    try:
        subs = sorted(c.name for c in p.iterdir() if c.is_dir() and not c.name.startswith("."))[:300]
    except OSError as e:
        raise HTTPException(400, str(e))
    return {"path": str(p), "parent": str(p.parent), "dirs": subs}


# --- voice -----------------------------------------------------------------------
@app.get("/api/voice")
async def get_voice():
    return await voice_state()


@app.post("/api/voice/mute")
async def global_mute(body: Flag):
    voice.set_global_mute(body.value)
    await voice_changed()
    return await voice_state()


@app.post("/api/sessions/{sid}/narrate")
async def narrate_session(sid: str, body: Flag):
    """Ambient narration of every assistant message (off by default; alerts are separate)."""
    if sid in manager.sessions:
        s = manager.sessions[sid]
        s.narrate = body.value
        await s.publish()
    elif terminal.get(sid):
        ids = set(db.get_pref("terminal_narrate", []))
        ids.add(sid) if body.value else ids.discard(sid)
        db.set_pref("terminal_narrate", sorted(ids))
        await hub.emit("session_state", session=terminal.get(sid).to_dict())
    else:
        raise HTTPException(404)
    return {"ok": True}


# --- hook events + reports ----------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/profile")
async def profile():
    """What the UI may offer: work profile = local STT only, no scouts."""
    return {"profile": PROFILE, "work": WORK, "scouts": SCOUTS_ENABLED, "demo": os.environ.get("VARIO_DEMO") == "1",
            "version": VERSION,
            "stt": {"mode": STT_MODE, **stt.status()}, "triage": compressor.auth}


@app.post("/api/voice/transcribe")
async def transcribe(request: Request, partial: bool = False):
    """16 kHz mono WAV from the dashboard mic -> text (local whisper). The browser then posts the text to
    /api/voice/utterance, the same entry point Web Speech results use."""
    if STT_MODE != "local":
        raise HTTPException(400, "local STT is off (STT=webspeech is set)")
    wav = await request.body()
    if not wav.startswith(b"RIFF") or len(wav) > 20_000_000:
        raise HTTPException(400, "expected a WAV body under 20 MB")
    if partial and stt.lock.locked():
        return {"text": None, "busy": True}           # live caption: skip a frame rather than queue behind one
    names = [x["name"] for x in sorted(all_sessions(), key=lambda x: -(x.get("updated_at") or 0))] + db.recent_projects()
    try:
        return await stt.transcribe(wav, vocab_prompt(names), partial=partial)
    except Exception as e:
        raise HTTPException(503, f"local STT failed: {e}")


@app.post("/api/hooks/{event}")
async def hook_event(event: str, payload: dict):
    """Claude Code hook payloads POSTed by ~/.claude/hooks/notify.sh (Notification) and speak.sh (Stop)."""
    t0 = time.time()
    handler = {"notification": terminal.on_notification, "stop": terminal.on_stop}.get(event)
    if not handler:
        raise HTTPException(404, "unknown hook event")
    if event == "stop" and payload.get("stop_hook_active"):
        return {"accepted": False}
    # reply at once so the hook (curl -m 1) never times out into its direct-speech fallback
    task = asyncio.create_task(handler(payload, t0))
    task.add_done_callback(lambda t: t.exception() and log.error("hook %s failed: %r", event, t.exception()))
    return {"accepted": True}


@app.get("/api/reports")
async def get_reports(status: str = "queued,delivered", limit: int = 200):
    rows = db.list_reports(tuple(status.split(",")), limit)
    counts: dict[str, dict] = {}
    for r in db.list_reports(("queued", "delivered"), 1000):
        c = counts.setdefault(r["project"], {"total": 0, "decision_needed": 0})
        c["total"] += 1
        c["decision_needed"] += r["type"] == "decision_needed"
    return {"reports": [report_public(r) for r in rows], "counts": counts}


class ReportUpdate(BaseModel):
    status: str
    decision_text: str | None = None


def live_sessions():
    """Session objects (dashboard + terminal), for loops that need more than the dicts."""
    return [*manager.sessions.values(), *terminal.sessions.values()]


def live_events(s) -> list[dict]:
    return db.events(s.id) if s.id in manager.sessions else list(s.events)


def session_cwd(sid: str | None) -> str | None:
    s = manager.sessions.get(sid) or terminal.get(sid) if sid else None
    return getattr(s, "cwd", None) or None


@app.get("/api/reports/{rid}/evidence")
async def report_evidence(rid: int):
    """What a report is about: diffs, the exact command, sources, transcript lines (see evidence.py)."""
    r = db.get_report(rid)
    if not r:
        raise HTTPException(404)
    live = None
    s = manager.sessions.get(r.get("session_id"))
    key = r.get("dedupe_key") or ""
    if s and key.startswith("block:") and key[6:] in s.permissions:    # dashboard permission prompt, in memory
        p = s.permissions[key[6:]]
        live = {"id": key[6:], "name": p["tool"], "input": p["input"]}
    return await asyncio.to_thread(evidence.for_report, r, cwd=session_cwd(r.get("session_id")), live_call=live)


@app.get("/api/sessions/{sid}/turn")
async def session_turn(sid: str):
    """Files edited in the session's latest turn, each with a diff."""
    return {"files": await asyncio.to_thread(evidence.turn_files, sid, session_cwd(sid))}


@app.post("/api/reports/{rid}")
async def update_report(rid: int, body: ReportUpdate):
    if body.status not in ("answered", "skipped", "queued"):
        raise HTTPException(400, "status must be answered, skipped or queued")
    r = await reports.answer(rid, body.decision_text or body.status, "dashboard", status=body.status)
    if not r:
        raise HTTPException(404)
    return r


@app.post("/api/voice/stop")
async def stop_speech():
    await voice.stop()
    return {"ok": True}


def routable_sessions() -> list[dict]:
    """Sessions you can address by name: live ones, plus any session that still has an open report
    (e.g. a terminal session the watcher stopped tracking after a restart)."""
    out = all_sessions()
    known = {s["id"] for s in out}
    for r in db.list_reports(("queued", "delivered"), 500):
        sid = r.get("session_id")
        if sid and sid not in known:
            known.add(sid)
            out.append({"id": sid, "name": r["project"], "cwd": "", "source": "terminal", "readonly": True,
                        "state": "waiting" if r["type"] == "decision_needed" else "done", "activity": None,
                        "updated_at": r["created_at"]})
    return out


class _Ctx:
    all_sessions = staticmethod(routable_sessions)
    set_muted = staticmethod(set_muted)
    voice_changed = staticmethod(voice_changed)

    @staticmethod
    async def interrupt(sid):
        await owned(sid).interrupt()

    @staticmethod
    async def retry(sid):
        return await retry_session(sid)

    @staticmethod
    async def research(kind, sid):
        if not SCOUTS_ENABLED:
            raise HTTPException(403, SCOUTS_OFF_SAY)
        return await launch_research(kind, sid)


router = Router(manager, terminal, _Ctx, reports)
briefing = Briefing(reports, router)
reports.on_created = briefing.report_created
scouts.on_landed = briefing.research_landed
awake = Awake(all_sessions, briefing)


# --- retry, outcomes, context, housekeeping ------------------------------------------------------
RETRY_DEFAULT = "That last attempt failed ({error}). Please try it again."


def retry_message(sid: str) -> str:
    """The session's retry message: a per-session pref, else RETRY_MESSAGE (vario.conf); {error} is the last error."""
    tmpl = db.get_pref("retry_messages", {}).get(sid) or setting("RETRY_MESSAGE", RETRY_DEFAULT)
    errs = [r for r in db.error_reports(sid) if not (r.get("decision_text") or "").startswith("collapsed into #")]
    err = (errs[-1]["body"] or errs[-1]["summary"]).strip().splitlines()[0][:200] if errs else "an error"
    return tmpl.replace("{error}", err)


async def retry_session(sid: str) -> dict:
    """Send the retry message to a session and close its open error reports. {"ok", "say", "routed_to"}."""
    s = manager.sessions.get(sid) or terminal.get(sid)
    if not s:
        return {"ok": False, "say": "I can't find that session."}
    msg = retry_message(sid)
    if sid in manager.sessions:
        asyncio.create_task(s.send(msg))
        ok, where, say = True, sid, f"Retrying {s.name}."
    else:
        res = await router.send_to_terminal(sid, msg)
        ok, where = res.answered, res.routed_to
        say = f"Retrying {s.name}." if ok else res.say
    if ok:
        for r in db.error_reports(sid):
            if r["status"] in ("queued", "delivered"):
                await reports.answer(r["id"], "retried", where)
    log.info("retry %s via %s: %s", sid[:8], where, "sent" if ok else "failed")
    return {"ok": ok, "say": say, "routed_to": where}


@app.post("/api/sessions/{sid}/retry")
async def retry(sid: str):
    if sid not in manager.sessions and not terminal.get(sid):
        raise HTTPException(404)
    res = await retry_session(sid)
    db.log_decision(project=(manager.sessions.get(sid) or terminal.get(sid)).name, utterance="retry (button)",
                    action="retry", routed_to=res.get("routed_to"), result=res["say"])
    return res


async def outcome_changed(sid: str, outcome: dict):
    s = manager.sessions.get(sid) or terminal.get(sid)
    if s:
        s.info.set_outcome(outcome)
        await hub.emit("session_state", session=s.to_dict())


CTX_ALERT, CTX_RESET = 0.90, 0.75


async def check_context(s):
    """A session crossing 90% of its context window files one low-urgency report (re-armed below 75%)."""
    from .info import context_window
    used = s.info.context or 0
    frac = used / context_window(s.info.model, used)
    if frac >= CTX_ALERT and not s.info.ctx_alerted:
        s.info.ctx_alerted = True
        await reports.create(source="session", project=s.name, type="fyi", urgency=2,
                             summary=f"{s.name} is at {round(frac * 100)}% of its context. Time to wrap up or hand off.",
                             body=f"{used:,} tokens in the last request.", session_id=s.id,
                             key=f"ctx:{s.id}:{int(getattr(s, 'turn_started_at', 0) or 0)}", triage=False)
    elif frac < CTX_RESET:
        s.info.ctx_alerted = False


async def housekeeping():
    """Every minute: context alerts. Every hour: purge ignored comms older than COMMS_RETENTION_DAYS (default 3)."""
    n = 0
    while True:
        if n % 60 == 0:
            try:
                gone = db.purge_comms(float(setting("COMMS_RETENTION_DAYS", "3")))
                if gone:
                    log.info("purged %d ignored comms entries", gone)
            except Exception:
                log.exception("comms purge")
        for s in live_sessions():
            try:
                await check_context(s)
            except Exception:
                log.exception("context check for %s", getattr(s, "name", "?"))
        n += 1
        await asyncio.sleep(60)


# --- first-run setup (see firstrun.py) -----------------------------------------------------------------
class SetupMark(BaseModel):
    status: str                 # done | skipped
    note: str | None = None


class HooksConsent(BaseModel):
    diff: list[str]             # the diff the user reviewed; the install refuses if settings.json moved on


class VoicePick(BaseModel):
    voice: str
    preview: bool = True


@app.get("/api/setup")
async def setup_state():
    return {**firstrun.state(), "jobs": firstrun.job_state(), "warnings": firstrun.warnings()}


@app.post("/api/setup/steps/{step}")
async def setup_mark(step: str, body: SetupMark):
    if step not in firstrun.STEPS or body.status not in ("done", "skipped"):
        raise HTTPException(400, "unknown step or status")
    return firstrun.mark(step, body.status, body.note or "")


@app.post("/api/setup/finish")
async def setup_finish(profile: str | None = None):
    return firstrun.finish(profile)


@app.get("/api/setup/deps")
async def setup_deps():
    return await asyncio.to_thread(firstrun.deps)


@app.post("/api/setup/runtime")
async def setup_runtime():
    asyncio.create_task(firstrun.install_runtime())
    return {"started": True}


@app.get("/api/setup/models")
async def setup_models():
    return firstrun.model_specs()


@app.post("/api/setup/models/{key}")
async def setup_model(key: str, verify: bool = False):
    if key not in firstrun.MANIFEST:
        raise HTTPException(404)
    asyncio.create_task(firstrun.fetch_model(key, verify_only=verify))
    return {"started": True}


@app.get("/api/setup/hooks")
async def setup_hooks_plan():
    return await asyncio.to_thread(firstrun.hooks_plan)


@app.post("/api/setup/hooks")
async def setup_hooks_install(body: HooksConsent):
    return await asyncio.to_thread(firstrun.install_hooks, body.diff)


@app.post("/api/setup/sound")
async def setup_sound(start: bool = False):
    if start:
        await firstrun.start_bubo()
    return await firstrun.test_sound()


@app.get("/api/setup/voices")
async def setup_voices():
    return {"voices": firstrun.voices(), "speed": voice_conf_get("SPEED", "1.0")}


@app.post("/api/setup/voice")
async def setup_voice(body: VoicePick):
    if body.voice not in {v["id"] for v in firstrun.voices()}:
        raise HTTPException(400, "unknown voice")
    set_voice_conf("VOICE", body.voice)
    spoke = False
    if body.preview:
        name = next(v["name"] for v in firstrun.voices() if v["id"] == body.voice)
        spoke = await voice.speak(f"Hi, this is {name}. I'll read your reports like this.", kind="command")
    return {"voice": body.voice, "spoke": spoke}


@app.get("/api/awake")
async def get_awake():
    return awake.state()
inbox.on_scout_report = scouts.report_landed


class NewScout(BaseModel):
    brief: str = ""
    project: str | None = None
    linked_report_id: int | None = None
    kind: str | None = None          # a research playbook (GET /api/research/playbooks)
    session_id: str | None = None    # research the product this session is working on
    focus: str | None = None         # optional: what to look at especially


def _session_obj(sid: str | None):
    return (manager.sessions.get(sid) or terminal.get(sid)) if sid else None


def recent_asks(s) -> list[str]:
    """The last few things the user asked the session (its own words, no tool output)."""
    asks = [e["text"] for e in live_events(s) if e.get("role") == "user" and e.get("kind") == "text"
            and e.get("text") and not e["text"].startswith("<")]
    return asks[-4:]


async def launch_research(kind: str, sid: str | None, focus: str | None = None, project: str | None = None) -> dict:
    s = _session_obj(sid)
    if sid and not s:
        raise HTTPException(404, "no such session")
    if s:
        ctx = await asyncio.to_thread(research.product_context, s.to_dict(), recent_asks(s))
        return await scouts.launch("", s.name, kind=kind, session_id=s.id, focus=focus, context=ctx)
    if not project:
        raise HTTPException(400, "pick a session (or name a project) to research")
    return await scouts.launch("", project, kind=kind, focus=focus, context=f"Product / project name: {project}")


@app.get("/api/research/playbooks")
async def research_playbooks():
    return {"playbooks": [research.public(p) for p in research.playbooks().values()],
            "custom_dir": str(research.USER_DIR), "library": str(research.LIBRARY)}


@app.post("/api/scouts")
async def launch_scout(body: NewScout):
    if not SCOUTS_ENABLED:
        raise HTTPException(403, SCOUTS_OFF_SAY)
    if body.kind:
        try:
            return await launch_research(body.kind, body.session_id, body.focus, body.project)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    if not body.brief.strip():
        raise HTTPException(400, "empty brief")
    return await scouts.launch(body.brief, body.project, body.linked_report_id)


HANDOFF_NEXT = {
    "features": "Then propose a plan for the #1 recommendation. Don't start building until I say so.",
    "competition": "Then tell me in a few lines what it changes for how we position or build this.",
    "market": "Then tell me in a few lines what it changes for who we build this for first.",
    "users": "Then tell me which pain we should lead with, and anything in the product it suggests changing.",
}


@app.post("/api/scouts/{scout_id}/handoff")
async def handoff_research(scout_id: int):
    """Send a finished research report to the session it was about: a pointer to the markdown file, not the text."""
    sc = db.get_scout(scout_id)
    if not sc or not sc.get("library_path"):
        raise HTTPException(404, "no finished research report for that scout")
    s = _session_obj(sc.get("session_id"))
    if not s:
        raise HTTPException(409, "that session isn't running anymore")
    msg = (f"I ran research for this project ({sc['brief']}). The report is at {sc['library_path']}: read it. "
           + HANDOFF_NEXT.get(sc.get("kind") or "", "Then tell me what it changes for this project."))
    if s.id in manager.sessions:
        asyncio.create_task(s.send(msg))
        res = {"ok": True, "say": f"Sent the research to {s.name}.", "routed_to": s.id}
    else:
        r = await router.send_to_terminal(s.id, msg)
        res = {"ok": r.answered, "say": r.say if not r.answered else f"Sent the research to {s.name}.",
               "routed_to": r.routed_to}
    db.log_decision(project=s.name, utterance="hand research to session", action="reply", routed_to=res.get("routed_to"),
                    result=res["say"])
    return res


@app.get("/api/scouts")
async def get_scouts():
    return db.list_scouts()


class Utterance(BaseModel):
    text: str
    source: str = "harness"            # mic | ptt | harness
    selected_session_id: str | None = None
    confidence: float | None = None    # whisper's, from /api/voice/transcribe
    stt_ms: int | None = None
    during_hold: int | None = None     # said while this utterance's confirm card was up (the mic listens for "no")
    report_id: int | None = None       # answer this report (the evidence panel's Approve / Deny / reply)


@app.post("/api/voice/utterance")
async def utterance(body: Utterance):
    """The ONE entry point for spoken input. The mic, push-to-talk and the dev harness all post here,
    so the harness exercises exactly the classify/route pipeline the mic uses."""
    if body.report_id is not None and not db.get_report(body.report_id):
        raise HTTPException(404, "no such report")      # before it's logged, so Comms doesn't keep a dead row
    return await process_utterance(body.text, body.source, body.selected_session_id,
                                   confidence=body.confidence, stt_ms=body.stt_ms, during_hold=body.during_hold,
                                   report_id=body.report_id)


async def process_utterance(text: str, source: str, selected: str | None, *, confidence: float | None = None,
                            stt_ms: int | None = None, heard: str | None = None, parent_id: int | None = None,
                            during_hold: int | None = None, report_id: int | None = None) -> dict:
    """Log it, handle corrections and cancels, then plan -> confirm hold -> run (see comms.py)."""
    text = text.strip()
    if not text:
        raise HTTPException(400, "empty utterance")
    row = await comms.record(source=source, text=text, heard=heard, confidence=confidence, stt_ms=stt_ms,
                             selected_session_id=selected, parent_id=parent_id)
    cid = row["id"]

    # Hard floor: too unsure to act on. Never reaches the command parser, the classifier or the briefing.
    # The one exception: a bare "no"/"cancel" can still stop a confirm card (that never dispatches anything).
    if below_floor(source, confidence) and not (comms.pending and is_cancel(text)):
        why = f"rejected: low confidence ({confidence:.0%} < {min_dispatch_conf():.0%})"
        await comms.update(cid, status="rejected", outcome=why)
        log.info("comms #%s %s: %r", cid, why, text)
        return {"mode": "rejected", "action": None, "rejected": True, "reason": why, "say": None, "comms_id": cid}

    # "no, I said ..." -> cancel what's pending (or take the last utterance) and re-run the corrected words
    fixed = correction(text)
    if fixed and not heard:
        p = comms.pending
        prev = db.get_comms(p.id) if p else db.last_comms(exclude=cid)
        if p:
            await comms.cancel(p.id, f"corrected to \u201c{fixed}\u201d")
        await comms.update(cid, mode="command", action="correct", via="local", target=None,
                           descr=f"correct \u201c{(prev or {}).get('text', '')}\u201d \u2192 \u201c{fixed}\u201d",
                           status="dispatched", outcome="re-running the corrected words")
        if prev:
            comms.learn(prev["text"], fixed)
            await comms.update(prev["id"], corrected_by=cid)
        return await process_utterance(fixed, "correction", (prev or {}).get("selected_session_id") or selected,
                                       confidence=confidence, heard=(prev or {}).get("text"),
                                       parent_id=(prev or {}).get("id"))

    # something said while a confirm card is up: "no"/"cancel" cancels it; "confirm" sends a card that waits
    # for one (if it was heard clearly enough itself); anything else replaces it
    if comms.pending and is_confirm(text):
        # "confirm" / "send" sends the card on screen now (an ordinary one would have gone on silence anyway)
        pid = comms.pending.id
        if comms.pending.explicit and not confirm_heard(text, source, confidence):
            why = f"rejected: low confidence for a confirm ({'unknown' if confidence is None else f'{confidence:.0%}'} < {strict_conf():.0%})"
            await comms.update(cid, mode="command", action="confirm", via="local", descr=f"confirm #{pid}",
                               status="rejected", outcome=why)
            return {"mode": "command", "action": "confirm", "rejected": True, "reason": why, "comms_id": cid}
        comms.dispatch_now(pid)
        await comms.update(cid, mode="command", action="confirm", via="local", descr=f"confirm #{pid}",
                           status="dispatched", outcome=f"confirmed #{pid}")
        return {"mode": "command", "action": "confirm", "say": "Confirmed.", "comms_id": cid}
    if comms.pending:
        if is_cancel(text):
            pid = comms.pending.id
            await comms.cancel(pid, "cancelled by voice")
            await comms.update(cid, mode="command", action="cancel", via="local", descr=f"cancel #{pid}",
                               status="dispatched", outcome="Cancelled.")
            await voice.speak("Cancelled.", kind="command")
            return {"mode": "command", "action": "cancel", "say": "Cancelled.", "comms_id": cid}
        if briefing.armed and briefing.phase != "listening":
            # the briefing's card: it isn't taking new answers until this one is settled, so don't drop it
            why = "ignored: a confirm card is up (say confirm or cancel)"
            await comms.update(cid, status="ignored", outcome=why)
            return {"mode": "briefing", "accepted": False, "reason": why, "comms_id": cid}
        await comms.cancel(comms.pending.id, "replaced by a newer utterance")
    elif during_hold and is_cancel(text):
        # a "no" meant for a card that already went out: don't let it turn into a new "deny"
        say = "Too late, that was already sent. Say it again to correct it."
        await comms.update(cid, mode="command", action="cancel", via="local", descr=f"cancel #{during_hold}",
                           status="ignored", outcome=say)
        await voice.speak(say, kind="command")
        return {"mode": "command", "action": "cancel", "say": say, "comms_id": cid}

    # a reply aimed at a specific report goes to the briefing only if that's the report it's reading
    if briefing.armed and (report_id is None or (briefing.current or {}).get("id") == report_id):
        res = await briefing.utterance(text, source, cid)
        if not res.get("accepted"):
            await comms.finish(cid, res, status="ignored", outcome=res.get("reason") or "not accepted")
        elif res.get("action") == "stop":
            await comms.update(cid, mode="briefing", action="stop", via="local", descr="stop briefing")
            await comms.finish(cid, {"say": "stopped briefing"})
        return {"mode": "briefing", **res, "comms_id": cid}

    plan = await plan_utterance(text, source, selected, cid, heard, report_id)
    await comms.classified(cid, plan)
    if plan.action in ("unintelligible", "unknown"):
        # gibberish (the classifier said so) or no such command: logged, never dispatched
        res = await plan.run()
        why = "rejected: unintelligible" if plan.action == "unintelligible" else "rejected: unknown command"
        await comms.update(cid, status="rejected", outcome=f"{why}. {res.get('say') or ''}".strip())
        return {**res, "rejected": True, "reason": why, "comms_id": cid}
    if not await comms.hold(cid, plan, text):
        return {"mode": plan.mode, "action": plan.action, "cancelled": True, "say": "Cancelled.", "comms_id": cid}
    try:
        res = await plan.run()
    except Exception as e:
        await comms.finish(cid, status="failed", outcome=f"{type(e).__name__}: {e}")
        raise
    await comms.finish(cid, res)
    return {**res, "comms_id": cid}


async def plan_utterance(text: str, source: str, selected: str | None, cid: int, heard: str | None,
                         report_id: int | None = None) -> Plan:
    """Outside briefing: arm/disarm, then commands, then replies (selected session > "<project>, ..." >
    most recently alerted session's open decision). Works out what would happen without doing it.
    report_id (the evidence panel) pins the report being answered and skips straight to the router."""
    q = lambda t: f"\u201c{t}\u201d"
    pinned = db.get_report(report_id) if report_id else None
    if report_id and not pinned:
        raise HTTPException(404, "no such report")
    if pinned and pinned["type"] != "decision_needed" and re.match(r"^(approve|deny|yes|no)\b", text, re.I):
        # a yes/no aimed at an fyi/research report would be typed into its session as "Yes, go ahead."
        async def refuse():
            return {"mode": "command", "action": "unknown", "say": "That report isn't a decision, so there's nothing to approve."}
        return Plan("command", "unknown", pinned["project"], "approve/deny on a non-decision report", refuse,
                    via="local", holdable=False)
    if not pinned and re.match(r"^(start|begin|arm)( the)? briefing( mode)?$|^briefing( mode)? on$", text, re.I):
        async def arm():
            await briefing.arm()
            return {"mode": "command", "action": "arm"}
        return Plan("command", "arm", None, "start briefing", arm, via="local", spoken="Starting the briefing.")

    m = re.match(r"^(?:(retry|try again|try it again)|(kill|stop))(?: it| that| the session)?[.!]?$", text.strip(), re.I)
    err = pinned or next((r for r in db.open_reports(type_="decision_needed")
                          if (r.get("dedupe_key") or "").startswith(("error:", "fail:"))
                          and r.get("session_id") == (selected or reports.last_alerted)), None)
    if m and err and (err.get("dedupe_key") or "").startswith(("error:", "fail:")) and err.get("session_id"):
        # "retry" / "kill it" answering an error report ("failed the same way 3 times. Retry, or kill it?")
        sid, name = err["session_id"], err["project"]
        if m[1]:
            async def do_retry():
                res = await retry_session(sid)
                await voice.speak(res["say"], kind="command")
                db.log_decision(project=name, utterance=text, action="retry", routed_to=res.get("routed_to"),
                                result=res["say"], comms_id=cid, heard=heard)
                return {"mode": "reply", "action": "retry", **res}
            return Plan("reply", "retry", name, f"retry {name}", do_retry, via="local", spoken=f"Retrying {name}.")

        async def do_kill():
            if sid in manager.sessions:
                await manager.sessions[sid].interrupt()
                say = f"Stopped {name}."
            else:
                say = f"I can't stop a terminal session; dismissed the error for {name}."
            await reports.answer(err["id"], "killed", sid, status="skipped")
            await voice.speak(say, kind="command")
            db.log_decision(project=name, utterance=text, action="kill", routed_to=sid, result=say, comms_id=cid,
                            heard=heard)
            return {"mode": "reply", "action": "kill", "say": say}
        return Plan("reply", "interrupt", name, f"kill {name}", do_kill, via="local", spoken=f"Stopping {name}.")

    hit = None if pinned else commands.match(_Ctx, text)
    if hit:
        fn, _, target = hit

        async def command():
            cmd = await commands.call(_Ctx, hit)
            if cmd.get("say"):
                await voice.speak(cmd["say"], kind="command")
            if heard is not None or source in ("rerun", "edit"):
                db.log_decision(project=target, utterance=text, action=cmd["command"], routed_to=cmd.get("session_id"),
                                result=cmd.get("say"), comms_id=cid, heard=heard)
            return {"mode": "command", **cmd}
        spoken = {"switch": f"Switching to {target}.", "mute": f"Muting {target}.", "status": "Getting the status.",
                  "interrupt": f"Stopping {target}.", "help": "Listing commands.",
                  "retry": f"Retrying {target}.", "research": f"Starting research on {target}."}.get(fn.__name__)
        return Plan("command", fn.__name__, target, f"{fn.__name__.rstrip('_')} {q(text)}", command, via="command",
                    spoken=spoken)

    report = pinned
    if not report and selected:
        report = next(iter(db.open_reports(session_id=selected, type_="decision_needed")), None)
        if not report:
            s = manager.sessions.get(selected)
            if s and not split_project_prefix(_Ctx, text)[0]:
                async def send():
                    await s.send(text)
                    await voice.speak(f"Sent to {s.name}.", kind="command")
                    db.log_decision(project=s.name, utterance=text, action="reply", routed_to=s.id, result="sent",
                                    comms_id=cid, heard=heard)
                    return {"mode": "reply", "action": "reply", "say": f"Sent to {s.name}.", "routed_to": s.id}
                return Plan("reply", "reply", s.name, f"send {q(text)} \u2192 {s.name}", send, via="local",
                            spoken=f"Telling {s.name}: {text.rstrip('.')}.")
    if not report and reports.last_alerted:
        report = next(iter(db.open_reports(session_id=reports.last_alerted, type_="decision_needed")), None)
    if not report and not split_project_prefix(_Ctx, text)[0] and not SCOUT_RE.match(text):
        async def unknown():
            say = f"Sorry, I don't know the command: {text}."
            await voice.speak(say, kind="command")
            return {"mode": "command", "command": None, "say": say}
        return Plan("command", "unknown", None, f"unknown command {q(text)}", unknown, via="local", holdable=False)

    rp = await router.plan(text, report)
    target, desc = rp.describe()
    if rp.intent.action == "unintelligible":
        async def garbled():
            say = "Sorry, I didn't catch that."
            await voice.speak(say, kind="command")
            return {"mode": "reply", "action": "unintelligible", "say": say}
        return Plan("reply", "unintelligible", target, f"unintelligible {q(text)}", garbled, via=rp.intent.via,
                    holdable=False)

    async def route():
        res = await router.run(rp, source, comms_id=cid, heard=heard)
        await voice.speak(res.say, kind="command")
        await hub.emit("reply_sent", report_id=(rp.report or {}).get("id"), utterance=text, action=res.action,
                       say=res.say, routed_to=res.routed_to, source=source)
        return {"mode": "reply", "action": res.action, "say": res.say, "routed_to": res.routed_to, **res.detail}
    return Plan("reply", rp.intent.action, target, desc, route, via=rp.intent.via, spoken=rp.spoken())


# --- Comms pane ----------------------------------------------------------------------------------
@app.get("/api/comms")
async def get_comms(limit: int = 200):
    return {"entries": [comms_public(r) for r in db.list_comms(limit)], "pending": comms.pending_state(),
            "flag_below": flag_below()}


class Rerun(BaseModel):
    text: str | None = None            # edited text; omit to re-send as it was


@app.post("/api/comms/{cid}/rerun")
async def rerun_comms(cid: int, body: Rerun):
    """Re-send an utterance, or run an edited version of it through the same pipeline."""
    row = db.get_comms(cid)
    if not row:
        raise HTTPException(404, "no such utterance")
    text = (body.text or row["text"]).strip()
    edited = text != row["text"]
    if edited:
        comms.learn(row["text"], text)
    res = await process_utterance(text, "edit" if edited else "rerun", row["selected_session_id"],
                                  heard=row["text"] if edited else None, parent_id=cid)
    if edited and res.get("comms_id"):
        await comms.update(cid, corrected_by=res["comms_id"])
    return res


@app.post("/api/voice/pending/{cid}/cancel")
async def cancel_pending(cid: int):
    """Esc / the Cancel button on the confirm card."""
    return {"ok": await comms.cancel(cid, "cancelled (Esc)")}


@app.post("/api/voice/pending/{cid}/dispatch")
async def dispatch_pending(cid: int):
    return {"ok": comms.dispatch_now(cid)}


@app.post("/api/voice/pending/{cid}/extend")
async def extend_pending(cid: int):
    """The mic heard you start talking during the hold."""
    return {"pending": await comms.extend(cid)}


@app.get("/api/vocab")
async def get_vocab():
    return {"vocab": comms.vocab(), "suggestions": comms.suggestions(), "mishears": db.mishears(1)[:30]}


class Word(BaseModel):
    word: str


@app.post("/api/vocab")
async def add_vocab(body: Word):
    if not re.fullmatch(r"[\w&' .-]{2,40}", body.word.strip()):
        raise HTTPException(400, "one word or name, letters and digits")
    return {"vocab": comms.add_vocab(body.word), "suggestions": comms.suggestions()}


@app.post("/api/vocab/dismiss")
async def dismiss_vocab(body: Word):
    db.dismiss_mishear(body.word)
    return {"suggestions": comms.suggestions()}


class MicDebug(BaseModel):
    event: str
    detail: str | None = None
    ua: str | None = None


mic_log = logging.getLogger("mic")
mic_events: deque = deque(maxlen=200)


@app.post("/api/voice/mic-debug")
async def mic_debug(body: MicDebug):
    """Browser speech-recognition events, so mic problems show up in the backend log."""
    mic_log.info("%s %s | %s", body.event, body.detail or "", (body.ua or "")[:80])
    mic_events.append({"t": time.time(), "event": body.event, "detail": body.detail})
    return {"ok": True}


@app.get("/api/voice/mic-debug")
async def mic_debug_recent():
    """The last 200 mic events (in memory only), for diagnosing without the dev terminal."""
    return list(mic_events)


class MusicSettings(BaseModel):
    mode: str | None = None           # pause | duck | off
    resume: bool | None = None


@app.get("/api/music")
async def get_music():
    return {**music_mod.settings(), "playing": [a for a, _ in await music_mod.playing()]}


@app.post("/api/music")
async def set_music(body: MusicSettings):
    if body.mode is not None:
        if body.mode not in ("pause", "duck", "off"):
            raise HTTPException(400, "mode must be pause, duck or off")
        db.set_pref("music_mode", body.mode)
    if body.resume is not None:
        db.set_pref("music_resume", body.resume)
    return await get_music()


class MicState(BaseModel):
    on: bool


@app.post("/api/music/mic")
async def music_mic(body: MicState):
    """The dashboard mic opened/closed: music pauses while you talk and comes back after."""
    await music_mod.music.mic(body.on)
    return {"ok": True}


@app.get("/api/briefing")
async def get_briefing():
    return briefing.state()


@app.post("/api/briefing/silence")
async def briefing_silence():
    return await briefing.silence()


@app.post("/api/briefing")
async def set_briefing(body: Flag):
    return await (briefing.arm() if body.value else briefing.disarm())


@app.post("/api/briefing/auto")
async def set_briefing_auto(body: Flag):
    """Arm briefing automatically when a report is queued (while a dashboard is open)."""
    return await briefing.set_auto(body.value)


@app.get("/api/decision-record")
async def decision_record(days: float = 1, project: str | None = None):
    return db.decision_record(time.time() - days * 86400, project)


@app.get("/api/decisions")
async def decisions(limit: int = 200):
    return db.decision_log(limit)


@app.post("/api/command")
async def run_command(body: Turn):
    res = await commands.run(_Ctx, body.text)
    if res.get("say"):
        await voice.speak(res["say"], kind="command")
    return res


@app.get("/api/commands")
async def list_commands():
    return [h for _, _, h in commands.COMMANDS if h]


# --- websocket -------------------------------------------------------------------
@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    await hub.add(socket)
    try:
        await socket.send_json({"type": "snapshot", "sessions": all_sessions(), "voice": await voice_state(),
                                "briefing": briefing.state(), "awake": awake.state()})
        while True:
            await socket.receive_text()     # client->server traffic goes over REST; this just keeps it open
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(socket)


@app.get("/api/version")
async def version():
    return {"version": VERSION, "web": WEB_DIR.exists()}


# The built UI (web/out), same origin as the API. Mounted last so every /api and /ws route above wins.
if WEB_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
