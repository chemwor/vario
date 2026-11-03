"""Per-session detail for the session rows: model, branch, title, context size, what this turn has touched."""
import json
import os
import re
import time
from pathlib import Path

from . import db
from .tests_seen import TestTracker
from .config import HOME, setting

EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
AGENT_TOOLS = {"Task", "Agent"}


def git_branch(cwd: str) -> str | None:
    """Branch from .git/HEAD (walking up), without spawning git."""
    p = Path(cwd or "/")
    for d in (p, *p.parents):
        head = d / ".git" / "HEAD"
        try:
            if (d / ".git").is_file():        # worktree: "gitdir: <path>"
                head = Path((d / ".git").read_text().split(":", 1)[1].strip()) / "HEAD"
            if head.exists():
                ref = head.read_text().strip()
                return ref.rsplit("/", 1)[-1] if ref.startswith("ref:") else ref[:7]
        except (OSError, IndexError):
            return None
    return None


def _default_model() -> str:
    try:
        return str(json.loads((HOME / ".claude" / "settings.json").read_text()).get("model") or "")
    except (OSError, ValueError):
        return ""


_DEFAULT_MODEL = _default_model()      # e.g. "opus[1m]": the 1M-context variant is the default here


def context_window(model: str | None, used: int | None) -> int:
    """Tokens the session's model can hold. CONTEXT_WINDOW (vario.conf) overrides. Otherwise 1M when the model
    says [1m], when settings.json defaults that model family to [1m], or when it's already past 200k."""
    forced = setting("CONTEXT_WINDOW", "")
    if forced.isdigit():
        return int(forced)
    m = (model or "").lower()
    fam = re.sub(r"\[1m\]", "", _DEFAULT_MODEL.lower())
    if "[1m]" in m or ("[1m]" in _DEFAULT_MODEL.lower() and fam and fam in m) or (used or 0) > 200_000:
        return 1_000_000
    return 200_000


class SessionInfo:
    def __init__(self, sid: str | None = None):
        self.sid = sid
        self.outcome: dict | None = None     # last turn: {label: ok|failed|partial, line} (triage)
        self._outcome_loaded = False
        self.ctx_alerted = False             # filed the 90% context report (reset below 75%)
        self.model = None
        self.branch = None
        self.title = None
        self.mode = None             # permission mode: default / acceptEdits / auto / plan ...
        self.context = None          # tokens in the last request (input + cache)
        self.cost = None             # last turn, dashboard sessions only
        self.tools = 0               # tool calls this turn
        self.files: list[str] = []   # files edited this turn, most recent last
        self.agents: set[str] = set()  # subagent tool_use ids still running
        self.last_tool_at: float | None = None  # last tool call or result (stuck detection)
        self.stuck = False           # set by stuck.py: working, but no tool events for STUCK_MINUTES
        self.tests = TestTracker()   # test runs seen in this session's Bash calls

    def new_turn(self):
        self.tools, self.files, self.agents = 0, [], set()

    def tool(self, name: str, inp: dict | None, tid: str | None, ts: float | None = None):
        self.tools += 1
        self.last_tool_at = ts or time.time()
        inp = inp or {}
        self.tests.started(tid, name, inp, ts)
        if name in EDIT_TOOLS:
            f = os.path.basename(str(inp.get("file_path") or inp.get("notebook_path") or ""))
            if f:
                if f in self.files:
                    self.files.remove(f)
                self.files.append(f)
        elif name in AGENT_TOOLS and tid:
            self.agents.add(tid)

    def result(self, tid: str | None, ts: float | None = None, text: str | None = None, is_error: bool = False):
        self.agents.discard(tid)
        self.last_tool_at = ts or time.time()
        self.tests.finished(tid, text, is_error, ts)

    def usage(self, u: dict | None):
        if u:
            n = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            if n:
                self.context = n

    def end_turn(self):
        self.agents = set()

    def set_outcome(self, outcome: dict | None):
        self.outcome, self._outcome_loaded = outcome, True

    def to_dict(self) -> dict:
        if not self._outcome_loaded and self.sid:      # after a restart: the last labelled turn's outcome
            self._outcome_loaded = True
            self.outcome = db.last_outcome(self.sid)
        return {"model": self.model, "branch": self.branch, "title": self.title, "mode": self.mode,
                "context": self.context, "context_window": context_window(self.model, self.context),
                "cost": self.cost, "tools": self.tools, "files": self.files[-6:],
                "agents": len(self.agents), "stuck": self.stuck, "outcome": self.outcome,
                "tests": self.tests.summary()}
