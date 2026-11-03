"""Paths and tunables. Everything can be overridden with env vars."""
import os
from pathlib import Path

HOME = Path.home()
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# --- profile: env var wins, then vario.conf (KEY=VALUE lines), then the default ----------------------
# (pallas.conf / mission-control.conf and PALLAS_CONFIG / MC_CONFIG: pre-rename names, still read when
#  there's no vario.conf)
# An installed Vario (the `vario` CLI) sets VARIO_HOME (default ~/.local/share/vario): config, data and logs live
# there, outside the app folder, so upgrades replace the app and keep your data. A source checkout without
# VARIO_HOME keeps everything in the repo as before (vario.conf next to the Makefile, backend/data/).
VARIO_HOME = Path(os.environ["VARIO_HOME"]).expanduser() if os.environ.get("VARIO_HOME") else None
_conf_env = os.environ.get("VARIO_CONFIG") or os.environ.get("PALLAS_CONFIG") or os.environ.get("MC_CONFIG")
CONFIG_FILE = Path(_conf_env) if _conf_env else (VARIO_HOME / "vario.conf") if VARIO_HOME else next(
    (p for p in (REPO_ROOT / n for n in ("vario.conf", "pallas.conf", "mission-control.conf")) if p.exists()),
    REPO_ROOT / "vario.conf")
VERSION = (REPO_ROOT / "VERSION").read_text().strip() if (REPO_ROOT / "VERSION").exists() else "dev"


def _read_conf(path: Path) -> dict[str, str]:
    out = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.split(" #", 1)[0].split("\t#", 1)[0]          # trailing comment
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


_CONF = _read_conf(CONFIG_FILE)


def setting(key: str, default: str = "") -> str:
    return os.environ.get(key) or _CONF.get(key) or default


PROFILE = setting("PROFILE", "personal").lower()     # personal | work
WORK = PROFILE == "work"
# Speech-to-text is local by default: the browser records, Whisper transcribes on this Mac, nothing leaves it.
# STT=webspeech brings back the browser's speech service (it sends audio to Google) as a fallback; the work
# profile ignores that and stays local.
STT_MODE = "local" if WORK else setting("STT", "local")               # local | webspeech
# Engine: auto = mlx-whisper on Apple Silicon, else whisper.cpp (also the fallback if mlx-whisper won't start)
STT_ENGINE = setting("STT_ENGINE", "auto")                             # auto | mlx | whispercpp
# small.en + vocabulary hints measured best here (0% WER on clean clips, ~75 ms); large-v3-turbo is an option
STT_MODEL = setting("STT_MODEL", "mlx-community/whisper-small.en-mlx")
STT_PYTHON = setting("STT_PYTHON", str(HOME / ".local" / "share" / "claude-voice" / "venv" / "bin" / "python"))
WHISPER_CPP_BIN = setting("WHISPER_CPP_BIN", "whisper-cli")
WHISPER_CPP_MODEL = setting("WHISPER_CPP_MODEL", "")                   # /path/to/ggml-small.en.bin
# Extra words Whisper should expect (comma-separated), on top of live session names and the commands
STT_VOCAB = setting("STT_VOCAB", "")
SCOUTS_ENABLED = not WORK and setting("SCOUTS", "on") != "off"
# Env vars that would make the claude CLI bill an API key instead of the logged-in account.
API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
HOOK_DIR = Path(os.environ.get("CLAUDE_HOOK_DIR", HOME / ".claude" / "hooks"))
VOICE_CONF = HOOK_DIR / "voice.conf"            # shared with the hooks and bubo


def voice_conf(key: str, default: str = "") -> str:
    """A voice.conf value, read fresh each call so edits apply without a restart."""
    return _read_conf(VOICE_CONF).get(key) or default


def set_voice_conf(key: str, value: str, comment: str | None = None):
    """Set KEY=value in voice.conf in place (appended, with an optional comment line, if it isn't there)."""
    try:
        lines = VOICE_CONF.read_text().splitlines()
    except OSError:
        lines = []
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        lines += ["", *([f"# {comment}"] if comment else []), f"{key}={value}"]
    tmp = VOICE_CONF.with_suffix(".conf.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(VOICE_CONF)


def bubo_sock() -> Path:
    """bubo's socket: BUBO_SOCKET (env, then voice.conf; relative to the hook dir), default tts.sock."""
    s = os.environ.get("BUBO_SOCKET") or os.environ.get("TTS_SOCK") or voice_conf("BUBO_SOCKET", "tts.sock")
    p = Path(s).expanduser()
    return p if p.is_absolute() else HOOK_DIR / p


# Home for file drops, scout output and the archive. ~/Pallas and ~/MissionControl (pre-rename paths) link to it.
VARIO_DIR = Path(os.environ.get("VARIO_DIR") or os.environ.get("PALLAS_DIR") or os.environ.get("MISSION_CONTROL_DIR")
                 or HOME / "Vario")
MUTE_FILE = HOOK_DIR / ".muted"
WATCHER_DIR = HOOK_DIR / "watchers"          # live-hook watchers (avoid double speech)
PROJECTS_DIR = Path(os.environ.get("CLAUDE_PROJECTS_DIR", HOME / ".claude" / "projects"))
DATA_DIR = Path(os.environ.get("DASHBOARD_DATA_DIR") or (VARIO_HOME / "data" if VARIO_HOME
                                                        else Path(__file__).resolve().parent.parent / "data"))
# the built web app (web/out); the backend serves it at / when it exists
WEB_DIR = Path(os.environ.get("VARIO_WEB_DIR") or REPO_ROOT / "web" / "out")
DB_PATH = DATA_DIR / "dashboard.sqlite3"

# Prefer the user's installed cleanup script so the dashboard speaks exactly like the hooks.
_HOOK_CLEAN = HOOK_DIR / "tts-clean.pl"
CLEAN_SCRIPT = _HOOK_CLEAN if _HOOK_CLEAN.exists() else Path(__file__).parent / "vendor" / "tts-clean.pl"

MIN_CHARS = int(os.environ.get("TTS_MIN_CHARS", "20"))       # skip "Let me check." style filler
TERMINAL_WINDOW_S = int(os.environ.get("TERMINAL_WINDOW_S", str(2 * 3600)))  # show terminal sessions active in last 2h
TERMINAL_SPEAK = os.environ.get("TERMINAL_SPEAK", "1") == "1"
PORT = int(os.environ.get("DASHBOARD_API_PORT", "8787"))
