"""Evidence for a report: what the question is actually about.

Everything is read locally: the session's transcript (~/.claude/projects/*/<sid>.jsonl, written by both
terminal and dashboard sessions) and the files it touched. Items:
  diff     {tool, path, diff, state}   state: pending (awaiting approval) | applied | not in file now | snippet |
                                        new file | written
  command  {tool, command, description}
  input    {tool, text}                 any other tool call, as JSON
  sources  {sources: [{title, url}]}    a scout report's Sources section
  lines    {lines: [...]}               transcript lines (stuck reports)
  text     {title, text}
"""
import difflib
from datetime import datetime
import json
import os
import re
from pathlib import Path

from .config import PROJECTS_DIR

TAIL_BYTES = 4_000_000
MAX_DIFF_LINES = 600
MAX_FILE_BYTES = 2_000_000
EDIT_TOOLS = ("Edit", "MultiEdit", "Write")


def transcript_path(sid: str) -> Path | None:
    return next(iter(PROJECTS_DIR.glob(f"*/{sid}.jsonl")), None)


def _records(path: Path, full: bool = False) -> list[dict]:
    """Transcript records (main thread only). The last TAIL_BYTES unless full."""
    size = path.stat().st_size
    tail = not full and size > TAIL_BYTES
    with open(path, "rb") as f:
        f.seek(size - TAIL_BYTES if tail else 0)
        data = f.read()
    lines = data.split(b"\n")
    if tail:
        lines = lines[1:]                     # cut mid-line
    out = []
    for raw in lines:
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict) and not d.get("isSidechain"):
            out.append(d)
    return out


def _is_prompt(d: dict) -> bool:
    """A real user prompt (not a tool result, command injection or meta line): a turn boundary."""
    if d.get("type") != "user" or d.get("isMeta") or d.get("isCompactSummary"):
        return False
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return not c.startswith("<")
    return isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "text"
                                       and not b.get("text", "").startswith("<") for b in c)


def _calls(records: list[dict]) -> list[dict]:
    """tool_use blocks in order: {id, name, input, uuid}."""
    out = []
    for d in records:
        if d.get("type") != "assistant":
            continue
        for b in (d.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                out.append({"id": b.get("id"), "name": b.get("name", ""), "input": b.get("input") or {},
                            "uuid": d.get("uuid"), "ts": _ts(d.get("timestamp"))})
    return out


def _ts(s) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _results(records: list[dict]) -> dict[str, float]:
    """tool_use_id -> when its result landed."""
    out = {}
    for d in records:
        c = (d.get("message") or {}).get("content") if d.get("type") == "user" else None
        for b in c if isinstance(c, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                out[b.get("tool_use_id")] = _ts(d.get("timestamp"))
    return out


def _turn(records: list[dict], until_uuid: str | None = None) -> list[dict]:
    """Records of the turn that contains until_uuid (default: the latest turn)."""
    end = len(records)
    if until_uuid:
        end = next((i + 1 for i, d in enumerate(records) if d.get("uuid") == until_uuid), len(records))
    start = 0
    for i in range(end - 1, -1, -1):
        if _is_prompt(records[i]):
            start = i
            break
    return records[start:end]


# --- diffs ------------------------------------------------------------------------------------
def _rel(path: str, cwd: str | None) -> str:
    try:
        if cwd and os.path.commonpath([path, cwd]) == cwd:
            return os.path.relpath(path, cwd)
    except ValueError:
        pass
    return path.replace(str(Path.home()), "~", 1)


def _read(path: str) -> str | None:
    try:
        p = Path(path)
        if p.stat().st_size > MAX_FILE_BYTES:
            return None
        return p.read_text(errors="replace")
    except OSError:
        return None


def _apply(text: str, pairs, reverse=False) -> str | None:
    for old, new, every in (reversed(pairs) if reverse else pairs):
        a, b = (new, old) if reverse else (old, new)
        if not a or a not in text:
            return None
        text = text.replace(a, b) if every else text.replace(a, b, 1)
    return text


def _unified(before: str, after: str, name: str) -> tuple[str, bool]:
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{name}", f"b/{name}", n=3,
                                      lineterm=""))
    return "\n".join(lines[:MAX_DIFF_LINES]), len(lines) > MAX_DIFF_LINES


def _pairs(call: dict) -> list[tuple[str, str, bool]]:
    i = call["input"]
    if call["name"] == "MultiEdit":
        return [(str(e.get("old_string", "")), str(e.get("new_string", "")), bool(e.get("replace_all")))
                for e in i.get("edits") or []]
    return [(str(i.get("old_string", "")), str(i.get("new_string", "")), bool(i.get("replace_all")))]


