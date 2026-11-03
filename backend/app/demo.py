"""`vario demo`: made-up sessions, reports and comms so the UI can be explored (or recorded) without real work.

seed(demo_home) runs `python -m app.demo` in a child process whose VARIO_HOME is the demo folder, so the database
it writes is the demo's own file; the child refuses to run against any other path. The sessions are Claude Code
transcripts written into the demo's own projects folder, which the demo backend watches like real ones.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

MODEL = "claude-sonnet-4-5"


def seed(demo_home: Path):
    demo_home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "VARIO_HOME": str(demo_home), "VARIO_DEMO": "1",
           "CLAUDE_PROJECTS_DIR": str(demo_home / "projects"), "CLAUDE_HOOK_DIR": str(demo_home / "hooks")}
    env.pop("DASHBOARD_DATA_DIR", None)
    subprocess.run([sys.executable, "-m", "app.demo", str(demo_home)], cwd=Path(__file__).resolve().parent.parent,
                   env=env, check=True)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def _transcript(projects: Path, name: str, turns: list[dict], age_s: float) -> str:
    """Write one fake Claude Code session; returns its id."""
    sid = str(uuid.uuid4())
    cwd = f"/demo/{name}"
    folder = projects / ("-demo-" + name)
    folder.mkdir(parents=True, exist_ok=True)
    t = time.time() - age_s
    lines = [{"type": "ai-title", "aiTitle": turns[0].get("title", name), "sessionId": sid}]
    for turn in turns:
        t += 5
        lines.append({"type": "user", "cwd": cwd, "gitBranch": "main", "timestamp": _iso(t), "uuid": str(uuid.uuid4()),
                      "sessionId": sid, "message": {"role": "user", "content": turn["ask"]}})
        for step in turn.get("tools", []):
            t += 8
            tid = "toolu_" + uuid.uuid4().hex[:20]
            lines.append({"type": "assistant", "cwd": cwd, "timestamp": _iso(t), "uuid": str(uuid.uuid4()), "sessionId": sid,
                          "message": {"model": MODEL, "role": "assistant", "stop_reason": "tool_use",
                                      "usage": {"input_tokens": 12, "cache_read_input_tokens": turn.get("ctx", 40_000)},
                                      "content": [{"type": "tool_use", "id": tid, "name": step[0], "input": step[1]}]}})
            if not step[2]:                              # still running: no result yet
                continue
            t += 4
            lines.append({"type": "user", "cwd": cwd, "timestamp": _iso(t), "uuid": str(uuid.uuid4()), "sessionId": sid,
                          "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                                   "content": step[2]}]}})
        if turn.get("error"):
            t += 3
            lines.append({"type": "assistant", "cwd": cwd, "timestamp": _iso(t), "uuid": str(uuid.uuid4()), "sessionId": sid,
                          "isApiErrorMessage": True,
                          "message": {"model": "<synthetic>", "role": "assistant", "content": [{"type": "text", "text": turn["error"]}]}})
        elif turn.get("answer"):
            t += 6
            lines.append({"type": "assistant", "cwd": cwd, "timestamp": _iso(t), "uuid": str(uuid.uuid4()), "sessionId": sid,
                          "message": {"model": MODEL, "role": "assistant", "stop_reason": "end_turn",
                                      "usage": {"input_tokens": 20, "cache_read_input_tokens": turn.get("ctx", 40_000)},
                                      "content": [{"type": "text", "text": turn["answer"]}]}})
    f = folder / f"{sid}.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    os.utime(f, (t, t))
    return sid


def _run(demo_home: Path):
    from . import db
    from .config import DB_PATH
    # the one rule: never the real database
    if not Path(DB_PATH).resolve().is_relative_to(demo_home.resolve()):
        sys.exit(f"refusing to seed {DB_PATH}: it isn't inside the demo folder {demo_home}")
    if db.list_reports(("queued", "delivered", "answered", "skipped"), 1):
        print("demo data already seeded")
        return
    projects = Path(os.environ["CLAUDE_PROJECTS_DIR"])
    api = _transcript(projects, "acme-api", [{
        "title": "Rate limiting for the public API", "ask": "Add per-key rate limiting to the public API and test it.",
        "ctx": 152_000,
        "tools": [("Read", {"file_path": "/demo/acme-api/src/middleware.ts"}, "export function auth(...)"),
                  ("Edit", {"file_path": "/demo/acme-api/src/ratelimit.ts", "old_string": "", "new_string": "..."}, "ok"),
                  ("Bash", {"command": "npm test -- ratelimit", "description": "Run the rate limit tests"}, None)]}], 240)
    billing = _transcript(projects, "billing-service", [{
        "title": "Invoices table migration", "ask": "Create the invoices table and backfill from the orders table.",
        "tools": [("Write", {"file_path": "/demo/billing-service/migrations/0042_invoices.sql", "content": "CREATE TABLE invoices (...);"}, "ok")],
        "answer": "The migration is written. Should I run it against staging now? It locks the orders table for about 20 seconds."}], 900)
    mobile = _transcript(projects, "mobile-app", [{
        "title": "Dark mode toggle", "ask": "Add a dark mode toggle to settings.", "ctx": 61_000,
        "tools": [("Edit", {"file_path": "/demo/mobile-app/src/Settings.tsx", "old_string": "", "new_string": "..."}, "ok"),
                  ("Bash", {"command": "npm run build"}, "Build succeeded")],
        "answer": "Done. The toggle is in Settings > Appearance, it follows the system setting until changed, and the build passes."}], 1500)
    docs = _transcript(projects, "docs-site", [{
        "title": "Search index", "ask": "Rebuild the search index for the docs site.",
        "tools": [("Bash", {"command": "npm run index"}, "indexing 412 pages")],
        "error": "API Error: 529 Overloaded. The API is temporarily overloaded; try again in a moment."}], 3000)

    now = time.time()

    def report(**r):
        created = r.pop("ago", 60)
        row = db.create_report(source=r.pop("source", "session"), triaged=1, **r)
        db.update_report(row["id"], created_at=now - created)
        return row

    report(project="billing-service", type="decision_needed", urgency=4, session_id=billing, dedupe_key=f"final:{billing}:1",
           summary="Migration 0042 is written. Run it against staging now? It locks orders for about 20 seconds.",
           body="The migration is written. Should I run it against staging now?", ago=840)
    report(project="docs-site", type="decision_needed", urgency=4, session_id=docs, dedupe_key=f"error:{docs}:1",
           summary="docs-site has failed the same way 3 times. Retry, or kill it?", repeat=3,
           body="API Error: 529 Overloaded.", err_sig="api error: # overloaded.", ago=2900)
    report(project="mobile-app", type="fyi", urgency=2, session_id=mobile, dedupe_key=f"final:{mobile}:1",
           summary="Dark mode toggle is in Settings, follows the system until changed; the build passes.",
           body="Done.", outcome="ok", ago=1400)
    report(project="acme-api", type="fyi", urgency=2, session_id=api, dedupe_key=f"ctx:{api}:1",
           summary="acme-api is at 76% of its context. Time to wrap up or hand off soon.", ago=200)
    report(project="research", source="scout", type="research", urgency=3, dedupe_key="scout:demo:1",
           summary="Three hosted rate-limit services compared: two price per request, one per key; all support sliding windows.",
           body="(demo findings)", ago=5200)

    for text, conf, status, outcome, action in [
            ("billing-service, run it on staging", 0.91, "dispatched", "Sent to billing-service.", "reply"),
            ("mm the uh", 0.12, "rejected", "rejected: low confidence (12% < 40%)", None),
            ("approve", 0.55, "cancelled", "not confirmed: low confidence needs a click or “confirm”", "approve"),
            ("what's the status", 0.88, "dispatched", "acme-api is running tests. billing-service is waiting on you.", "status"),
            ("thanks", 0.71, "ignored", "not listening (phase=speaking)", None)]:
        db.add_comms(ts=now - 300, source="mic", text=text, confidence=conf, status=status, outcome=outcome, action=action)
        now += 20
    db.log_decision(project="mobile-app", utterance="looks good, ship it", action="approve", routed_to=mobile,
                    result="Approved. Sent to mobile-app.")
    print(f"seeded demo data in {demo_home}")


if __name__ == "__main__":
    _run(Path(sys.argv[1]).resolve())
