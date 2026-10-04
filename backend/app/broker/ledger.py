"""The paper account's ledger, in SQLite: every execution with its fills, the
states it passed through, and each user's auto-execution policy."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "ledger" / "ledger.sqlite"
DEFAULT_POLICY = {"enabled": False, "min_downside": 0.08, "max_put_cost": 0.02}

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, alert_id TEXT NOT NULL, title TEXT NOT NULL,
    action TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL, broker TEXT NOT NULL,
    created_at TEXT NOT NULL, detail TEXT, verification TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT, execution_id TEXT NOT NULL REFERENCES executions(id), body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, execution_id TEXT NOT NULL REFERENCES executions(id),
    state TEXT NOT NULL, at TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS policies (
    user_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, min_downside REAL NOT NULL, max_put_cost REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS executions_by_user ON executions(user_id, created_at);
"""


def _connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_FILE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find(execution_id: str) -> dict | None:
    with _lock, _connect() as db:
        row = db.execute("SELECT * FROM executions WHERE id = ?", (execution_id,)).fetchone()
        return _full(db, row) if row else None


def _full(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    fills = db.execute("SELECT body FROM fills WHERE execution_id = ? ORDER BY id", (row["id"],)).fetchall()
    events = db.execute("SELECT state, at, detail FROM events WHERE execution_id = ? ORDER BY id", (row["id"],)).fetchall()
    return {**{k: row[k] for k in row.keys() if k != "verification"},
            "verification": json.loads(row["verification"]) if row["verification"] else None,
            "fills": [json.loads(f["body"]) for f in fills],
            "events": [dict(e) for e in events]}


def record(execution: dict, events: list[tuple[str, str]], fills: list[dict]) -> dict:
    """Write one execution with the states it passed through and its fills."""
    with _lock, _connect() as db:
        db.execute(
            "INSERT INTO executions (id, user_id, alert_id, title, action, mode, state, broker, created_at, detail, "
            "verification) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (execution["id"], execution["user_id"], execution["alert_id"], execution["title"], execution["action"],
             execution["mode"], execution["state"], execution["broker"], now(), execution.get("detail"),
             json.dumps(execution["verification"]) if execution.get("verification") else None))
        for state, detail in events:
            db.execute("INSERT INTO events (execution_id, state, at, detail) VALUES (?, ?, ?, ?)",
                       (execution["id"], state, now(), detail))
        for fill in fills:
            db.execute("INSERT INTO fills (execution_id, body) VALUES (?, ?)", (execution["id"], json.dumps(fill)))
        return _full(db, db.execute("SELECT * FROM executions WHERE id = ?", (execution["id"],)).fetchone())


def executions(user_id: str, limit: int = 50) -> list[dict]:
    with _lock, _connect() as db:
        rows = db.execute("SELECT * FROM executions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                          (user_id, limit)).fetchall()
        return [_full(db, row) for row in rows]


def shares_sold(user_id: str, ticker: str) -> float:
    """Shares of a ticker this user's filled paper orders have already sold."""
    return sum(fill["quantity"] for execution in executions(user_id, 1000) if execution["state"] == "FILLED"
               for fill in execution["fills"] if fill["type"] == "sell" and fill["ticker"] == ticker)


def get_policy(user_id: str) -> dict:
    with _lock, _connect() as db:
        row = db.execute("SELECT * FROM policies WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return dict(DEFAULT_POLICY)
    return {"enabled": bool(row["enabled"]), "min_downside": row["min_downside"], "max_put_cost": row["max_put_cost"]}


def set_policy(user_id: str, policy: dict) -> dict:
    with _lock, _connect() as db:
        db.execute("INSERT INTO policies (user_id, enabled, min_downside, max_put_cost) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled, "
                   "min_downside = excluded.min_downside, max_put_cost = excluded.max_put_cost",
                   (user_id, int(policy["enabled"]), policy["min_downside"], policy["max_put_cost"]))
    return get_policy(user_id)
