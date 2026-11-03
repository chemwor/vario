"""Phase C: scouts - headless research sessions that report back through the inbox.

A scout gets a brief, may only search/fetch the web and write ONE file (its report, straight into
~/Vario/inbox/), and runs under a turn + dollar budget. The inbox watcher ingests the file
like any other drop; reports from scouts are always typed `research`. If the scout was launched against
a pending decision, that decision's urgency is bumped when the research lands, and briefing mode will
bring the decision back up after reading the findings.
"""
import asyncio
import logging
import os
import re
import time
from pathlib import Path

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, PermissionResultAllow, PermissionResultDeny,
                              ResultMessage, TextBlock, query)

from . import db, research
from .config import SCOUTS_ENABLED
from .hub import hub
from .inbox import INBOX, ROOT

log = logging.getLogger("scouts")
SCOUT_DIR = ROOT / "scouts"
SCOUTS_OFF_SAY = "Scouts are off in work profile."


class ScoutsOff(Exception):
    pass
SCOUT_MODEL = os.environ.get("SCOUT_MODEL", "sonnet")
SCOUT_BUDGET_USD = float(os.environ.get("SCOUT_BUDGET_USD", "1.00"))
SCOUT_MAX_TURNS = int(os.environ.get("SCOUT_MAX_TURNS", "30"))
SCOUT_TIMEOUT_S = int(os.environ.get("SCOUT_TIMEOUT_S", "900"))

HEADER = """---
source: scout
project: {project}
type: research
scout_id: {id}
summary: <ONE sentence, max 25 words, plain spoken words: the answer/recommendation>
---
# Question
{brief}
"""
TEMPLATE = HEADER + """
## Findings
- <specific facts with numbers, prices, dates>

## Sources
- <title> - <url>

## Recommendation
<what to do and why, 2-4 sentences>

## Confidence
<high | medium | low> - <why>
"""

RULES = """You are a research scout working for a busy developer. Research the brief using WebSearch and
WebFetch, then write your report with the Write tool to EXACTLY this path:
  {path}
The report MUST follow this template (keep the front matter keys exactly):

{template}
Rules: cite real sources you actually opened; prefer primary/official sources; give concrete numbers;
say what you could not verify. Be efficient: a handful of searches is usually enough. Writing the
report file is the LAST thing you do; after writing it, reply with one short line and stop."""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "research"


