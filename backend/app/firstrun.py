"""First-run setup: the wizard's checks and actions (dependencies, voice models, hooks, sound, prefs).

Every step can be skipped. What each step ended as (done / skipped) is kept in DATA_DIR/setup.json; skipped or
failing steps become warnings in the Health panel rather than errors anywhere else.

Paths are overridable so the wizard can be exercised without touching a real install:
CLAUDE_SETTINGS (default ~/.claude/settings.json), CLAUDE_HOOK_DIR, SPEAK_SRC (a folder with hooks/; default voice/).
"""
import asyncio
import difflib
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import host, voice
from .config import (CONFIG_FILE, DATA_DIR, HOME, HOOK_DIR, PORT, REPO_ROOT, STT_MODEL, STT_PYTHON, set_voice_conf,
                     setting)
from .hub import hub

log = logging.getLogger("setup")

SETUP_FILE = DATA_DIR / "setup.json"
STEPS = ["deps", "models", "hooks", "sound", "mic", "prefs"]
STEP_LABEL = {"deps": "Dependencies", "models": "Voice models", "hooks": "Claude Code hooks", "sound": "Test sound",
              "mic": "Microphone", "prefs": "Theme and voice"}
SETTINGS = Path(os.environ.get("CLAUDE_SETTINGS") or HOME / ".claude" / "settings.json")
MANIFEST = json.loads((Path(__file__).parent / "models.json").read_text())["models"]
VOICE_HOME = Path(STT_PYTHON).parent.parent.parent          # ~/.local/share/claude-voice
HOOK_SCRIPTS = ["tts-lib.sh", "tts-clean.pl", "bubo.py", "tts-watch.sh", "speak.sh", "notify.sh", "session-start.sh",
                "session-end.sh"]
# (event, script, timeout, matcher): the same hooks install.sh registers
HOOKS = [("SessionStart", "session-start.sh", 5, False), ("Stop", "speak.sh", 10, False),
         ("Notification", "notify.sh", 5, True), ("SessionEnd", "session-end.sh", 5, False)]
STAMP = ".vario-hooks-version"


# --- state -------------------------------------------------------------------------------------------
def _load() -> dict:
    try:
        return json.loads(SETUP_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save(d: dict):
    SETUP_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETUP_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(SETUP_FILE)


def mark(step: str, status: str, note: str = "") -> dict:
    d = _load()
    d.setdefault("steps", {})[step] = {"status": status, "note": note, "at": time.time()}
    _save(d)
    return state()


def hooks_registered() -> bool:
    cmds = {f"{HOOK_DIR}/{s}" for _, s, _, _ in HOOKS}
    try:
        hooks = json.loads(SETTINGS.read_text()).get("hooks", {})
    except (OSError, ValueError):
        return False
    have = {h.get("command") for groups in hooks.values() for g in groups for h in g.get("hooks", [])}
    return cmds <= have


def state() -> dict:
    """first_run: nothing set up yet (no setup record, no config file, no hooks): the UI opens the wizard."""
    d = _load()
    first = (not d.get("finished") and not d.get("steps") and not CONFIG_FILE.exists() and not hooks_registered()
             and os.environ.get("VARIO_DEMO") != "1")          # the demo is for looking around, not setting up
    steps = {s: d.get("steps", {}).get(s, {"status": "pending", "note": "", "at": None}) for s in STEPS}
    return {"first_run": first, "finished": bool(d.get("finished")), "steps": steps, "labels": STEP_LABEL,
            "config_file": str(CONFIG_FILE), "config_exists": CONFIG_FILE.exists()}


def warnings() -> list[dict]:
    """Skipped setup steps, for the Health panel."""
    out = []
    for s, v in state()["steps"].items():
        if v["status"] == "skipped":
            out.append({"step": s, "label": STEP_LABEL[s], "note": v.get("note") or "skipped during setup"})
    return out


def finish(profile: str | None = None) -> dict:
    """Write vario.conf (from the example) if there isn't one, and remember setup as finished."""
    if not CONFIG_FILE.exists():
        example = REPO_ROOT / "vario.conf.example"
        text = example.read_text() if example.exists() else "PROFILE=personal\n"
        if profile in ("personal", "work"):
            text = re.sub(r"(?m)^PROFILE=.*$", f"PROFILE={profile}", text, count=1)
        CONFIG_FILE.write_text(text)
        log.info("wrote %s", CONFIG_FILE)
    d = _load()
    d["finished"] = time.time()
    _save(d)
    return state()


# --- 1. dependencies ----------------------------------------------------------------------------------
def _run(cmd: list[str], timeout: float = 8) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr).strip().splitlines()[0] if r.returncode == 0 and (r.stdout or r.stderr) else None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def _ver(s: str | None) -> tuple:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", s or "")
    return tuple(int(x or 0) for x in m.groups()) if m else ()


