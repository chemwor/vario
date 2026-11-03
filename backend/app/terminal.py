"""Read-only tiles for Claude Code sessions started in a terminal.

Polls ~/.claude/projects/*/*.jsonl, tails files active in the last TERMINAL_WINDOW_S, and turns new
lines into the same events/states/speech as dashboard sessions. Dashboard-owned session ids are skipped.
If the live hook watcher (~/.claude/hooks/watchers/<sid>.pid) is already speaking a session, the
dashboard stays quiet for it to avoid double speech.
"""
import asyncio
import json
import logging
import os
import time
from collections import deque
from pathlib import Path

from . import db, session_reports, voice
from .info import SessionInfo
from .config import PROJECTS_DIR, TERMINAL_WINDOW_S
from .events import from_blocks, tool_result_text
from .hub import hub

log = logging.getLogger("terminal")
HISTORY_BYTES = 2_000_000
# a mid-turn session this quiet is treated as interrupted; well past STUCK_MINUTES so "possibly stuck" shows first
STALE_S = 60 * 60
SKIP_TYPES = {"attachment", "permission-mode", "mode", "last-prompt", "atis-latch", "ai-title",
              "file-history-snapshot", "file-history-delta", "summary"}


class TerminalSession:
    def __init__(self, path: Path):
        self.path = path
        self.id = path.stem
        self.cwd = ""
        self.name = path.parent.name
        self.state, self.activity, self.snippet = "done", None, None
        self.turn_started_at = None
        self.created_at = path.stat().st_ctime
        self.updated_at = path.stat().st_mtime
        self.offset = 0
        self.buf = b""
        self.events: deque = deque(maxlen=400)
        self.n_events = 0
        self.pending_text = None
        self.pending_key = None
        self.pending_tools: dict[str, dict] = {}   # tool_use_id -> {tool, input, ts}, no result yet
        self.last_final = None
        self.info = SessionInfo(self.id)

    def to_dict(self):
        if self.state == "working" and time.time() - self.updated_at > STALE_S:
            self.state, self.activity = "done", None     # interrupted or abandoned mid-turn
        muted = self.id in set(db.get_pref("terminal_muted", []))
        return {"id": self.id, "cwd": self.cwd, "name": self.name, "state": self.state, "activity": self.activity,
                "snippet": self.snippet, "muted": muted, "narrate": self.narrate, "created_at": self.created_at, "updated_at": self.updated_at,
                "turn_started_at": self.turn_started_at, "source": "terminal", "readonly": True, "live": True,
                "permissions": [], "info": self.info.to_dict()}

    @property
    def muted(self):
        return self.id in set(db.get_pref("terminal_muted", []))

    @property
    def narrate(self):
        return self.id in set(db.get_pref("terminal_narrate", []))

    # --- reading ----------------------------------------------------------------
    def read_new(self) -> list[dict]:
        size = self.path.stat().st_size
        if size < self.offset:          # truncated/rewritten
            self.offset, self.buf = 0, b""
        if size == self.offset:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            data = f.read(size - self.offset)
        self.offset = size
        data = self.buf + data
        lines = data.split(b"\n")
        self.buf = lines.pop()          # incomplete trailing line
        out = []
        for raw in lines:
            if not raw.strip():
                continue
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
        return out

    def load_history(self):
        size = self.path.stat().st_size
        self.offset = max(0, size - HISTORY_BYTES)
        first = self.offset > 0
        lines = self.read_new()
        if first and lines:
            lines = lines[1:]           # probably cut mid-line
        for line in lines:
            self.apply(line, live=False)

    # --- interpreting -------------------------------------------------------------
    def apply(self, d: dict, live: bool) -> list[tuple]:
        """Update state from one JSONL record. Returns signals when live:
        ("message", text) intermediate text, ("final", text, key) turn ended, ("error", text, key),
        ("resolved", tool_use_id) a pending tool got its result (permission answered)."""
        out = []
        t = d.get("type")
        if t == "ai-title" and d.get("aiTitle"):
            self.info.title = d["aiTitle"]
        elif t == "permission-mode" and d.get("permissionMode"):
            self.info.mode = d["permissionMode"]
        if t in SKIP_TYPES or d.get("isSidechain"):
            return out
        if d.get("cwd") and not self.cwd:
            self.cwd = d["cwd"]
            self.name = os.path.basename(self.cwd.rstrip("/")) or self.name
        if d.get("gitBranch"):
            self.info.branch = d["gitBranch"]
        ts = _ts(d.get("timestamp")) or time.time()
        msg = d.get("message") or {}
        uid = d.get("uuid", "")

        if t == "user" and not (d.get("isMeta") or d.get("isCompactSummary") or d.get("isVisibleInTranscriptOnly")):
            content = msg.get("content")
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self.info.result(b.get("tool_use_id"), ts, tool_result_text(b.get("content")),
                                     bool(b.get("is_error")))
                    if self.pending_tools.pop(b.get("tool_use_id"), None):
                        out.append(("resolved", b.get("tool_use_id")))
            evs = from_blocks("user", content, uid, ts=ts)
            for e in evs:
                if e["kind"] == "tool_result":
                    e["role"] = "tool"
                self._add(e)
            if any(e["kind"] == "tool_result" for e in evs):
                if self.state == "waiting":
                    self.state = "working"
            elif any(e["kind"] == "text" for e in evs):
                text = next(e["text"] for e in evs if e["kind"] == "text")
                if not text.startswith("<"):            # skip command/system injections
                    self.state, self.activity, self.turn_started_at = "working", "thinking", ts
                    self.snippet = text[:160]
                    self.pending_tools.clear()
                    self.info.new_turn()
                    out.append(("resolved", None))
        elif t == "assistant":
            if d.get("isApiErrorMessage") or msg.get("model") == "<synthetic>":
                text = "".join(b.get("text", "") for b in msg.get("content") or [] if isinstance(b, dict))
                if d.get("isApiErrorMessage") or text.lower().startswith("api error"):
                    self.state, self.activity = "error", None
                    out.append(("error", text, uid))
                return out if live else []
            final = msg.get("stop_reason") == "end_turn"
            self.info.model = msg.get("model") or self.info.model
            self.info.usage(msg.get("usage"))
            for b in msg.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    self.pending_tools[b.get("id")] = {"tool": b.get("name"), "input": b.get("input") or {}, "ts": ts}
                    self.info.tool(b.get("name", ""), b.get("input"), b.get("id"), ts)
            for e in from_blocks("assistant", msg.get("content"), uid, ts=ts):
                self._add(e)
                if e["kind"] == "text":
                    if self.pending_text:
                        out.append(("message", self.pending_text))
                    self.pending_text, self.pending_key = e["text"], uid
                    self.snippet = e["text"][:160]
                elif e["kind"] == "tool_use":
                    if self.pending_text:
                        out.append(("message", self.pending_text))
                        self.pending_text = None
                    self.state, self.activity = "working", e["activity"]
            if final:
                out += self._finish(uid)
            elif self.state not in ("working", "waiting"):
                self.state = "working"
        elif t == "system" and d.get("subtype") in ("turn_duration", "stop_hook_summary"):
            if self.state == "working" or self.pending_text:
                out += self._finish(uid)
        self.updated_at = max(self.updated_at, ts)
        return out if live else []

    def _add(self, e):
        self.events.append(e)
        self.n_events += 1

    def _finish(self, uid):
        text, key = self.pending_text, self.pending_key or uid
        self.pending_text = None
        if self.state == "done" and not text:
            return []
        self.state, self.activity = "done", None
        self.info.end_turn()
        self.last_final = (text or "", key)
        return [("final", text or "", key)]

    def pending_permission(self):
        """Most recent tool call still waiting for its result (what a permission prompt is about)."""
        if not self.pending_tools:
            return None, None
        tid = max(self.pending_tools, key=lambda k: self.pending_tools[k]["ts"])
        return tid, self.pending_tools[tid]


