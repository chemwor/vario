"""The `vario` command (bin/vario runs this with the app's venv).

  vario [start] [--no-open]   start the backend (it serves the UI) and open it; the first launch opens setup
  vario stop                  stop it
  vario status                running or not, URL, version, setup and hooks state
  vario doctor [--deep]       every check, pass / warn / fail (exit 1 on a failure); --deep also hashes models
  vario demo [--stop]         a separate instance with made-up sessions and reports (marked DEMO), own data dir
  vario mute | unmute         silence / restore all speech
  vario voice [NAME [SPEED]]  show or set the voice; `vario voice list`, `vario voice restart`
  vario autostart on|off      start Vario at login (LaunchAgent on macOS, systemd user unit on Linux)
  vario logs                  follow the log
  vario uninstall [--data] [--models] [--yes]
  vario run                   run in the foreground (what autostart uses)

Everything lives under VARIO_HOME (default ~/.local/share/vario): config, data, logs, run state.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
VARIO_HOME = Path(os.environ.setdefault("VARIO_HOME", str(HOME / ".local" / "share" / "vario"))).expanduser()
APP_ROOT = Path(__file__).resolve().parent.parent.parent          # the app folder (has backend/, web/, voice/)
PORT = int(os.environ.setdefault("DASHBOARD_API_PORT", os.environ.get("VARIO_PORT", "8787")))
DEMO_PORT = int(os.environ.get("VARIO_DEMO_PORT", "8797"))
RUN = VARIO_HOME / "run"
LOGS = VARIO_HOME / "logs"
BIN_LINK = Path(os.environ.get("VARIO_BIN_DIR", HOME / ".local" / "bin")) / "vario"
LABEL = "com.vario.dashboard"

TTY = sys.stdout.isatty()
G, R, Y, D, B, X = (("\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m") if TTY else ("",) * 6)


def ok(label, detail=""):
    print(f"  {G}✓{X} {label:<34} {D}{detail}{X}")


def warn(label, detail=""):
    print(f"  {Y}!{X} {label:<34} {detail}")


def bad(label, detail=""):
    print(f"  {R}✗{X} {label:<34} {detail}")


# --- the server process ------------------------------------------------------------------------------
def _pidfile(port: int) -> Path:
    return RUN / f"vario-{port}.pid"


def _pid(port: int) -> int | None:
    try:
        pid = int(_pidfile(port).read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _get(url: str, timeout: float = 2) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def url(port: int = PORT) -> str:
    return f"http://127.0.0.1:{port}"


def _server_env(port: int, extra: dict | None = None) -> dict:
    return {**os.environ, "VARIO_HOME": str(VARIO_HOME), "DASHBOARD_API_PORT": str(port), "NEXT_TELEMETRY_DISABLED": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1", **(extra or {})}


def _uvicorn(port: int) -> list[str]:
    # 127.0.0.1 only: nothing on the network can reach it (see SECURITY.md)
    return [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning"]


def start(port: int = PORT, open_browser: bool = True, extra_env: dict | None = None, label: str = "Vario") -> int:
    if not (APP_ROOT / "web" / "out" / "index.html").exists():
        bad("web app not built", f"run: cd {APP_ROOT}/web && npm ci && npx next build")
        return 1
    pid = _pid(port)
    if pid and _get(url(port) + "/api/health"):
        print(f"{label} is already running at {url(port)} (pid {pid}).")
    else:
        RUN.mkdir(parents=True, exist_ok=True)
        LOGS.mkdir(parents=True, exist_ok=True)
        log = open(LOGS / f"vario-{port}.log", "ab")
        p = subprocess.Popen(_uvicorn(port), cwd=APP_ROOT / "backend", env=_server_env(port, extra_env),
                             stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        _pidfile(port).write_text(str(p.pid))
        for _ in range(120):
            if _get(url(port) + "/api/health"):
                break
            if p.poll() is not None:
                bad(f"{label} exited during startup", f"see {LOGS / f'vario-{port}.log'}")
                return 1
            time.sleep(0.25)
        else:
            bad(f"{label} didn't answer within 30s", f"see {LOGS / f'vario-{port}.log'}")
            return 1
        print(f"{label} is running at {B}{url(port)}{X} (pid {p.pid}).")
    if open_browser:
        from .host import open_url
        open_url(url(port))
    return 0


def stop(port: int = PORT, label: str = "Vario") -> int:
    pid = _pid(port)
    if not pid:
        print(f"{label} isn't running.")
        _pidfile(port).unlink(missing_ok=True)
        return 0
    os.killpg(pid, signal.SIGTERM) if hasattr(os, "killpg") else os.kill(pid, signal.SIGTERM)
    for _ in range(40):
        try:
            os.kill(pid, 0)
            time.sleep(0.25)
        except OSError:
            break
    else:
        os.kill(pid, signal.SIGKILL)
    _pidfile(port).unlink(missing_ok=True)
    print(f"Stopped {label}.")
    return 0


def run_foreground() -> int:
    os.chdir(APP_ROOT / "backend")
    os.environ.update(_server_env(PORT))
    os.execv(sys.executable, _uvicorn(PORT))


# --- status / doctor --------------------------------------------------------------------------------------
def status() -> int:
    pid = _pid(PORT)
    health = _get(url() + "/api/health") if pid else None
    from .config import VERSION
    print(f"{B}Vario {VERSION}{X}  {D}{APP_ROOT}{X}")
    if health:
        ok("running", f"{url()} (pid {pid})")
        setup = _get(url() + "/api/setup") or {}
        steps = setup.get("steps", {})
        skipped = [k for k, v in steps.items() if v.get("status") == "skipped"]
        if setup.get("finished"):
            (warn if skipped else ok)("setup", f"finished{', skipped: ' + ', '.join(skipped) if skipped else ''}")
        else:
            warn("setup", "not finished: open the dashboard to run it")
    else:
        warn("not running", "start it with: vario")
    from . import firstrun
    v = firstrun.installed_version()
    (ok if v["version"] else warn)("hooks", v["version"] or "not installed (setup installs them)")
    return 0


def _claude_version() -> str | None:
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=10).stdout
        return out.split()[0] if out.strip() else None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def _vt(v: str | None) -> tuple:
    try:
        return tuple(int(x) for x in (v or "").split("-")[0].split("."))
    except ValueError:
        return ()


def _du(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).lstat().st_size
            except OSError:
                pass
    return total


def _blobs(model_dir: Path) -> int:
    """Bytes behind a Hugging Face model folder (its snapshot links point into a shared blob store)."""
    total = 0
    for f in model_dir.glob("snapshots/*/**/*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def doctor(deep: bool = False) -> int:
    from . import firstrun, host
    from .config import DB_PATH, HOOK_DIR, STT_PYTHON, VERSION, bubo_sock
    fails = 0

    def check(cond, label, good, problem, required=True):
        nonlocal fails
        if cond:
            ok(label, good)
        elif required:
            bad(label, problem)
            fails += 1
        else:
            warn(label, problem)

    print(f"{B}vario doctor{X}  {D}Vario {VERSION}, {host.capabilities()['os']}{X}\n\nDependencies")
    for d in firstrun.deps():
        check(d["ok"], d["label"], d["version"] or "", d["fix"], d["required"])

    print("\nClaude Code")
    tested_file = APP_ROOT / "tested-claude-version.txt"
    tested = tested_file.read_text().strip() if tested_file.exists() else None
    cv = _claude_version()
    if cv and tested and _vt(cv) > _vt(tested):
        warn("version", f"{cv} is newer than the last tested ({tested}); report anything odd")
    else:
        check(bool(cv), "version", f"{cv} (tested with {tested})", "claude not found")

    print("\nVario")
    pid = _pid(PORT)
    health = _get(url() + "/api/health") if pid else None
    check(bool(health), "backend", f"{url()} (127.0.0.1 only)", "not running: start it with `vario`", required=False)
    check((APP_ROOT / "web" / "out" / "index.html").exists(), "web app", "built", "not built")
    setup = firstrun.state()
    skipped = [firstrun.STEP_LABEL[k] for k, v in setup["steps"].items() if v["status"] == "skipped"]
    check(setup["finished"] and not skipped, "setup", "finished",
          "skipped: " + ", ".join(skipped) if skipped else "not finished (open the dashboard)", required=False)
    plan = firstrun.hooks_plan()
    hv = firstrun.installed_version()["version"]
    current = plan.get("ok") and not plan.get("changes") and all(f["state"] == "same" for f in plan.get("files", []))
    check(bool(hv) and current, "hooks", f"{hv} in {HOOK_DIR}",
          "not installed" if not hv else f"out of date ({hv}; this version ships {plan.get('version')}): re-run setup's hooks step",
          required=False)

    print("\nVoice")
    import asyncio
    from . import voice
    alive = asyncio.run(voice.tts_online())
    check(alive, "bubo (voice daemon)", str(bubo_sock()), "not running: it starts when something speaks", required=False)
    check(not voice.global_muted(), "speech", "on", "muted (vario unmute)", required=False)
    caps = host.capabilities()
    check(caps["chime"], "sound output", "", "no afplay/paplay/aplay", required=False)
    check(caps["notifications"], "desktop notifications", "", "install notify-send (libnotify)", required=False)
    check(caps["keep_awake"], "keep awake while working", "", "no caffeinate/systemd-inhibit", required=False)
    if not caps["kokoro"]:
        warn("Kokoro voice", "Apple Silicon only in v0.1; speech falls back to espeak-ng / say")
    for key, m in firstrun.MANIFEST.items():
        home = Path(os.path.expanduser(m["hf_home"]))
        d = home / "hub" / ("models--" + m["repo"].replace("/", "--")) / "snapshots" / m["revision"]
        present = all((d / f["path"]).exists() for f in m["files"])
        if present and deep and Path(STT_PYTHON).exists():
            job = asyncio.run(firstrun.fetch_model(key, verify_only=True))
            check(job.get("ok"), m["label"], "sha256 verified", f"checksum failed: {', '.join(job.get('bad', [])[:3])}")
        else:
            check(present, m["label"], "present" + ("" if deep else " (--deep checks sha256)"),
                  "not downloaded (setup downloads it)", required=False)

    print("\nDisk")
    db_size = sum(_du(p) for p in DB_PATH.parent.glob(DB_PATH.name + "*"))
    ok("database", f"{db_size / 1e6:.1f} MB  {DB_PATH}")
    model_bytes = 0
    for m in firstrun.MANIFEST.values():
        model_bytes += _blobs(Path(os.path.expanduser(m["hf_home"])) / "hub" / ("models--" + m["repo"].replace("/", "--")))
    ok("models", f"{model_bytes / 1e6:.0f} MB")
    venv = firstrun.VOICE_HOME / "venv"
    if venv.exists():
        ok("voice runtime", f"{_du(venv) / 1e6:.0f} MB  {venv}")

    print()
    if fails:
        print(f"{R}{fails} check(s) failed.{X}")
    else:
        print(f"{G}No failures.{X}")
    return 1 if fails else 0


# --- mute / voice -------------------------------------------------------------------------------------
def mute(on: bool) -> int:
    from .config import MUTE_FILE
    import asyncio
    from . import voice
    if on:
        MUTE_FILE.parent.mkdir(parents=True, exist_ok=True)
        MUTE_FILE.touch()
        asyncio.run(voice.stop())
        print("Muted. `vario unmute` turns speech back on.")
    else:
        MUTE_FILE.unlink(missing_ok=True)
        print("Speech on.")
    return 0


def voice_cmd(args: list[str]) -> int:
    from . import firstrun
    from .config import set_voice_conf, voice_conf
    if not args:
        print(f"voice {voice_conf('VOICE', 'af_heart')}, speed {voice_conf('SPEED', '1.0')}  (vario voice list)")
        return 0
    if args[0] == "list":
        cur = firstrun.voice_current()
        for v in firstrun.voices():
            print(f"  {'*' if v['id'] == cur else ' '} {v['id']:<14} {v['desc']}")
        return 0
    if args[0] == "restart":
        from .config import HOOK_DIR
        lib = HOOK_DIR / "tts-lib.sh"
        if not lib.exists():
            bad("hooks not installed", "run setup first")
            return 1
        subprocess.run(["bash", "-c", f'. "{lib}"; p="$(cat "$HOOK_DIR/bubo.pid" 2>/dev/null)"; '
                        '[ -n "$p" ] && kill "$p" 2>/dev/null; sleep 0.5; start_daemon'], check=False)
        print("Restarting bubo (it takes a few seconds to warm up).")
        return 0
    ids = {v["id"] for v in firstrun.voices()}
    if args[0] not in ids:
        bad("unknown voice", f"{args[0]} (vario voice list)")
        return 1
    set_voice_conf("VOICE", args[0])
    if len(args) > 1:
        try:
            sp = float(args[1])
            assert 0.5 <= sp <= 2.0
        except (ValueError, AssertionError):
            bad("speed", "between 0.5 and 2.0")
            return 1
        set_voice_conf("SPEED", f"{sp:g}")
    print(f"voice {args[0]}" + (f", speed {args[1]}" if len(args) > 1 else ""))
    return 0


# --- autostart ----------------------------------------------------------------------------------------
def _launch_agent() -> Path:
    return HOME / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _systemd_unit() -> Path:
    return HOME / ".config" / "systemd" / "user" / "vario.service"


def autostart(on: bool) -> int:
    from .host import MAC
    exe = str(BIN_LINK if BIN_LINK.exists() else APP_ROOT / "bin" / "vario")
    if MAC:
        plist = _launch_agent()
        tgt = f"gui/{os.getuid()}/{LABEL}"
        subprocess.run(["launchctl", "bootout", tgt], capture_output=True)
        if not on:
            plist.unlink(missing_ok=True)
            print("Autostart off.")
            return 0
        plist.parent.mkdir(parents=True, exist_ok=True)
        LOGS.mkdir(parents=True, exist_ok=True)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{exe}</string><string>run</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>VARIO_HOME</key><string>{VARIO_HOME}</string>
    <key>VARIO_PORT</key><string>{PORT}</string>
    <key>PATH</key><string>{os.environ.get('PATH', '/usr/bin:/bin')}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>{LOGS / 'autostart.log'}</string>
  <key>StandardErrorPath</key><string>{LOGS / 'autostart.log'}</string>
</dict></plist>
""")
        stop()                                          # the LaunchAgent owns it from here
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], capture_output=True, text=True)
        print("Autostart on: Vario starts at login." if r.returncode == 0 else f"launchctl: {r.stderr.strip()}")
        return r.returncode
    unit = _systemd_unit()
    if not on:
        subprocess.run(["systemctl", "--user", "disable", "--now", "vario.service"], capture_output=True)
        unit.unlink(missing_ok=True)
        print("Autostart off.")
        return 0
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(f"""[Unit]
Description=Vario
[Service]
Environment=VARIO_HOME={VARIO_HOME}
Environment=VARIO_PORT={PORT}
ExecStart={exe} run
Restart=on-failure
[Install]
WantedBy=default.target
""")
    stop()
    r = subprocess.run(["systemctl", "--user", "enable", "--now", "vario.service"], capture_output=True, text=True)
    print("Autostart on." if r.returncode == 0 else f"systemctl: {r.stderr.strip()}")
    return r.returncode