def edit_diff(path: str, pairs, cwd: str | None, awaiting: bool = False) -> dict:
    """Diff for Edit/MultiEdit pairs against the file on disk, so hunks carry real line numbers.
    awaiting (an approval prompt): the edit hasn't run, so look for the old text first. Otherwise the edit
    already ran: look for the new text; if it's gone, the edit was refused or changed later."""
    name, cur = _rel(path, cwd), _read(path)
    before = after = None
    state = "snippet"
    if cur is not None:
        fwd = lambda: _apply(cur, pairs)
        rev = lambda: _apply(cur, pairs, reverse=True)
        for kind in (("pending", "applied") if awaiting else ("applied", "not in file now")):
            if kind in ("pending", "not in file now") and (nxt := fwd()) is not None:
                before, after, state = cur, nxt, kind
                break
            if kind == "applied" and (prev := rev()) is not None:
                before, after, state = prev, cur, kind
                break
    if before is None:
        before = "\n".join(o for o, _, _ in pairs)
        after = "\n".join(n for _, n, _ in pairs)
    diff, cut = _unified(before, after, name)
    return {"kind": "diff", "path": path, "name": name, "diff": diff, "state": state, "truncated": cut}


def write_diff(path: str, content: str, cwd: str | None, awaiting: bool = False) -> dict:
    """awaiting: diff against what's on disk now. Otherwise the write already ran and what it replaced is
    unknown, so the written content is shown in full."""
    name, cur = _rel(path, cwd), _read(path)
    if awaiting and cur is not None:
        state, before = "pending", cur
    else:
        state, before = ("new file" if awaiting else "written"), ""
    diff, cut = _unified(before, content, name)
    return {"kind": "diff", "path": path, "name": name, "diff": diff, "state": state, "truncated": cut}


def call_item(call: dict, cwd: str | None, awaiting: bool = False) -> dict:
    """awaiting: the call is waiting on an approval (it hasn't run)."""
    name, i = call["name"], call["input"]
    if name in ("Edit", "MultiEdit") and i.get("file_path"):
        return {**edit_diff(str(i["file_path"]), _pairs(call), cwd, awaiting), "tool": name}
    if name == "Write" and i.get("file_path"):
        return {**write_diff(str(i["file_path"]), str(i.get("content", "")), cwd, awaiting), "tool": name}
    if name.endswith("apply_migration") and i.get("query"):     # Supabase MCP: the SQL, as a new-file diff
        diff, cut = _unified("", str(i["query"]), f"migration/{i.get('name') or 'unnamed'}.sql")
        return {"kind": "diff", "tool": name, "path": str(i.get("name") or ""), "name": f"migration {i.get('name') or ''}".strip(),
                "diff": diff, "state": "pending" if awaiting else "migration", "truncated": cut}
    if name == "Bash":
        return {"kind": "command", "tool": name, "command": str(i.get("command", "")),
                "description": str(i.get("description") or "")}
    return {"kind": "input", "tool": name, "text": json.dumps(i, indent=2, ensure_ascii=False)[:8000]}


