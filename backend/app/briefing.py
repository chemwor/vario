"""Briefing mode: a half-duplex loop that walks the report queue by voice.

  speaking  -> the next report is read out ("Billing, needs decision: approve the contractors migration?")
  listening -> playback has finished; mic window open for LISTEN_S (frontend plays a chime, opens the mic)
  routing   -> an utterance arrived (mic or dev harness, same endpoint); router classifies + executes
  speaking  -> confirmation ("Sent to billing."), report marked answered, next report
Silence for LISTEN_S skips to the next report and leaves it queued. While armed, the briefing loop owns
delivery: the "speak urgent decisions immediately" path and the 3-minute reminders are suspended.
Music is only held down while the loop is busy (speaking, listening, routing); once the queue is empty
(idle) it comes back, and the next report pauses it again.
Auto (pref "briefing_auto", default on): a new report arms briefing by itself while a dashboard is open
(the browser mic is what hears your answer); an auto-armed briefing turns off again when the last
dashboard tab closes, so the normal spoken alerts take over.
"""
import asyncio
import logging
import os
import time

from . import db, voice
from .config import voice_conf
from .hub import hub
from .music import music
from .comms import Plan, comms

log = logging.getLogger("briefing")
LISTEN_S = float(os.environ.get("BRIEFING_LISTEN_S", "15"))
def voice_name() -> str:
    """Who the briefing voice says it is (VOICE_NAME in voice.conf, read on every arm/disarm)."""
    return voice_conf("VOICE_NAME", "Athena")


TYPE_PHRASE = {"decision_needed": "needs decision", "research": "research", "fyi": "update"}