# --- uninstall ----------------------------------------------------------------------------------------
def _ask(q: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        return False
    return input(f"{q} [y/N] ").strip().lower() in ("y", "yes")


def uninstall(data: bool, models: bool, yes: bool) -> int:
    from . import firstrun
    from .host import MAC
    plan = firstrun.uninstall_plan()
    print(f"{B}Uninstall Vario{X}\n")
    if plan["diff"]:
        print(f"This change to {plan['settings']} removes Vario's hooks (a backup is written first):\n")
        for line in plan["diff"]:
            c = G if line.startswith("+") and not line.startswith("+++") else R if line.startswith("-") and not line.startswith("---") else D
            print(f"  {c}{line}{X}")
        print()
    else:
        print("No Vario hooks are registered in settings.json.\n")
    targets = [f"hook scripts in {firstrun.HOOK_DIR}", f"the app in {VARIO_HOME / 'app'}", f"the `vario` command ({BIN_LINK})"]
    if data:
        targets.append(f"your data and config ({VARIO_HOME})")
    if models:
        targets.append(f"voice runtime and models ({firstrun.VOICE_HOME}, Whisper in the Hugging Face cache)")
    print("Also removes: " + "; ".join(targets) + ".")
    if not data:
        print(f"{D}Keeps your data in {VARIO_HOME} (add --data to remove it) and the models (add --models).{X}")
    if not _ask("Go ahead?", yes):
        print("Nothing changed.")
        return 1
    stop()
    stop(DEMO_PORT, "the demo")
    # bubo: stop it if it's ours
    subprocess.run(["bash", "-c", f'p="$(cat "{firstrun.HOOK_DIR}/bubo.pid" 2>/dev/null)"; [ -n "$p" ] && kill "$p"'],
                   capture_output=True)
    res = firstrun.uninstall_hooks(remove_voice_conf=data)
    if res.get("backup"):
        ok("hooks removed", f"settings backup: {res['backup']}")
    else:
        ok("hook scripts removed")
    if MAC:
        for label in (LABEL, "com.vario.bubo"):
            plist = HOME / "Library" / "LaunchAgents" / f"{label}.plist"
            # only ours: a plist that points into this home's Vario install / hook dir
            if plist.exists() and (str(VARIO_HOME) in plist.read_text() or str(firstrun.HOOK_DIR) in plist.read_text()
                                   or str(BIN_LINK) in plist.read_text()):
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
                plist.unlink()
                ok("LaunchAgent removed", label)
    elif _systemd_unit().exists():
        autostart(False)
    if BIN_LINK.is_symlink() or BIN_LINK.exists():
        BIN_LINK.unlink()
        ok("command removed", str(BIN_LINK))
    if models:
        for m in firstrun.MANIFEST.values():
            d = Path(os.path.expanduser(m["hf_home"])) / "hub" / ("models--" + m["repo"].replace("/", "--"))
            # newer Hugging Face caches keep file data in a shared blob store: remove the blobs our snapshots point to
            for link in d.glob("snapshots/*/**/*"):
                try:
                    if link.is_symlink():
                        Path(os.path.realpath(link)).unlink(missing_ok=True)
                except OSError:
                    pass
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(firstrun.VOICE_HOME, ignore_errors=True)
        ok("voice runtime and models removed")
        print(f"  {D}uv's download cache (~/.cache/uv) is shared with other tools; `uv cache clean` empties it.{X}")
    app_dir = VARIO_HOME / "app"
    if data:
        shutil.rmtree(VARIO_HOME, ignore_errors=True)
        ok("data and config removed", str(VARIO_HOME))
        from .config import VARIO_DIR
        if VARIO_DIR.exists():                         # the drop folder: only if nothing was ever dropped in it
            if any(p.is_file() for p in VARIO_DIR.rglob("*")):
                warn("kept your drop folder", f"{VARIO_DIR} has files in it")
            else:
                shutil.rmtree(VARIO_DIR, ignore_errors=True)
    elif app_dir.exists():
        shutil.rmtree(app_dir, ignore_errors=True)
        (VARIO_HOME / "current").unlink(missing_ok=True)
        shutil.rmtree(VARIO_HOME / "venv", ignore_errors=True)
        ok("app removed", f"data kept in {VARIO_HOME}")
    print("\nVario is uninstalled.")
    if "/Cellar/" in str(APP_ROOT):
        print("Installed with Homebrew: finish with `brew uninstall vario`.")
    return 0


# --- demo -----------------------------------------------------------------------------------------------
def demo(stop_it: bool, open_browser: bool) -> int:
    demo_home = VARIO_HOME / "demo"
    env = {"VARIO_HOME": str(demo_home), "VARIO_DEMO": "1", "CLAUDE_PROJECTS_DIR": str(demo_home / "projects"),
           "CLAUDE_HOOK_DIR": str(demo_home / "hooks"), "VARIO_DIR": str(demo_home / "drops"), "SCOUTS": "off",
           "STUCK_MINUTES": "999"}
    if stop_it:
        return stop(DEMO_PORT, "the demo")
    from . import demo as seed
    seed.seed(demo_home)
    print(f"{Y}DEMO{X}: made-up sessions and reports in {demo_home} (your real data isn't touched).")
    return start(DEMO_PORT, open_browser, env, label="The demo")


def main(argv: list[str] | None = None) -> int:
    import logging
    logging.basicConfig(level=logging.ERROR)          # the CLI prints its own findings; no library chatter
    p = argparse.ArgumentParser(prog="vario", description="Vario: a decision inbox and voice briefing for Claude Code.")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("start", help="start Vario and open it (the default)")
    s.add_argument("--no-open", action="store_true")
    sub.add_parser("stop", help="stop Vario")
    sub.add_parser("status", help="is it running, setup state")
    d = sub.add_parser("doctor", help="check everything")
    d.add_argument("--deep", action="store_true", help="also verify model checksums")
    dm = sub.add_parser("demo", help="explore with made-up data (separate instance)")
    dm.add_argument("--stop", action="store_true")
    dm.add_argument("--no-open", action="store_true")
    sub.add_parser("mute", help="silence all speech")
    sub.add_parser("unmute", help="turn speech back on")
    v = sub.add_parser("voice", help="show/set the voice: vario voice [list|restart|NAME [SPEED]]")
    v.add_argument("args", nargs="*")
    a = sub.add_parser("autostart", help="start at login: on|off")
    a.add_argument("state", choices=["on", "off"])
    sub.add_parser("logs", help="follow the log")
    u = sub.add_parser("uninstall", help="remove hooks, LaunchAgent, the command (and optionally data/models)")
    u.add_argument("--data", action="store_true", help="also remove data and config")
    u.add_argument("--models", action="store_true", help="also remove the voice runtime and models")
    u.add_argument("--yes", "-y", action="store_true")
    sub.add_parser("run", help="run in the foreground")
    sub.add_parser("version")
    args = p.parse_args(argv)
    cmd = args.cmd or "start"
    if cmd == "start":
        return start(open_browser=not getattr(args, "no_open", False))
    if cmd == "stop":
        return stop()
    if cmd == "status":
        return status()
    if cmd == "doctor":
        return doctor(args.deep)
    if cmd == "demo":
        return demo(args.stop, not args.no_open)
    if cmd in ("mute", "unmute"):
        return mute(cmd == "mute")
    if cmd == "voice":
        return voice_cmd(args.args)
    if cmd == "autostart":
        return autostart(args.state == "on")
    if cmd == "logs":
        f = LOGS / f"vario-{PORT}.log"
        if not f.exists():
            print("No log yet.")
            return 0
        os.execvp("tail", ["tail", "-n", "50", "-f", str(f)])
    if cmd == "uninstall":
        return uninstall(args.data, args.models, args.yes)
    if cmd == "run":
        return run_foreground()
    if cmd == "version":
        from .config import VERSION
        print(VERSION)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
