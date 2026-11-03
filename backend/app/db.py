"""SQLite registry of dashboard-owned sessions + their event log."""
import json
import sqlite3
import threading
import time

from .config import DATA_DIR, DB_PATH

_lock = threading.Lock()
DATA_DIR.mkdir(parents=True, exist_ok=True)
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(
    """
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY,           -- == Claude Code session id
      cwd TEXT NOT NULL,
      name TEXT NOT NULL,            -- spoken/display project name
      state TEXT NOT NULL,
      activity TEXT,
      snippet TEXT,
      muted INTEGER NOT NULL DEFAULT 0,
      created_at REAL NOT NULL,
      updated_at REAL NOT NULL,
      turn_started_at REAL
    );
    CREATE TABLE IF NOT EXISTS events (
      session_id TEXT NOT NULL,
      seq INTEGER NOT NULL,
      body TEXT NOT NULL,
      PRIMARY KEY (session_id, seq)
    );
    CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT);
    """
)
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS reports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source TEXT NOT NULL CHECK (source IN ('session','scout','app_drop','system')),
      project TEXT NOT NULL,
      type TEXT NOT NULL CHECK (type IN ('decision_needed','research','fyi')),
      urgency INTEGER NOT NULL CHECK (urgency BETWEEN 1 AND 5),
      summary TEXT NOT NULL,              -- one spoken sentence
      body TEXT NOT NULL DEFAULT '',
      created_at REAL NOT NULL,
      status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','delivered','answered','skipped')),
      decision_text TEXT,
      decision_routed_to TEXT,
      -- bookkeeping beyond the core schema
      session_id TEXT,                    -- originating Claude session, if any
      dedupe_key TEXT UNIQUE,             -- e.g. block:<tool_use_id>; one report per event
      triaged INTEGER NOT NULL DEFAULT 0, -- 1 once haiku (or the fallback) has classified it
      delivered_at REAL,
      reminders INTEGER NOT NULL DEFAULT 0,
      answered_at REAL
    );
    CREATE INDEX IF NOT EXISTS reports_status ON reports(status);
    """
)
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS decision_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts REAL NOT NULL,
      report_id INTEGER,
      project TEXT,
      utterance TEXT NOT NULL,       -- what I said
      action TEXT NOT NULL,          -- approve/deny/reply/spawn/note/later/skip/...
      routed_to TEXT,                -- session id, 'decision_log', 'tmux:%3', ...
      result TEXT                    -- what happened ("sent", "terminal unreachable", ...)
    );
    """
)
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS scouts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      brief TEXT NOT NULL,
      project TEXT NOT NULL,
      linked_report_id INTEGER,          -- decision this research informs (its urgency is bumped on landing)
      status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','done','failed')),
      created_at REAL NOT NULL,
      finished_at REAL,
      out_path TEXT,
      report_id INTEGER,                 -- the research report it produced
      cost_usd REAL,
      error TEXT
    );
    """
)
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS comms (            -- every utterance and what came of it (Comms pane)
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts REAL NOT NULL,
      source TEXT NOT NULL,          -- mic | ptt | harness | rerun | edit | correction
      text TEXT NOT NULL,            -- what was acted on
      heard TEXT,                    -- the original transcript, when this is a correction of it
      confidence REAL,               -- whisper's, 0..1 (null: typed, or an engine without one)
      stt_ms INTEGER,
      selected_session_id TEXT,
      mode TEXT,                     -- briefing | command | reply
      action TEXT,                   -- approve | deny | reply | switch | cancel | correct | ...
      via TEXT,                      -- local | haiku | fallback | command
      target TEXT,                   -- who it goes to (display name)
      descr TEXT,                    -- the confirm card line: send "yes, run it" -> billing
      status TEXT NOT NULL DEFAULT 'received',  -- received | held | dispatched | cancelled | ignored | failed
      outcome TEXT,                  -- what was said back / why it stopped
      routed_to TEXT,
      parent_id INTEGER,             -- the utterance this re-runs or corrects
      corrected_by INTEGER
    );
    CREATE INDEX IF NOT EXISTS comms_ts ON comms(ts);
    CREATE TABLE IF NOT EXISTS mishears (         -- words you corrected, for VOCAB suggestions
      word TEXT PRIMARY KEY,         -- lower-case
      display TEXT NOT NULL,         -- as you wrote it
      count INTEGER NOT NULL DEFAULT 0,
      heard_as TEXT,                 -- the most recent mishear
      updated REAL NOT NULL,
      dismissed INTEGER NOT NULL DEFAULT 0
    );
    """
)
for col, decl in (("comms_id", "INTEGER"), ("heard", "TEXT")):
    if col not in {r[1] for r in _conn.execute("PRAGMA table_info(decision_log)")}:
        _conn.execute(f"ALTER TABLE decision_log ADD COLUMN {col} {decl}")