class Briefing:
    def __init__(self, reports, router):
        self.reports, self.router = reports, router
        self.armed = False
        self.phase = "off"              # off | idle | speaking | listening | routing
        self.current: dict | None = None
        self.listen_until: float | None = None
        self.presented: set[int] = set()
        self.utterances: asyncio.Queue = asyncio.Queue()
        self.new_report = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.last_result: dict | None = None
        self.up_next: list[int] = []            # research that just landed: read before anything else
        self.follow_ups: dict[int, int] = {}    # research report id -> decision to bring back right after
        self.music_held = False
        self.auto_armed = False                 # armed by a new report, not by you

    def state(self) -> dict:
        r = self.current
        return {"armed": self.armed, "auto": self.auto, "phase": self.phase, "listen_until": self.listen_until,
                "listen_s": LISTEN_S, "remaining": len(self._remaining()),
                "current": {"id": r["id"], "project": r["project"], "type": r["type"], "summary": r["summary"]} if r else None,
                "last_result": self.last_result}

    def _remaining(self):
        t = time.time()
        return [r for r in db.list_reports(("queued", "delivered"), 500)
                if r["id"] not in self.presented and not (r.get("snoozed_until") and r["snoozed_until"] > t)]

    async def _set(self, phase: str, **kw):
        self.phase = phase
        busy = phase in ("speaking", "listening", "routing")
        if busy and not self.music_held:          # output or input: music down
            self.music_held = True
            await music.hold()
        elif not busy and self.music_held:        # idle / off: music back to its normal setting
            self.music_held = False
            await music.unhold()
        for k, v in kw.items():
            setattr(self, k, v)
        await hub.emit("briefing_state", briefing=self.state())

    # --- arm / disarm -------------------------------------------------------------------
    @property
    def auto(self) -> bool:
        return bool(db.get_pref("briefing_auto", True))

    async def set_auto(self, on: bool):
        db.set_pref("briefing_auto", bool(on))
        await hub.emit("briefing_state", briefing=self.state())
        return self.state()

    async def arm(self, auto: bool = False):
        if self.armed:
            return self.state()
        self.armed, self.reports.briefing = True, True
        self.auto_armed = auto
        self.presented.clear()
        self.up_next.clear()
        self.utterances = asyncio.Queue()
        await self._set("speaking", current=None, listen_until=None)
        n = len(self._remaining())
        queued = f"{n} report{'s' if n != 1 else ''} queued." if n else "Nothing queued."
        await voice.speak_and_wait(f"{voice_name()} online. " + ("New report." if auto else queued))
        self.task = asyncio.create_task(self._loop())
        return self.state()

    async def disarm(self, speak: bool = True):
        if not self.armed:
            return self.state()
        self.armed, self.reports.briefing = False, False
        task, self.task = self.task, None
        if task and task is not asyncio.current_task():
            task.cancel()
        await self._set("off", current=None, listen_until=None)
        if speak:
            await voice.speak_and_wait(f"{voice_name()} standing by.")
        return self.state()

    def report_created(self, r: dict | None = None):
        self.new_report.set()
        if (not self.armed and r and r.get("status") == "queued" and hub.clients and self.auto):
            log.info("report #%s queued: auto-arming briefing", r["id"])
            asyncio.create_task(self.arm(auto=True))

    def research_landed(self, scout: dict, report: dict):
        """Read the findings next, then bring the decision they inform straight back."""
        self.up_next.append(report["id"])
        if scout.get("linked_report_id"):
            self.follow_ups[report["id"]] = scout["linked_report_id"]
        self.new_report.set()

    def _next(self) -> dict | None:
        while self.up_next:
            r = db.get_report(self.up_next.pop(0))
            if r and r["status"] in ("queued", "delivered") and r["id"] not in self.presented:
                return r
        return db.next_briefing_report(self.presented)

    # --- utterances ------------------------------------------------------------------------
    async def utterance(self, text: str, source: str, comms_id: int | None = None) -> dict:
        """Entry point for mic and harness alike. Only accepted while the mic window is open,
        except a bare 'stop briefing', which always works."""
        from .router import LOCAL, _N
        if _N(text) in LOCAL["stop"] - {"stop"} or (_N(text) == "stop" and self.phase != "listening"):
            await self.disarm()
            return {"accepted": True, "action": "stop"}
        if self.phase != "listening":
            return {"accepted": False, "reason": f"not listening (phase={self.phase})"}
        await self.utterances.put((text, source, comms_id))
        return {"accepted": True}

    async def silence(self) -> dict:
        """The mic gave up after its silence timeout: treat it like the listen window running out."""
        if self.phase != "listening":
            return {"accepted": False, "reason": f"not listening (phase={self.phase})"}
        await self.utterances.put((None, "silence", None))
        return {"accepted": True}

    # --- the loop --------------------------------------------------------------------------
    async def _loop(self):
        try:
            announced_empty = True          # arming already said how many are queued
            while self.armed:
                if self.auto_armed and not hub.clients:     # nobody left to answer: back to spoken alerts
                    log.info("dashboard closed: auto-armed briefing off")
                    await self.disarm(speak=False)
                    return
                r = self._next()
                if not r:
                    if not announced_empty:
                        announced_empty = True
                        await self._set("speaking", current=None)
                        await voice.speak_and_wait("That's everything for now. I'll read new reports as they come in."
                                                   if self.presented else "Nothing in the queue.")
                    await self._set("idle", current=None, listen_until=None)
                    self.new_report.clear()
                    try:
                        await asyncio.wait_for(self.new_report.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        pass
                    continue
                announced_empty = False
                self.presented.add(r["id"])
                await self._present(r)
                linked = self.follow_ups.pop(r["id"], None)
                if linked:                       # findings read -> the decision comes back up now
                    self.presented.discard(linked)
                    self.up_next.insert(0, linked)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("briefing loop crashed")
            await self.disarm(speak=False)

    async def _present(self, r: dict):
        await self._set("speaking", current=r, listen_until=None)
        intro = f"{r['project']}, {TYPE_PHRASE.get(r['type'], 'update')}: {r['summary']}"
        await voice.speak_and_wait(intro, session_id=r.get("session_id"))
        db.update_report(r["id"], status="delivered" if r["status"] == "queued" else r["status"],
                         delivered_at=r.get("delivered_at") or time.time())
        for attempt in range(2):                    # a cancelled answer gets one more listen window
            while not self.utterances.empty():      # drop anything said while we were talking
                self.utterances.get_nowait()
            await self._set("listening", listen_until=time.time() + LISTEN_S)
            try:
                text, source, cid = await asyncio.wait_for(self.utterances.get(), timeout=LISTEN_S)
                if text is None:                    # mic stopped on a pause: same as no answer
                    raise asyncio.TimeoutError
            except asyncio.TimeoutError:
                self.last_result = {"report_id": r["id"], "action": "silence", "say": "(no answer, left queued)"}
                log.info("report #%s: silence, left queued", r["id"])
                await self._set("routing", listen_until=None)
                return
            await self._set("routing", listen_until=None)
            plan = await self.router.plan(text, db.get_report(r["id"]))
            target, desc = plan.describe()
            if plan.intent.action == "unintelligible":     # never guessed into an action
                if cid:
                    await comms.update(cid, mode="briefing", action="unintelligible", via=plan.intent.via,
                                       target=target, descr=desc, status="rejected", outcome="rejected: unintelligible")
                await self._set("speaking")
                await voice.speak_and_wait("Sorry, I didn't catch that." + (" Go ahead." if attempt == 0 else ""))
                continue
            held = Plan("briefing", plan.intent.action, target, desc, lambda: None, via=plan.intent.via,
                        spoken=plan.spoken())
            if cid:
                await comms.classified(cid, held)
            if cid and not await comms.hold(cid, held, text):
                await self._set("speaking")
                await voice.speak_and_wait("Cancelled." + (" Go ahead." if attempt == 0 else ""))
                continue
            break
        else:
            return                                   # cancelled twice: leave it queued
        res = await self.router.run(plan, source, comms_id=cid)
        if cid:
            await comms.finish(cid, {"say": res.say, "routed_to": res.routed_to})
        self.last_result = {"report_id": r["id"], "utterance": text, "action": res.action, "say": res.say,
                            "routed_to": res.routed_to, **res.detail}
        await hub.emit("reply_sent", report_id=r["id"], utterance=text, action=res.action, say=res.say,
                       routed_to=res.routed_to, source=source)
        if res.action == "stop":
            await self.disarm()
            return
        await self._set("speaking")
        await voice.speak_and_wait(res.say)
