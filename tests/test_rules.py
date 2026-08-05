from datetime import datetime, timedelta

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


# --- Mutation-testing gap-fill tests -------------------------------------
# The tests above (verbatim from the brief) all pass against several
# deliberately-broken mutants of the implementation. Each test below was
# added because it is the smallest scenario that fails under one specific
# mutant and passes under the correct implementation. See task-7-report.md
# for the full mutation table.


def test_progress_credited_to_turn_it_closed_in(conn):
    """Catches: dropping _sync_progress_marker from tick() only.

    The closing delta appears while turns==2 (before the tick that would
    advance to turn 3). If tick() doesn't sync BEFORE incrementing, the
    marker only gets stamped whenever evaluate_rules() next runs -- by
    which point turns has already raced ahead, making stagnation look
    falsely recent (or never firing at all).

    A second, still-open criterion keeps R-01 reachable under the I1 fix
    (R-01 can never fire once everything is closed -- see
    test_r01_silent_when_everything_closed) while preserving the original
    scenario: C1 closes at turn 2, then 5 barren turns pass.
    """
    add_criterion(conn, "C1", "s", "true", {}, is_goal=True)
    add_criterion(conn, "C2", "s", "false", {}, is_goal=True)
    tick(conn)
    tick(conn)
    run_evidence(conn, "C1")
    record_status(conn, "C1")
    run_evidence(conn, "C2")
    record_status(conn, "C2")
    for _ in range(5):
        tick(conn)
    assert "R-01" in fired(evaluate_rules(conn, {"stagnation_turns": 3}))


def test_r04_budget_boundary_is_exclusive(conn):
    """Catches: changing R-04's strict `>` to `>=`.

    Spend exactly equal to the ceiling must not fire -- only spend that
    exceeds it.
    """
    add_criterion(conn, "C1", "s", "false", {}, is_goal=True)
    run_evidence(conn, "C1")
    add_spend(conn, 1000)
    assert "R-04" not in fired(evaluate_rules(conn, {"budget_tokens": 1000}))
    add_spend(conn, 1)
    assert "R-04" in fired(evaluate_rules(conn, {"budget_tokens": 1000}))


def test_all_closed_but_over_budget_is_not_success(conn):
    """Catches: terminal_state reporting success whenever every criterion
    is closed, without first checking whether the budget was exhausted.

    An exhausted budget must never be reported as success, even if every
    criterion happens to be closed.
    """
    add_criterion(conn, "C1", "s", "true", {}, is_goal=True)
    run_evidence(conn, "C1")
    record_status(conn, "C1")
    add_spend(conn, 9999)
    assert terminal_state(conn, {"budget_tokens": 10}) == "exhausted"


def test_r01_silent_on_empty_graph_after_ticks(conn):
    """Catches: dropping the `if st and ...` guard on R-01.

    An empty graph that has been ticked past the stagnation threshold must
    never fire R-01 -- there is no criterion to have made (or failed to
    make) progress.
    """
    for _ in range(5):
        tick(conn)
    assert "R-01" not in fired(evaluate_rules(conn, {"stagnation_turns": 3}))


# --- R-02 (staleness) coverage --------------------------------------------
# The brief's 12 tests, and the four gap-fill tests above, never set
# staleness_window_s, so derive_status() never returns "stale" anywhere in
# this file and the R-02 block in evaluate_rules was never executed by any
# test. These three tests close that gap: R-02 firing, R-02 staying quiet,
# and confirming R-02 is diagnostic-only (it must never move terminal_state
# off of `None` on its own).


def test_r02_staleness_fires_past_window(conn):
    add_criterion(conn, "C1", "s", "true", {}, staleness_window_s=10, is_goal=True)
    run_evidence(conn, "C1")
    ended = datetime.fromisoformat(
        conn.execute(
            "SELECT ended_at FROM runs WHERE criterion_id='C1'"
        ).fetchone()["ended_at"]
    )
    past_window = ended + timedelta(seconds=20)
    results = evaluate_rules(conn, {}, now=past_window)
    assert "R-02" in fired(results)
    detail = next(r["detail"] for r in results if r["rule"] == "R-02")
    assert "C1" in detail


