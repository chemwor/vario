"""Building blocks for turning session events into one-sentence asks.

- Permission asks are built from the tool call with templates (instant, no LLM).
- `compressor` is a warm haiku session (Agent SDK, kept connected so a call takes ~1-1.5s instead of
  ~10s for a cold `claude -p`). Triage uses it; callers fall back to truncation on failure/timeout.
"""
import asyncio
import json
import logging
import os
import re
import tempfile
from urllib.parse import urlparse

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock

from . import voice
from .config import API_KEY_VARS, WORK

log = logging.getLogger("alerts")

COMPRESS_TIMEOUT_S = 10
LONG_CHARS = 140          # asks/summaries longer than this get compressed

FINISH_PROMPT = ("Rewrite this end-of-task message as a spoken status update of at most 12 words, starting with "
                 "'done,'. Keep key facts and numbers (tests passing, counts, what was deployed). Plain words only.")
QUESTION_PROMPT = ("Rewrite this as ONE short spoken question (at most 20 words) that keeps the decision the user "
                   "must make and any options. Plain words only.")
PERMISSION_PROMPT = ("Rewrite this tool permission request as ONE short spoken yes/no question (at most 15 words) "
                     "starting with 'OK to'. Describe the effect in plain words, not the raw command.")
ERROR_PROMPT = "Summarize this error for a spoken alert in at most 10 words. Plain words only."


def api_key_vars_set() -> list[str]:
    """Names (never values) of env vars that would make the claude CLI bill an API key."""
    return [v for v in API_KEY_VARS if os.environ.get(v)]


async def claude_login() -> dict:
    """`claude auth status` for the logged-in account, with API key vars removed. Only loggedIn/authMethod kept."""
    env = {k: v for k, v in os.environ.items() if k not in API_KEY_VARS}
    try:
        p = await asyncio.create_subprocess_exec("claude", "auth", "status", "--json", env=env,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=15)
        d = json.loads(out or b"{}")
        return {"logged_in": bool(d.get("loggedIn")), "method": d.get("authMethod")}
    except Exception as e:
        return {"logged_in": False, "method": None, "error": type(e).__name__}