def deps() -> list[dict]:
    """Each: {id, label, ok, required, version, fix}. Nothing is installed here."""
    arm = platform.system() == "Darwin" and platform.machine() == "arm64"
    node = _run(["node", "-v"])
    rows = [
        {"id": "mac", "label": "macOS on Apple Silicon", "required": True, "ok": arm,
         "version": f"macOS {platform.mac_ver()[0]} {platform.machine()}" if platform.system() == "Darwin" else platform.system(),
         "fix": "Kokoro and local Whisper need an Apple Silicon Mac; voice falls back to system TTS / browser speech."},
        {"id": "python", "label": "Python 3.12+ (backend)", "required": True, "ok": sys.version_info >= (3, 12),
         "version": platform.python_version(), "fix": "uv python install 3.12"},
        {"id": "uv", "label": "uv", "required": True, "ok": bool(shutil.which("uv")), "version": _run(["uv", "--version"]),
         "fix": "brew install uv"},
        {"id": "node", "label": "Node 20.9+ (only to build the UI from source)", "ok": _ver(node) >= (20, 9),
         "required": not (REPO_ROOT / "web" / "out" / "index.html").exists(), "version": node,
         "fix": "brew install node (not needed: this install has the UI prebuilt)"},
        {"id": "jq", "label": "jq (used by the hooks)", "required": True, "ok": bool(shutil.which("jq")),
         "version": _run(["jq", "--version"]), "fix": "brew install jq"},
        {"id": "claude", "label": "Claude Code CLI", "required": True, "ok": bool(shutil.which("claude")),
         "version": _run(["claude", "--version"]), "fix": "Install Claude Code: https://docs.claude.com/claude-code"},
        {"id": "tmux", "label": "tmux (optional: lets Vario reply to terminal sessions)", "required": False,
         "ok": bool(shutil.which("tmux")), "version": _run(["tmux", "-V"]), "fix": "brew install tmux"},
    ]
    runtime = Path(STT_PYTHON).exists() and _run([STT_PYTHON, "-c", "import mlx_audio, mlx_whisper, huggingface_hub; print('ok')"], 30)
    rows.append({"id": "runtime", "label": "Voice runtime (Kokoro + Whisper, Python venv)", "required": True,
                 "ok": bool(runtime), "version": str(VOICE_HOME / "venv") if runtime else None,
                 "fix": "Press Install (about 1 GB download, a few minutes)", "action": "install-runtime"})
    login = _claude_login() if shutil.which("claude") else None
    rows.insert(6, {"id": "login", "label": "Claude Code login", "required": True, "ok": bool(login),
                    "version": login, "fix": "Not logged in: run `claude` in a terminal and use /login, then Check again"})
    return rows