def test_r02_silent_within_window(conn):
    add_criterion(conn, "C1", "s", "true", {}, staleness_window_s=100, is_goal=True)
    run_evidence(conn, "C1")
    ended = datetime.fromisoformat(
        conn.execute(
            "SELECT ended_at FROM runs WHERE criterion_id='C1'"
        ).fetchone()["ended_at"]
    )
    within_window = ended + timedelta(seconds=5)
    assert "R-02" not in fired(evaluate_rules(conn, {}, now=within_window))


# --- I1: a fully-met specification must not report `stalled` forever ----


def test_r01_silent_when_everything_closed(conn):
    """Nothing left to close is completion, not stagnation. Before this
    guard, R-01 fired forever once every criterion was closed (no closing
    delta ever appears again because there is nothing left to transition),
    permanently masking `success`."""
    add_criterion(conn, "C1", "s", "true", {}, is_goal=True)
    run_evidence(conn, "C1")
    record_status(conn, "C1")
    for _ in range(5):
        tick(conn)
    assert "R-01" not in fired(evaluate_rules(conn, {"stagnation_turns": 3}))
    assert terminal_state(conn, {"stagnation_turns": 3}) == "success"


def test_r01_still_fires_when_something_remains_open(conn):
    """The brake must remain able to fire: a still-open criterion plus N
    barren turns is genuine stagnation, guard or no guard."""
    add_criterion(conn, "C1", "s", "true", {}, is_goal=True)
    add_criterion(conn, "C2", "s", "false", {}, is_goal=True)
    run_evidence(conn, "C1")
    record_status(conn, "C1")
    run_evidence(conn, "C2")
    record_status(conn, "C2")
    for _ in range(5):
        tick(conn)
    assert "R-01" in fired(evaluate_rules(conn, {"stagnation_turns": 3}))
    assert terminal_state(conn, {"stagnation_turns": 3}) == "stalled"


# --- I2: has_cycle is wired into a rule; a cycle is terminal (`blocked`) -


def test_r07_cycle_fires_and_terminal_state_is_blocked(conn):
    add_criterion(conn, "C1", "s", "true", {})
    add_criterion(conn, "C2", "s", "true", {})
    link(conn, "C1", "C2", "depends_on")
    link(conn, "C2", "C1", "depends_on")
    results = evaluate_rules(conn, {})
    assert "R-07" in fired(results)
    detail = next(r["detail"] for r in results if r["rule"] == "R-07")
    assert "C1" in detail and "C2" in detail
    assert terminal_state(conn, {}) == "blocked"


def test_r07_silent_on_acyclic_graph(conn):
    add_criterion(conn, "C1", "s", "true", {})
    add_criterion(conn, "C2", "s", "true", {})
    link(conn, "C1", "C2", "depends_on")
    assert "R-07" not in fired(evaluate_rules(conn, {}))


def test_r02_is_diagnostic_only_and_does_not_change_terminal_state(conn):
    """A stale criterion is not closed, so it can't be success; and
    staleness alone is neither R-01 nor R-04, so it can't be stalled or
    exhausted either. The only correct terminal_state is None (keep
    working) -- pinned explicitly rather than only checking != "success".
    """
    add_criterion(conn, "C1", "s", "true", {}, staleness_window_s=10, is_goal=True)
    run_evidence(conn, "C1")
    ended = datetime.fromisoformat(
        conn.execute(
            "SELECT ended_at FROM runs WHERE criterion_id='C1'"
        ).fetchone()["ended_at"]
    )
    past_window = ended + timedelta(seconds=20)
    assert terminal_state(conn, {}, now=past_window) is None
