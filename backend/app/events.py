"""Normalized transcript events shared by SDK sessions and terminal (JSONL) sessions.

event = {id, ts, role: user|assistant|tool|system, kind: text|tool_use|tool_result|thinking|result|error|permission,
         text, tool?, input?, activity?}
"""
import json
import time
import uuid as _uuid

from .activity import describe


def ev(role, kind, text="", **extra):
    return {"id": extra.pop("id", None) or _uuid.uuid4().hex, "ts": extra.pop("ts", None) or time.time(),
            "role": role, "kind": kind, "text": text, **extra}


def short(obj, n=400) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


def tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return "" if content is None else str(content)


def from_blocks(role: str, blocks, base_id: str, ts=None) -> list[dict]:
    """Content blocks as plain dicts (JSONL shape) -> events."""
    out = []
    if isinstance(blocks, str):
        return [ev(role, "text", blocks, id=f"{base_id}:0", ts=ts)] if blocks.strip() else []
    for i, b in enumerate(blocks or []):
        t, bid = b.get("type"), f"{base_id}:{i}"
        if t == "text" and b.get("text", "").strip():
            out.append(ev(role, "text", b["text"], id=bid, ts=ts))
        elif t == "thinking" and b.get("thinking"):
            out.append(ev("assistant", "thinking", b["thinking"], id=bid, ts=ts))
        elif t == "tool_use":
            out.append(ev("assistant", "tool_use", short(b.get("input", {})), id=bid, ts=ts, tool=b.get("name"),
                          tool_use_id=b.get("id"), activity=describe(b.get("name", ""), b.get("input"))))
        elif t == "tool_result":
            out.append(ev("tool", "tool_result", short(tool_result_text(b.get("content")), 1500), id=bid, ts=ts,
                          tool_use_id=b.get("tool_use_id"), is_error=bool(b.get("is_error"))))
    return out
