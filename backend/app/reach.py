"""Can a reply reach a terminal session? Today: tmux (send-keys). Otherwise the reply is unavailable."""
import asyncio
import logging
import os
import shutil

log = logging.getLogger("reach")


async def _run(*args, timeout=3) -> tuple[int, str]:
    try:
        p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
        return p.returncode, out.decode(errors="replace")
    except Exception:
        return 1, ""


async def tmux_pane_for(cwd: str) -> str | None:
    """A tmux pane running claude in this cwd (exactly one match required)."""
    if not cwd or not shutil.which("tmux"):
        return None
    code, out = await _run("tmux", "list-panes", "-a", "-F", "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}")
    if code != 0:
        return None
    matches = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and os.path.realpath(parts[2]) == os.path.realpath(cwd) and \
                any(k in parts[1].lower() for k in ("claude", "node")):
            matches.append(parts[0])
    return matches[0] if len(matches) == 1 else None


async def tmux_screen(pane: str, lines: int = 40) -> str:
    code, out = await _run("tmux", "capture-pane", "-p", "-t", pane, "-S", f"-{lines}")
    return out if code == 0 else ""


async def tmux_send(pane: str, *keys: str) -> bool:
    code, _ = await _run("tmux", "send-keys", "-t", pane, *keys)
    return code == 0


# --- cmux ---------------------------------------------------------------------------
# cmux launches each terminal `claude` with --session-id <id> (== transcript file name) and puts
# CMUX_SURFACE_ID / CMUX_WORKSPACE_ID in its environment. We read ONLY those two variables from the
# process environment (it also holds a cmux socket token, which is never logged or stored).
import re as _re

PERMISSION_SCREEN = _re.compile(r"Do you want to|❯\s*1\.\s*Yes|1\.\s*Yes\b")
_DIALOG_TAIL = _re.compile(r"^\s*(?:[│|]\s*)?(?:❯\s*)?\d\.\s|esc|tab to|shift|enter to|^[\s╭╮╰╯─│|]*$", _re.I)


def permission_dialog_showing(screen: str) -> bool:
    """True only if a permission dialog is the LAST thing on screen: the 'Do you want to ...' line may be
    followed by nothing but numbered options, key hints, or box borders. Old dialogs scrolled up in
    history don't count."""
    lines = [l for l in screen.rstrip().splitlines()]
    idx = max((i for i, l in enumerate(lines) if "Do you want to" in l), default=None)
    if idx is None:
        return False
    tail = [l for l in lines[idx + 1:] if l.strip()]
    return bool(tail) and all(_DIALOG_TAIL.search(l) for l in tail) and any(_re.search(r"\d\.\s*Yes", l) for l in tail)


async def cmux_surface_for(session_id: str) -> dict | None:
    if not session_id or not shutil.which("cmux"):
        return None
    code, out = await _run("ps", "-axo", "pid=,command=")
    if code != 0:
        return None
    pid = None
    for line in out.splitlines():
        line = line.strip()
        if _re.search(rf"(?:--session-id|--resume|-r)[= ]{_re.escape(session_id)}\b", line) and "claude" in line \
                and "/bin/bash" not in line:
            pid = line.split()[0]
            break
    if not pid:
        return None
    code, env = await _run("ps", "eww", "-o", "command=", "-p", pid)
    if code != 0:
        return None
    surface = _re.search(r"\bCMUX_SURFACE_ID=([0-9A-Fa-f-]{36})\b", env)
    workspace = _re.search(r"\bCMUX_WORKSPACE_ID=([0-9A-Fa-f-]{36})\b", env)
    if not surface:
        return None
    return {"surface": surface.group(1), "workspace": workspace.group(1) if workspace else None, "pid": pid}


def _cmux_target(t: dict) -> list[str]:
    args = ["--surface", t["surface"]]
    if t.get("workspace"):
        args += ["--workspace", t["workspace"]]
    return args


async def cmux_screen(t: dict, lines: int = 40) -> str:
    code, out = await _run("cmux", "read-screen", *_cmux_target(t), "--lines", str(lines))
    return out if code == 0 else ""


async def cmux_key(t: dict, key: str) -> bool:
    code, _ = await _run("cmux", "send-key", *_cmux_target(t), key)
    return code == 0


async def cmux_text(t: dict, text: str) -> bool:
    # cmux turns \n / \r / \t escapes into keypresses: strip backslashes and newlines so spoken
    # text can never submit early
    safe = text.replace("\\", " ").replace("\n", " ").replace("\r", " ")
    code, _ = await _run("cmux", "send", *_cmux_target(t), "--", safe)
    return code == 0