def _claude_login() -> str | None:
    """The login method if `claude` is logged in (only loggedIn / authMethod are read, never the account)."""
    try:
        r = subprocess.run(["claude", "auth", "status", "--json"], capture_output=True, text=True, timeout=15)
        d = json.loads(r.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return (d.get("authMethod") or "logged in") if d.get("loggedIn") else None


# --- background jobs (runtime install, model downloads), progress over the websocket -----------------
jobs: dict[str, dict] = {}


async def _emit(job: str, **kw):
    jobs.setdefault(job, {}).update(kw)
    await hub.emit("setup_progress", job=job, **jobs[job])


def job_state() -> dict:
    return jobs


async def install_runtime() -> dict:
    """The voice venv: the same commands as claude-speak/install.sh step 1, output streamed as a log."""
    job = "runtime"
    if jobs.get(job, {}).get("running"):
        return jobs[job]
    if not shutil.which("uv"):
        await _emit(job, running=False, ok=False, log=["uv is missing: brew install uv"])
        return jobs[job]
    venv = VOICE_HOME / "venv"
    py = str(venv / "bin" / "python")
    env = {**os.environ, "UV_CACHE_DIR": str(VOICE_HOME / ".uv-cache")}
    cmds = []
    if not Path(py).exists():
        cmds.append(["uv", "venv", "--python", "3.12", str(venv)])
    cmds.append(["uv", "pip", "install", "--python", py, "mlx-audio", "misaki[en]", "mlx-whisper",
                 "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/"
                 "en_core_web_sm-3.8.0-py3-none-any.whl"])
    VOICE_HOME.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    await _emit(job, running=True, ok=None, log=lines)
    ok = True
    for cmd in cmds:
        lines.append("$ " + " ".join(cmd[:4]) + (" ..." if len(cmd) > 4 else ""))
        p = await asyncio.create_subprocess_exec(*cmd, env=env, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
        async for raw in p.stdout:
            lines.append(raw.decode(errors="replace").rstrip()[:200])
            del lines[:-40]
            await _emit(job, log=lines)
        if await p.wait() != 0:
            ok = False
            break
    await _emit(job, running=False, ok=ok, log=lines)
    return jobs[job]


def model_specs() -> list[dict]:
    """The pinned models, plus a note when STT_MODEL is set to something the manifest doesn't cover."""
    out = []
    for key, m in MANIFEST.items():
        out.append({"key": key, "label": m["label"], "repo": m["repo"], "revision": m["revision"][:10],
                    "files": len(m["files"]), "bytes": sum(f["size"] for f in m["files"]),
                    "custom": key == "whisper" and STT_MODEL != m["repo"]})
    return out


async def fetch_model(key: str, verify_only: bool = False) -> dict:
    """Download (or just verify) one model with model_fetch.py under the voice venv; sha256-checks every file."""
    job = f"model:{key}"
    if key not in MANIFEST:
        raise KeyError(key)
    if jobs.get(job, {}).get("running"):
        return jobs[job]
    if not Path(STT_PYTHON).exists():
        await _emit(job, running=False, ok=False, error="The voice runtime isn't installed yet.", done=0, total=0)
        return jobs[job]
    spec = MANIFEST[key]
    if os.environ.get("SETUP_HF_HOME"):                      # a scratch model cache (demos, tests)
        spec = {**spec, "hf_home": os.environ["SETUP_HF_HOME"]}
    total = sum(f["size"] for f in spec["files"])
    files: dict[str, str] = {}
    await _emit(job, running=True, ok=None, verify_only=verify_only, done=0, total=total, files=files, error=None,
                bad=[], checked=0)
    p = await asyncio.create_subprocess_exec(
        STT_PYTHON, str(Path(__file__).parent / "model_fetch.py"), *(["--verify-only"] if verify_only else []),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    p.stdin.write(json.dumps(spec).encode())
    p.stdin.close()
    result = None
    async for raw in p.stdout:
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if ev["ev"] == "progress":
            await _emit(job, done=ev["done"], total=ev["total"])
        elif ev["ev"] == "file":
            files[ev["path"]] = ev["state"]
            if ev.get("error"):
                jobs[job]["error"] = ev["error"]
            await _emit(job, files=files, checked=sum(v in ("ok", "bad", "missing") for v in files.values()))
        elif ev["ev"] == "done":
            result = ev
    err = (await p.stderr.read()).decode(errors="replace").strip().splitlines()
    await p.wait()
    ok = bool(result and result["ok"])
    await _emit(job, running=False, ok=ok, bad=(result or {}).get("bad", []),
                error=jobs[job].get("error") or (None if result else (err[-1] if err else "the downloader stopped")))
    log.info("model %s %s: %s", key, "verify" if verify_only else "fetch", "ok" if ok else jobs[job])
    return jobs[job]


# --- 3. hooks: show the exact settings.json change, write it only after consent ------------------------
def speak_src() -> Path | None:
    """Where the hook scripts come from: SPEAK_SRC (vario.conf), else voice/ in this repo."""
    for p in (setting("SPEAK_SRC"), REPO_ROOT / "voice"):
        if p and (Path(p) / "hooks" / "speak.sh").exists():
            return Path(p)
    return None


def source_version(src: Path) -> str:
    """Content hash of the hook scripts (same as install.sh: sha256 of the files concatenated, first 12)."""
    h = hashlib.sha256()
    for f in HOOK_SCRIPTS:
        h.update((src / "hooks" / f).read_bytes())
    return h.hexdigest()[:12]


def installed_version() -> dict:
    """{version, installed_at, source} from the stamp; version "unknown" when hooks exist without one."""
    try:
        v, src, at = (HOOK_DIR / STAMP).read_text().splitlines()[:3]
        return {"version": v, "source": src, "installed_at": float(at)}
    except (OSError, ValueError):
        return {"version": "unknown" if hooks_registered() else None, "source": None, "installed_at": None}


def _merged(settings: dict) -> dict:
    """settings with Vario's hooks registered: install.sh's jq merge (drop our old entries, append fresh ones)."""
    out = json.loads(json.dumps(settings))
    hooks = out.setdefault("hooks", {})
    for ev, script, timeout, matcher in HOOKS:
        cmd = f"{HOOK_DIR}/{script}"
        keep = [g for g in hooks.get(ev, []) if all(h.get("command") != cmd for h in g.get("hooks", []))]
        hooks[ev] = keep + [{**({"matcher": ""} if matcher else {}),
                             "hooks": [{"type": "command", "command": cmd, "timeout": timeout}]}]
    return out


def hooks_plan() -> dict:
    """Everything the install would do, without doing it: the settings.json diff and the files it copies."""
    src = speak_src()
    try:
        before_text = SETTINGS.read_text()
        before = json.loads(before_text)
    except FileNotFoundError:
        before_text, before = "", {}
    except ValueError as e:
        return {"ok": False, "error": f"{SETTINGS} isn't valid JSON ({e}); fix it first, nothing was changed."}
    after = _merged(before)
    old = json.dumps(before, indent=2).splitlines() if before_text else []
    new = json.dumps(after, indent=2).splitlines()
    diff = list(difflib.unified_diff(old, new, f"{SETTINGS} (now)", f"{SETTINGS} (after)", lineterm="", n=3))
    files = []
    if src:
        for f in HOOK_SCRIPTS:
            dest = HOOK_DIR / f
            same = dest.exists() and dest.read_bytes() == (src / "hooks" / f).read_bytes()
            files.append({"name": f, "dest": str(dest), "state": "same" if same else "update" if dest.exists() else "new"})
        if not (HOOK_DIR / "voice.conf").exists():
            files.append({"name": "voice.conf", "dest": str(HOOK_DIR / "voice.conf"), "state": "new"})
    return {"ok": bool(src), "error": None if src else "The hook scripts (voice/hooks) weren't found: set SPEAK_SRC in vario.conf.",
            "settings": str(SETTINGS), "settings_exists": bool(before_text), "diff": diff, "changes": bool(diff),
            "files": files, "source": str(src) if src else None, "version": source_version(src) if src else None,
            "installed": installed_version(), "hook_dir": str(HOOK_DIR)}


INSTALL_RECORD = DATA_DIR / "hooks-install.json"


def _install_record() -> dict:
    try:
        return json.loads(INSTALL_RECORD.read_text())
    except (OSError, ValueError):
        return {}


def _unmerged(settings: dict, commands: set[str]) -> dict:
    """settings without Vario's hook entries (groups left empty are dropped, and an empty hooks block too)."""
    out = json.loads(json.dumps(settings))
    hooks = out.get("hooks", {})
    for ev in list(hooks):
        groups = []
        for g in hooks[ev]:
            hs = [h for h in g.get("hooks", []) if h.get("command") not in commands]
            if hs:
                groups.append({**g, "hooks": hs})
            elif not g.get("hooks"):
                groups.append(g)
        if groups:
            hooks[ev] = groups
        else:
            del hooks[ev]
    if "hooks" in out and not out["hooks"]:
        del out["hooks"]
    return out


def uninstall_plan() -> dict:
    """The settings.json change uninstall would make: the inverse of the install, by command, not a stale backup."""
    rec = _install_record()
    commands = set(rec.get("commands") or [f"{HOOK_DIR}/{s_}" for _, s_, _, _ in HOOKS])
    try:
        before = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        before = {}
    after = _unmerged(before, commands)
    diff = list(difflib.unified_diff(json.dumps(before, indent=2).splitlines(), json.dumps(after, indent=2).splitlines(),
                                     f"{SETTINGS} (now)", f"{SETTINGS} (after uninstall)", lineterm="", n=3))
    scripts = [str(HOOK_DIR / f) for f in rec.get("scripts", HOOK_SCRIPTS) if (HOOK_DIR / f).exists()]
    return {"settings": str(SETTINGS), "diff": diff, "scripts": scripts, "record": rec}


def uninstall_hooks(remove_voice_conf: bool = False) -> dict:
    """Remove Vario's hook entries from settings.json (backed up first) and its scripts from the hook dir."""
    plan = uninstall_plan()
    backup = None
    created = plan["record"].get("settings_existed") is False
    if created and SETTINGS.exists() and _unmerged(json.loads(SETTINGS.read_text() or "{}"),
                                                   set(plan["record"].get("commands") or [])) == {}:
        SETTINGS.unlink()                       # setup created it and nothing else is in it: put things back as they were
    elif plan["diff"]:
        backup = SETTINGS.with_name(f"{SETTINGS.name}.bak-{time.strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(SETTINGS, backup)
        after = _unmerged(json.loads(SETTINGS.read_text()), set(plan["record"].get("commands") or
                                                               [f"{HOOK_DIR}/{s_}" for _, s_, _, _ in HOOKS]))
        tmp = SETTINGS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(after, indent=2) + "\n")
        tmp.replace(SETTINGS)
    for f in plan["scripts"]:
        Path(f).unlink(missing_ok=True)
    for extra in (STAMP, "tts.sock", "bubo.pid", "bubo.lock", ".music-paused", ".music-gen", "bubo.log", "bubo.log.old"):
        (HOOK_DIR / extra).unlink(missing_ok=True)
    for d in ("cache", "watchers"):                  # bubo's phrase cache, transcript watcher state
        shutil.rmtree(HOOK_DIR / d, ignore_errors=True)
    if remove_voice_conf:
        (HOOK_DIR / "voice.conf").unlink(missing_ok=True)
        (HOOK_DIR / ".muted").unlink(missing_ok=True)
    try:
        HOOK_DIR.rmdir()                             # only if nothing else of yours is in it
    except OSError:
        pass
    INSTALL_RECORD.unlink(missing_ok=True)
    return {**plan, "backup": str(backup) if backup else None}


def install_hooks(expect_diff: list[str]) -> dict:
    """Copy the scripts, back up settings.json, register the hooks. Refuses if settings.json changed since the
    diff that was shown (expect_diff), so what was consented to is exactly what's written."""
    plan = hooks_plan()
    if not plan["ok"]:
        return plan
    if plan["diff"] != expect_diff:
        return {**plan, "ok": False, "error": "settings.json changed since you reviewed it; review the new diff."}
    src = Path(plan["source"])
    HOOK_DIR.mkdir(parents=True, exist_ok=True)
    for f in HOOK_SCRIPTS:
        shutil.copy2(src / "hooks" / f, HOOK_DIR / f)
        if f.endswith((".sh", ".py")):
            os.chmod(HOOK_DIR / f, 0o755)
    if not (HOOK_DIR / "voice.conf").exists():                 # keep an existing voice.conf
        shutil.copy2(src / "hooks" / "voice.conf", HOOK_DIR / "voice.conf")
    set_voice_conf("VARIO_URL", f"http://127.0.0.1:{PORT}", "Where the hooks post events (this Vario).")
    backup = None
    if plan["changes"]:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        if SETTINGS.exists():
            backup = SETTINGS.with_name(f"{SETTINGS.name}.bak-{time.strftime('%Y%m%d%H%M%S')}")
            shutil.copy2(SETTINGS, backup)
        before = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
        tmp = SETTINGS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_merged(before), indent=2) + "\n")
        tmp.replace(SETTINGS)
    (HOOK_DIR / STAMP).write_text(f"{plan['version']}\n{src}\n{time.time()}\n")
    # what this install added, so `vario uninstall` removes exactly that (and nothing you added since)
    rec = _install_record()
    rec.update({"hook_dir": str(HOOK_DIR), "settings": str(SETTINGS), "scripts": HOOK_SCRIPTS,
                "commands": [f"{HOOK_DIR}/{s_}" for _, s_, _, _ in HOOKS], "version": plan["version"],
                "diff": plan["diff"] or rec.get("diff", []), "backup": str(backup) if backup else rec.get("backup"),
                "voice_conf_created": rec.get("voice_conf_created") or any(f["name"] == "voice.conf" for f in plan["files"]),
                "settings_existed": rec.get("settings_existed", plan["settings_exists"]),
                "installed_at": time.time()})
    INSTALL_RECORD.parent.mkdir(parents=True, exist_ok=True)
    INSTALL_RECORD.write_text(json.dumps(rec, indent=1))
    log.info("hooks %s installed from %s (backup %s)", plan["version"], src, backup)
    return {**hooks_plan(), "ok": True, "backup": str(backup) if backup else None}


# --- 4. sound / 6. voice ------------------------------------------------------------------------------
async def start_bubo(wait_s: float = 45) -> bool:
    if await voice.tts_online():
        return True
    lib = HOOK_DIR / "tts-lib.sh"
    if not lib.exists():
        return False
    await asyncio.create_subprocess_exec("bash", "-c", f'. "{lib}"; daemon_alive || start_daemon',
                                         stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                                         env={**os.environ, "CLAUDE_HOOK_DIR": str(HOOK_DIR)})
    end = time.time() + wait_s
    while time.time() < end:
        if await voice.tts_online():
            return True
        await asyncio.sleep(0.5)
    return False


async def test_sound(text: str = "This is Athena. If you can hear me, the voice is working.") -> dict:
    """A chime through the speakers (no daemon needed), then a spoken line through bubo if it's up."""
    chime = await host.play_chime()
    online = await voice.tts_online()
    muted = voice.global_muted()
    spoke = online and not muted and await voice.speak(text, kind="command")
    return {"chime": chime, "bubo": online, "muted": muted, "spoke": bool(spoke)}


def voices() -> list[dict]:
    """English Kokoro voices (from the pinned model), current one marked."""
    accent = {"a": "American", "b": "British"}
    sex = {"f": "female", "m": "male"}
    cur = voice_current()
    out = []
    for f in MANIFEST["kokoro"]["files"]:
        m = re.match(r"voices/(([ab])([fm])_(\w+))\.safetensors$", f["path"])
        if m:
            out.append({"id": m[1], "name": m[4].capitalize(), "desc": f"{accent[m[2]]} {sex[m[3]]}", "current": m[1] == cur})
    return out


def voice_current() -> str:
    from .config import voice_conf
    return voice_conf("VOICE", "af_heart")
