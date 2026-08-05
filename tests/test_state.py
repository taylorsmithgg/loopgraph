import threading
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


def test_record_status_concurrent_transition_emits_exactly_one_delta(tmp_path):
    """I6: the read (_last_recorded) and the conditional insert must be
    one atomic unit. Eight threads, each on its own connection to the
    same db file, race to be the first to observe and record C1's
    unproven -> closed transition right after the barrier releases. A
    read-then-write race would let several of them read `previous ==
    "unproven"` before any of them has written, each independently
    deciding "this is new" -- producing duplicate deltas for one actual
    transition. Exactly one must survive."""
    path = tmp_path / "g.db"
    setup_conn = open_db(path)
    add_criterion(setup_conn, "C1", "s", "true", {})
    run_evidence(setup_conn, "C1")
    setup_conn.close()

    n_threads = 8
    barrier = threading.Barrier(n_threads)
    errors = []

    def worker():
        try:
            conn = open_db(path)
            barrier.wait()
            record_status(conn, "C1")
            conn.close()
        except Exception as exc:  # pragma: no cover - surfaced via errors
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"worker thread(s) raised: {errors}"

    verify_conn = open_db(path)
    rows = list(
        verify_conn.execute(
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


# --- Supplementary tests -------------------------------------------------
# The brief's staleness test (test_passing_run_goes_stale_past_window) uses
# a `now` two hours past a one-hour window, so both `age > window` and the
# incorrect `age >= window` agree, and so do `ended_at` and `started_at`
# (they're milliseconds apart for a fast command). Neither mutation is
# caught. These two tests pin the exact boundary and force the two
# timestamps apart so both bugs are observable.


def test_stale_boundary_is_exclusive(conn):
    add_criterion(conn, "C1", "s", "true", {}, staleness_window_s=100)
    run = run_evidence(conn, "C1")
    ended = datetime.fromisoformat(run["ended_at"])
    assert derive_status(conn, "C1", now=ended + timedelta(seconds=100)) == "closed"
    assert (
        derive_status(conn, "C1", now=ended + timedelta(seconds=100, microseconds=1))
        == "stale"
    )


def test_staleness_measured_from_ended_at_not_started_at(conn):
    add_criterion(conn, "C1", "s", "true", {}, staleness_window_s=100)
    run_evidence(conn, "C1")
    now = datetime.now(timezone.utc)
    conn.execute(
        "UPDATE runs SET started_at = ?, ended_at = ? WHERE criterion_id = 'C1'",
        ((now - timedelta(hours=5)).isoformat(), (now - timedelta(seconds=10)).isoformat()),
    )
    # started_at is 5h ago (>> window); ended_at is 10s ago (< window).
    assert derive_status(conn, "C1", now=now) == "closed"


def test_derive_status_raises_for_missing_criterion(conn):
    with pytest.raises(ValueError, match="no such criterion: NOPE"):
        derive_status(conn, "NOPE")


def test_record_status_does_not_fabricate_unproven_self_transition(conn):
    # No completed run at all: repeated calls must stay unproven and must
    # not write a delta, since nothing has actually changed.
    add_criterion(conn, "C1", "s", "true", {})
    assert record_status(conn, "C1") == "unproven"
    assert record_status(conn, "C1") == "unproven"
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM deltas WHERE entity_id='C1'"
        ).fetchone()["n"]
        == 0
    )

    # Only run so far timed out (ok stays NULL): still unproven, still no
    # delta — a timeout must not be recorded as a state change either.
    add_criterion(conn, "C2", "s", "sleep 5", {}, timeout_s=1)
    run_evidence(conn, "C2")
    assert record_status(conn, "C2") == "unproven"
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM deltas WHERE entity_id='C2'"
        ).fetchone()["n"]
        == 0
    )

    # That same criterion later actually succeeds: this is a genuine first
    # transition and must be recorded exactly once.
    conn.execute("UPDATE nodes SET evidence_cmd='true' WHERE id='C2'")
    run_evidence(conn, "C2")
    assert record_status(conn, "C2") == "closed"
    rows = list(
        conn.execute(
            "SELECT * FROM deltas WHERE entity_id='C2' "
            "AND change_type='STATE_TRANSITION'"
        )
    )
    assert len(rows) == 1
    assert rows[0]["old_value"] == "unproven" and rows[0]["new_value"] == "closed"


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


# --- Supplementary tests -------------------------------------------------
# The four tests above never exercise the own-status gate on a criterion
# that already has a settled (closed/stale) status but still has an
# unclosed dependency, because in every given scenario the "own status
# gate" and the "any/all dependency" check happen to agree (C1 always has
# no dependencies, so an empty-dependency check trivially returns the
# right answer regardless of whether the status gate is even present).
# These tests force a case where a criterion's own status is already
# closed or stale while its dependency is still open, so a missing or
# too-narrow status gate in is_blocked/blocked is observable. They also
# cover "unproven" explicitly, since a gate written as `== "open"` instead
# of `in ("open", "unproven")` would otherwise slip through undetected.


def test_closed_own_status_with_open_dependency_is_not_blocked(conn):
    add_criterion(conn, "C1", "s", "false", {})  # dependency, stays open
    add_criterion(conn, "C2", "s", "true", {})  # depender, closes regardless
    link(conn, "C2", "C1", "depends_on")
    run_evidence(conn, "C1")
    run_evidence(conn, "C2")
    assert derive_status(conn, "C2") == "closed"
    assert is_blocked(conn, "C2") is False
    assert blocked(conn) == {}


def test_stale_own_status_with_open_dependency_is_not_blocked(conn):
    add_criterion(conn, "C1", "s", "false", {})  # dependency, stays open
    add_criterion(conn, "C2", "s", "true", {}, staleness_window_s=1)
    link(conn, "C2", "C1", "depends_on")
    run_evidence(conn, "C1")
    run_evidence(conn, "C2")
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    assert derive_status(conn, "C2", now=later) == "stale"
    assert is_blocked(conn, "C2", now=later) is False
    assert blocked(conn, now=later) == {}
    # C2 is workable again (stale), but its dependency C1 is still open,
    # so C2 itself must not appear; only the dependency-free C1 does.
    assert workable(conn, now=later) == ["C1"]


def test_unproven_treated_like_open_for_blocking(conn):
    add_criterion(conn, "C1", "s", "false", {})  # dependency, stays open
    add_criterion(conn, "C2", "s", "true", {})  # depender, never run
    link(conn, "C2", "C1", "depends_on")
    run_evidence(conn, "C1")
    assert derive_status(conn, "C2") == "unproven"
    assert is_blocked(conn, "C2") is True
    assert blocked(conn) == {"C2": ["C1"]}
    assert workable(conn) == ["C1"]
