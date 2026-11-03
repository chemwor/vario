"""Dashboard-owned Claude Code sessions, driven headlessly through the Claude Agent SDK."""
import asyncio
import dataclasses
import logging
import os
import time
import uuid

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    UserMessage,
)

from . import db, voice
from .activity import describe
from . import session_reports
from .config import PROJECTS_DIR
from .events import ev, from_blocks, short, tool_result_text
from .hub import hub
from .info import SessionInfo, git_branch

log = logging.getLogger("sessions")

WORKING, WAITING, DONE, ERROR = "working", "waiting", "done", "error"


def project_name(cwd: str) -> str:
    return os.path.basename(cwd.rstrip("/")) or cwd


def transcript_exists(sid: str) -> bool:
    return any(PROJECTS_DIR.glob(f"*/{sid}.jsonl"))


def _block_dict(b) -> dict:
    d = dataclasses.asdict(b) if dataclasses.is_dataclass(b) else dict(b)
    name = type(b).__name__
    d["type"] = {"TextBlock": "text", "ThinkingBlock": "thinking", "ToolUseBlock": "tool_use",
                 "ToolResultBlock": "tool_result"}.get(name, d.get("type", name))
    return d


class ManagedSession:
    def __init__(self, row: dict):
        self.id = row["id"]
        self.cwd = row["cwd"]
        self.name = row["name"]
        self.state = row.get("state") or DONE
        self.activity = row.get("activity")
        self.snippet = row.get("snippet")
        self.muted = bool(row.get("muted"))
        self.narrate = bool(row.get("narrate"))      # speak every assistant message (off by default)
        self.created_at = row.get("created_at") or db.now()
        self.updated_at = row.get("updated_at") or self.created_at
        self.turn_started_at = row.get("turn_started_at")
        self.client: ClaudeSDKClient | None = None
        self.reader: asyncio.Task | None = None
        self.permissions: dict[str, dict] = {}     # request_id -> {future, tool, input, activity}
        self.pending_text: str | None = None       # last assistant text, spoken as intermediate or final
        self.partial_key: str | None = None
        self._lock = asyncio.Lock()
        self.info = SessionInfo(self.id)
        self.info.mode, self.info.branch = "acceptEdits", git_branch(self.cwd)

    # --- serialization ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id, "cwd": self.cwd, "name": self.name, "state": self.state, "activity": self.activity,
            "snippet": self.snippet, "muted": self.muted, "narrate": self.narrate, "created_at": self.created_at,
            "updated_at": self.updated_at, "turn_started_at": self.turn_started_at,
            "source": "dashboard", "readonly": False, "live": self.client is not None,
            "permissions": [{"request_id": k, "tool": v["tool"], "input": short(v["input"], 600),
                             "activity": v["activity"]} for k, v in self.permissions.items()],
            "info": self.info.to_dict(),
        }

    def persist(self):
        db.upsert_session({**self.to_dict(), "muted": int(self.muted), "narrate": int(self.narrate)})

    async def publish(self):
        self.updated_at = db.now()
        self.persist()
        await hub.emit("session_state", session=self.to_dict())

    async def record(self, event: dict, partial_key: str | None = None):
        db.append_event(self.id, event)
        await hub.emit("message_delta", session_id=self.id, final=True, event=event, replaces=partial_key)

    async def say(self, text: str, kind="message"):
        # messages: "Webapp: tests are passing"; alerts read as a sentence: "Webapp finished."
        line = f"{self.name} {text}" if kind == "alert" else f"{self.name}: {text}"
        await voice.speak(line, session_id=self.id, muted=self.muted, kind=kind)

    async def flush_pending_speech(self):
        text, self.pending_text = self.pending_text, None
        if text and self.narrate:
            spoken = voice.clean(text)
            if voice.substantive(spoken):
                await self.say(spoken)

    # --- SDK client ---------------------------------------------------------
    async def _ensure_client(self):
        if self.client:
            return
        resume = transcript_exists(self.id)
        opts = ClaudeAgentOptions(
            cwd=self.cwd,
            permission_mode="acceptEdits",
            system_prompt={"type": "preset", "preset": "claude_code"},
            include_partial_messages=True,
            can_use_tool=self._can_use_tool,
            # project/local only: user-level hooks (the voice hooks) would double-speak SDK sessions
            setting_sources=["project", "local"],
            resume=self.id if resume else None,
            session_id=None if resume else self.id,
            stderr=lambda line: log.debug("[%s] %s", self.name, line.rstrip()),
        )
        client = ClaudeSDKClient(options=opts)
        await client.connect()
        self.client = client
        self.reader = asyncio.create_task(self._read_loop())

    async def send(self, text: str):
        async with self._lock:
            try:
                await self._ensure_client()
            except Exception as e:
                log.exception("connect failed")
                await self._fail(f"Could not start Claude: {e}")
                return
            await self.record(ev("user", "text", text))
            self.snippet = text[:160]
            self.state, self.activity, self.turn_started_at = WORKING, "thinking", db.now()
            self.info.new_turn()
            self.info.branch = git_branch(self.cwd)
            asyncio.create_task(session_reports.moved_on(self.id, f"new turn: {text[:120]}"))
            await self.publish()
            await self.client.query(text)

    async def interrupt(self):
        if self.client and self.state in (WORKING, WAITING):
            for p in list(self.permissions.values()):
                if not p["future"].done():
                    p["future"].set_result(PermissionResultDeny(message="Interrupted by user", interrupt=True))
            await self.client.interrupt()

    async def close(self):
        client, self.client = self.client, None
        if self.reader:
            self.reader.cancel()
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def _fail(self, msg: str):
        self.state, self.activity = ERROR, None
        await self.record(ev("system", "error", msg))
        await self.publish()
        await session_reports.errored(self.id, self.name, msg, key=f"fail:{self.id}:{time.time()}")

    # --- permissions ----------------------------------------------------------
    async def _can_use_tool(self, tool, inp, ctx):
        t0 = time.time()
        rid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        act = describe(tool, inp)
        self.permissions[rid] = {"future": fut, "tool": tool, "input": inp, "activity": act}
        prev = self.state
        self.state = WAITING
        await self.flush_pending_speech()
        await self.record(ev("system", "permission", f"Wants to use {tool}: {act}", tool=tool, request_id=rid))
        await self.publish()
        await hub.emit("permission_request", session_id=self.id, request_id=rid, tool=tool,
                       input=short(inp, 600), activity=act)
        asyncio.create_task(session_reports.blocked_on_tool(self.id, self.name, tool, inp, key=rid, t0=t0))
        try:
            return await fut
        finally:
            self.permissions.pop(rid, None)
            if self.state == WAITING and not self.permissions:
                self.state = prev if prev != WAITING else WORKING
            await self.publish()

    async def resolve_permission(self, rid: str, allow: bool, message: str | None):
        p = self.permissions.get(rid)
        if not p or p["future"].done():
            return False
        if allow:
            res = PermissionResultAllow(updated_input=p["input"])
        else:
            res = PermissionResultDeny(message=message or "The user denied this action.")
        p["future"].set_result(res)
        await session_reports.moved_on(self.id, f"{'allowed' if allow else 'denied'}" + (f": {message}" if message else ""),
                                       key=rid)
        await self.record(ev("user", "text", f"{'Allowed' if allow else 'Denied'} {p['tool']}" +
                             (f": {message}" if message else "")))
        return True

    # --- message stream ---------------------------------------------------------
    async def _read_loop(self):
        try:
            async for msg in self.client.receive_messages():
                try:
                    await self._handle(msg)
                except Exception:
                    log.exception("handling %s", type(msg).__name__)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("reader died")
            self.client = None
            await self._fail(f"Claude process ended: {e}")

    async def _handle(self, msg):
        if isinstance(msg, StreamEvent):
            if msg.parent_tool_use_id:
                return
            e = msg.event or {}
            t = e.get("type")
            if t == "content_block_start" and (e.get("content_block") or {}).get("type") == "text":
                self.partial_key = f"partial:{msg.uuid}"
                await hub.emit("message_delta", session_id=self.id, final=False, key=self.partial_key, delta="")
            elif t == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta" and self.partial_key:
                await hub.emit("message_delta", session_id=self.id, final=False, key=self.partial_key,
                               delta=e["delta"]["text"])
            return

        if isinstance(msg, AssistantMessage):
            if msg.parent_tool_use_id:  # subagent chatter: activity only
                return
            blocks = [_block_dict(b) for b in msg.content]
            self.info.model = msg.model or self.info.model
            self.info.usage(msg.usage)
            for b in blocks:
                if b.get("type") == "tool_use":
                    self.info.tool(b.get("name", ""), b.get("input"), b.get("id"))
            for e in from_blocks("assistant", blocks, msg.uuid or uuid.uuid4().hex):
                if e["kind"] == "text":
                    await self.flush_pending_speech()
                    self.pending_text = e["text"]
                    self.snippet = e["text"][:160]
                    key, self.partial_key = self.partial_key, None
                    await self.record(e, partial_key=key)
                elif e["kind"] == "tool_use":
                    await self.flush_pending_speech()
                    self.activity = e["activity"]
                    await self.record(e)
                    await hub.emit("tool_activity", session_id=self.id, activity=self.activity, tool=e["tool"])
                elif e["kind"] == "thinking":
                    await self.record(e)
            await self.publish()
            return

        if isinstance(msg, UserMessage):
            if msg.parent_tool_use_id or isinstance(msg.content, str):
                return
            blocks = [_block_dict(b) for b in msg.content]
            full = {b.get("tool_use_id"): b for b in blocks if b.get("type") == "tool_result"}
            for e in from_blocks("tool", blocks, msg.uuid or uuid.uuid4().hex):
                if e["kind"] == "tool_result":
                    b = full.get(e.get("tool_use_id")) or {}
                    self.info.result(e.get("tool_use_id"), text=tool_result_text(b.get("content")),
                                     is_error=bool(b.get("is_error")))
                    await self.record(e)
            await self.publish()
            return

        if isinstance(msg, ResultMessage):
            failed = msg.is_error or msg.subtype not in ("success", None)
            self.state, self.activity = (ERROR if failed else DONE), None
            self.info.end_turn()
            self.info.cost = msg.total_cost_usd
            secs = (msg.duration_ms or 0) / 1000
            cost = f", ${msg.total_cost_usd:.3f}" if msg.total_cost_usd else ""
            await self.record(ev("system", "error" if failed else "result",
                                 f"{'Error' if failed else 'Done'} in {secs:.0f}s, {msg.num_turns} turns{cost}" +
                                 (f": {msg.result}" if failed and msg.result else "")))
            await self.publish()
            text, self.pending_text = self.pending_text, None
            key = msg.uuid or f"{self.id}:{time.time()}"
            if failed:
                asyncio.create_task(session_reports.errored(self.id, self.name,
                                                            msg.result or "; ".join(msg.errors or []), key=key,
                                                            t0=time.time()))
            else:
                asyncio.create_task(session_reports.finished(self.id, self.name, text, key=key, t0=time.time()))
            return

        if isinstance(msg, SystemMessage) and msg.subtype == "init":
            sid = (msg.data or {}).get("session_id")
            if sid and sid != self.id:
                log.warning("SDK session id %s differs from dashboard id %s", sid, self.id)


