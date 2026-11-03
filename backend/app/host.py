"""The few things that differ between macOS and Linux, in one place. Each picks the first tool that exists and
degrades to doing nothing (never raising) when none does, so a missing tool shows up as a Health warning, not a crash.

  play_chime()       afplay (macOS) | paplay | aplay
  inhibit_sleep_cmd  caffeinate -i (macOS) | systemd-inhibit (Linux), tied to the backend's pid
  notify(title, ...) osascript notification (macOS) | notify-send
  say_cmd(text)      say (macOS) | espeak-ng        (last-resort speech when bubo is down)
  open_url(url)      open (macOS) | xdg-open
Windows is out of scope for v0.1.
"""
import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("platform")
MAC = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")

CHIMES = [Path("/System/Library/Sounds/Glass.aiff"),
          Path("/usr/share/sounds/freedesktop/stereo/complete.oga"),
          Path("/usr/share/sounds/alsa/Front_Center.wav")]


def _first(*cmds: str) -> str | None:
    return next((c for c in cmds if shutil.which(c)), None)


def player() -> list[str] | None:
    if MAC and shutil.which("afplay"):
        return ["afplay"]
    p = _first("paplay", "aplay")
    return [p] + (["-q"] if p == "aplay" else []) if p else None


async def play_chime() -> bool:
    """A short system sound through the speakers. False if there's no player or no sound file."""
    cmd = player()
    sound = next((c for c in CHIMES if c.exists() and (c.suffix != ".oga" or cmd and cmd[0] == "paplay")), None)
    if not cmd or not sound:
        return False
    try:
        p = await asyncio.create_subprocess_exec(*cmd, str(sound), stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.DEVNULL)
        return await p.wait() == 0
    except OSError:
        return False


def inhibit_sleep_cmd(pid: int, why: str = "Vario: sessions are running") -> list[str] | None:
    """A command that keeps the machine from idle-sleeping until it's killed or `pid` exits."""
    if MAC and shutil.which("caffeinate"):
        return ["caffeinate", "-i", "-w", str(pid)]
    if shutil.which("systemd-inhibit") and shutil.which("tail"):
        return ["systemd-inhibit", "--what=idle", "--who=Vario", f"--why={why}", "--mode=block",
                "tail", f"--pid={pid}", "-f", "/dev/null"]
    return None


def notify_cmd(title: str, body: str) -> list[str] | None:
    if MAC and shutil.which("osascript"):
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}"']
    if shutil.which("notify-send"):
        return ["notify-send", "--app-name=Vario", title, body]
    return None


async def notify(title: str, body: str) -> bool:
    cmd = notify_cmd(title, body[:240])
    if not cmd:
        return False
    try:
        p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        return await asyncio.wait_for(p.wait(), 5) == 0
    except (OSError, asyncio.TimeoutError):
        return False


def say_cmd(text: str) -> list[str] | None:
    if MAC and shutil.which("say"):
        return ["say", "-r", "200", "--", text]
    if shutil.which("espeak-ng"):
        return ["espeak-ng", "-s", "175", "--", text]
    return None


def open_url(url: str) -> bool:
    cmd = ["open", url] if MAC else ["xdg-open", url] if shutil.which("xdg-open") else None
    if not cmd:
        return False
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def capabilities() -> dict:
    """What this machine can do, for Health and `vario doctor`."""
    return {"os": "macOS" if MAC else "Linux" if LINUX else sys.platform,
            "chime": bool(player()), "keep_awake": bool(inhibit_sleep_cmd(os.getpid())),
            "notifications": bool(notify_cmd("t", "b")), "fallback_speech": bool(say_cmd("t")),
            "kokoro": MAC and os.uname().machine == "arm64"}
