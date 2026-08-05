import sqlite3
import pytest
from loopgraph.db import open_db
from loopgraph.graph import add_criterion, get_node


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
    """Reopening an existing db must not throw AND must not clobber
    existing data -- CREATE TABLE IF NOT EXISTS makes the schema
    idempotent, but the original version of this test never inserted a
    row before the second open_db, so it could not tell "schema
    survives" apart from "schema gets silently rebuilt from scratch"."""
    path = tmp_path / "g.db"
    first = open_db(path)
    add_criterion(first, "C1", "row must survive reopen", "true", {})
    first.close()

    conn = open_db(path)
    row = get_node(conn, "C1")
    assert row is not None
    assert row["statement"] == "row must survive reopen"


def test_foreign_keys_enforced(tmp_path):
    conn = open_db(tmp_path / "g.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO edges (src, dst, rel_type, created_at) "
            "VALUES ('nope', 'alsonope', 'depends_on', '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()


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


def test_deltas_immutable_after_insert(tmp_path):
    """Emit deltas, snapshot all columns, emit more, verify originals unchanged."""
    conn = open_db(tmp_path / "g.db")

    emit_delta(conn, "C1", "STATE_TRANSITION", "open", "closed")
    emit_delta(conn, "C2", "STALENESS", None, "stale")

    snapshot_before = {
        r["id"]: {k: r[k] for k in r.keys()}
        for r in conn.execute("SELECT * FROM deltas WHERE id IN (1, 2)")
    }

    emit_delta(conn, "C3", "THRESHOLD_BREACH", "low", "high")
    emit_delta(conn, "C4", "DEPENDENCY_RISK", "none", "critical")

    snapshot_after = {
        r["id"]: {k: r[k] for k in r.keys()}
        for r in conn.execute("SELECT * FROM deltas WHERE id IN (1, 2)")
    }

    assert snapshot_before == snapshot_after

    all_ids = [r["id"] for r in conn.execute("SELECT id FROM deltas ORDER BY id")]
    for i in range(len(all_ids) - 1):
        assert all_ids[i] < all_ids[i + 1]


def test_meta_roundtrip(tmp_path):
    conn = open_db(tmp_path / "g.db")
    assert meta_get(conn, "turns") is None
    meta_set(conn, "turns", "7")
    assert meta_get(conn, "turns") == "7"
    meta_set(conn, "turns", "8")
    assert meta_get(conn, "turns") == "8"


def test_failed_emit_delta_does_not_burn_clock(tmp_path):
    """Clock is not incremented if emit_delta fails; no orphaned clock values."""
    conn = open_db(tmp_path / "g.db")

    initial_clock = next_clock(conn)
    assert initial_clock == 1

    with pytest.raises(ValueError):
        emit_delta(conn, "C1", "INVALID_TYPE", None, None)

    next_clock_after_type_fail = next_clock(conn)
    assert next_clock_after_type_fail == 2
    assert list(conn.execute("SELECT count(*) FROM deltas"))[0][0] == 0

    current_clock = next_clock(conn)
    assert current_clock == 3

    with pytest.raises(sqlite3.IntegrityError):
        emit_delta(conn, None, "STATE_TRANSITION", "a", "b")

    next_clock_after_constraint_fail = next_clock(conn)
    assert next_clock_after_constraint_fail == 4
    assert list(conn.execute("SELECT count(*) FROM deltas"))[0][0] == 0
