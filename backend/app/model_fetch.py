"""Download and verify one pinned voice model (see models.json). Runs under the voice venv's python, which has
huggingface_hub; the backend reads its stdout, one JSON object per line:

  {"ev": "start", "total": <bytes>, "files": <n>}
  {"ev": "file", "path": ..., "state": "cached" | "downloading" | "ok" | "bad", "sha256": ...}
  {"ev": "progress", "done": <bytes>, "total": <bytes>}
  {"ev": "done", "ok": true|false, "bad": [paths]}

usage: python model_fetch.py <model json on stdin> [--verify-only]
Files land in the normal Hugging Face cache under the model's HF_HOME, so bubo and the STT worker find them.
"""
import hashlib
import json
import os
import sys
import time

spec = json.load(sys.stdin)
VERIFY_ONLY = "--verify-only" in sys.argv
os.environ["HF_HOME"] = os.path.expanduser(spec["hf_home"])
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from huggingface_hub import hf_hub_download, try_to_load_from_cache  # noqa: E402  (after HF_HOME is set)
from tqdm.auto import tqdm  # noqa: E402


def out(**kw):
    print(json.dumps(kw), flush=True)


files = spec["files"]
total = sum(f["size"] for f in files)
done = 0
last = 0.0


def progress(force=False):
    global last
    now = time.time()
    if force or now - last > 0.25:
        last = now
        out(ev="progress", done=done, total=total)


class Bar(tqdm):
    """hf_hub_download's progress bar, reported as bytes instead of drawn."""
    def __init__(self, *a, **kw):
        kw["disable"] = False
        kw["file"] = open(os.devnull, "w")
        super().__init__(*a, **kw)

    def update(self, n=1):
        global done
        done += n or 0
        progress()
        return super().update(n)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


out(ev="start", total=total, files=len(files))
bad = []
for f in files:
    path = try_to_load_from_cache(spec["repo"], f["path"], revision=spec["revision"])
    if isinstance(path, str) and os.path.exists(path) and not VERIFY_ONLY and sha256(path) != f["sha256"]:
        # a damaged copy: drop it (the blob and its snapshot link) so it's fetched fresh below
        blob = os.path.realpath(path)
        os.chmod(blob, 0o644)
        os.remove(blob)
        os.remove(path)
        path = None
    if isinstance(path, str) and os.path.exists(path):
        out(ev="file", path=f["path"], state="cached")
        done += f["size"]
    elif VERIFY_ONLY:
        out(ev="file", path=f["path"], state="missing")
        bad.append(f["path"])
        continue
    else:
        out(ev="file", path=f["path"], state="downloading")
        before = done
        try:
            path = hf_hub_download(spec["repo"], f["path"], revision=spec["revision"], tqdm_class=Bar)
        except Exception as e:  # network, disk, 404 ...
            out(ev="file", path=f["path"], state="bad", error=f"{type(e).__name__}: {e}"[:300])
            bad.append(f["path"])
            done = before + f["size"]
            continue
        done = before + f["size"]           # small files don't always report through the bar
    got = sha256(path)
    state = "ok" if got == f["sha256"] else "bad"
    if state == "bad":
        bad.append(f["path"])
    out(ev="file", path=f["path"], state=state, sha256=got)
    progress()
progress(force=True)
if not bad:
    # Point the cache's "main" ref at the pinned revision: bubo and the STT worker load by repo id while
    # offline, which resolves "main", and a download by commit hash doesn't record that ref.
    ref = os.path.join(os.environ["HF_HOME"], "hub", "models--" + spec["repo"].replace("/", "--"), "refs", "main")
    os.makedirs(os.path.dirname(ref), exist_ok=True)
    with open(ref, "w") as fh:
        fh.write(spec["revision"])
out(ev="done", ok=not bad, bad=bad)
