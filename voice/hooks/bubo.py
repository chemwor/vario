#!/usr/bin/env python3
"""Bubo: the warm Kokoro TTS daemon for Claude Code hooks and Vario.

Loads the model once and listens on a unix socket: BUBO_SOCKET in voice.conf (default tts.sock,
relative to the hook dir; an absolute path works too).
Protocol: one JSON object per connection, newline-terminated:
  {"cmd": "speak",  "text": "..."}   sentence-pipelined speech, preempts current audio
  {"cmd": "notify", "text": "..."}   cached phrase, preempts current audio
  {"cmd": "stop"}                    stop playback now
  {"cmd": "ping"}                    -> "pong"
  {"cmd": "status"}                  -> {"busy": bool} (synthesizing, queued, or playing audio)
Synthesis runs on the main thread (MLX is happiest there); playback runs on a
worker thread, so sentence N+1 is generated while sentence N plays.
"""
import fcntl, json, os, queue, re, shutil, signal, socket, subprocess, sys, tempfile, threading, time, wave, warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
VOICE_HOME = os.path.expanduser("~/.local/share/claude-voice")
os.environ.setdefault("HF_HOME", os.path.join(VOICE_HOME, "hf"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

HOOK_DIR = os.environ.get("CLAUDE_HOOK_DIR", os.path.expanduser("~/.claude/hooks"))
os.chdir(HOOK_DIR)  # relative socket path sidesteps the 104-char sun_path limit
LOCK, PIDF, MUTE, CONF, CACHE = "bubo.lock", "bubo.pid", ".muted", "voice.conf", "cache"
MODEL_ID = os.environ.get("KOKORO_MODEL", "mlx-community/Kokoro-82M-bf16")
PLAYER = ["afplay"] if sys.platform == "darwin" else ["paplay"] if shutil.which("paplay") else ["aplay", "-q"]


def log(msg):
    print(f"{time.time():.3f} {msg}", flush=True)


# --- single instance -------------------------------------------------------
lock_fd = open(LOCK, "w")
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(0)  # another daemon owns it; exit 0 so launchd doesn't respawn us
with open(PIDF, "w") as f:
    f.write(str(os.getpid()))

import numpy as np
from mlx_audio.tts.utils import load_model

# --- shared state ----------------------------------------------------------
state = threading.Lock()
epoch = 0                  # bumped by every preempting request
current_proc = None        # running player process
pending = None             # latest (epoch, cmd, text) awaiting synthesis
pending_cv = threading.Condition(state)
play_q = queue.Queue()     # (epoch, wav_path, is_temp, label)
synthesizing = False       # main thread is generating audio for the current request


def muted():
    return os.path.exists(MUTE)


def conf_all():
    conf = {"VOICE": "af_heart", "SPEED": "1.1"}
    try:
        for line in open(CONF):
            m = re.match(r"\s*([A-Z_]+)\s*=\s*([^\s#]+)", line)
            if m:
                conf[m.group(1)] = m.group(2).strip("'\"")
    except OSError:
        pass
    return conf


def read_conf():
    conf = conf_all()
    try:
        speed = float(conf["SPEED"])
    except ValueError:
        speed = 1.1
    return conf["VOICE"], speed


# Socket path from voice.conf, read once at startup (restart bubo after changing it).
SOCK = os.path.expanduser(conf_all().get("BUBO_SOCKET", "tts.sock"))


def split_chunks(text):
    """Sentences, with a long first sentence split at a clause so audio starts sooner."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if sents and len(sents[0]) > 50:
        m = re.search(r"^(.{12,}?[,;:])\s+(.+)$", sents[0])
        if m:
            sents[0:1] = [m.group(1), m.group(2)]
    return sents


def drain_play_q():
    while True:
        try:
            _, path, is_temp, _ = play_q.get_nowait()
        except queue.Empty:
            return
        if is_temp:
            _unlink(path)


def _unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def stop_audio_locked():
    global current_proc
    if current_proc and current_proc.poll() is None:
        current_proc.kill()
    current_proc = None
    drain_play_q()


def preempt(cmd, text):
    """Called from socket threads: cancel everything and schedule new speech."""
    global epoch, pending
    with state:
        epoch += 1
        stop_audio_locked()
        pending = (epoch, cmd, text, time.time()) if cmd in ("speak", "notify") else None
        pending_cv.notify()
        return epoch


# --- synthesis -------------------------------------------------------------
model = None


def synth(text, voice, speed):
    lang = "b" if voice[:1] == "b" else "a"
    chunks = [np.asarray(r.audio) for r in model.generate(text=text, voice=voice, speed=speed, lang_code=lang)]
    return np.concatenate(chunks) if chunks else None


def write_wav(path, audio):
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(getattr(model, "sample_rate", 24000))
        w.writeframes(pcm.tobytes())


def say_fallback(text):
    cmd = (["say", "-v", "Samantha", "-r", "220", "--", text] if sys.platform == "darwin"
           else ["espeak-ng", "-s", "175", "--", text] if shutil.which("espeak-ng") else None)
    if cmd:
        with state:
            global current_proc
            current_proc = subprocess.Popen(cmd)


def is_current(ep):
    with state:
        return ep == epoch


def handle(ep, cmd, text, t_recv):
    if muted():
        return
    voice, speed = read_conf()
    if cmd == "notify":
        os.makedirs(CACHE, exist_ok=True)
        path = os.path.join(CACHE, f"notify-{voice}-{speed:g}.wav")
        if not os.path.exists(path):
            audio = synth(text, voice, speed)
            if audio is None:
                raise RuntimeError("empty audio")
            write_wav(path + ".tmp", audio)
            os.replace(path + ".tmp", path)
        play_q.put((ep, path, False, f"ep={ep} chunk=0 recv={t_recv:.3f}"))
        return
    for i, chunk in enumerate(split_chunks(text)):
        if not is_current(ep):
            return
        t0 = time.time()
        audio = synth(chunk, voice, speed)
        log(f"synth ep={ep} chunk={i} chars={len(chunk)} ms={(time.time() - t0) * 1000:.0f} queued_ms={(t0 - t_recv) * 1000:.0f}")
        if audio is None or not is_current(ep):
            continue
        fd, path = tempfile.mkstemp(prefix="claude-tts-", suffix=".wav")
        os.close(fd)
        write_wav(path, audio)
        play_q.put((ep, path, True, f"ep={ep} chunk={i} recv={t_recv:.3f}"))


def player_loop():
    global current_proc
    while True:
        ep, path, is_temp, label = play_q.get()
        with state:
            if ep != epoch or muted():
                proc = None
            else:
                proc = current_proc = subprocess.Popen(PLAYER + [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                log(f"play_start {label}")
        if proc:
            proc.wait()
        if is_temp:
            _unlink(path)


# --- socket server ---------------------------------------------------------
def client(conn):
    with conn:
        conn.settimeout(2)
        buf = b""
        try:
            while b"\n" not in buf and len(buf) < 256 * 1024:
                part = conn.recv(65536)
                if not part:
                    break
                buf += part
            req = json.loads(buf.decode("utf-8", "replace").strip() or "{}")
        except (OSError, ValueError):
            conn.sendall(b"err\n")
            return
        cmd, text = req.get("cmd", "speak"), (req.get("text") or "").strip()
        if cmd == "ping":
            conn.sendall(b"pong\n")
            return
        if cmd == "status":
            with state:
                busy = (synthesizing or pending is not None or not play_q.empty()
                        or (current_proc is not None and current_proc.poll() is None))
            conn.sendall((json.dumps({"busy": busy}) + "\n").encode())
            return
        if cmd in ("speak", "notify") and (not text or muted()):
            conn.sendall(b"ok\n")
            return
        if cmd not in ("speak", "notify", "stop"):
            conn.sendall(b"err\n")
            return
        ep = preempt(cmd, text)
        log(f"recv {cmd} ep={ep} len={len(text)}")
        conn.sendall(b"ok\n")


def serve():
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    _unlink(SOCK)
    try:
        srv.bind(SOCK)
    except OSError as e:   # e.g. an absolute BUBO_SOCKET over the 104-byte sun_path limit
        log(f"cannot listen on {SOCK}: {e} (BUBO_SOCKET in voice.conf)")
        os._exit(1)
    os.chmod(SOCK, 0o600)
    srv.listen(16)
    log(f"bubo listening on {SOCK}")
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=client, args=(conn,), daemon=True).start()


def shutdown(*_):
    with state:
        stop_audio_locked()
    _unlink(SOCK)
    os._exit(1)


def main():
    global model, pending
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    t = time.time()
    model = load_model(MODEL_ID)
    voice, speed = read_conf()
    synth("Ready.", voice, speed)  # warm up graph, G2P and voice embedding
    log(f"model warm in {time.time() - t:.1f}s")
    threading.Thread(target=player_loop, daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    while True:
        with state:
            while pending is None:
                pending_cv.wait()
            req, pending = pending, None
        ep, cmd, text, t_recv = req
        global synthesizing
        synthesizing = True
        try:
            handle(ep, cmd, text, t_recv)
        except Exception as e:  # never die on a bad request; still say something
            log(f"synth error: {e!r}")
            if is_current(ep) and not muted():
                say_fallback(text)
        finally:
            synthesizing = False


if __name__ == "__main__":
    main()