if "snoozed_until" not in {r[1] for r in _conn.execute("PRAGMA table_info(reports)")}:
    _conn.execute("ALTER TABLE reports ADD COLUMN snoozed_until REAL")
_rcols = {r[1] for r in _conn.execute("PRAGMA table_info(reports)")}
for col, decl in (("repeat", "INTEGER NOT NULL DEFAULT 1"),   # identical errors collapsed into this one
                  ("last_seen", "REAL"),                        # latest occurrence (dedupe window)
                  ("err_sig", "TEXT"),                          # normalized error text, for matching repeats
                  ("outcome", "TEXT"),                          # end of turn: ok | failed | partial (triage)
                  ("outcome_line", "TEXT")):                    # what failed, when it wasn't ok
    if col not in _rcols:
        _conn.execute(f"ALTER TABLE reports ADD COLUMN {col} {decl}")
_scols = {r[1] for r in _conn.execute("PRAGMA table_info(scouts)")}
for col, decl in (("kind", "TEXT"),             # research playbook: competition | market | features | users | ...
                  ("session_id", "TEXT"),       # the session whose product it researches (report attaches to it)
                  ("library_path", "TEXT")):    # the finished report, kept under ~/Vario/research/<product>/
    if col not in _scols:
        _conn.execute(f"ALTER TABLE scouts ADD COLUMN {col} {decl}")
if "narrate" not in {r[1] for r in _conn.execute("PRAGMA table_info(sessions)")}:
    _conn.execute("ALTER TABLE sessions ADD COLUMN narrate INTEGER NOT NULL DEFAULT 0")


def upsert_session(s: dict):
    cols = ["id", "cwd", "name", "state", "activity", "snippet", "muted", "narrate", "created_at", "updated_at",
            "turn_started_at"]
    with _lock, _conn:
        _conn.execute(
            f"INSERT INTO sessions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
            f"ON CONFLICT(id) DO UPDATE SET {','.join(f'{c}=excluded.{c}' for c in cols[1:])}",
            [s.get(c) for c in cols],
        )