def _ts(s):
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class TerminalWatcher:
    def __init__(self, owned_ids):
        self.owned_ids = owned_ids          # callable -> set of dashboard session ids
        self.sessions: dict[str, TerminalSession] = {}

    def candidates(self):
        cutoff = time.time() - TERMINAL_WINDOW_S
        for p in PROJECTS_DIR.glob("*/*.jsonl"):
            try:
                if p.stat().st_mtime >= cutoff:
                    yield p
            except OSError:
                continue

    async def run(self):
        last_scan = 0.0
        while True:
            try:
                now = time.time()
                if now - last_scan > 3:
                    last_scan = now
                    await self._scan()
                for s in list(self.sessions.values()):
                    await self._poll(s)
            except Exception:
                log.exception("terminal watcher tick")
            await asyncio.sleep(0.4)

    async def _scan(self):
        owned = self.owned_ids()
        seen = set()
        for p in self.candidates():
            sid = p.stem
            seen.add(sid)
            if sid in owned or sid in self.sessions:
                continue
            if time.time() - p.stat().st_ctime < 3:     # give a just-created dashboard session time to register
                continue
            s = TerminalSession(p)
            await asyncio.to_thread(s.load_history)
            self.sessions[sid] = s
            await hub.emit("session_state", session=s.to_dict())
        for sid in list(self.sessions):
            if sid not in seen or sid in owned:
                self.sessions.pop(sid)
                await hub.emit("session_removed", session_id=sid)

    async def _poll(self, s: TerminalSession, t0: float | None = None):
        lines = await asyncio.to_thread(s.read_new)
        if not lines:
            return
        before = (s.state, s.activity)
        signals, n0 = [], s.n_events
        for d in lines:
            signals += s.apply(d, live=True)
        k = s.n_events - n0
        for e in (list(s.events)[-k:] if k else []):
            await hub.emit("message_delta", session_id=s.id, final=True, event=e)
        if (s.state, s.activity) != before and s.activity:
            await hub.emit("tool_activity", session_id=s.id, activity=s.activity)
        await hub.emit("session_state", session=s.to_dict())
        for sig in signals:
            kind = sig[0]
            if kind == "resolved":
                await session_reports.moved_on(s.id, "answered in terminal", key=sig[1])
            elif kind == "final":
                await session_reports.finished(s.id, s.name, sig[1], key=sig[2], t0=t0 or time.time())
            elif kind == "error":
                await session_reports.errored(s.id, s.name, sig[1], key=sig[2], t0=t0 or time.time())
            # The hook watcher stays quiet whenever this backend is up, so narration is ours alone here.
            elif kind == "message" and s.narrate and not s.muted:
                spoken = voice.clean(sig[1])
                if voice.substantive(spoken):
                    await voice.speak(f"{s.name}: {spoken}", session_id=s.id)

    # --- hook events (POSTed by ~/.claude/hooks/notify.sh and speak.sh) ---------------
    async def ensure(self, payload: dict) -> TerminalSession | None:
        sid, path = payload.get("session_id"), payload.get("transcript_path")
        if not sid or sid in self.owned_ids():
            return None
        s = self.sessions.get(sid)
        if not s and path and Path(path).exists():
            s = TerminalSession(Path(path))
            await asyncio.to_thread(s.load_history)
            self.sessions[sid] = s
            await hub.emit("session_state", session=s.to_dict())
        if s and payload.get("cwd") and not s.cwd:
            s.cwd, s.name = payload["cwd"], os.path.basename(payload["cwd"].rstrip("/")) or s.name
        return s

    async def on_notification(self, payload: dict, t0: float):
        s = await self.ensure(payload)
        if not s:
            return {"handled": False}
        await self._poll(s, t0)
        msg = str(payload.get("message", ""))
        ntype = str(payload.get("notification_type") or "")
        low = msg.lower()
        if "permission" in ntype or "permission" in low or "approval" in low:
            tid, pend = s.pending_permission()
            for _ in range(10):                      # tool_use line may land a beat after the hook fires
                if pend:
                    break
                await asyncio.sleep(0.1)
                await self._poll(s, t0)
                tid, pend = s.pending_permission()
            s.state = "waiting"
            await hub.emit("session_state", session=s.to_dict())
            if pend:
                await session_reports.blocked_on_tool(s.id, s.name, pend["tool"], pend["input"], key=tid, t0=t0)
            else:
                await session_reports.blocked_generic(s.id, s.name, msg.replace("Claude ", "", 1) or "needs your permission.",
                                                      key=f"notif:{s.id}:{s.n_events}", t0=t0)
            return {"handled": True, "kind": "permission"}
        if "idle" in ntype or "waiting for your input" in low:
            if s.last_final:                         # already reported when the turn ended (deduped by key)
                await session_reports.finished(s.id, s.name, s.last_final[0], key=s.last_final[1], t0=t0)
            else:
                await session_reports.blocked_generic(s.id, s.name, "is waiting for your input.",
                                                      key=f"idle:{s.id}:{s.n_events}", t0=t0)
            return {"handled": True, "kind": "idle"}
        await session_reports.blocked_generic(s.id, s.name, msg or "needs your attention.",
                                              key=f"notif:{s.id}:{s.n_events}:{msg[:40]}", t0=t0)
        return {"handled": True, "kind": "other"}

    async def on_stop(self, payload: dict, t0: float):
        """Stop hook: the turn ended. Poll right away (the watcher dedupes by message uuid)."""
        s = await self.ensure(payload)
        if not s:
            return {"handled": False}
        key = s.last_final[1] if s.last_final else None
        for _ in range(15):                          # final line can land just after the hook fires
            await self._poll(s, t0)
            if s.last_final and s.last_final[1] != key:
                break
            await asyncio.sleep(0.1)
        return {"handled": True}

    def get(self, sid):
        return self.sessions.get(sid)
