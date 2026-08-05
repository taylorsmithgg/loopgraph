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
        "SELECT * FROM deltas WHERE change_type='OWNERSHIP_CHANGE' ORDER BY logical_clock"
    ).fetchone()
    assert row["entity_id"] == "C1" and row["old_value"] is None and row["new_value"] == "agent-7"
    # Second transition to verify old_value tracking
    set_owner(conn, "C1", "agent-8")
    assert get_node(conn, "C1")["owner"] == "agent-8"
    row2 = conn.execute(
        "SELECT * FROM deltas WHERE change_type='OWNERSHIP_CHANGE' ORDER BY logical_clock DESC LIMIT 1"
    ).fetchone()
    assert row2["entity_id"] == "C1" and row2["old_value"] == "agent-7" and row2["new_value"] == "agent-8"


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


def test_set_owner_raises_on_missing_criterion(conn):
    with pytest.raises(ValueError, match="no such criterion: C99"):
        set_owner(conn, "C99", "agent-7")
    # Verify no delta was emitted by the failed call
    row = conn.execute(
        "SELECT * FROM deltas WHERE entity_id='C99'"
    ).fetchone()
    assert row is None


def test_has_cycle_diamond_dag(conn):
    """Diamond DAG (reconvergent edges) must not be flagged as cyclic."""
    for cid in ("C1", "C2", "C3", "C4"):
        add_criterion(conn, cid, "s", "true", {})
    link(conn, "C1", "C2", "depends_on")
    link(conn, "C1", "C3", "depends_on")
    link(conn, "C2", "C4", "depends_on")
    link(conn, "C3", "C4", "depends_on")
    assert has_cycle(conn) is None


def test_link_rejects_unknown_rel_type(conn):
    """`--rel depends-on` (hyphen, not the real `depends_on`) must be
    rejected, not silently stored as an edge no traversal reads."""
    add_criterion(conn, "C1", "s", "true", {})
    add_criterion(conn, "C2", "s", "true", {})
    with pytest.raises(ValueError):
        link(conn, "C2", "C1", "depends-on")
    assert dependencies(conn, "C2") == []
    assert dependents(conn, "C1") == []
    row = conn.execute("SELECT count(*) AS n FROM edges").fetchone()
    assert row["n"] == 0


def test_link_accepts_every_allowed_rel_type(conn):
    for cid in ("C1", "C2"):
        add_criterion(conn, cid, "s", "true", {})
    for rel in ("depends_on", "blocks", "owned_by", "evidenced_by", "escalates_to"):
        link(conn, "C1", "C2", rel)
    row = conn.execute("SELECT count(*) AS n FROM edges").fetchone()
    assert row["n"] == 5


def test_self_loop_is_detected_as_a_cycle(conn):
    """`link C1 C1` is accepted by link() (self-dependency is not
    rejected at authoring time), so has_cycle must still catch it."""
    add_criterion(conn, "C1", "s", "true", {})
    link(conn, "C1", "C1", "depends_on")
    assert has_cycle(conn) is not None


def test_has_cycle_disjoint_components_with_cycle(conn):
    """Cycle in second component must be detected even when first is clean."""
    for cid in ("C1", "C2", "C8", "C9"):
        add_criterion(conn, cid, "s", "true", {})
    # Clean chain: C1 depends_on C2
    link(conn, "C1", "C2", "depends_on")
    # Separate 2-cycle: C8 depends_on C9, C9 depends_on C8
    link(conn, "C8", "C9", "depends_on")
    link(conn, "C9", "C8", "depends_on")
    assert has_cycle(conn) is not None