def all_sessions() -> list[dict]:
    with _lock:
        return [dict(r) for r in _conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC")]


def delete_session(sid: str):
    with _lock, _conn:
        _conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        _conn.execute("DELETE FROM events WHERE session_id=?", (sid,))


def append_event(sid: str, event: dict) -> int:
    with _lock, _conn:
        seq = _conn.execute("SELECT COALESCE(MAX(seq), -1) + 1 FROM events WHERE session_id=?", (sid,)).fetchone()[0]
        _conn.execute("INSERT INTO events VALUES (?,?,?)", (sid, seq, json.dumps(event)))
        return seq


def events(sid: str, limit: int = 500) -> list[dict]:
    with _lock:
        rows = _conn.execute(
            "SELECT body FROM (SELECT seq, body FROM events WHERE session_id=? ORDER BY seq DESC LIMIT ?) ORDER BY seq",
            (sid, limit),
        )
        return [json.loads(r[0]) for r in rows]


def get_pref(key: str, default=None):
    with _lock:
        r = _conn.execute("SELECT value FROM prefs WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_pref(key: str, value):
    with _lock, _conn:
        _conn.execute("INSERT OR REPLACE INTO prefs VALUES (?,?)", (key, json.dumps(value)))


def now() -> float:
    return time.time()


# --- reports ---------------------------------------------------------------------
REPORT_ORDER = ("CASE type WHEN 'decision_needed' THEN 0 ELSE 1 END, urgency DESC, created_at ASC")


def create_report(**r) -> dict | None:
    """Insert a report; returns None if dedupe_key already exists."""
    r.setdefault("created_at", now())
    cols = list(r)
    with _lock, _conn:
        try:
            cur = _conn.execute(f"INSERT INTO reports ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                [r[c] for c in cols])
        except sqlite3.IntegrityError:
            return None
        return dict(_conn.execute("SELECT * FROM reports WHERE id=?", (cur.lastrowid,)).fetchone())


def update_report(rid: int, **fields) -> dict | None:
    if fields:
        with _lock, _conn:
            _conn.execute(f"UPDATE reports SET {','.join(f'{k}=?' for k in fields)} WHERE id=?", [*fields.values(), rid])
    return get_report(rid)


def find_error(session_id: str, sig: str, since: float) -> dict | None:
    """The report a repeat of this error collapses into: same session and signature, seen since `since`."""
    with _lock:
        r = _conn.execute("SELECT * FROM reports WHERE session_id=? AND err_sig=? AND COALESCE(last_seen, created_at)>=? "
                          "AND COALESCE(decision_text,'') NOT LIKE 'collapsed into #%' ORDER BY id DESC LIMIT 1",
                          (session_id, sig, since)).fetchone()
    return dict(r) if r else None


def error_reports(session_id: str | None = None) -> list[dict]:
    """Session error reports (keys error:/fail:), oldest first."""
    q = "SELECT * FROM reports WHERE (dedupe_key LIKE 'error:%' OR dedupe_key LIKE 'fail:%')"
    args: tuple = ()
    if session_id:
        q, args = q + " AND session_id=?", (session_id,)
    with _lock:
        return [dict(r) for r in _conn.execute(q + " ORDER BY id", args)]


def last_outcome(session_id: str) -> dict | None:
    with _lock:
        r = _conn.execute("SELECT outcome, outcome_line FROM reports WHERE session_id=? AND outcome IS NOT NULL "
                          "ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
    return {"label": r[0], "line": r[1]} if r else None


def get_report(rid: int) -> dict | None:
    with _lock:
        r = _conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    return dict(r) if r else None


def find_report(dedupe_key: str) -> dict | None:
    with _lock:
        r = _conn.execute("SELECT * FROM reports WHERE dedupe_key=?", (dedupe_key,)).fetchone()
    return dict(r) if r else None


def open_reports(session_id: str | None = None, type_: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM reports WHERE status IN ('queued','delivered')", []
    if session_id:
        q, args = q + " AND session_id=?", args + [session_id]
    if type_:
        q, args = q + " AND type=?", args + [type_]
    with _lock:
        return [dict(r) for r in _conn.execute(q + f" ORDER BY {REPORT_ORDER}", args)]


def list_reports(statuses=("queued", "delivered"), limit=200) -> list[dict]:
    marks = ",".join("?" * len(statuses))
    with _lock:
        return [dict(r) for r in _conn.execute(
            f"SELECT * FROM reports WHERE status IN ({marks}) ORDER BY {REPORT_ORDER} LIMIT ?", [*statuses, limit])]


def log_decision(**d) -> int:
    d.setdefault("ts", now())
    cols = list(d)
    with _lock, _conn:
        return _conn.execute(f"INSERT INTO decision_log ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                             [d[c] for c in cols]).lastrowid


def recent_projects(limit=40) -> list[str]:
    """Project names seen in reports, most recent first (vocabulary hints for speech-to-text)."""
    with _lock:
        rows = _conn.execute("SELECT project FROM reports WHERE project IS NOT NULL AND project != '' "
                             "GROUP BY project ORDER BY MAX(created_at) DESC LIMIT ?", (limit,)).fetchall()
    return [r[0] for r in rows]


def decision_log(limit=200) -> list[dict]:
    with _lock:
        return [dict(r) for r in _conn.execute("SELECT * FROM decision_log ORDER BY ts DESC LIMIT ?", (limit,))]


def next_briefing_report(exclude: set[int]) -> dict | None:
    """Next open report in queue order, skipping snoozed ones and ones already presented this pass."""
    t = now()
    with _lock:
        for r in _conn.execute(f"SELECT * FROM reports WHERE status IN ('queued','delivered') "
                               f"AND (snoozed_until IS NULL OR snoozed_until <= ?) ORDER BY {REPORT_ORDER}", (t,)):
            if r["id"] not in exclude:
                return dict(r)
    return None


def create_scout(**d) -> dict:
    d.setdefault("created_at", now())
    cols = list(d)
    with _lock, _conn:
        rid = _conn.execute(f"INSERT INTO scouts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [d[c] for c in cols]).lastrowid
    return get_scout(rid)


def update_scout(sid: int, **fields) -> dict:
    with _lock, _conn:
        _conn.execute(f"UPDATE scouts SET {','.join(f'{k}=?' for k in fields)} WHERE id=?", [*fields.values(), sid])
    return get_scout(sid)


def get_scout(sid: int) -> dict | None:
    with _lock:
        r = _conn.execute("SELECT * FROM scouts WHERE id=?", (sid,)).fetchone()
    return dict(r) if r else None


def list_scouts(limit=50) -> list[dict]:
    with _lock:
        return [dict(r) for r in _conn.execute("SELECT * FROM scouts ORDER BY created_at DESC LIMIT ?", (limit,))]


def decision_record(since: float, project: str | None = None) -> list[dict]:
    """The daily record: every voice decision plus every answered/skipped report, newest first."""
    with _lock:
        log = [dict(r) for r in _conn.execute(
            "SELECT d.*, r.summary AS report_summary, r.type AS report_type FROM decision_log d "
            "LEFT JOIN reports r ON r.id = d.report_id WHERE d.ts >= ? ORDER BY d.ts DESC", (since,))]
        closed = [dict(r) for r in _conn.execute(
            "SELECT * FROM reports WHERE status IN ('answered','skipped') AND COALESCE(answered_at, created_at) >= ? "
            "ORDER BY COALESCE(answered_at, created_at) DESC", (since,))]
    voiced = {d["report_id"] for d in log if d["report_id"]}
    out = [{"ts": d["ts"], "project": d["project"], "report_id": d["report_id"], "report_summary": d["report_summary"],
            "report_type": d["report_type"], "said": d["utterance"], "action": d["action"],
            "routed_to": d["routed_to"], "result": d["result"], "via": "voice", "heard": d.get("heard"),
            "comms_id": d.get("comms_id")} for d in log]
    for r in closed:
        if r["id"] in voiced:
            continue
        out.append({"ts": r.get("answered_at") or r["created_at"], "project": r["project"], "report_id": r["id"],
                    "report_summary": r["summary"], "report_type": r["type"], "said": r["decision_text"],
                    "action": r["status"], "routed_to": r["decision_routed_to"], "result": None,
                    "via": "terminal" if (r["decision_text"] or "").startswith("answered in terminal") else
                           "session" if (r["decision_text"] or "").startswith(("continued", "allowed", "denied", "new turn"))
                           else "dashboard"})
    out.sort(key=lambda x: -x["ts"])
    return [x for x in out if not project or (x["project"] or "").lower() == project.lower()]


# --- comms -------------------------------------------------------------------------------------------
def add_comms(**d) -> dict:
    d.setdefault("ts", now())
    cols = list(d)
    with _lock, _conn:
        cid = _conn.execute(f"INSERT INTO comms ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [d[c] for c in cols]).lastrowid
    return get_comms(cid)


def update_comms(cid: int, **fields) -> dict | None:
    if fields:
        with _lock, _conn:
            _conn.execute(f"UPDATE comms SET {','.join(f'{k}=?' for k in fields)} WHERE id=?", [*fields.values(), cid])
    return get_comms(cid)


def purge_comms(days: float) -> int:
    """Drop ignored utterances older than `days` (the rest stay: they're the record of what was acted on)."""
    with _lock, _conn:
        return _conn.execute("DELETE FROM comms WHERE status='ignored' AND ts < ?", (now() - days * 86400,)).rowcount


def get_comms(cid: int) -> dict | None:
    with _lock:
        r = _conn.execute("SELECT * FROM comms WHERE id=?", (cid,)).fetchone()
    return dict(r) if r else None


def list_comms(limit=200) -> list[dict]:
    with _lock:
        return [dict(r) for r in _conn.execute("SELECT * FROM comms ORDER BY id DESC LIMIT ?", (limit,))]


def last_comms(exclude: int | None = None) -> dict | None:
    """The most recent utterance that did something (not a cancel or a correction itself)."""
    with _lock:
        r = _conn.execute("SELECT * FROM comms WHERE id != ? AND COALESCE(action,'') NOT IN ('cancel','correct') "
                          "ORDER BY id DESC LIMIT 1", (exclude or -1,)).fetchone()
    return dict(r) if r else None


def count_mishear(word: str, display: str, heard_as: str):
    with _lock, _conn:
        _conn.execute("INSERT INTO mishears (word, display, count, heard_as, updated) VALUES (?,?,1,?,?) "
                      "ON CONFLICT(word) DO UPDATE SET count=count+1, display=excluded.display, "
                      "heard_as=excluded.heard_as, updated=excluded.updated", (word, display, heard_as, now()))


def mishears(min_count=1) -> list[dict]:
    with _lock:
        return [dict(r) for r in _conn.execute("SELECT * FROM mishears WHERE count >= ? AND dismissed = 0 "
                                               "ORDER BY count DESC, updated DESC", (min_count,))]


def dismiss_mishear(word: str):
    with _lock, _conn:
        _conn.execute("UPDATE mishears SET dismissed=1 WHERE word=?", (word.lower(),))

