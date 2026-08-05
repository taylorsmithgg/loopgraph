---
title: Implementation plan
description: The staged plan the core was built to, test-first, with the acceptance criteria for each stage.
---

# loopgraph Core (P0+P1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic core that computes whether a specification is met — a context graph of criteria whose status is derived from evidence commands and never written by an agent.

**Architecture:** SQLite holds nodes, edges, evidence runs and an append-only delta log. There is deliberately **no `status` column** — status is derived on read from run history plus staleness, so no caller can assert "done." Threshold rules are pure predicates over that derived state. A stdlib-only CLI exposes authoring, evidence execution and state queries; later plans wire it into `Stop`/`SubagentStop` hooks.

**Tech Stack:** Python 3.12 (via uv), stdlib only at runtime (`sqlite3`, `argparse`, `subprocess`, `json`, `re`), pytest for tests.

## Global Constraints

- Runtime dependencies: **stdlib only.** The gate runs on every turn; import latency is a feature. pytest is a dev dependency.
- Python 3.12, pinned via `uv python pin 3.12`. System Python is 3.9.6 — do not use it.
- Always use an isolated virtual environment; prefer `uv`.
- **Never** add attribution, credit, signatures, or metadata identifying Claude, Codex, OpenAI, Anthropic or any AI as author — this includes commit messages and code comments.
- No `status` column may be added to any table. Status is derived. This is the load-bearing invariant of the whole system.
- All timestamps stored as ISO-8601 UTC strings (`datetime.now(timezone.utc).isoformat()`).
- Every guard must be watched to fail before it is trusted: each rule gets a test that proves it fires **and** a test that proves it stays silent.

## Deviations from the spec (deliberate, flagged)

1. **Spec §8 R-03 `blocked`** is defined as "criterion open ∧ all deps open ∧ age > threshold." That conflicts with the §6.1 gate example ("C9 blocked by C7"). This plan implements `blocked` as **dependency-derived** — open/unproven with at least one non-closed dependency — which is what the gate example requires. Age-based escalation is deferred to the gate plan.
2. **Spec §8 R-06 `dependency risk`** ("closing C would not unblock anything workable") is not directly checkable without a goal marker. This plan implements it as **orphan detection**: an open criterion with no incoming `depends_on` edge that is not flagged `is_goal`. Same intent, decidable.
3. Turn counting and token spend are inputs the harness supplies. P1 stores them in `meta` and exposes `tick` / `spend` commands so rules R-01 and R-04 are testable now; the hooks plan wires real values.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | package metadata, pytest dev dep |
| `src/loopgraph/db.py` | connection, schema, logical clock, delta emission |
| `src/loopgraph/graph.py` | node/edge CRUD, ownership, dependency traversal |
| `src/loopgraph/evidence.py` | run an evidence command, evaluate expectations |
| `src/loopgraph/state.py` | derive status, workability, termination |
| `src/loopgraph/rules.py` | threshold rules R-01…R-06 |
| `src/loopgraph/cli.py` | argparse entry point |
| `tests/` | one test module per source module |

---

### Task 1: Project scaffold and schema

**Files:**
- Create: `pyproject.toml`, `src/loopgraph/__init__.py`, `src/loopgraph/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing
- Produces: `open_db(path: str | Path) -> sqlite3.Connection` — returns a connection with WAL enabled, foreign keys on, `row_factory = sqlite3.Row`, and the schema applied idempotently.

- [ ] **Step 1: Create the project skeleton**

```bash
cd ~/src/loopgraph
uv python pin 3.12
mkdir -p src/loopgraph tests
touch src/loopgraph/__init__.py
```

Write `pyproject.toml`:

```toml
[project]
name = "loopgraph"
version = "0.1.0"
description = "Deterministic goal-state substrate for agent loops"
requires-python = ">=3.12"
dependencies = []

[project.scripts]
loopgraph = "loopgraph.cli:main"

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/loopgraph"]
```

```bash
uv sync
```

- [ ] **Step 2: Write the failing test**

`tests/test_db.py`:

```python
import sqlite3
import pytest
from loopgraph.db import open_db


def test_open_db_creates_schema(tmp_path):
    conn = open_db(tmp_path / "g.db")
    names = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"nodes", "edges", "runs", "deltas", "meta"} <= names


