"""Research playbooks: competition, market, features, end-user. Each is a markdown file with front matter
(label, voice trigger words, budget) and two parts: how to work, and the report template the scout fills in.

The built-in playbooks live next to this file (research/*.md). To change one, copy it to
~/Vario/research/playbooks/<kind>.md and edit it; a file there wins. A new <kind>.md there adds a new kind.

A playbook run is aimed at a product: the one a session is working on. Vario builds a short product context
from that session's project folder (README, CLAUDE.md, package metadata) and what the session has been asked,
so the scout researches *that* product. The scout itself still can't read local files; it gets this text.
"""
import json
import re
import time
from pathlib import Path

from .config import VARIO_DIR

BUILTIN = Path(__file__).parent / "research"
USER_DIR = VARIO_DIR / "research" / "playbooks"       # your edited / extra playbooks
LIBRARY = VARIO_DIR / "research"                      # finished reports, one folder per product
CONTEXT_LIMIT = 7000                                   # characters of product context handed to the scout


def _parse(path: Path) -> dict | None:
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return None
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    body = m.group(2)
    parts = re.split(r"(?m)^## Report\s*$", body, maxsplit=1)
    if len(parts) != 2:
        return None
    kind = meta.get("kind") or path.stem
    return {"kind": kind, "label": meta.get("label", kind.title()), "description": meta.get("description", ""),
            "voice": [w.strip().lower() for w in meta.get("voice", kind).split(",") if w.strip()],
            "budget_usd": float(meta.get("budget_usd", "1.00")), "max_turns": int(meta.get("max_turns", "30")),
            "instructions": parts[0].strip(), "template": parts[1].strip(), "path": str(path),
            "custom": path.parent == USER_DIR}


def playbooks() -> dict[str, dict]:
    """kind -> playbook; files in ~/Vario/research/playbooks override the built-in ones."""
    out = {}
    for d in (BUILTIN, USER_DIR):
        for p in sorted(d.glob("*.md")) if d.exists() else []:
            try:
                pb = _parse(p)
            except OSError:
                pb = None
            if pb:
                out[pb["kind"]] = pb
    return out


def public(pb: dict) -> dict:
    return {k: pb[k] for k in ("kind", "label", "description", "budget_usd", "path", "custom", "voice")}


def kind_for(words: str) -> str | None:
    """'competitor' / 'reddit' / 'market sizing' -> a playbook kind, by its voice words."""
    w = words.lower().strip()
    best = None
    for kind, pb in playbooks().items():
        for v in pb["voice"] + [kind]:
            if v and (w == v or w.startswith(v) or v in w.split()):
                if not best or len(v) > best[1]:
                    best = (kind, len(v))
    return best[0] if best else None


# --- product context ----------------------------------------------------------------------------------
def _read(p: Path, n: int) -> str:
    try:
        return p.read_text(errors="ignore")[:n].strip() if p.is_file() else ""
    except OSError:
        return ""


def _manifest(cwd: Path) -> str:
    bits = []
    try:
        pj = json.loads(_read(cwd / "package.json", 20000) or "{}")
        for k in ("name", "description", "keywords", "homepage"):
            if pj.get(k):
                bits.append(f"{k}: {pj[k] if not isinstance(pj[k], list) else ', '.join(map(str, pj[k]))}")
    except ValueError:
        pass
    py = _read(cwd / "pyproject.toml", 6000)
    for k in ("name", "description", "keywords"):
        m = re.search(rf'(?m)^{k}\s*=\s*(.+)$', py)
        if m:
            bits.append(f"{k}: {m.group(1).strip()}")
    return "\n".join(bits)


def product_context(session: dict, recent_asks: list[str]) -> str:
    """What the session's product is, from its folder and its conversation. Only these files are read:
    README*, CLAUDE.md, package.json / pyproject.toml metadata. Never .env or source code."""
    cwd = Path(session.get("cwd") or "")
    parts = [f"Product / project name: {session.get('name')}"]
    if session.get("info", {}).get("title"):
        parts.append(f"What the session is working on: {session['info']['title']}")
    if recent_asks:
        parts.append("Recent requests to the session:\n" + "\n".join(f"- {a[:300]}" for a in recent_asks[-4:]))
    if cwd.is_dir():
        m = _manifest(cwd)
        if m:
            parts.append("Package metadata:\n" + m)
        readme = next((p for p in sorted(cwd.glob("README*")) if p.is_file()), None)
        if readme:
            parts.append(f"README (excerpt):\n{_read(readme, 3500)}")
        cm = _read(cwd / "CLAUDE.md", 1500)
        if cm:
            parts.append(f"Project notes (CLAUDE.md, excerpt):\n{cm}")
    return "\n\n".join(parts)[:CONTEXT_LIMIT]


# --- rendering ----------------------------------------------------------------------------------------
def render(pb: dict, product: str, context: str, focus: str | None) -> tuple[str, str]:
    """(brief, report template body) for a playbook run."""
    focus_line = f"Focus especially on: {focus.strip()}" if focus and focus.strip() else ""
    fill = lambda s: s.replace("{product}", product).replace("{focus}", focus_line).replace("{date}", time.strftime("%Y-%m-%d"))
    brief = (f"{pb['label']} for {product}.{(' ' + focus_line) if focus_line else ''}\n\n"
             f"{fill(pb['instructions'])}\n\n## Product context (from the project and its Claude session)\n\n"
             f"{context or '(none available: research from the product name)'}")
    return brief, fill(pb["template"])


def save_to_library(project: str, kind: str, body: str) -> Path:
    """Keep finished research as markdown: ~/Vario/research/<project>/<date>-<kind>.md."""
    slug = re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-") or "research"
    d = LIBRARY / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{time.strftime('%Y-%m-%d-%H%M')}-{kind}.md"
    p.write_text(body)
    return p
