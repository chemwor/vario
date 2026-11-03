"""Phase D: keep the Mac awake while work is in flight.

Holds a `caffeinate -i` assertion (prevents idle system sleep; the display may still sleep) while any
dashboard session is working/waiting, any terminal session is working, any scout is running, or
briefing mode is armed. caffeinate is started with `-w <our pid>` so it can never outlive the backend.
It is a process assertion only: no system power settings are changed.
"""
import asyncio
import logging
import os
import time

from . import db, host
from .hub import hub

log = logging.getLogger("awake")
CHECK_S = 10


class Awake:
    def __init__(self, sessions_fn, briefing):
        self.sessions_fn, self.briefing = sessions_fn, briefing
        self.proc: asyncio.subprocess.Process | None = None
        self.reasons: list[str] = []
        self.since: float | None = None

    def active_reasons(self) -> list[str]:
        out = []
        for s in self.sessions_fn():
            if s["source"] == "dashboard" and s["state"] in ("working", "waiting"):
                out.append(f"{s['name']} {s['state']}")
            elif s["source"] == "terminal" and s["state"] == "working":
                out.append(f"{s['name']} working")
        out += [f"scout {sc['id']}" for sc in db.list_scouts(20) if sc["status"] == "running"]
        if self.briefing.armed:
            out.append("briefing mode")
        return out

    def state(self) -> dict:
        return {"holding": self.proc is not None and self.proc.returncode is None, "reasons": self.reasons,
                "since": self.since, "available": bool(host.inhibit_sleep_cmd(os.getpid()))}

    async def _start(self):
        cmd = host.inhibit_sleep_cmd(os.getpid())       # caffeinate (macOS) / systemd-inhibit (Linux)
        if not cmd:
            return
        self.proc = await asyncio.create_subprocess_exec(*cmd,
                                                         stdout=asyncio.subprocess.DEVNULL,
                                                         stderr=asyncio.subprocess.DEVNULL)
        self.since = time.time()
        log.info("holding sleep assertion (%s)", ", ".join(self.reasons))

    async def _stop(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            await self.proc.wait()
            log.info("released sleep assertion")
        self.proc, self.since = None, None

    async def loop(self):
        try:
            while True:
                reasons = self.active_reasons()
                changed = reasons != self.reasons
                self.reasons = reasons
                holding = self.proc is not None and self.proc.returncode is None
                if reasons and not holding:
                    await self._start()
                    changed = True
                elif not reasons and holding:
                    await self._stop()
                    changed = True
                if changed:
                    await hub.emit("awake", awake=self.state())
                await asyncio.sleep(CHECK_S)
        finally:
            await self._stop()