def test_no_status_column_anywhere(tmp_path):
    """The load-bearing invariant: status is derived, never stored."""
    conn = open_db(tmp_path / "g.db")
    for table in ("nodes", "edges", "runs", "deltas", "meta"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert "status" not in cols, f"{table} must not store status"


def test_open_db_is_idempotent(tmp_path):
    path = tmp_path / "g.db"
    open_db(path).close()
    conn = open_db(path)
    assert conn.execute("SELECT count(*) AS n FROM nodes").fetchone()["n"] == 0


def test_foreign_keys_enforced(tmp_path):
    conn = open_db(tmp_path / "g.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO edges (src, dst, rel_type, created_at) "
            "VALUES ('nope', 'alsonope', 'depends_on', '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loopgraph.db'`

- [ ] **Step 4: Write the implementation**

`src/loopgraph/db.py`:

```python
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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .python-version src/loopgraph tests/test_db.py
git commit -m "feat: sqlite schema with no status column"
```

---

### Task 2: Logical clock and delta emission

**Files:**
- Modify: `src/loopgraph/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `open_db`
- Produces:
  - `next_clock(conn) -> int` — monotonic counter in `meta`, incremented atomically.
  - `emit_delta(conn, entity_id: str, change_type: str, old, new) -> int` — appends to `deltas`, returns the logical clock value. Raises `ValueError` on an unknown `change_type`.
  - `meta_get(conn, key, default=None) -> str | None`, `meta_set(conn, key, value) -> None`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
from loopgraph.db import emit_delta, next_clock, meta_get, meta_set


def test_clock_is_monotonic(tmp_path):
    conn = open_db(tmp_path / "g.db")
    assert [next_clock(conn) for _ in range(3)] == [1, 2, 3]


def test_emit_delta_appends_and_stamps_clock(tmp_path):
    conn = open_db(tmp_path / "g.db")
    emit_delta(conn, "C1", "STATE_TRANSITION", "open", "closed")
    emit_delta(conn, "C1", "STALENESS", "closed", "stale")
    rows = list(conn.execute("SELECT * FROM deltas ORDER BY id"))
    assert [r["change_type"] for r in rows] == ["STATE_TRANSITION", "STALENESS"]
    assert [r["logical_clock"] for r in rows] == [1, 2]
    assert rows[0]["new_value"] == "closed"


def test_emit_delta_rejects_unknown_change_type(tmp_path):
    conn = open_db(tmp_path / "g.db")
    with pytest.raises(ValueError):
        emit_delta(conn, "C1", "MADE_UP", None, None)


def test_delta_log_is_append_only_in_practice(tmp_path):
    """Nothing in the API mutates a delta; verify ids only grow."""
    conn = open_db(tmp_path / "g.db")
    for _ in range(5):
        emit_delta(conn, "C1", "STATE_TRANSITION", None, "closed")
    ids = [r["id"] for r in conn.execute("SELECT id FROM deltas ORDER BY id")]
    assert ids == sorted(ids) and len(set(ids)) == 5


def test_meta_roundtrip(tmp_path):
    conn = open_db(tmp_path / "g.db")
    assert meta_get(conn, "turns") is None
    meta_set(conn, "turns", "7")
    assert meta_get(conn, "turns") == "7"
    meta_set(conn, "turns", "8")
    assert meta_get(conn, "turns") == "8"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'emit_delta'`

- [ ] **Step 3: Write the implementation**

Append to `src/loopgraph/db.py`:

```python
def meta_get(conn: sqlite3.Connection, key: str, default: str | None = None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def next_clock(conn: sqlite3.Connection) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = int(meta_get(conn, "clock", "0"))
        nxt = current + 1
        meta_set(conn, "clock", str(nxt))
        conn.execute("COMMIT")
        return nxt
    except Exception:
        conn.execute("ROLLBACK")
        raise


def emit_delta(
    conn: sqlite3.Connection,
    entity_id: str,
    change_type: str,
    old_value=None,
    new_value=None,
) -> int:
    if change_type not in CHANGE_TYPES:
        raise ValueError(f"unknown change_type: {change_type}")
    clock = next_clock(conn)
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/loopgraph/db.py tests/test_db.py
git commit -m "feat: logical clock and append-only delta log"
```

---

### Task 3: Node and edge CRUD

**Files:**
- Create: `src/loopgraph/graph.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `open_db`, `emit_delta`, `utcnow`
- Produces:
  - `add_criterion(conn, id, statement, evidence_cmd, expect: dict, staleness_window_s=None, timeout_s=120, is_goal=False) -> None`
  - `get_node(conn, id) -> sqlite3.Row | None`
  - `all_criteria(conn) -> list[sqlite3.Row]` — ordered by `id`
  - `link(conn, src, dst, rel_type) -> None` — `src depends_on dst` means src needs dst closed
  - `dependencies(conn, id) -> list[str]` — direct `depends_on` targets
  - `dependents(conn, id) -> list[str]` — direct inbound `depends_on` sources
  - `set_owner(conn, id, owner: str | None) -> None` — emits `OWNERSHIP_CHANGE`
  - `has_cycle(conn) -> list[str] | None` — returns a cycle path, or None

- [ ] **Step 1: Write the failing test**

`tests/test_graph.py`:

```python
import pytest
from loopgraph.db import open_db
from loopgraph.graph import (
    add_criterion, all_criteria, dependencies, dependents,
    get_node, has_cycle, link, set_owner,
)


@pytest.fixture
def conn(tmp_path):
    return open_db(tmp_path / "g.db")


def test_add_and_get_criterion(conn):
    add_criterion(conn, "C1", "the lake has audit rows", "echo 1", {"stdout_int_gte": 1})
    node = get_node(conn, "C1")
    assert node["type"] == "criterion"
    assert node["statement"] == "the lake has audit rows"
    assert node["expect_json"] == '{"stdout_int_gte": 1}'
    assert node["owner"] is None


def test_get_missing_node_returns_none(conn):
    assert get_node(conn, "nope") is None


def test_all_criteria_sorted(conn):
    for cid in ("C3", "C1", "C2"):
        add_criterion(conn, cid, "s", "true", {})
    assert [c["id"] for c in all_criteria(conn)] == ["C1", "C2", "C3"]


def test_link_and_traverse(conn):
    add_criterion(conn, "C1", "s", "true", {})
    add_criterion(conn, "C2", "s", "true", {})
    link(conn, "C2", "C1", "depends_on")
    assert dependencies(conn, "C2") == ["C1"]
    assert dependents(conn, "C1") == ["C2"]
    assert dependencies(conn, "C1") == []


def test_set_owner_emits_ownership_delta(conn):
    add_criterion(conn, "C1", "s", "true", {})
    set_owner(conn, "C1", "agent-7")
    assert get_node(conn, "C1")["owner"] == "agent-7"
    row = conn.execute(
        "SELECT * FROM deltas WHERE change_type='OWNERSHIP_CHANGE'"
    ).fetchone()
    assert row["entity_id"] == "C1" and row["new_value"] == "agent-7"


def test_has_cycle_detects_loop(conn):
    for cid in ("C1", "C2", "C3"):
        add_criterion(conn, cid, "s", "true", {})
    link(conn, "C1", "C2", "depends_on")
    link(conn, "C2", "C3", "depends_on")
    link(conn, "C3", "C1", "depends_on")
    assert has_cycle(conn) is not None


def test_has_cycle_silent_on_dag(conn):
    """The guard must be watched to stay quiet, not only to fire."""
    for cid in ("C1", "C2", "C3"):
        add_criterion(conn, cid, "s", "true", {})
    link(conn, "C1", "C2", "depends_on")
    link(conn, "C2", "C3", "depends_on")
    assert has_cycle(conn) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_graph.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loopgraph.graph'`

- [ ] **Step 3: Write the implementation**

`src/loopgraph/graph.py`:

```python
"""Node and edge operations. Nothing here writes a status."""

from __future__ import annotations

import json
import sqlite3

from .db import emit_delta, utcnow


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


def link(conn: sqlite3.Connection, src: str, dst: str, rel_type: str) -> None:
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
    old = get_node(conn, id)["owner"]
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_graph.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/loopgraph/graph.py tests/test_graph.py
git commit -m "feat: node and edge crud with cycle detection"
```

---

### Task 4: Evidence runner

**Files:**
- Create: `src/loopgraph/evidence.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `open_db`, `get_node`
- Produces:
  - `evaluate(expect: dict, exit_code: int, stdout: str) -> bool` — every key must pass (AND). An empty dict means `exit_zero`. Unknown key raises `ValueError`.
  - `run_evidence(conn, criterion_id: str) -> sqlite3.Row` — executes the command, writes a `runs` row, returns it. Sets `ok=None` on timeout, `ok=1`/`ok=0` otherwise.

  Supported expectation keys: `exit_zero` (bool), `stdout_int_gte` (int, parses the last integer in stdout), `stdout_contains` (str), `stdout_matches` (regex str).

- [ ] **Step 1: Write the failing test**

`tests/test_evidence.py`:

```python
import pytest
from loopgraph.db import open_db
from loopgraph.evidence import evaluate, run_evidence
from loopgraph.graph import add_criterion


@pytest.fixture
def conn(tmp_path):
    return open_db(tmp_path / "g.db")


@pytest.mark.parametrize(
    "expect,code,out,want",
    [
        ({}, 0, "", True),
        ({}, 1, "", False),
        ({"exit_zero": True}, 0, "", True),
        ({"stdout_int_gte": 1}, 0, "5\n", True),
        ({"stdout_int_gte": 1}, 0, "0\n", False),
        ({"stdout_int_gte": 1}, 0, "no digits", False),
        ({"stdout_contains": "ok"}, 0, "all ok here", True),
        ({"stdout_contains": "ok"}, 0, "nope", False),
        ({"stdout_matches": r"^\d+ rows$"}, 0, "42 rows", True),
        ({"stdout_matches": r"^\d+ rows$"}, 0, "rows", False),
        # every key must pass
        ({"exit_zero": True, "stdout_contains": "ok"}, 1, "ok", False),
    ],
)
def test_evaluate(expect, code, out, want):
    assert evaluate(expect, code, out) is want


def test_evaluate_rejects_unknown_key():
    with pytest.raises(ValueError):
        evaluate({"vibes": "good"}, 0, "")


def test_run_evidence_records_pass(conn):
    add_criterion(conn, "C1", "s", "echo 7", {"stdout_int_gte": 5})
    run = run_evidence(conn, "C1")
    assert run["ok"] == 1
    assert run["exit_code"] == 0
    assert run["stdout"].strip() == "7"
    assert run["ended_at"] is not None


def test_run_evidence_records_fail(conn):
    add_criterion(conn, "C1", "s", "echo 2", {"stdout_int_gte": 5})
    assert run_evidence(conn, "C1")["ok"] == 0


def test_timeout_is_unproven_not_failed(conn):
    """A timeout must never read as a decided result."""
    add_criterion(conn, "C1", "s", "sleep 5", {}, timeout_s=1)
    run = run_evidence(conn, "C1")
    assert run["timed_out"] == 1
    assert run["ok"] is None


def test_run_evidence_requires_a_command(conn):
    add_criterion(conn, "C1", "s", None, {})
    with pytest.raises(ValueError):
        run_evidence(conn, "C1")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_evidence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loopgraph.evidence'`

- [ ] **Step 3: Write the implementation**

`src/loopgraph/evidence.py`:

```python
"""Executes evidence commands. The only writer of run results."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess

from .db import utcnow
from .graph import get_node

_LAST_INT = re.compile(r"(-?\d+)(?!.*-?\d)", re.S)


def evaluate(expect: dict, exit_code: int, stdout: str) -> bool:
    if not expect:
        expect = {"exit_zero": True}
    for key, want in expect.items():
        if key == "exit_zero":
            if (exit_code == 0) is not bool(want):
                return False
        elif key == "stdout_int_gte":
            m = _LAST_INT.search(stdout)
            if not m or int(m.group(1)) < int(want):
                return False
        elif key == "stdout_contains":
            if str(want) not in stdout:
                return False
        elif key == "stdout_matches":
            if not re.search(str(want), stdout, re.M):
                return False
        else:
            raise ValueError(f"unknown expectation key: {key}")
    return True


def run_evidence(conn: sqlite3.Connection, criterion_id: str) -> sqlite3.Row:
    node = get_node(conn, criterion_id)
    if node is None:
        raise ValueError(f"no such criterion: {criterion_id}")
    cmd = node["evidence_cmd"]
    if not cmd:
        raise ValueError(f"criterion {criterion_id} has no evidence command")

    started = utcnow()
    cur = conn.execute(
        "INSERT INTO runs (criterion_id, started_at) VALUES (?, ?)",
        (criterion_id, started),
    )
    run_id = cur.lastrowid

    timed_out = 0
    exit_code = None
    stdout = stderr = ""
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=node["timeout_s"],
        )
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = 1
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + "\n[loopgraph] timed out"
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")

    ok = None if timed_out else int(
        evaluate(json.loads(node["expect_json"]), exit_code, stdout)
    )

    conn.execute(
        "UPDATE runs SET ended_at=?, exit_code=?, stdout=?, stderr=?, "
        "timed_out=?, ok=? WHERE id=?",
        (utcnow(), exit_code, stdout, stderr, timed_out, ok, run_id),
    )
    return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_evidence.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add src/loopgraph/evidence.py tests/test_evidence.py
git commit -m "feat: evidence runner with timeout as undecided"
```

---

### Task 5: Status derivation

**Files:**
- Create: `src/loopgraph/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `all_criteria`, `dependencies`, `get_node`
- Produces:
  - `derive_status(conn, criterion_id, now: datetime | None = None) -> str` — one of `unproven`, `open`, `closed`, `stale`
  - `record_status(conn, criterion_id, now=None) -> str` — derives, and emits `STATE_TRANSITION` (or `STALENESS` when moving to `stale`) if it changed since the last recorded transition for that entity
  - `statuses(conn, now=None) -> dict[str, str]`

  Rules, in order: no completed run → `unproven`; latest completed run `ok=0` → `open`; `ok=1` and no window → `closed`; `ok=1` and age > window → `stale`; else `closed`.

- [ ] **Step 1: Write the failing test**

`tests/test_state.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest
from loopgraph.db import open_db
from loopgraph.evidence import run_evidence
from loopgraph.graph import add_criterion
from loopgraph.state import derive_status, record_status, statuses


@pytest.fixture
def conn(tmp_path):
    return open_db(tmp_path / "g.db")


def test_never_run_is_unproven(conn):
    add_criterion(conn, "C1", "s", "true", {})
    assert derive_status(conn, "C1") == "unproven"


def test_timeout_leaves_it_unproven(conn):
    add_criterion(conn, "C1", "s", "sleep 5", {}, timeout_s=1)
    run_evidence(conn, "C1")
    assert derive_status(conn, "C1") == "unproven"


def test_failing_run_is_open(conn):
    add_criterion(conn, "C1", "s", "false", {})
    run_evidence(conn, "C1")
    assert derive_status(conn, "C1") == "open"


def test_passing_run_is_closed(conn):
    add_criterion(conn, "C1", "s", "true", {})
    run_evidence(conn, "C1")
    assert derive_status(conn, "C1") == "closed"


def test_passing_run_goes_stale_past_window(conn):
    add_criterion(conn, "C1", "s", "true", {}, staleness_window_s=3600)
    run_evidence(conn, "C1")
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert derive_status(conn, "C1") == "closed"
    assert derive_status(conn, "C1", now=later) == "stale"


def test_latest_run_wins(conn):
    add_criterion(conn, "C1", "s", "true", {})
    run_evidence(conn, "C1")
    conn.execute("UPDATE nodes SET evidence_cmd='false' WHERE id='C1'")
    run_evidence(conn, "C1")
    assert derive_status(conn, "C1") == "open"


def test_record_status_emits_transition_once(conn):
    add_criterion(conn, "C1", "s", "true", {})
    run_evidence(conn, "C1")
    assert record_status(conn, "C1") == "closed"
    assert record_status(conn, "C1") == "closed"
    rows = list(
        conn.execute(
            "SELECT * FROM deltas WHERE entity_id='C1' "
            "AND change_type='STATE_TRANSITION'"
        )
    )
    assert len(rows) == 1
    assert rows[0]["old_value"] == "unproven" and rows[0]["new_value"] == "closed"


def test_record_status_uses_staleness_change_type(conn):
    add_criterion(conn, "C1", "s", "true", {}, staleness_window_s=1)
    run_evidence(conn, "C1")
    record_status(conn, "C1")
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    assert record_status(conn, "C1", now=later) == "stale"
    assert conn.execute(
        "SELECT count(*) AS n FROM deltas WHERE change_type='STALENESS'"
    ).fetchone()["n"] == 1


def test_statuses_covers_all(conn):
    add_criterion(conn, "C1", "s", "true", {})
    add_criterion(conn, "C2", "s", "false", {})
    add_criterion(conn, "C3", "s", "true", {})
    run_evidence(conn, "C1")
    run_evidence(conn, "C2")
    assert statuses(conn) == {"C1": "closed", "C2": "open", "C3": "unproven"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loopgraph.state'`

- [ ] **Step 3: Write the implementation**

`src/loopgraph/state.py`:

```python
"""Derives status. No caller may assert one."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .db import emit_delta
from .graph import all_criteria, get_node

TERMINAL_OK = "closed"


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
    previous = _last_recorded(conn, criterion_id)
    if current != previous:
        change = "STALENESS" if current == "stale" else "STATE_TRANSITION"
        emit_delta(
            conn, criterion_id, change, previous or "unproven", current
        )
    return current


def statuses(
    conn: sqlite3.Connection, now: datetime | None = None
) -> dict[str, str]:
    return {
        c["id"]: derive_status(conn, c["id"], now=now)
        for c in all_criteria(conn)
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_state.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/loopgraph/state.py tests/test_state.py
git commit -m "feat: derive status from run history and staleness"
```

---

### Task 6: Workability and blocking

**Files:**
- Modify: `src/loopgraph/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `derive_status`, `dependencies`
- Produces:
  - `is_blocked(conn, criterion_id, now=None) -> bool` — status in `{open, unproven}` and at least one dependency is not `closed`
  - `workable(conn, now=None) -> list[str]` — sorted ids that are `open`/`unproven`/`stale` with all dependencies `closed`
  - `blocked(conn, now=None) -> dict[str, list[str]]` — id → the dependency ids holding it up

- [ ] **Step 1: Write the failing test**

Append to `tests/test_state.py`:

```python
from loopgraph.graph import link
from loopgraph.state import blocked, is_blocked, workable


def test_dependency_blocks_until_closed(conn):
    add_criterion(conn, "C1", "s", "false", {})
    add_criterion(conn, "C2", "s", "false", {})
    link(conn, "C2", "C1", "depends_on")
    run_evidence(conn, "C1")
    run_evidence(conn, "C2")
    assert is_blocked(conn, "C2") is True
    assert is_blocked(conn, "C1") is False
    assert blocked(conn) == {"C2": ["C1"]}
    assert workable(conn) == ["C1"]


def test_closing_dependency_unblocks(conn):
    add_criterion(conn, "C1", "s", "true", {})
    add_criterion(conn, "C2", "s", "false", {})
    link(conn, "C2", "C1", "depends_on")
    run_evidence(conn, "C1")
    run_evidence(conn, "C2")
    assert blocked(conn) == {}
    assert workable(conn) == ["C2"]


def test_stale_criterion_is_workable_again(conn):
    add_criterion(conn, "C1", "s", "true", {}, staleness_window_s=1)
    run_evidence(conn, "C1")
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    assert workable(conn, now=later) == ["C1"]


def test_all_closed_means_nothing_workable(conn):
    add_criterion(conn, "C1", "s", "true", {})
    run_evidence(conn, "C1")
    assert workable(conn) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL with `ImportError: cannot import name 'workable'`

- [ ] **Step 3: Write the implementation**

Append to `src/loopgraph/state.py` (add `dependencies` to the existing `graph` import):

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_state.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/loopgraph/state.py tests/test_state.py
git commit -m "feat: dependency-derived workability and blocking"
```

---

### Task 7: Threshold rules and terminal state

**Files:**
- Create: `src/loopgraph/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Consumes: `statuses`, `all_criteria`, `dependents`, `meta_get`, `meta_set`
- Produces:
  - `tick(conn) -> int` — increments and returns `meta['turns']`
  - `add_spend(conn, tokens: int) -> int` — increments and returns `meta['spend']`
  - `evaluate_rules(conn, cfg: dict, now=None) -> list[dict]` — each `{"rule": "R-01", "detail": str}`. `cfg` keys: `stagnation_turns` (default 3), `budget_tokens` (default None = no ceiling).
  - `terminal_state(conn, cfg, now=None) -> str | None` — `no-op` / `exhausted` / `stalled` / `success`, or `None` meaning keep working. Precedence: exhausted > stalled > success > None.

  Rules: **R-01** no closing delta within `stagnation_turns`; **R-02** any `stale`; **R-04** spend over ceiling; **R-05** any `unproven`; **R-06** open criterion with no dependents and `is_goal=0`.

- [ ] **Step 1: Write the failing test**

`tests/test_rules.py`:

```python
import pytest
from loopgraph.db import open_db
from loopgraph.evidence import run_evidence
from loopgraph.graph import add_criterion, link
from loopgraph.rules import add_spend, evaluate_rules, terminal_state, tick
from loopgraph.state import record_status


@pytest.fixture
def conn(tmp_path):
    return open_db(tmp_path / "g.db")


def fired(results):
    return {r["rule"] for r in results}


def test_no_criteria_is_noop(conn):
    assert terminal_state(conn, {}) == "no-op"


def test_all_closed_is_success(conn):
    add_criterion(conn, "C1", "s", "true", {}, is_goal=True)
    run_evidence(conn, "C1")
    record_status(conn, "C1")
    assert terminal_state(conn, {}) == "success"


def test_open_criterion_is_not_terminal(conn):
    add_criterion(conn, "C1", "s", "false", {}, is_goal=True)
    run_evidence(conn, "C1")
    assert terminal_state(conn, {}) is None


def test_unproven_blocks_success_and_fires_r05(conn):
    add_criterion(conn, "C1", "s", "true", {}, is_goal=True)
    assert terminal_state(conn, {}) is None
    assert "R-05" in fired(evaluate_rules(conn, {}))


def test_r05_silent_when_all_proven(conn):
    add_criterion(conn, "C1", "s", "true", {}, is_goal=True)
    run_evidence(conn, "C1")
    assert "R-05" not in fired(evaluate_rules(conn, {}))


def test_r01_stagnation_fires_after_barren_turns(conn):
    add_criterion(conn, "C1", "s", "false", {}, is_goal=True)
    run_evidence(conn, "C1")
    record_status(conn, "C1")
    for _ in range(3):
        tick(conn)
    assert "R-01" in fired(evaluate_rules(conn, {"stagnation_turns": 3}))
    assert terminal_state(conn, {"stagnation_turns": 3}) == "stalled"


def test_r01_silent_when_progress_is_recent(conn):
    add_criterion(conn, "C1", "s", "true", {}, is_goal=True)
    for _ in range(3):
        tick(conn)
    run_evidence(conn, "C1")
    record_status(conn, "C1")
    assert "R-01" not in fired(evaluate_rules(conn, {"stagnation_turns": 3}))


def test_r04_budget_ceiling(conn):
    add_criterion(conn, "C1", "s", "false", {}, is_goal=True)
    run_evidence(conn, "C1")
    add_spend(conn, 500)
    assert "R-04" not in fired(evaluate_rules(conn, {"budget_tokens": 1000}))
    add_spend(conn, 600)
    assert "R-04" in fired(evaluate_rules(conn, {"budget_tokens": 1000}))
    assert terminal_state(conn, {"budget_tokens": 1000}) == "exhausted"


def test_exhausted_outranks_stalled(conn):
    add_criterion(conn, "C1", "s", "false", {}, is_goal=True)
    run_evidence(conn, "C1")
    record_status(conn, "C1")
    for _ in range(5):
        tick(conn)
    add_spend(conn, 9999)
    cfg = {"stagnation_turns": 3, "budget_tokens": 10}
    assert terminal_state(conn, cfg) == "exhausted"


def test_error_never_counts_as_success(conn):
    """Exhausted budget with unmet criteria must not report success."""
    add_criterion(conn, "C1", "s", "false", {}, is_goal=True)
    run_evidence(conn, "C1")
    add_spend(conn, 9999)
    assert terminal_state(conn, {"budget_tokens": 10}) != "success"


def test_r06_orphan_criterion(conn):
    add_criterion(conn, "C1", "s", "false", {}, is_goal=True)
    add_criterion(conn, "C9", "orphan", "false", {})
    run_evidence(conn, "C1")
    run_evidence(conn, "C9")
    assert "R-06" in fired(evaluate_rules(conn, {}))


def test_r06_silent_when_wired_in(conn):
    add_criterion(conn, "C1", "s", "false", {}, is_goal=True)
    add_criterion(conn, "C2", "s", "false", {})
    link(conn, "C1", "C2", "depends_on")
    run_evidence(conn, "C1")
    run_evidence(conn, "C2")
    assert "R-06" not in fired(evaluate_rules(conn, {}))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loopgraph.rules'`

- [ ] **Step 3: Write the implementation**

`src/loopgraph/rules.py`:

```python
"""Threshold rules. Every rule is a pure predicate over derived state."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .db import meta_get, meta_set
from .graph import all_criteria, dependents
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
    conn: sqlite3.Connection, cfg: dict, now: datetime | None = None
) -> list[dict]:
    out: list[dict] = []
    st = statuses(conn, now=now)

    stagnation = cfg.get("stagnation_turns", DEFAULT_STAGNATION_TURNS)
    if st and _turns_since_progress(conn) >= stagnation:
        out.append(
            {"rule": "R-01", "detail": f"no criterion closed in {stagnation} turns"}
        )

    stale = sorted(k for k, v in st.items() if v == "stale")
    if stale:
        out.append({"rule": "R-02", "detail": f"stale: {', '.join(stale)}"})

    ceiling = cfg.get("budget_tokens")
    spend = int(meta_get(conn, "spend", "0"))
    if ceiling is not None and spend > int(ceiling):
        out.append({"rule": "R-04", "detail": f"spend {spend} over ceiling {ceiling}"})

    unproven = sorted(k for k, v in st.items() if v == "unproven")
    if unproven:
        out.append(
            {"rule": "R-05", "detail": f"evidence never completed: {', '.join(unproven)}"}
        )

    orphans = sorted(
        c["id"]
        for c in all_criteria(conn)
        if not c["is_goal"]
        and st.get(c["id"]) != "closed"
        and not dependents(conn, c["id"])
    )
    if orphans:
        out.append(
            {"rule": "R-06", "detail": f"orphan criteria: {', '.join(orphans)}"}
        )

    return out


def terminal_state(
    conn: sqlite3.Connection, cfg: dict, now: datetime | None = None
) -> str | None:
    st = statuses(conn, now=now)
    if not st:
        return "no-op"
    rules = {r["rule"] for r in evaluate_rules(conn, cfg, now=now)}
    if "R-04" in rules:
        return "exhausted"
    if "R-01" in rules:
        return "stalled"
    if all(v == "closed" for v in st.values()):
        return "success"
    return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_rules.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/loopgraph/rules.py tests/test_rules.py
git commit -m "feat: threshold rules and terminal state precedence"
```

---

### Task 8: CLI

**Files:**
- Create: `src/loopgraph/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above
- Produces: `main(argv: list[str] | None = None) -> int` — exit code `0` when the specification is met (`terminal_state == "success"`), `1` when work remains, `2` on usage/internal error.

  Subcommands: `init`, `add`, `link`, `run`, `status`, `next`, `check`, `tick`, `spend`. Global `--db PATH` (default `./.loopgraph.db`), `--json` on `status`/`check`.

  **Exit code 0 means the specification is met.** The gate depends on this.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
import json
import pytest
from loopgraph.cli import main


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "g.db")


def run(db, *args):
    return main(["--db", db, *args])


def test_check_exits_nonzero_while_work_remains(db, capsys):
    run(db, "add", "C1", "--statement", "s", "--cmd", "false")
    assert run(db, "check") == 1
    assert "C1" in capsys.readouterr().out


def test_check_exits_zero_when_specification_met(db):
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--goal")
    run(db, "run")
    assert run(db, "check") == 0


def test_unproven_criterion_keeps_check_nonzero(db):
    """Never-run evidence must not read as met."""
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--goal")
    assert run(db, "check") == 1


def test_status_json_shape(db, capsys):
    run(db, "add", "C1", "--statement", "lake has rows", "--cmd", "echo 5",
        "--expect", '{"stdout_int_gte": 1}', "--goal")
    run(db, "run")
    run(db, "status", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["statuses"] == {"C1": "closed"}
    assert payload["terminal_state"] == "success"
    assert payload["workable"] == []


def test_next_lists_only_unblocked(db, capsys):
    run(db, "add", "C1", "--statement", "s", "--cmd", "false")
    run(db, "add", "C2", "--statement", "s", "--cmd", "false")
    run(db, "link", "C2", "C1")
    run(db, "run")
    run(db, "next")
    assert capsys.readouterr().out.split() == ["C1"]


def test_check_reports_real_command_output(db, capsys):
    run(db, "add", "C1", "--statement", "s", "--cmd", "echo boom; exit 3")
    run(db, "run")
    run(db, "check")
    assert "boom" in capsys.readouterr().out


def test_bad_expect_json_is_usage_error(db):
    assert run(db, "add", "C1", "--statement", "s", "--cmd", "true",
               "--expect", "not json") == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loopgraph.cli'`

- [ ] **Step 3: Write the implementation**

`src/loopgraph/cli.py`:

```python
"""Command line entry point. Exit 0 means the specification is met."""

from __future__ import annotations

import argparse
import json
import sys

from .db import open_db
from .evidence import run_evidence
from .graph import add_criterion, all_criteria, link
from .rules import add_spend, evaluate_rules, terminal_state, tick
from .state import blocked, record_status, statuses, workable


def _report(conn, cfg) -> dict:
    return {
        "statuses": statuses(conn),
        "workable": workable(conn),
        "blocked": blocked(conn),
        "rules": evaluate_rules(conn, cfg),
        "terminal_state": terminal_state(conn, cfg),
    }


def _print_human(conn, report) -> None:
    st = report["statuses"]
    counts = {s: sum(1 for v in st.values() if v == s) for s in set(st.values())}
    print(" ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty")
    for cid, status in sorted(st.items()):
        if status == "closed":
            continue
        run = conn.execute(
            "SELECT * FROM runs WHERE criterion_id=? ORDER BY id DESC LIMIT 1",
            (cid,),
        ).fetchone()
        detail = ""
        if run is not None:
            tail = (run["stdout"] or "").strip().splitlines()[-3:]
            detail = f" exit={run['exit_code']} {' | '.join(tail)}"
        print(f"{cid} {status}:{detail}")
    for cid, deps in sorted(report["blocked"].items()):
        print(f"{cid} blocked by {', '.join(deps)}")
    for rule in report["rules"]:
        print(f"{rule['rule']} {rule['detail']}")
    print(f"terminal_state={report['terminal_state']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="loopgraph")
    p.add_argument("--db", default=".loopgraph.db")
    p.add_argument("--stagnation-turns", type=int, default=3)
    p.add_argument("--budget-tokens", type=int, default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    a = sub.add_parser("add")
    a.add_argument("id")
    a.add_argument("--statement", required=True)
    a.add_argument("--cmd", required=True)
    a.add_argument("--expect", default="{}")
    a.add_argument("--staleness", type=int, default=None)
    a.add_argument("--timeout", type=int, default=120)
    a.add_argument("--goal", action="store_true")

    lk = sub.add_parser("link")
    lk.add_argument("src")
    lk.add_argument("dst")
    lk.add_argument("--rel", default="depends_on")

    r = sub.add_parser("run")
    r.add_argument("id", nargs="?")

    for name in ("status", "check"):
        s = sub.add_parser(name)
        s.add_argument("--json", action="store_true")

    sub.add_parser("next")
    sub.add_parser("tick")
    sp = sub.add_parser("spend")
    sp.add_argument("tokens", type=int)

    args = p.parse_args(argv)
    cfg = {
        "stagnation_turns": args.stagnation_turns,
        "budget_tokens": args.budget_tokens,
    }
    conn = open_db(args.db)

    if args.cmd == "init":
        return 0

    if args.cmd == "add":
        try:
            expect = json.loads(args.expect)
        except json.JSONDecodeError as exc:
            print(f"--expect is not valid JSON: {exc}", file=sys.stderr)
            return 2
        add_criterion(
            conn, args.id, args.statement, args.cmd, expect,
            staleness_window_s=args.staleness, timeout_s=args.timeout,
            is_goal=args.goal,
        )
        return 1

    if args.cmd == "link":
        link(conn, args.src, args.dst, args.rel)
        return 1

    if args.cmd == "run":
        targets = [args.id] if args.id else [c["id"] for c in all_criteria(conn)]
        for cid in targets:
            run_evidence(conn, cid)
            record_status(conn, cid)
        return 0 if terminal_state(conn, cfg) == "success" else 1

    if args.cmd == "next":
        for cid in workable(conn):
            print(cid)
        return 0 if not workable(conn) else 1

    if args.cmd == "tick":
        print(tick(conn))
        return 1

    if args.cmd == "spend":
        print(add_spend(conn, args.tokens))
        return 1

    report = _report(conn, cfg)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(conn, report)
    return 0 if report["terminal_state"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -v`
Expected: 64 passed (db 9, graph 7, evidence 16, state 13, rules 12, cli 7)

- [ ] **Step 6: Prove it end to end by hand**

```bash
cd /tmp && rm -f demo.db
uv run --project ~/src/loopgraph loopgraph --db demo.db \
  add C1 --statement "tests pass" --cmd "true" --goal
uv run --project ~/src/loopgraph loopgraph --db demo.db check; echo "exit=$?"
# expect: exit=1, C1 unproven, R-05 fires
uv run --project ~/src/loopgraph loopgraph --db demo.db run
uv run --project ~/src/loopgraph loopgraph --db demo.db check; echo "exit=$?"
# expect: exit=0, terminal_state=success
```

- [ ] **Step 7: Commit**

```bash
git add src/loopgraph/cli.py tests/test_cli.py
git commit -m "feat: cli with exit 0 meaning specification met"
```

---

## Self-review notes

**Spec coverage.** §5.1 criterion schema → Task 3. §5.2 edges → Task 3 (`owned_by`/`evidenced_by`/`escalates_to` are storable via `link` but unused until the hooks plan). §5.3 delta events → Task 2. §6.2 termination → Tasks 5–7. §6.3 gate safety → **hooks plan, not here.** §7 fan-out → hooks plan. §8 rules → Task 7. §9 model roles, §10 security, §11 metrics → later plans. §13 SQLite/WAL → Task 1.

**Deliberately out of scope:** R-03 age-based blocked escalation, `contested` terminal state, and cost-per-accepted-change reporting. Each needs turn/spend data the harness supplies; they belong with the hooks and metrics plans.

**Type consistency:** `derive_status` returns the same four strings consumed by `workable`, `blocked`, `evaluate_rules` and `_print_human`. `terminal_state` returns `str | None` everywhere; the CLI compares against `"success"` only. `evaluate_rules` returns `list[dict]` with keys `rule` and `detail`, consumed as such in `_print_human` and in the `fired()` test helper.

**Progress marker:** stagnation is measured by stamping `meta['turns_at_last_progress']` when a new closing delta first becomes visible (`_sync_progress_marker`, called from both `tick` and `_turns_since_progress`). This is why R-01 stays silent when a criterion closes late in a barren stretch — the marker moves forward to the current turn.