class Scouts:
    def __init__(self):
        self.tasks: dict[int, asyncio.Task] = {}
        self.on_landed = lambda scout, report: None      # set by main (briefing re-queues the decision)

    async def launch(self, brief: str, project: str | None = None, linked_report_id: int | None = None, *,
                     kind: str | None = None, session_id: str | None = None, focus: str | None = None,
                     context: str = "") -> dict:
        """A free-form brief, or a playbook run (kind) aimed at a session's product (context: product_context)."""
        if not SCOUTS_ENABLED:
            raise ScoutsOff(SCOUTS_OFF_SAY)
        project = project or "research"
        pb = None
        if kind:
            pb = research.playbooks().get(kind)
            if not pb:
                raise ValueError(f"no research playbook called {kind!r}")
            prompt, body = research.render(pb, project, context, focus)
            title = f"{pb['label']} for {project}" + (f": {focus.strip()}" if focus and focus.strip() else "")
        else:
            prompt, body, title = brief.strip(), None, brief.strip()
        s = db.create_scout(brief=title, project=project, linked_report_id=linked_report_id)
        out = INBOX / f"scout-{s['id']}--{_slug(project)}.md"
        s = db.update_scout(s["id"], out_path=str(out), kind=kind, session_id=session_id)
        await hub.emit("scout", scout=s)
        template = (HEADER.format(project=project, id=s["id"], brief=title) + "\n" + body + "\n") if body else None
        self.tasks[s["id"]] = asyncio.create_task(self._run(s, out, prompt, template, pb))
        log.info("scout #%s launched (%s%s): %s", s["id"], project, f", {kind}" if kind else "", title)
        return s

    async def _run(self, s: dict, out: Path, prompt: str | None = None, template: str | None = None,
                   pb: dict | None = None):
        cwd = SCOUT_DIR / str(s["id"])
        cwd.mkdir(parents=True, exist_ok=True)
        tmp = cwd / out.name          # write here first, then move into the inbox in one step

        async def guard(tool, inp, ctx):
            if tool in ("WebSearch", "WebFetch"):
                return PermissionResultAllow()
            if tool == "Write" and os.path.realpath(str(inp.get("file_path", ""))) in (os.path.realpath(tmp),
                                                                                         os.path.realpath(out)):
                return PermissionResultAllow(updated_input={**inp, "file_path": str(tmp)})
            return PermissionResultDeny(message=f"Scouts may only search the web and write {tmp}.")

        opts = ClaudeAgentOptions(
            cwd=str(cwd), model=SCOUT_MODEL, tools=["WebSearch", "WebFetch", "Write"],
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": RULES.format(path=tmp, template=template or TEMPLATE.format(
                               project=s["project"], id=s["id"], brief=s["brief"]))},
            can_use_tool=guard, setting_sources=[], max_turns=pb["max_turns"] if pb else SCOUT_MAX_TURNS,
            max_budget_usd=pb["budget_usd"] if pb else SCOUT_BUDGET_USD,
            extra_args={"no-session-persistence": None, "strict-mcp-config": None},
            env={"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "false"},
        )
        final_text, cost, err = "", None, None

        async def prompt_stream():
            # can_use_tool requires streaming input
            yield {"type": "user", "message": {"role": "user", "content": f"Research brief: {prompt or s['brief']}"}}

        try:
            async with asyncio.timeout(SCOUT_TIMEOUT_S):
                async for m in query(prompt=prompt_stream(), options=opts):
                    if isinstance(m, AssistantMessage) and not m.parent_tool_use_id:
                        t = "".join(b.text for b in m.content if isinstance(b, TextBlock))
                        if t.strip():
                            final_text = t
                    elif isinstance(m, ResultMessage):
                        cost = m.total_cost_usd
                        if m.is_error:
                            err = m.result or m.subtype
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            log.warning("scout #%s failed: %s", s["id"], err)

        out.parent.mkdir(parents=True, exist_ok=True)
        if tmp.exists():
            os.replace(tmp, out)                      # atomic hand-off to the inbox watcher
        else:                                         # never lose the work: file a low-confidence report
            out.write_text(TEMPLATE.split("# Question")[0]
                           .replace("<ONE sentence, max 25 words, plain spoken words: the answer/recommendation>",
                                    f"Scout {s['id']} did not finish its report" + (f" ({err[:60]})" if err else ""))
                           + f"# Question\n{s['brief']}\n\n## Findings\n{final_text or '(none)'}\n\n"
                             f"## Confidence\nlow - the scout did not write a structured report\n")
        db.update_scout(s["id"], status="failed" if err and not tmp.exists() and not out.exists() else "done",
                        finished_at=time.time(), cost_usd=cost, error=err)
        await hub.emit("scout", scout=db.get_scout(s["id"]))
        log.info("scout #%s finished (cost %s, err %s)", s["id"], cost, err)

    async def report_landed(self, scout_id: int, report: dict):
        """Inbox ingested a scout report: link it and bump the decision it informs."""
        s = db.get_scout(scout_id)
        if not s:
            return
        s = db.update_scout(scout_id, report_id=report["id"])
        if s.get("session_id") or s.get("kind"):
            # a playbook run: the report belongs to the session's product, and is kept as markdown
            fields = {"session_id": s["session_id"]} if s.get("session_id") else {}
            report = db.update_report(report["id"], **fields) or report
            await hub.emit("report", report=report)
            try:
                path = research.save_to_library(s["project"], s.get("kind") or "research",
                                                f"# {s['brief']}\n\n{report['body']}\n")
                s = db.update_scout(scout_id, library_path=str(path))
            except OSError as e:
                log.warning("couldn't save scout #%s to the research library: %s", scout_id, e)
        await hub.emit("scout", scout=s)
        linked = db.get_report(s["linked_report_id"]) if s.get("linked_report_id") else None
        if linked and linked["status"] in ("queued", "delivered"):
            r = db.update_report(linked["id"], urgency=min(5, linked["urgency"] + 1), snoozed_until=None)
            await hub.emit("report", report=r)
            log.info("scout #%s landed: decision #%s urgency %s -> %s", scout_id, linked["id"], linked["urgency"],
                     r["urgency"])
        self.on_landed(s, report)


scouts = Scouts()
