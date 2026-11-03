"""Local speech-to-text worker (mlx-whisper). Runs under the voice venv's Python, not the backend's.

Protocol (one JSON object per line):  stdin {"path": "/tmp/x.wav", "prompt": "vocabulary hints" | null}
  ->  stdout {"text": "...", "ms": 123, "confidence": 0.74}
confidence = exp(token-weighted mean log-probability) over the kept segments, 0..1.
The model stays loaded between requests. Audio never leaves the machine.
Standalone on purpose: no imports from the app package.
"""
import json
import math
import sys
import time
import wave

import numpy as np


def load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError("expected 16-bit mono WAV")
        rate = w.getframerate()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if rate != 16000:                                   # whisper wants 16 kHz
        n = int(len(audio) * 16000 / rate)
        audio = np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio).astype(np.float32)
    return audio


def main():
    model = sys.argv[1]
    import mlx_whisper
    t = time.time()
    mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo=model, language="en")  # load + warm
    print(json.dumps({"ready": True, "model": model, "ms": int((time.time() - t) * 1000)}), flush=True)
    for line in sys.stdin:
        try:
            req = json.loads(line)
            t = time.time()
            out = mlx_whisper.transcribe(load_wav(req["path"]), path_or_hf_repo=model, language="en",
                                         condition_on_previous_text=False, initial_prompt=req.get("prompt"))
            segs = [s for s in out.get("segments", []) if s.get("no_speech_prob", 0) < 0.8]
            text = " ".join(s["text"].strip() for s in segs)
            n = sum(len(s.get("tokens", [])) for s in segs)
            conf = math.exp(sum(s["avg_logprob"] * len(s.get("tokens", [])) for s in segs) / n) if n else None
            print(json.dumps({"text": text.strip(), "ms": int((time.time() - t) * 1000),
                              "confidence": round(conf, 3) if conf is not None else None}), flush=True)
        except Exception as e:
            print(json.dumps({"error": f"{type(e).__name__}: {e}"}), flush=True)


if __name__ == "__main__":
    main()
