"""Pause (or duck) music when Vario needs your attention.

Modes (pref "music_mode"): pause (default) | duck | off.  Pref "music_resume" (default True): resume
paused players once speech (and any open mic) is over, or when briefing mode is disarmed. Ducked volume is always restored.
Controls Spotify and Apple Music via AppleScript, and only if they are already running (never launches
them). Browser audio (YouTube etc.) is not controllable this way.
"""
import asyncio
import logging
import shutil

from . import db

log = logging.getLogger("music")
PLAYERS = ("Spotify", "Music")
DUCK_LEVEL = 15


def settings() -> dict:
    return {"mode": db.get_pref("music_mode", "pause"), "resume": bool(db.get_pref("music_resume", True)),
            "duck_level": DUCK_LEVEL}


async def _osa(script: str) -> str | None:
    if not shutil.which("osascript"):
        return None
    try:
        p = await asyncio.create_subprocess_exec("osascript", "-e", script, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), 4)
        if p.returncode != 0:
            log.warning("osascript failed: %s", err.decode().strip()[:200])
            return None
        return out.decode().strip()
    except Exception as e:
        log.warning("osascript error: %s", e)
        return None


async def playing() -> list[tuple[str, int]]:
    """[(app, volume)] for players that are running AND playing."""
    out = []
    for app in PLAYERS:
        r = await _osa(f'if application "{app}" is running then tell application "{app}" to return '
                       f'(player state as string) & "|" & (sound volume as string)\nreturn "off|0"')
        if r and r.startswith("playing|"):
            out.append((app, int(r.split("|")[1] or 0)))
    return out


class Music:
    def __init__(self):
        self.paused: set[str] = set()          # players we paused
        self.ducked: dict[str, int] = {}       # player -> original volume
        self.holds = 0                         # briefing mode holds music down for its whole duration
        self.lock = asyncio.Lock()
        self.gen = 0                           # bumped by every quiet(); a stale release_when_idle() backs off
        self.mic_open = False                  # the dashboard mic is recording
        self.awaiting: set[int] = set()        # dashboard asks spoken and not answered yet: music waits for you

    async def quiet(self) -> list[str]:
        """Called before speech meant for the user. Returns the players acted on."""
        self.gen += 1
        mode = settings()["mode"]
        if mode == "off":
            return []
        async with self.lock:
            acted = []
            for app, vol in await playing():
                if mode == "duck":
                    if app not in self.ducked:
                        self.ducked[app] = vol
                        await _osa(f'tell application "{app}" to set sound volume to {DUCK_LEVEL}')
                        acted.append(app)
                else:
                    await _osa(f'tell application "{app}" to pause')
                    self.paused.add(app)
                    acted.append(app)
            if acted:
                log.info("music %s: %s", "ducked" if mode == "duck" else "paused", ", ".join(acted))
            return acted

    async def release(self, force_resume: bool | None = None):
        """Undo quiet(): restore ducked volume; resume paused players if the user wants that."""
        if self.holds > 0:
            return
        async with self.lock:
            for app, vol in self.ducked.items():
                await _osa(f'tell application "{app}" to set sound volume to {vol}')
            self.ducked.clear()
            resume = settings()["resume"] if force_resume is None else force_resume
            if resume:
                for app in self.paused:
                    await _osa(f'tell application "{app}" to play')
                if self.paused:
                    log.info("music resumed: %s", ", ".join(self.paused))
            self.paused.clear()

    async def release_when_idle(self, grace: float = 0.4):
        """Release once the voice daemon has been quiet for a moment, unless something newer took over
        (another line was spoken, the mic is open, or briefing holds the music down)."""
        from . import voice
        gen = self.gen
        await asyncio.sleep(grace)
        quiet_since = None
        for _ in range(600):                   # ~2 min ceiling
            if self.gen != gen or self.mic_open or self.holds or self.awaiting:
                return
            if await voice.busy():
                quiet_since = None
            elif quiet_since is None:
                quiet_since = asyncio.get_running_loop().time()
            elif asyncio.get_running_loop().time() - quiet_since >= 0.6:
                break
            await asyncio.sleep(0.2)
        if self.gen == gen and not self.mic_open and not self.awaiting and (self.paused or self.ducked):
            await self.release()

    async def mic(self, on: bool):
        """Dashboard mic opened/closed. Closing waits a little: a spoken reply usually follows."""
        self.mic_open = on
        if on:
            await self.quiet()
        else:
            asyncio.get_running_loop().create_task(self.release_when_idle(grace=3.0))

    def await_answer(self, report_id: int, max_s: float = 300):
        """A dashboard session asked something out loud: keep the music down until it's answered
        (or max_s passes). Terminal sessions never do this; their music resumes right after the speech."""
        self.awaiting.add(report_id)

        async def expire():
            await asyncio.sleep(max_s)
            self.answered(report_id)
        asyncio.get_running_loop().create_task(expire())

    def answered(self, report_id: int):
        if report_id in self.awaiting:
            self.awaiting.discard(report_id)
            asyncio.get_running_loop().create_task(self.release_when_idle(grace=2.0))   # a confirmation may follow

    async def hold(self):
        self.holds += 1
        await self.quiet()

    async def unhold(self):
        self.holds = max(0, self.holds - 1)
        await self.release()


music = Music()
