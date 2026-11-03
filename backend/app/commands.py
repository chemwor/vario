"""Voice/command-bar commands. Add one by decorating an async function with @command(patterns...).

Each handler gets (ctx, match) and returns a dict: {"say": str?, "action": str?, "session_id": str?}.
`say` is spoken by bubo and shown in the UI; `action` is handled by the frontend
("select" focuses a session).
"""
import difflib
import re
import time

from . import voice

COMMANDS: list[tuple[list[re.Pattern], callable, str]] = []


def command(*patterns, help=""):
    def deco(fn):
        COMMANDS.append(([re.compile(p, re.I) for p in patterns], fn, help or fn.__doc__ or ""))
        return fn
    return deco


def _norm(s: str) -> str:
    # "Smith & Co" == "Smith and Co" == "Smith---Co" (Whisper writes "&", people say "and")
    return re.sub(r"[^a-z0-9]", "", re.sub(r"&|\band\b", "", s.lower()))


def find_session(ctx, spoken: str):
    """Fuzzy-match a spoken project name to a session (dashboard sessions first, then most recent)."""
    want = _norm(re.sub(r"^(the )|( (session|project|one))$", "", spoken.strip(), flags=re.I))
    if not want:
        return None
    ranked = sorted(ctx.all_sessions(), key=lambda s: (s["source"] != "dashboard", -s["updated_at"]))
    names = [(_norm(s["name"]), s) for s in ranked]
    for test in (lambda n: n == want, lambda n: n.startswith(want), lambda n: want in n):
        for n, s in names:
            if n and test(n):
                return s
    close = difflib.get_close_matches(want, [n for n, _ in names], n=1, cutoff=0.6)
    return next((s for n, s in names if close and n == close[0]), None)


STATE_PHRASE = {"working": "is {activity}", "waiting": "is waiting on you", "done": "is done", "error": "hit an error"}


def status_line(s) -> str:
    phrase = STATE_PHRASE.get(s["state"], s["state"]).format(activity=s.get("activity") or "working")
    return f"{s['name']} {phrase}."


@command(r"^(?:what'?s|what is) (?:the )?status\??$", r"^status( report| update)?$", r"^what'?s (?:happening|going on)\??$",
         r"^how(?:'s| is) (?:it|everything) going\??$", help="what's the status - one line per active session")
async def status(ctx, m):
    recent = time.time() - 1800
    active = [s for s in ctx.all_sessions() if s["state"] in ("working", "waiting") or s["updated_at"] > recent]
    active.sort(key=lambda s: ({"waiting": 0, "error": 1, "working": 2}.get(s["state"], 3), -s["updated_at"]))
    if not active:
        return {"say": "No active sessions."}
    return {"say": " ".join(status_line(s) for s in active[:6])}


@command(r"^(?:switch|go|change|jump) to (?P<name>.+)$", r"^(?:open|show|select) (?P<name>.+)$",
         help="switch to <project> - focus that session")
async def switch(ctx, m):
    s = find_session(ctx, m["name"])
    if not s:
        return {"say": f"I couldn't find {m['name']}."}
    return {"action": "select", "session_id": s["id"], "say": f"Switched to {s['name']}."}


@command(r"^(?P<verb>mute|unmute|silence) (?P<name>.+)$", help="mute/unmute <project|all>")
async def mute(ctx, m):
    on = m["verb"].lower() != "unmute"
    if _norm(m["name"]) in ("all", "everything", "everyone", "allsessions", "global"):
        voice.set_global_mute(on)
        await ctx.voice_changed()
        return {"say": None if on else "Voice unmuted."}
    s = find_session(ctx, m["name"])
    if not s:
        return {"say": f"I couldn't find {m['name']}."}
    await ctx.set_muted(s["id"], on)
    return {"say": f"{s['name']} {'muted' if on else 'unmuted'}."}


@command(r"^(?:stop|interrupt|cancel|halt) (?P<name>(?!talking$|speaking$).+)$", help="stop <project> - interrupt its turn")
async def interrupt(ctx, m):
    s = find_session(ctx, m["name"])
    if not s or s["source"] != "dashboard":
        return {"say": f"I can't stop {m['name']}."}
    await ctx.interrupt(s["id"])
    return {"say": f"Stopping {s['name']}."}


@command(r"^(?:retry|try again|rerun|re-run) (?P<name>.+)$", help="retry <project> - send its retry message")
async def retry(ctx, m):
    s = find_session(ctx, m["name"])
    if not s:
        return {"say": f"I couldn't find {m['name']}."}
    res = await ctx.retry(s["id"])
    return {"say": res["say"], "session_id": s["id"] if res["ok"] else None}


@command(r"^(?P<kind>[\w -]+?) research (?:on|for|into|about) (?P<name>.+)$",
         r"^research (?:the )?(?P<kind>competition|competitors?|market|users?|end users?|reddit|features?) (?:on|for|of) (?P<name>.+)$",
         help="<competition|market|features|user> research for <project> - a research playbook on that product")
async def research(ctx, m):
    from . import research as rs
    kind = rs.kind_for(m["kind"])
    if not kind:
        return {"say": f"I don't have a {m['kind']} research playbook. I have: "
                       + ", ".join(p["label"] for p in rs.playbooks().values()) + "."}
    s = find_session(ctx, m["name"])
    if not s:
        return {"say": f"I couldn't find {m['name']}."}
    try:
        await ctx.research(kind, s["id"])
    except Exception as e:                      # scouts off, playbook error
        return {"say": str(getattr(e, "detail", e))}
    return {"say": f"Starting {rs.playbooks()[kind]['label'].lower()} for {s['name']}. It'll land in your inbox.",
            "session_id": s["id"]}


@command(r"^(?:stop|quiet|be quiet|shut up|silence|stop talking|stop speaking)$", help="stop - cut off current speech")
async def hush(ctx, m):
    await voice.stop()
    return {}


@command(r"^(?:help|what can i say)\??$", help="help - list commands")
async def help_(ctx, m):
    return {"say": "You can say: " + "; ".join(h.split(" - ")[0] for _, _, h in COMMANDS if h) + "."}


def match(ctx, text: str):
    """(handler, match, target name) for a command, without running it; None if it isn't one."""
    t = re.sub(r"[.!?,]+$", "", text.strip())
    for patterns, fn, _ in COMMANDS:
        for p in patterns:
            m = p.match(t)
            if m:
                name = m.groupdict().get("name")
                s = find_session(ctx, name) if name else None
                return fn, m, (s["name"] if s else name)
    return None


async def call(ctx, hit) -> dict:
    fn, m, _ = hit
    res = await fn(ctx, m) or {}
    res.setdefault("command", fn.__name__)
    return res


async def run(ctx, text: str) -> dict:
    hit = match(ctx, text)
    if hit:
        return await call(ctx, hit)
    return {"command": None, "say": f"Sorry, I don't know the command: {re.sub(r'[.!?,]+$', '', text.strip())}."}