# --- one-sentence compression via a warm haiku session --------------------------------
class Compressor:
    def __init__(self):
        self.client: ClaudeSDKClient | None = None
        self.lock = asyncio.Lock()
        self.uses = 0
        self.cwd = tempfile.mkdtemp(prefix="mc-compress-")
        # path: "sdk" (Agent SDK through the claude CLI) or "rules" (no model calls; rule-based triage/routing)
        self.auth = {"path": "sdk", "reason": "not checked yet"}
        keys = api_key_vars_set()
        if WORK and keys:                     # decided up front, so not even the first call can slip through
            self.auth = {"path": "rules", "reason": f"{', '.join(keys)} is set; the work profile won't let it "
                                                    "override the claude login"}

    async def check_auth(self):
        """Decide (and log) which auth path triage uses. Work profile: Agent SDK on the claude login only."""
        keys = api_key_vars_set()
        if WORK and keys:
            pass                                       # already "rules" from __init__
        else:
            login = await claude_login()
            if keys:                                   # personal profile: the CLI will bill the key
                self.auth = {"path": "sdk", "reason": f"Agent SDK with {keys[0]} (personal profile)"}
            elif login["logged_in"]:
                self.auth = {"path": "sdk", "reason": f"Agent SDK on the claude login ({login['method']})"}
            elif WORK:
                self.auth = {"path": "rules", "reason": "claude is not logged in"}
            else:
                self.auth = {"path": "sdk", "reason": "claude login status unknown"}
        log.info("triage auth: %s (%s)", "rule-based" if self.auth["path"] == "rules" else "Agent SDK",
                 self.auth["reason"])

    def _options(self):
        return ClaudeAgentOptions(
            model="haiku", tools=[], setting_sources=[], cwd=self.cwd, max_turns=1,
            system_prompt=("You are a terse text processor. Do exactly what the instruction asks with the given text and "
                           "output only the result: no preamble, no markdown fences."),
            # no transcript file (it would show up as a terminal tile) and no MCP/claude.ai connectors
            extra_args={"no-session-persistence": None, "strict-mcp-config": None},
            env={"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "false"},
        )

    async def _reset(self):
        c, self.client, self.uses = self.client, None, 0
        if c:
            try:
                await c.disconnect()
            except Exception:
                pass

    async def warm(self):
        await self.check_auth()
        if self.auth["path"] == "rules":
            return
        try:
            async with self.lock:
                await self._ensure()
        except Exception as e:
            log.warning("compressor warm-up failed: %s", e)

    async def _ensure(self):
        if self.client is None or self.uses >= 25:      # recycle to keep the context small
            await self._reset()
            self.client = ClaudeSDKClient(options=self._options())
            await self.client.connect()

    async def compress(self, instruction: str, text: str, timeout: float = COMPRESS_TIMEOUT_S) -> str | None:
        if self.auth["path"] == "rules":           # hard fail: callers use their rule-based fallback
            return None
        try:
            async with asyncio.timeout(timeout):
                async with self.lock:
                    await self._ensure()
                    self.uses += 1
                    await self.client.query(f"{instruction}\n\n<text>\n{text[:4000]}\n</text>")
                    out = ""
                    async for m in self.client.receive_response():
                        if isinstance(m, AssistantMessage):
                            out += "".join(b.text for b in m.content if isinstance(b, TextBlock))
            out = out.strip().strip('"').strip()
            return out or None
        except Exception as e:  # timeout, auth, CLI missing ...
            log.warning("haiku call failed (%s)", type(e).__name__)
            await self._reset()
            return None


compressor = Compressor()


def truncate(text: str, n: int = LONG_CHARS) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    m = re.match(r"(.{40,}[.!?])\s", cut)
    return m.group(1) if m else cut.rsplit(" ", 1)[0] + "…"


async def one_sentence(instruction: str, text: str) -> str:
    text = voice.clean(text) or text
    if len(text) <= LONG_CHARS:
        return text
    return await compressor.compress(instruction, text) or truncate(text)


# --- building the ask ------------------------------------------------------------------
def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s[:1].isupper() and not s[1:2].isupper() else s


def permission_ask(tool: str, inp: dict) -> tuple[str, bool]:
    """(spoken question, needs_compression)."""
    inp = inp or {}
    base = lambda p: os.path.basename(str(p).rstrip("/")) or str(p)
    if tool == "Bash":
        desc = (inp.get("description") or "").strip().rstrip(".")
        if desc:
            return f"OK to {_lower_first(desc)}?", False
        cmd = re.sub(r"\s+", " ", str(inp.get("command", ""))).strip()
        if len(cmd) <= 40 and not re.search(r"[|;&<>$`\\]", cmd):
            return f"OK to run {cmd}?", False
        return f"Bash command:\n{cmd}", True
    if tool in ("Edit", "MultiEdit"):
        return f"OK to edit {base(inp.get('file_path', 'a file'))}?", False
    if tool == "Write":
        return f"OK to write {base(inp.get('file_path', 'a file'))}?", False
    if tool == "NotebookEdit":
        return f"OK to edit the notebook {base(inp.get('notebook_path', ''))}?", False
    if tool == "Read":
        return f"OK to read {base(inp.get('file_path', 'a file'))}?", False
    if tool == "WebFetch":
        return f"OK to fetch {urlparse(str(inp.get('url', ''))).netloc or 'a web page'}?", False
    if tool == "WebSearch":
        return f"OK to search the web for {inp.get('query', '')}?", False
    if tool in ("Task", "Agent"):
        return f"OK to start a subagent to {_lower_first(str(inp.get('description', 'help')))}?", False
    if tool == "AskUserQuestion":
        return question_ask(inp)
    if tool == "ExitPlanMode":
        plan = str(inp.get("plan", ""))
        return (f"Plan ready. Approve this plan?\n\n{plan}", True) if plan else ("Plan ready. Approve the plan?", False)
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        return f"OK to use {parts[-1].replace('_', ' ')} from {parts[1].replace('_', ' ')}?", False
    return f"OK to use {tool}?", False


def question_ask(inp: dict) -> tuple[str, bool]:
    qs = inp.get("questions") or []
    parts = []
    for q in qs[:3]:
        opts = [o.get("label", "") for o in (q.get("options") or []) if o.get("label")]
        s = q.get("question", "").strip()
        if opts:
            s += " Options: " + (", ".join(opts[:-1]) + f", or {opts[-1]}" if len(opts) > 1 else opts[0]) + "."
        parts.append(s)
    text = " ".join(parts) or "has a question for you."
    return text, len(text) > LONG_CHARS


def final_question(text: str) -> str | None:
    """If a finished turn ends by asking the user something, return that question."""
    cleaned = voice.clean(text) or text
    sents = re.split(r"(?<=[.!?])\s+", cleaned.strip())
    tail = " ".join(sents[-2:]) if sents else ""
    return tail if tail.rstrip().endswith("?") else None