class SessionManager:
    def __init__(self):
        self.sessions: dict[str, ManagedSession] = {}

    def load(self):
        for row in db.all_sessions():
            s = ManagedSession(row)
            if s.state in (WORKING, WAITING):   # backend restarted mid-turn
                s.state, s.activity = DONE, None
            s.permissions.clear()
            self.sessions[s.id] = s
            s.persist()

    def owned_ids(self) -> set[str]:
        return set(self.sessions)

    async def create(self, cwd: str, prompt: str | None, name: str | None = None, sid: str | None = None) -> ManagedSession:
        cwd = os.path.abspath(os.path.expanduser(cwd))
        if not os.path.isdir(cwd):
            raise ValueError(f"Not a directory: {cwd}")
        sid = sid or str(uuid.uuid4())
        if sid in self.sessions:
            return self.sessions[sid]
        s = ManagedSession({"id": sid, "cwd": cwd, "name": name or project_name(cwd), "state": DONE,
                            "created_at": db.now()})
        self.sessions[sid] = s
        await s.publish()
        if prompt:
            asyncio.create_task(s.send(prompt))
        return s

    async def remove(self, sid: str):
        s = self.sessions.pop(sid, None)
        if s:
            await s.close()
            db.delete_session(sid)
            await hub.emit("session_removed", session_id=sid)

    async def shutdown(self):
        await asyncio.gather(*(s.close() for s in self.sessions.values()), return_exceptions=True)


manager = SessionManager()
