"""Speech output via bubo, the Kokoro daemon (socket from voice.conf BUBO_SOCKET). No TTS lives here.

The daemon preempts on every request, so a newer message cuts off an older one.
"""
import asyncio
import json
import logging
import subprocess
import time

from .config import CLEAN_SCRIPT, MIN_CHARS, MUTE_FILE, bubo_sock
from .hub import hub

log = logging.getLogger("voice")


def clean(text: str) -> str:
    """Same markdown cleanup + 500-char truncation the hooks use (tts-clean.pl)."""
    try:
        out = subprocess.run(["perl", str(CLEAN_SCRIPT)], input=text, capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:
        return text.strip()[:500]


def substantive(text: str) -> bool:
    return len(text) >= MIN_CHARS and any(c.isalnum() for c in text)


def global_muted() -> bool:
    return MUTE_FILE.exists()


def set_global_mute(on: bool):
    if on:
        MUTE_FILE.parent.mkdir(parents=True, exist_ok=True)
        MUTE_FILE.touch()
        asyncio.get_running_loop().create_task(_send({"cmd": "stop"}))
    else:
        MUTE_FILE.unlink(missing_ok=True)


async def _send(req: dict) -> bool:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(bubo_sock())), timeout=1)
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        reply = await asyncio.wait_for(reader.readline(), timeout=2)
        writer.close()
        return reply.strip() in (b"ok", b"pong")
    except Exception as e:
        log.warning("bubo unreachable (%s): %s", bubo_sock(), type(e).__name__)
        return False


async def tts_online() -> bool:
    return await _send({"cmd": "ping"})


async def speak(text: str, *, session_id: str | None = None, muted: bool = False, kind: str = "message") -> bool:
    """Send already-prefixed text to the daemon. Emits a `speak` event for on-screen captions."""
    if not text or muted or global_muted():
        return False
    from .music import music                   # every spoken line pauses/ducks music first (setting: off|pause|duck)
    await music.quiet()
    ok = await _send({"cmd": "speak", "text": text})
    await hub.emit("speak", session_id=session_id, text=text, kind=kind, delivered=ok)
    if music.paused or music.ducked:           # bring it back once this (and anything after it) is done
        asyncio.get_running_loop().create_task(music.release_when_idle())
    return ok


async def stop():
    await _send({"cmd": "stop"})


async def busy() -> bool | None:
    """True while the daemon is synthesizing/playing; None if it doesn't support `status`."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(bubo_sock())), timeout=1)
        writer.write(b'{"cmd":"status"}\n')
        await writer.drain()
        reply = await asyncio.wait_for(reader.readline(), timeout=1)
        writer.close()
        return bool(json.loads(reply).get("busy"))
    except Exception:
        return None


async def speak_and_wait(text: str, *, session_id: str | None = None, kind: str = "briefing", max_s: float = 60) -> bool:
    """Speak and return once playback has finished (so we never listen while speaking)."""
    ok = await speak(text, session_id=session_id, kind=kind)
    if not ok:
        return False
    start = time.time()
    await asyncio.sleep(0.3)                       # let the daemon pick the request up
    while time.time() - start < max_s:
        b = await busy()
        if b is None:                              # old daemon: estimate ~2.6 words/sec at speed 1.1
            await asyncio.sleep(max(0.0, len(text.split()) / 2.6 + 0.5 - (time.time() - start)))
            break
        if not b:
            break
        await asyncio.sleep(0.15)
    return True
