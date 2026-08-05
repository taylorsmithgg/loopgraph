"""Node and edge operations. Nothing here writes a status."""

from __future__ import annotations

import json
import sqlite3

from .db import emit_delta, utcnow

# Allowed edge relation types, per design doc §5.2. Anything else (a typo
# like "depends-on") is silently invisible to every traversal in this
# module, so it must be rejected at authoring time rather than stored.
ALLOWED_REL_TYPES = frozenset(
    {"depends_on", "blocks", "owned_by", "evidenced_by", "escalates_to",
     # memory relations: the graph half of recall. A memory that knows what
     # it relates to, what replaced it and what it was inferred from can be
     # traversed, not just matched.
     "relates_to", "supersedes", "derived_from", "observed_in"}
)


def add_criterion(
    conn: sqlite3.Connection,
    id: str,
    statement: str,
    evidence_cmd: str,
    expect: dict,
    staleness_window_s: int | None = None,
    timeout_s: int = 120,
    is_goal: bool = False,
) -> None:
    # Deferred import: evidence.py imports get_node from this module, so a
    # module-level import here would be circular. By call time both
    # modules are already fully loaded.
    from .evidence import validate_expect

    validate_expect(expect)
    now = utcnow()
    conn.execute(
        "INSERT INTO nodes (id, type, statement, evidence_cmd, expect_json, "
        "staleness_window_s, timeout_s, is_goal, created_at, updated_at) "
        "VALUES (?, 'criterion', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            id, statement, evidence_cmd, json.dumps(expect),
            staleness_window_s, timeout_s, int(is_goal), now, now,
        ),
    )


def get_node(conn: sqlite3.Connection, id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM nodes WHERE id = ?", (id,)).fetchone()


def all_criteria(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM nodes WHERE type='criterion' ORDER BY id")
    )


def drop_criterion(conn: sqlite3.Connection, id: str) -> bool:
    """Remove a criterion. Returns False if there was nothing to remove.

    A criterion nobody can withdraw is a trap once criteria are derived
    rather than typed: a misread of the request would hold the turn open
    against a goal the user never had. Edges and runs go with it (ON DELETE
    CASCADE); the deltas stay, because the record of having believed
    something is not invalidated by stopping believing it.
    """
    node = get_node(conn, id)
    if node is None or node["type"] != "criterion":
        return False
    emit_delta(conn, id, "CRITERION_DROPPED", node["statement"], None)
    conn.execute("DELETE FROM nodes WHERE id = ?", (id,))
    return True


def link(conn: sqlite3.Connection, src: str, dst: str, rel_type: str) -> None:
    if rel_type not in ALLOWED_REL_TYPES:
        raise ValueError(
            f"unknown rel_type: {rel_type!r} (allowed: "
            f"{', '.join(sorted(ALLOWED_REL_TYPES))})"
        )
    conn.execute(
        "INSERT OR IGNORE INTO edges (src, dst, rel_type, created_at) "
        "VALUES (?, ?, ?, ?)",
        (src, dst, rel_type, utcnow()),
    )


def dependencies(conn: sqlite3.Connection, id: str) -> list[str]:
    return [
        r["dst"]
        for r in conn.execute(
            "SELECT dst FROM edges WHERE src = ? AND rel_type='depends_on' "
            "ORDER BY dst",
            (id,),
        )
    ]


def dependents(conn: sqlite3.Connection, id: str) -> list[str]:
    return [
        r["src"]
        for r in conn.execute(
            "SELECT src FROM edges WHERE dst = ? AND rel_type='depends_on' "
            "ORDER BY src",
            (id,),
        )
    ]


def set_owner(conn: sqlite3.Connection, id: str, owner: str | None) -> None:
    node = get_node(conn, id)
    if node is None:
        raise ValueError(f"no such criterion: {id}")
    old = node["owner"]
    conn.execute(
        "UPDATE nodes SET owner = ?, updated_at = ? WHERE id = ?",
        (owner, utcnow(), id),
    )
    emit_delta(conn, id, "OWNERSHIP_CHANGE", old, owner)


def has_cycle(conn: sqlite3.Connection) -> list[str] | None:
    """Iterative DFS over depends_on. Returns a cycle path or None."""
    graph = {c["id"]: dependencies(conn, c["id"]) for c in all_criteria(conn)}
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {n: WHITE for n in graph}

    for root in graph:
        if colour[root] != WHITE:
            continue
        stack = [(root, iter(graph[root]))]
        path = [root]
        colour[root] = GREY
        while stack:
            node, it = stack[-1]
            advanced = False
            for nb in it:
                if nb not in colour:
                    continue
                if colour[nb] == GREY:
                    return path + [nb]
                if colour[nb] == WHITE:
                    colour[nb] = GREY
                    path.append(nb)
                    stack.append((nb, iter(graph[nb])))
                    advanced = True
                    break
            if not advanced:
                colour[node] = BLACK
                stack.pop()
                path.pop()
    return None
