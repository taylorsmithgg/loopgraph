"""The Stop hook itself. It had no test, which is why it shipped inverted:
`stop_hook_active` was read as "blocking impossible" when it actually means
"already continuing from a block", so the gate allowed every ordinary stop and
the drive loop never ran once.
"""

import importlib.util
import io
import json
import os
import sys

import pytest

from loopgraph import coord
from loopgraph.db import meta_get, meta_set, open_db
from loopgraph.graph import add_criterion

HOOK = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "hooks", "loop_gate.py",
)


def _load_hook():
    spec = importlib.util.spec_from_file_location("loop_gate", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """Run the hook against a throwaway db and return (event -> stdout json)."""
    db = str(tmp_path / "gate.db")
    monkeypatch.setattr(coord, "default_db_path", lambda: db)
    mod = _load_hook()

    monkeypatch.setenv("LOOPGRAPH_SESSION", "test")

    def run(**event):
        event.setdefault("hook_event_name", "Stop")
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
        buf = io.StringIO()
        real, sys.stdout = sys.stdout, buf
        try:
            rc = mod.main()
        finally:
            sys.stdout = real
        out = buf.getvalue().strip()
        return rc, (json.loads(out) if out else {})

    run.db = db
    return run


def _criterion(db, cid="C1", cmd="false"):
    """Owned by this session: an unowned goal is deliberately not enforced
    (see test_spec_demand), so leaving it unstamped would test nothing."""
    conn = open_db(db)
    add_criterion(conn, cid, f"{cid} holds", cmd, {})
    coord.set_node_flags(conn, cid, session=coord.session_key() or "test")
    return conn


def test_fresh_stop_with_open_criterion_blocks(gate):
    """The regression. stop_hook_active is FALSE on an ordinary first stop --
    the one moment blocking works. The old gate returned 0 right here."""
    _criterion(gate.db)
    rc, out = gate(stop_hook_active=False)
    assert rc == 0
    assert out.get("decision") == "block"
    assert "C1" in out["reason"]


def test_missing_stop_hook_active_still_blocks(gate):
    _criterion(gate.db)
    _, out = gate()
    assert out.get("decision") == "block"


def test_continuation_keeps_blocking_and_counts_up(gate):
    """stop_hook_active=True is a continuation, not a stop sign: the gate has
    its own cap, so it must keep driving rather than fold on block two."""
    _criterion(gate.db)
    _, first = gate(stop_hook_active=False)
    _, second = gate(stop_hook_active=True)
    assert first["reason"].startswith("loopgraph: specification not met (block 1/")
    assert second["reason"].startswith("loopgraph: specification not met (block 2/")


def test_fresh_stop_resets_the_block_count(gate):
    conn = _criterion(gate.db)
    meta_set(conn, "consecutive_blocks", "6")
    _, out = gate(stop_hook_active=False)
    assert "(block 1/" in out["reason"]


def test_gives_up_at_the_cap_without_claiming_success(gate, monkeypatch):
    monkeypatch.setenv("LOOPGRAPH_MAX_BLOCKS", "2")
    conn = _criterion(gate.db)
    gate(stop_hook_active=False)
    gate(stop_hook_active=True)
    _, out = gate(stop_hook_active=True)
    assert "decision" not in out                      # stop is allowed
    assert "giving up" in out["systemMessage"]
    assert "stalled" in out["systemMessage"]
    assert meta_get(conn, "consecutive_blocks") == "0"


def test_met_specification_allows_stop_silently(gate):
    conn = _criterion(gate.db, cmd="true")
    meta_set(conn, "consecutive_blocks", "3")
    rc, out = gate(stop_hook_active=False)
    assert (rc, out) == (0, {})
    assert meta_get(conn, "consecutive_blocks") == "0"


def test_no_criteria_is_silent(gate):
    rc, out = gate(stop_hook_active=False)
    assert (rc, out) == (0, {})


def test_loop_disabled_allows_stop(gate, monkeypatch):
    _criterion(gate.db)
    monkeypatch.setenv("LOOPGRAPH_LOOP", "0")
    rc, out = gate(stop_hook_active=False)
    assert (rc, out) == (0, {})


def test_turn_counter_advances_so_stagnation_can_fire(gate):
    """R-01 reads a turn counter nothing was incrementing, so 'stalled' could
    never be reached from a live session."""
    conn = _criterion(gate.db)
    gate(stop_hook_active=False)
    gate(stop_hook_active=True)
    assert int(meta_get(conn, "turns", "0")) == 2


def test_broken_gate_fails_open(gate, monkeypatch):
    _criterion(gate.db)
    monkeypatch.setattr(coord, "loop_enabled", lambda conn: (_ for _ in ()).throw(RuntimeError("boom")))
    rc, out = gate(stop_hook_active=False)
    assert rc == 0
    assert "decision" not in out
    assert "loop gate error" in out["systemMessage"]


def test_stagnation_governs_the_drive_not_the_block_cap(gate, monkeypatch):
    """Raising LOOPGRAPH_MAX_BLOCKS from 5 to 20 achieved nothing on its own:
    with nothing closing, R-01 fires after STAGNATION_TURNS and ends the drive
    at 7 blocks, cap or no cap. Two limits, the smaller one silently
    governing, and the documented one never reached."""
    monkeypatch.setenv("LOOPGRAPH_MAX_BLOCKS", "20")
    _criterion(gate.db, cmd="false")
    blocks = 0
    _, out = gate(stop_hook_active=False)
    while out.get("decision") == "block":
        blocks += 1
        assert blocks <= 20, "blocked past its own cap"
        _, out = gate(stop_hook_active=True)
    mod = _load_hook()
    assert blocks == mod.STAGNATION_TURNS - 1
    assert "stalled" in out["systemMessage"]


def test_the_cap_is_the_backstop_when_work_keeps_closing(gate, monkeypatch):
    """Progress resets stagnation, so a session that keeps closing criteria
    can drive indefinitely -- and then the block cap is what bounds it."""
    monkeypatch.setenv("LOOPGRAPH_MAX_BLOCKS", "3")
    conn = _criterion(gate.db, cmd="false")
    blocks = 0
    _, out = gate(stop_hook_active=False)
    while out.get("decision") == "block":
        blocks += 1
        # Something closes every turn, so R-01 never fires.
        add_criterion(conn, f"done{blocks}", "closed work", "true", {})
        coord.set_node_flags(conn, f"done{blocks}", session=coord.session_key() or "test")
        _, out = gate(stop_hook_active=True)
    assert blocks == 3 and "giving up" in out["systemMessage"]


def test_the_gate_stays_under_the_harness_ceiling(gate, monkeypatch):
    """Claude Code overrides at CLAUDE_CODE_STOP_HOOK_BLOCK_CAP and ends the
    turn with its own message. Blocking past it means the harness cuts the
    drive off mid-flight instead of loopgraph naming the terminal state."""
    monkeypatch.setenv("LOOPGRAPH_MAX_BLOCKS", "20")
    monkeypatch.setenv("CLAUDE_CODE_STOP_HOOK_BLOCK_CAP", "25")
    _criterion(gate.db, cmd="false")
    from loopgraph import coord
    assert coord.max_blocks() < int(os.environ["CLAUDE_CODE_STOP_HOOK_BLOCK_CAP"])


def _foreign_criterion(db, cid="C1", cmd="false"):
    """Open, and owned by a session that is not this one -- the shape that
    produced 998 repeats of the same note across this machine."""
    conn = open_db(db)
    add_criterion(conn, cid, f"{cid} holds", cmd, {})
    coord.set_node_flags(conn, cid, session="some-dead-session")
    return conn


def test_loose_note_is_said_once_not_every_stop(gate):
    """The nag that could not be ended. With no criteria of our own, this
    path runs on EVERY stop of a long session; unsuppressed it repeated the
    same two ids 432 times in one real session and changed nothing."""
    _foreign_criterion(gate.db)
    _, first = gate(stop_hook_active=False)
    assert "NOT enforced" in first.get("systemMessage", "")
    for _ in range(5):
        _, again = gate(stop_hook_active=False)
        assert again == {}, "the loose note repeated on a later stop"


def test_loose_note_speaks_again_when_the_set_changes(gate):
    """Suppression is keyed by the set, not by 'said once ever'. A NEW
    unenforced criterion is news, or silence becomes the old bug again."""
    _foreign_criterion(gate.db, "C1")
    _, first = gate(stop_hook_active=False)
    assert "C1" in first.get("systemMessage", "")
    _foreign_criterion(gate.db, "C2")
    _, second = gate(stop_hook_active=False)
    assert "C2" in second.get("systemMessage", "")


def test_loose_note_carries_age(gate):
    """adopt-or-drop is undecidable from the id alone: an hour-old criterion
    from a live sibling reads differently from a three-week-old orphan."""
    _foreign_criterion(gate.db)
    _, out = gate(stop_hook_active=False)
    assert "d old" in out.get("systemMessage", "")


def test_status_stays_quiet_when_a_sibling_stopped_last(gate, monkeypatch):
    """$HOME is not a git repo, so every session there shares one db and
    `last_gate_session` holds whoever stopped most recently. Comparing against
    it alone reported MISMATCH -- "criteria added here will not bind it" -- at
    a session whose identity was fine, and sent a reader after a bug that did
    not exist."""
    from loopgraph.cli import _gate_line
    gate(stop_hook_active=False)                    # our own gate runs: proof
    conn = open_db(gate.db)
    meta_set(conn, "last_gate_session", "some-sibling-session")
    assert "MISMATCH" not in _gate_line(conn, gate.db)


def test_status_says_unverified_before_this_session_has_stopped(gate):
    """Not yet provable is not the same as broken, and must not be worded as
    though the gate has stopped gating."""
    from loopgraph.cli import _gate_line
    conn = open_db(gate.db)
    meta_set(conn, "gate_seen_any", "1")
    meta_set(conn, "last_gate_session", "some-sibling-session")
    line = _gate_line(conn, gate.db)
    assert "unverified" in line
    assert "will not bind" not in line


def test_status_is_silent_when_no_gate_has_ever_run(gate):
    from loopgraph.cli import _gate_line
    conn = open_db(gate.db)
    meta_set(conn, "last_gate_session", "some-sibling-session")
    assert "MISMATCH" not in _gate_line(conn, gate.db)
