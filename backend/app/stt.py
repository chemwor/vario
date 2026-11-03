"""Local speech-to-text. The browser records 16 kHz WAV and POSTs it here; nothing leaves the machine.

Engines (STT_ENGINE):
  auto        mlx-whisper on Apple Silicon when the voice venv is there, else whisper.cpp
  mlx         mlx-whisper in a warm worker process (stt_worker.py) under the voice venv's Python
  whispercpp  whisper.cpp's `whisper-cli`, one process per request; WHISPER_CPP_MODEL = path to a ggml model
If mlx-whisper fails and whisper.cpp is set up, requests fall back to whisper.cpp.
Each request carries a vocabulary prompt (VOCAB from voice.conf, session names, commands, VOICE_NAME, STT_VOCAB) so project
names and short answers come out right. The transcript then goes through /api/voice/utterance exactly
like the dev harness.
"""
import asyncio
import json
import logging
import os
import platform
import shutil
import tempfile
import time
from pathlib import Path

from .config import (DATA_DIR, VARIO_DIR, PROJECTS_DIR, STT_ENGINE, STT_MODEL, STT_PYTHON, STT_VOCAB,
                     WHISPER_CPP_BIN, WHISPER_CPP_MODEL, voice_conf)

log = logging.getLogger("stt")
WORKER = Path(__file__).parent / "stt_worker.py"
TMP_DIR = DATA_DIR / "stt-tmp"
TIMEOUT_S = 60
# Words the router acts on, worth biasing Whisper towards (measured: without them "no" came out as "know")
COMMAND_WORDS = ("yes, no, approve, deny, skip, next, snooze, later, scout, note that, mute, stop briefing, "
                 "start briefing, status, stats, repeat, details")


_known: tuple[float, list[str]] = (0.0, [])


def _project_name(d: Path) -> tuple[float, str] | None:
    """(last used, folder name) for one ~/.claude/projects entry: the newest transcript's cwd, or, with no
    transcripts, the directory name decoded ("-Users-me-Desktop-Kamstrup-Tool" -> "Kamstrup Tool")."""
    files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[:1]:
        with open(f, errors="ignore") as fh:
            for _, line in zip(range(50), fh):
                if '"cwd"' not in line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd:
                    p = Path(cwd)
                    if str(p).startswith(("/private/", "/tmp/", "/var/")) or VARIO_DIR in p.parents:
                        return None                        # scratch dirs and scout runs aren't projects
                    return f.stat().st_mtime, p.name
    home = "-" + str(Path.home()).strip("/").replace("/", "-") + "-"
    if not d.name.startswith(home):
        return None
    rest = d.name[len(home):].split("-", 1)                 # drop the parent folder (Desktop, code, ...)
    if len(rest) < 2 or not rest[1] or rest[0] in (VARIO_DIR.name, "Pallas", "MissionControl"):
        return None                                         # scout runs live under ~/Vario
    return d.stat().st_mtime, rest[1].replace("-", " ").strip()


def known_projects() -> list[str]:
    """Every project Claude Code knows about, most recently used first. Cached for 10 minutes."""
    global _known
    if time.time() - _known[0] < 600:
        return _known[1]
    found = []
    for d in (PROJECTS_DIR.iterdir() if PROJECTS_DIR.is_dir() else []):
        try:
            if d.is_dir() and (hit := _project_name(d)):
                found.append(hit)
        except OSError:
            continue
    names = list(dict.fromkeys(n for _, n in sorted(found, reverse=True)))
    # A parent folder decoded into several names ("Acme Corp Client Repos", "... Side Projects Inc"):
    # keep it once as its own hint and strip it from the rest.
    # One pass, the prefix shared by the most names (longest on a tie).
    counts: dict[str, int] = {}
    for w in (n.split() for n in names):
        for k in (2, 3, 4):
            if len(w) > k:
                counts[" ".join(w[:k])] = counts.get(" ".join(w[:k]), 0) + 1
    best = max(((c, len(p.split()), p) for p, c in counts.items() if c >= 3), default=None)
    if best:
        pre = best[2]
        names = [n[len(pre) + 1:] if n.startswith(pre + " ") else n for n in names] + [pre]
    _known = (time.time(), list(dict.fromkeys(n for n in names if n)))
    return _known[1]


def vocab_prompt(names: list[str]) -> str:
    """Whisper's initial prompt: it spells these far more reliably once it has seen them."""
    vocab = [w.strip() for w in f"{voice_conf('VOCAB')},{STT_VOCAB}".split(",") if w.strip()]
    seen, out = set(), []
    for n in [*vocab, *names, voice_conf("VOICE_NAME", "Athena"), *known_projects()]:
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return (f"Projects: {', '.join(out[:60])}. " if out else "") + f"Answers: {COMMAND_WORDS}."


