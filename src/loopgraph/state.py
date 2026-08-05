"""Derives status. No caller may assert one."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .db import _emit_delta_in_transaction
from .graph import all_criteria, dependencies, get_node


def _latest_completed_run(conn: sqlite3.Connection, criterion_id: str):
    return conn.execute(
        "SELECT * FROM runs WHERE criterion_id = ? AND ok IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (criterion_id,),
    ).fetchone()


def derive_status(
    conn: sqlite3.Connection, criterion_id: str, now: datetime | None = None
) -> str:
    node = get_node(conn, criterion_id)
    if node is None:
        raise ValueError(f"no such criterion: {criterion_id}")
    run = _latest_completed_run(conn, criterion_id)
    if run is None:
        return "unproven"
    if not run["ok"]:
        return "open"
    window = node["staleness_window_s"]
    if window is None:
        return "closed"
    now = now or datetime.now(timezone.utc)
    age = (now - datetime.fromisoformat(run["ended_at"])).total_seconds()
    return "stale" if age > window else "closed"


def _last_recorded(conn: sqlite3.Connection, criterion_id: str) -> str | None:
    row = conn.execute(
        "SELECT new_value FROM deltas WHERE entity_id = ? AND change_type IN "
        "('STATE_TRANSITION', 'STALENESS') ORDER BY id DESC LIMIT 1",
        (criterion_id,),
    ).fetchone()
    return row["new_value"] if row else None


def record_status(
    conn: sqlite3.Connection, criterion_id: str, now: datetime | None = None
) -> str:
    current = derive_status(conn, criterion_id, now=now)
    # The read (_last_recorded) and the conditional insert must be one
    # atomic unit. Two autocommitted operations here is a read-then-write
    # race: concurrent callers can all read the same `previous` before any
    # of them has written, and each independently decides "this is a new
    # transition" -- producing duplicate closing deltas for one actual
    # transition. BEGIN IMMEDIATE takes the write lock up front, so only
    # one caller's read-and-maybe-insert can be in flight at a time.
    conn.execute("BEGIN IMMEDIATE")
    try:
        previous = _last_recorded(conn, criterion_id) or "unproven"
        if current != previous:
            change = "STALENESS" if current == "stale" else "STATE_TRANSITION"
            _emit_delta_in_transaction(conn, criterion_id, change, previous, current)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return current


def statuses(
    conn: sqlite3.Connection,
    now: datetime | None = None,
    only: set[str] | None = None,
) -> dict[str, str]:
    """`only` narrows the graph to one session's criteria -- see
    coord.owned_here. Omitting it keeps every criterion, which is what any
    human-facing report wants."""
    return {
        c["id"]: derive_status(conn, c["id"], now=now)
        for c in all_criteria(conn)
        if only is None or c["id"] in only
    }


def is_blocked(
    conn: sqlite3.Connection, criterion_id: str, now: datetime | None = None
) -> bool:
    if derive_status(conn, criterion_id, now=now) not in ("open", "unproven"):
        return False
    return any(
        derive_status(conn, dep, now=now) != "closed"
        for dep in dependencies(conn, criterion_id)
    )


def blocked(
    conn: sqlite3.Connection, now: datetime | None = None
) -> dict[str, list[str]]:
    out = {}
    for c in all_criteria(conn):
        cid = c["id"]
        if derive_status(conn, cid, now=now) not in ("open", "unproven"):
            continue
        holding = [
            dep
            for dep in dependencies(conn, cid)
            if derive_status(conn, dep, now=now) != "closed"
        ]
        if holding:
            out[cid] = holding
    return out


def workable(
    conn: sqlite3.Connection, now: datetime | None = None
) -> list[str]:
    out = []
    for c in all_criteria(conn):
        cid = c["id"]
        if derive_status(conn, cid, now=now) == "closed":
            continue
        if all(
            derive_status(conn, dep, now=now) == "closed"
            for dep in dependencies(conn, cid)
        ):
            out.append(cid)
    return sorted(out)
