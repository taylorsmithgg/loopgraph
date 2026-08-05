"""Connection, schema and the append-only delta log."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

CHANGE_TYPES = frozenset(
    {
        "STATE_TRANSITION",
        "THRESHOLD_BREACH",
        "STALENESS",
        "DEPENDENCY_RISK",
        "OWNERSHIP_CHANGE",
        "CRITERION_DROPPED",
        "MEMORY_RETAINED",
        "MEMORY_FORGOTTEN",
        "MEMORY_SUPERSEDED",
    }
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id                 TEXT PRIMARY KEY,
    type               TEXT NOT NULL,
    statement          TEXT NOT NULL DEFAULT '',
    evidence_cmd       TEXT,
    expect_json        TEXT NOT NULL DEFAULT '{}',
    staleness_window_s INTEGER,
    timeout_s          INTEGER NOT NULL DEFAULT 120,
    owner              TEXT,
    is_goal            INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    src        TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    dst        TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    rel_type   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (src, dst, rel_type)
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    criterion_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    exit_code    INTEGER,
    stdout       TEXT NOT NULL DEFAULT '',
    stderr       TEXT NOT NULL DEFAULT '',
    timed_out    INTEGER NOT NULL DEFAULT 0,
    ok           INTEGER
);

CREATE INDEX IF NOT EXISTS idx_runs_criterion ON runs (criterion_id, id DESC);

CREATE TABLE IF NOT EXISTS deltas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id     TEXT NOT NULL,
    change_type   TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    wall_time     TEXT NOT NULL,
    logical_clock INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deltas_entity ON deltas (entity_id, id DESC);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def meta_get(conn: sqlite3.Connection, key: str, default: str | None = None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def _next_clock_in_transaction(conn: sqlite3.Connection) -> int:
    """Increment clock, assuming transaction is already open. Internal only."""
    current = int(meta_get(conn, "clock", "0"))
    nxt = current + 1
    meta_set(conn, "clock", str(nxt))
    return nxt


def next_clock(conn: sqlite3.Connection) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        nxt = _next_clock_in_transaction(conn)
        conn.execute("COMMIT")
        return nxt
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _emit_delta_in_transaction(
    conn: sqlite3.Connection,
    entity_id: str,
    change_type: str,
    old_value=None,
    new_value=None,
) -> int:
    """Insert a delta row, assuming a transaction is already open. Internal
    only -- lets a caller (e.g. state.record_status) fold a read-then-
    decide-then-write sequence into its own single BEGIN IMMEDIATE instead
    of nesting a second autocommitted transaction inside it."""
    if change_type not in CHANGE_TYPES:
        raise ValueError(f"unknown change_type: {change_type}")
    clock = _next_clock_in_transaction(conn)
    conn.execute(
        "INSERT INTO deltas (entity_id, change_type, old_value, new_value, "
        "wall_time, logical_clock) VALUES (?, ?, ?, ?, ?, ?)",
        (
            entity_id,
            change_type,
            None if old_value is None else str(old_value),
            None if new_value is None else str(new_value),
            utcnow(),
            clock,
        ),
    )
    return clock


def emit_delta(
    conn: sqlite3.Connection,
    entity_id: str,
    change_type: str,
    old_value=None,
    new_value=None,
) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        clock = _emit_delta_in_transaction(
            conn, entity_id, change_type, old_value, new_value
        )
        conn.execute("COMMIT")
        return clock
    except Exception:
        conn.execute("ROLLBACK")
        raise