def _whispercpp_ready() -> bool:
    return bool(WHISPER_CPP_MODEL) and Path(WHISPER_CPP_MODEL).is_file() and bool(shutil.which(WHISPER_CPP_BIN))


def _resolve_engine() -> str:
    if STT_ENGINE in ("mlx", "whispercpp"):
        return STT_ENGINE
    arm = platform.system() == "Darwin" and platform.machine() == "arm64"
    return "mlx" if arm and os.access(STT_PYTHON, os.X_OK) else "whispercpp"


class STT:
    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self.ready = False
        self.error: str | None = None
        self.engine = _resolve_engine()
        self.fallback = False          # mlx-whisper failed; using whisper.cpp

    def active(self) -> str:
        return "whispercpp" if self.fallback else self.engine

    def status(self) -> dict:
        eng = self.active()
        return {"engine": eng, "model": WHISPER_CPP_MODEL if eng == "whispercpp" else STT_MODEL,
                "ready": self.ready if eng == "mlx" else _whispercpp_ready(), "fallback": self.fallback,
                "error": self.error}

    async def _ensure_worker(self):
        if self.proc and self.proc.returncode is None:
            return
        self.ready = False
        if not os.access(STT_PYTHON, os.X_OK):
            raise RuntimeError(f"STT python not found: {STT_PYTHON} (run the voice installer)")
        self.proc = await asyncio.create_subprocess_exec(
            STT_PYTHON, str(WORKER), STT_MODEL, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=600)   # first run downloads the model
        msg = json.loads(line or b"{}")
        if not msg.get("ready"):
            raise RuntimeError("STT worker failed to start (is mlx-whisper installed in the voice venv?)")
        self.ready, self.error = True, None
        log.info("stt ready: mlx-whisper %s (warm-up %s ms)", STT_MODEL, msg.get("ms"))

    def _fall_back(self, why: str) -> bool:
        """mlx-whisper is unusable: switch to whisper.cpp if it's set up."""
        if self.engine == "mlx" and not self.fallback and _whispercpp_ready():
            self.fallback = True
            log.warning("stt: mlx-whisper unavailable (%s); falling back to whisper.cpp", why)
            return True
        return False

    async def warm(self):
        if self.active() != "mlx":
            if not _whispercpp_ready():
                self.error = "whisper.cpp needs WHISPER_CPP_MODEL=/path/to/ggml-model.bin and whisper-cli on PATH"
                log.warning("stt: %s", self.error)
            return
        try:
            async with self.lock:
                await self._ensure_worker()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log.warning("stt warm-up failed: %s", self.error)
            self._fall_back(self.error)

    async def transcribe(self, wav: bytes, prompt: str | None = None, partial: bool = False) -> dict:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(suffix=".wav", dir=TMP_DIR)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav)
            t = time.time()
            conf = None                                    # whisper.cpp: no confidence
            if self.active() == "mlx":
                try:
                    text, conf = await self._mlx(path, prompt)
                except Exception as e:
                    if not self._fall_back(f"{type(e).__name__}: {e}"):
                        raise
                    text = await self._whispercpp(path, prompt)
            else:
                text = await self._whispercpp(path, prompt)
            ms = int((time.time() - t) * 1000)
            (log.debug if partial else log.info)("stt %s%s: %d bytes -> %d chars in %d ms", self.active(),
                                                 " (partial)" if partial else "", len(wav), len(text), ms)
            return {"text": text, "ms": ms, "engine": self.active(), "confidence": conf}
        finally:
            Path(path).unlink(missing_ok=True)            # recordings are never kept

    async def _mlx(self, path: str, prompt: str | None) -> tuple[str, float | None]:
        async with self.lock:
            try:
                await self._ensure_worker()
                self.proc.stdin.write((json.dumps({"path": path, "prompt": prompt}) + "\n").encode())
                await self.proc.stdin.drain()
                line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=TIMEOUT_S)
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                if self.proc and self.proc.returncode is None:
                    self.proc.kill()
                self.proc, self.ready = None, False
                raise
        msg = json.loads(line or b"{}")
        if "error" in msg:
            raise RuntimeError(msg["error"])
        return msg.get("text", ""), msg.get("confidence")

    async def _whispercpp(self, path: str, prompt: str | None) -> str:
        if not _whispercpp_ready():
            raise RuntimeError("whisper.cpp isn't set up: WHISPER_CPP_MODEL=/path/to/ggml-model.bin, whisper-cli on PATH")
        args = [WHISPER_CPP_BIN, "-m", WHISPER_CPP_MODEL, "-f", path, "-l", "en", "-nt", "-np"]
        if prompt:
            args += ["--prompt", prompt]
        p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=TIMEOUT_S)
        if p.returncode != 0:
            raise RuntimeError(f"whisper-cli exited {p.returncode}")
        return " ".join(out.decode().split())

    async def close(self):
        if self.proc and self.proc.returncode is None:
            self.proc.kill()


stt = STT()
