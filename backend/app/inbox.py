"""Watch ~/Vario/inbox/ for .md/.json drops; ingest each as an app_drop report, then archive it.

.json: {"project", "summary", "body", "type", "urgency", "source"} - all optional except some text.
.md:   optional front matter (--- project: x / type: / urgency: ---); the rest is the body.
Files are archived to ~/Vario/archive/YYYY-MM-DD/ (failed parses go to .../failed/).
"""
import asyncio
import json
import logging
import re
import shutil
import time
from pathlib import Path

from .config import VARIO_DIR
from .reports import first_line, reports

log = logging.getLogger("inbox")
ROOT = VARIO_DIR
INBOX, ARCHIVE = ROOT / "inbox", ROOT / "archive"
SETTLE_S = 0.5      # a file must stop changing for this long before we read it


async def _noop(*a):
    return None

on_scout_report = _noop   # set by main -> scouts.report_landed


def parse_drop(path: Path) -> dict:
    text = path.read_text(errors="replace")
    stem_project = path.stem.split("--")[0] if "--" in path.stem else None   # "billing--licenses.md"
    if path.suffix == ".json":
        d = json.loads(text)
        body = d.get("body") or d.get("text") or ""
        if not isinstance(body, str):
            body = json.dumps(body, indent=2)
        return {"project": d.get("project") or stem_project or "inbox", "summary": d.get("summary", ""),
                "body": body, "type": d.get("type"), "urgency": d.get("urgency"),
                "source": d.get("source") if d.get("source") in ("scout", "app_drop", "system") else "app_drop"}
    meta, body = {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip("\"'")
        body = m.group(2)
    return {"project": meta.get("project") or stem_project or "inbox", "summary": meta.get("summary", ""),
            "body": body.strip(), "type": meta.get("type"), "urgency": meta.get("urgency"),
            "source": meta.get("source") if meta.get("source") in ("scout", "app_drop", "system") else "app_drop",
            "scout_id": meta.get("scout_id")}


async def ingest(path: Path):
    day = ARCHIVE / time.strftime("%Y-%m-%d")
    try:
        d = parse_drop(path)
    except Exception as e:
        log.warning("could not parse %s: %s", path.name, e)
        (day / "failed").mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), day / "failed" / path.name)
        return
    hint_type = d["type"] if d["type"] in ("decision_needed", "research", "fyi") else "fyi"
    try:
        hint_urgency = int(d["urgency"])
    except (TypeError, ValueError):
        hint_urgency = 2
    st = path.stat()
    r = await reports.create(source=d["source"], project=d["project"], type=hint_type, urgency=hint_urgency,
                         summary=d["summary"] or first_line(d["body"])[:200], body=d["body"],
                         key=f"drop:{path.name}:{st.st_size}:{int(st.st_mtime)}")
    if r and d.get("scout_id"):
        try:
            await on_scout_report(int(d["scout_id"]), r)
        except Exception:
            log.exception("scout callback")
    day.mkdir(parents=True, exist_ok=True)
    dest = day / path.name
    if dest.exists():
        dest = day / f"{path.stem}-{int(time.time())}{path.suffix}"
    shutil.move(str(path), dest)
    log.info("ingested %s -> %s", path.name, dest)


async def watch():
    INBOX.mkdir(parents=True, exist_ok=True)
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    sizes: dict[Path, tuple[int, float]] = {}
    while True:
        try:
            now = time.time()
            for p in INBOX.iterdir():
                if p.suffix.lower() not in (".md", ".json") or p.name.startswith("."):
                    continue
                size = p.stat().st_size
                prev = sizes.get(p)
                if prev is None or prev[0] != size:
                    sizes[p] = (size, now)
                elif now - prev[1] >= SETTLE_S:
                    sizes.pop(p, None)
                    await ingest(p)
        except Exception:
            log.exception("inbox tick")
        await asyncio.sleep(0.5)
