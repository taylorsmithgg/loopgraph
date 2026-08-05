"""`add` as the entailment gate, plus the withdrawal and waiver paths.

A criterion that is green at authoring time is the whole failure dressed up
as compliance: the graph looks specified, `status` looks healthy, and the
gate has nothing to hold.
"""

import pytest

from loopgraph import coord
from loopgraph.cli import main
from loopgraph.db import open_db
from loopgraph.graph import all_criteria


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "g.db")


def run(db, *args):
    return main(["--db", db, *args])


def test_green_check_is_refused(db, capsys):
    rc = run(db, "add", "C1", "--statement", "s", "--cmd", "true")
    assert rc == 2
    assert "already passes" in capsys.readouterr().err


def test_refused_criterion_is_not_left_behind(db):
    run(db, "add", "C1", "--statement", "s", "--cmd", "true")
    assert all_criteria(open_db(db)) == []


def test_red_check_is_accepted_and_its_first_evidence_is_recorded(db, capsys):
    assert run(db, "add", "C1", "--statement", "s", "--cmd", "false") == 0
    run(db, "status")
    # Not "unproven": the gate already ran it, so the graph starts honest.
    assert "C1 open" in capsys.readouterr().out


def test_allow_green_is_the_stated_exception(db):
    assert run(db, "add", "C1", "--statement", "s", "--cmd", "true",
               "--allow-green") == 0


def test_guard_accepts_green_and_is_marked_as_a_fence(db):
    assert run(db, "add", "G1", "--statement", "suite passes", "--cmd", "true",
               "--guard") == 0
    assert coord.node_flags(open_db(db), "G1").get("guard") is True


def test_status_shows_where_a_criterion_came_from(db, capsys):
    """A gate holding the turn open against a check the user never wrote has
    to say so, or it is indistinguishable from a malfunction."""
    run(db, "add", "G1", "--statement", "suite passes", "--cmd", "false",
        "--guard")
    coord.set_node_flags(open_db(db), "G1", origin="auto")
    run(db, "run")
    run(db, "status")
    assert "[guard]" in capsys.readouterr().out


def test_narrow_check_is_accepted_but_called_out(db, capsys):
    run(db, "add", "C1", "--statement", "s",
        "--cmd", 'grep -q "persistent = true" nonexistent.yml')
    err = capsys.readouterr().err
    assert "narrow" in err and "weakness" in err


def test_wide_check_draws_no_complaint(db, capsys):
    run(db, "add", "C1", "--statement", "s", "--cmd", "pytest -q /nonexistent")
    assert "narrow" not in capsys.readouterr().err


def test_drop_removes_a_criterion(db):
    run(db, "add", "C1", "--statement", "s", "--cmd", "false")
    assert run(db, "drop", "C1") == 0
    assert all_criteria(open_db(db)) == []


def test_drop_of_an_unknown_id_is_a_usage_error(db, capsys):
    assert run(db, "drop", "nope") == 2
    assert "no such criterion" in capsys.readouterr().err


def test_drop_records_the_withdrawal(db):
    run(db, "add", "C1", "--statement", "s", "--cmd", "false")
    run(db, "drop", "C1")
    conn = open_db(db)
    kinds = [r["change_type"] for r in conn.execute(
        "SELECT change_type FROM deltas WHERE entity_id='C1'")]
    assert "CRITERION_DROPPED" in kinds


def test_noop_waives_a_pending_goal_on_the_record(db):
    conn = open_db(db)
    coord.note_goal(conn, "what does this function do")
    assert run(db, "noop", "--reason", "a question, nothing to check") == 0
    conn = open_db(db)
    assert coord.goal_pending(conn) == ""
    from loopgraph.db import meta_get
    assert "question" in meta_get(conn, "goal_waived_reason", "")


def test_check_answers_for_this_sessions_specification_only(db, monkeypatch):
    """A neighbouring session's open goal must not make every `check` here
    report failure forever."""
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    run(db, "add", "MINE", "--statement", "mine", "--cmd", "true", "--allow-green")
    run(db, "run", "MINE")
    conn = open_db(db)
    from loopgraph.graph import add_criterion
    add_criterion(conn, "THEIRS", "their goal", "false", {})
    coord.set_node_flags(conn, "THEIRS", session="session-A")
    assert run(db, "check") == 0


def test_status_still_shows_what_check_ignores(db, monkeypatch, capsys):
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    conn = open_db(db)
    from loopgraph.graph import add_criterion
    add_criterion(conn, "THEIRS", "their goal", "false", {})
    coord.set_node_flags(conn, "THEIRS", session="session-A")
    run(db, "status")
    out = capsys.readouterr().out
    assert "NOT enforced" in out and "owned by session-A" in out


def test_adopt_all_takes_on_every_loose_criterion(db, monkeypatch, capsys):
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    conn = open_db(db)
    from loopgraph.graph import add_criterion
    add_criterion(conn, "LOOSE", "nobody's goal", "false", {})
    assert run(db, "adopt", "--all") == 0
    assert coord.owned_here(open_db(db), "LOOSE") is True


def test_a_global_criterion_is_not_stamped_to_one_session(db, monkeypatch):
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    run(db, "add", "ALL", "--statement", "everyone's", "--cmd", "false", "--global")
    conn = open_db(db)
    assert coord.node_flags(conn, "ALL").get("session", "") == ""
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-C")
    assert coord.owned_here(conn, "ALL") is True


def test_drop_takes_several_ids(db):
    run(db, "add", "A", "--statement", "a", "--cmd", "false")
    run(db, "add", "B", "--statement", "b", "--cmd", "false")
    assert run(db, "drop", "A", "B") == 0
    assert all_criteria(open_db(db)) == []


def test_check_says_why_it_failed_when_nothing_is_ours(db, monkeypatch, capsys):
    """Exit 1 with no word said reads as "the work failed" when it means
    "none of this is yours"."""
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    conn = open_db(db)
    from loopgraph.graph import add_criterion
    add_criterion(conn, "THEIRS", "their goal", "false", {})
    coord.set_node_flags(conn, "THEIRS", session="session-A")
    assert run(db, "check") == 1
    assert "no criteria are owned by this session" in capsys.readouterr().err