# --- per report -----------------------------------------------------------------------------------
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)|(https?://[^\s)>\]]+)")


def sources(body: str) -> list[dict]:
    m = re.search(r"^##\s*Sources\s*$(.*?)(?=^##\s|\Z)", body or "", re.S | re.M | re.I)
    seen, out = set(), []
    for t, u, bare in _LINK.findall(m.group(1) if m else body or ""):
        url = (u or bare).rstrip(".,;")
        if url not in seen:
            seen.add(url)
            out.append({"title": t or url, "url": url})
    return out


def for_report(r: dict, *, cwd: str | None = None, live_call: dict | None = None) -> dict:
    """{items, note}. live_call: the in-memory pending tool call for a dashboard permission prompt."""
    key = r.get("dedupe_key") or ""
    kind, _, ref = key.partition(":")
    sid = r.get("session_id")

    if r.get("source") == "scout" or (r.get("type") == "research" and not sid):
        src = sources(r.get("body", ""))
        if src:
            return {"items": [{"kind": "sources", "sources": src}], "note": None}
        return {"items": [], "note": "This report doesn't list any sources."}
    if kind == "stuck":
        lines = [ln for ln in (r.get("body") or "").splitlines() if ln.strip()]
        return {"items": [{"kind": "lines", "lines": lines}], "note": None if lines else "No transcript lines were captured."}
    if kind == "error":
        return {"items": [{"kind": "text", "title": "Error", "text": r.get("body") or r.get("summary", "")}], "note": None}
    if not sid:
        return {"items": [], "note": "No evidence: this report isn't tied to a session or a scout."}
    if live_call:
        return {"items": [call_item(live_call, cwd, awaiting=True)], "note": None}

    path = transcript_path(sid)
    if not path:
        return {"items": [], "note": "No evidence: the session's transcript wasn't found on this Mac."}
    recs = _records(path)
    # an older report can point past the tail: read the whole transcript once
    if (kind == "block" and not ref.startswith("notif:") and ref not in {c["id"] for c in _calls(recs)}) or \
            (kind == "final" and ref not in {d.get("uuid") for d in recs}) or \
            (kind == "block" and recs and _ts(recs[0].get("timestamp")) > r.get("created_at", 0)):
        recs = _records(path, full=True)
    calls = _calls(recs)

    if kind == "block":
        if ref.startswith("notif:"):
            # the hook didn't say which tool: the latest call that was still waiting when the report was made
            at = r.get("created_at") or 0
            done = _results(recs)
            waiting = [c for c in calls if c["ts"] <= at + 2 and done.get(c["id"], float("inf")) > at]
            call = waiting[-1] if waiting else None
            if not call:
                return {"items": [], "note": "No evidence: the notification didn't name a tool call, and none was "
                                             "waiting on an answer when it arrived. It may be a question in the "
                                             "conversation itself; open the session to read it."}
        else:
            call = next((c for c in calls if c["id"] == ref), None)
        if call:
            # still unanswered (no result yet) means it hasn't run
            return {"items": [call_item(call, cwd, awaiting=call["id"] not in _results(recs))], "note": None}
        return {"items": [], "note": "No evidence: the tool call this approval is about isn't in the transcript."}

    if kind == "final":                         # the turn ended with a question: show what that turn changed
        end = next((i + 1 for i, d in enumerate(recs) if d.get("uuid") == ref), len(recs))
        for back in range(4):                   # a turn that only asks: look back up to 3 turns for the work
            start = next((i for i in range(end - 1, -1, -1) if _is_prompt(recs[i])), 0)
            items = _turn_items(_calls(recs[start:end]), cwd)
            if items:
                note = None if not back else (f"The question's own turn didn't edit files or run commands; this is from "
                                              f"{'the turn' if back == 1 else f'{back} turns'} before it.")
                return {"items": items, "note": note}
            if start == 0:
                break
            end = start
        return {"items": [], "note": "No evidence: neither that turn nor the 3 before it edited files or ran commands."}

    return {"items": [], "note": "No evidence could be extracted for this report."}


def _turn_items(calls: list[dict], cwd: str | None) -> list[dict]:
    """Edits and migrations in order, then the last 3 commands."""
    items = [call_item(c, cwd) for c in calls if c["name"] in EDIT_TOOLS or c["name"].endswith("apply_migration")]
    return items + [call_item(c, cwd) for c in [c for c in calls if c["name"] == "Bash"][-3:]]


def turn_files(sid: str, cwd: str | None) -> list[dict]:
    """Files edited in the session's latest turn, one combined diff per file (latest edit last)."""
    path = transcript_path(sid)
    if not path:
        return []
    by_file: dict[str, list[dict]] = {}
    for c in _calls(_turn(_records(path))):
        f = c["input"].get("file_path")
        if c["name"] in EDIT_TOOLS and f:
            by_file.setdefault(str(f), []).append(c)
    out = []
    for f, cs in by_file.items():
        if all(c["name"] != "Write" for c in cs):
            item = edit_diff(f, [p for c in cs for p in _pairs(c)], cwd)
        else:                                   # a Write in the mix: show each call on its own
            parts = [call_item(c, cwd) for c in cs]
            item = {**parts[-1], "diff": "\n".join(p["diff"] for p in parts)}
        out.append({**item, "edits": len(cs)})
    return out


def last_lines(events: list[dict], n: int = 3) -> list[str]:
    """The last n transcript events as one-line strings (stuck reports)."""
    out = []
    for e in events[-n:]:
        who = {"user": "you", "assistant": "claude"}.get(e.get("role"), e.get("role", ""))
        what = e.get("activity") if e.get("kind") == "tool_use" else e.get("text", "")
        what = re.sub(r"\s+", " ", str(what or e.get("kind", ""))).strip()
        out.append(f"{who} {e.get('kind', '')}: {what[:240]}")
    return out

