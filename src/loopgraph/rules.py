"""Threshold rules. Every rule is a pure predicate over derived state."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .db import meta_get, meta_set
from .graph import all_criteria, dependents, has_cycle
from .state import statuses

DEFAULT_STAGNATION_TURNS = 3


def tick(conn: sqlite3.Connection) -> int:
    _sync_progress_marker(conn)
    turns = int(meta_get(conn, "turns", "0")) + 1
    meta_set(conn, "turns", str(turns))
    return turns


def add_spend(conn: sqlite3.Connection, tokens: int) -> int:
    total = int(meta_get(conn, "spend", "0")) + int(tokens)
    meta_set(conn, "spend", str(total))
    return total


def _sync_progress_marker(conn: sqlite3.Connection) -> None:
    """Stamp the turn count at which the newest closing delta was first seen."""
    row = conn.execute(
        "SELECT id FROM deltas WHERE change_type='STATE_TRANSITION' "
        "AND new_value='closed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return
    if row["id"] > int(meta_get(conn, "last_progress_delta_id", "0")):
        meta_set(conn, "last_progress_delta_id", str(row["id"]))
        meta_set(conn, "turns_at_last_progress", meta_get(conn, "turns", "0"))


def _turns_since_progress(conn: sqlite3.Connection) -> int:
    _sync_progress_marker(conn)
    return int(meta_get(conn, "turns", "0")) - int(
        meta_get(conn, "turns_at_last_progress", "0")
    )


def evaluate_rules(
    conn: sqlite3.Connection,
    cfg: dict,
    now: datetime | None = None,
    only: set[str] | None = None,
) -> list[dict]:
    out: list[dict] = []
    st = statuses(conn, now=now, only=only)

    stagnation = cfg.get("stagnation_turns", DEFAULT_STAGNATION_TURNS)
    # A fully-met specification (every criterion closed) has nothing left
    # to close, so the absence of a recent closing delta is not
    # stagnation -- it's completion. Without this guard R-01 fires
    # forever once nothing remains open, permanently masking `success`.
    still_open = any(v != "closed" for v in st.values())
    if st and still_open and _turns_since_progress(conn) >= stagnation:
        out.append({"rule": "R-01", "detail":
                    f"no check has passed in the last {stagnation} turns"})

    stale = sorted(k for k, v in st.items() if v == "stale")
    if stale:
        out.append({"rule": "R-02", "detail": "these checks are old enough to "
                    f"need re-running: {', '.join(stale)}"})

    ceiling = cfg.get("budget_tokens")
    spend = int(meta_get(conn, "spend", "0"))
    if ceiling is not None and spend > int(ceiling):
        out.append({"rule": "R-04", "detail":
                    f"spent {spend} against a ceiling of {ceiling}"})

    unproven = sorted(k for k, v in st.items() if v == "unproven")
    if unproven:
        out.append({"rule": "R-05", "detail": "these checks have never been "
                    f"run: {', '.join(unproven)}"})

    # A guard is meant to stand alone -- it fences the repo, it is not a step
    # toward the goal -- so it is not an orphan, it is a fence.
    from .coord import node_flags

    orphans = sorted(
        c["id"]
        for c in all_criteria(conn)
        if not c["is_goal"]
        and (only is None or c["id"] in only)
        and not node_flags(conn, c["id"]).get("guard")
        and st.get(c["id"]) != "closed"
        and not dependents(conn, c["id"])
    )
    if orphans:
        out.append({"rule": "R-06", "detail": "these criteria are not "
                    f"connected to the goal: {', '.join(orphans)}"})

    cycle = has_cycle(conn)
    if cycle is not None:
        out.append({"rule": "R-07", "detail": "these criteria depend on each "
                    f"other in a loop: {' -> '.join(cycle)}"})

    return out


def terminal_state(
    conn: sqlite3.Connection,
    cfg: dict,
    now: datetime | None = None,
    only: set[str] | None = None,
) -> str | None:
    st = statuses(conn, now=now, only=only)
    if not st:
        return "no-op"
    rules = {r["rule"] for r in evaluate_rules(conn, cfg, now=now, only=only)}
    if "R-04" in rules:
        return "exhausted"
    if "R-01" in rules:
        return "stalled"
    # A cycle makes the criteria on it structurally unworkable (each
    # waits on the other), so it must never be reportable as success --
    # ranked after exhausted/stalled, before success.
    if "R-07" in rules:
        return "blocked"
    if all(v == "closed" for v in st.values()):
        # Guards all green is not success when the request itself was never
        # specified: that reports "met" for a specification nobody wrote.
        # Work remains -- saying what done means -- so this is in-progress,
        # and the Stop hook is what asks for it.
        from .coord import goal_pending, node_flags
        if goal_pending(conn) and not any(
            not node_flags(conn, cid).get("guard") for cid in st
        ):
            return None
        return "success"
    return None
