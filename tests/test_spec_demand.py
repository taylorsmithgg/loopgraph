"""The prompt hook and the Stop hook as one contract.

Declaring nothing was always the cheapest way past a gate that is inert
without criteria. These tests are about that hole being closed and, just as
importantly, about it closing again afterwards - a demand that cannot be
satisfied or waived is worse than the silence it replaced.
"""

import importlib.util
import io
import json
import os
import sys

import pytest

from loopgraph import coord
from loopgraph.db import open_db
from loopgraph.graph import add_criterion

HOOKS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")
GOAL = "make the ingest pipeline stop dropping events on restart"


def _hook(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HOOKS, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Both hooks over one database, with the detached fence stubbed out."""
    db = str(tmp_path / "g.db")
    monkeypatch.setattr(coord, "default_db_path", lambda *a, **k: db)
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-under-test")
    prompt_hook = _hook("spec_prompt")
    gate_hook = _hook("loop_gate")
    monkeypatch.setattr(prompt_hook, "_fence_in_background", lambda: None)

    def call(mod, **event):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
        buf = io.StringIO()
        real, sys.stdout = sys.stdout, buf
        try:
            mod.main()
        finally:
            sys.stdout = real
        out = buf.getvalue().strip()
        return json.loads(out) if out else {}

    class E:
        path = db
        submit = staticmethod(lambda p=GOAL: call(prompt_hook, prompt=p))
        stop = staticmethod(lambda **kw: call(gate_hook, **kw))
        conn = staticmethod(lambda: open_db(db))
    return E


def test_a_stated_goal_is_recorded_as_pending(env):
    env.submit()
    assert coord.goal_pending(env.conn()) == GOAL


def test_stop_refuses_a_turn_that_declared_nothing(env):
    env.submit()
    out = env.stop(stop_hook_active=False)
    assert out["decision"] == "block"
    assert "loopgraph add C1" in out["reason"]
    assert "loopgraph noop" in out["reason"]
    assert GOAL in out["reason"]


def test_declaring_a_criterion_satisfies_the_demand(env):
    env.submit()
    conn = env.conn()
    add_criterion(conn, "C1", "events survive restart", "false", {})
    coord.set_node_flags(conn, "C1", session=coord.session_key())
    out = env.stop(stop_hook_active=False)
    assert "loopgraph add C1" not in out.get("reason", "")
    assert coord.goal_pending(env.conn()) == ""


def test_a_guard_alone_does_not_count_as_a_specification(env):
    """A green suite is not a statement of what this request means."""
    conn = env.conn()
    env.submit()
    add_criterion(conn, "G-tests", "suite passes", "true", {})
    coord.set_node_flags(conn, "G-tests", guard=True, origin="auto")
    out = env.stop(stop_hook_active=False)
    assert "no end-state on record" in out["reason"]


def test_the_waiver_ends_the_demand(env):
    env.submit()
    coord.clear_goal(env.conn(), "a question, nothing to check")
    assert env.stop(stop_hook_active=False) == {}


def test_the_demand_gives_up_rather_than_trapping_the_turn(env):
    env.submit()
    for _ in range(coord.MAX_SPEC_BLOCKS):
        assert env.stop(stop_hook_active=True)["decision"] == "block"
    last = env.stop(stop_hook_active=True)
    assert "decision" not in last
    assert "UNVERIFIED" in last["systemMessage"]
    assert coord.goal_pending(env.conn()) == ""


def test_chatter_states_no_goal(env):
    env.submit("thanks")
    assert coord.goal_pending(env.conn()) == ""
    assert env.stop(stop_hook_active=False) == {}


def test_the_prompt_hook_stays_quiet_once_a_goal_criterion_exists(env):
    add_criterion(env.conn(), "C1", "s", "false", {})
    assert env.submit() == {}


def test_the_prompt_hook_still_speaks_when_only_guards_exist(env):
    conn = env.conn()
    add_criterion(conn, "G-tests", "suite passes", "true", {})
    coord.set_node_flags(conn, "G-tests", guard=True)
    assert "loopgraph add C1" in \
        env.submit()["hookSpecificOutput"]["additionalContext"]


def test_a_second_prompt_does_not_overwrite_the_first_goal(env):
    env.submit()
    env.submit("and also please make it faster while you are in there")
    assert coord.goal_pending(env.conn()) == GOAL


def test_another_sessions_goal_does_not_hold_this_turn(env, monkeypatch):
    """Observed live: two sessions in $HOME, one database, each holding the
    other's turn open on criteria it had never heard of."""
    conn = env.conn()
    add_criterion(conn, "THEIRS", "their goal", "false", {})
    coord.set_node_flags(conn, "THEIRS", session="session-A")
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    coord.clear_goal(conn)
    assert "decision" not in env.stop(stop_hook_active=False)


def test_my_own_goal_still_holds_my_turn(env, monkeypatch):
    conn = env.conn()
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    add_criterion(conn, "MINE", "my goal", "false", {})
    coord.set_node_flags(conn, "MINE", session="session-B")
    assert env.stop(stop_hook_active=False)["decision"] == "block"


def test_a_guard_binds_every_session(env, monkeypatch):
    """A broken suite is everyone's problem, whoever fenced it."""
    conn = env.conn()
    add_criterion(conn, "G-tests", "suite passes", "false", {})
    coord.set_node_flags(conn, "G-tests", guard=True, session="session-A")
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    assert env.stop(stop_hook_active=False)["decision"] == "block"


def test_an_unowned_criterion_does_not_hold_the_turn_but_is_named(env, monkeypatch):
    """Enforcing unowned criteria was tried and it is the worse failure: a
    session that dies mid-goal leaves a permanent hostage behind. Not
    enforced, never unmentioned."""
    conn = env.conn()
    add_criterion(conn, "LEGACY", "someone's old goal", "false", {})
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    out = env.stop(stop_hook_active=False)
    assert "decision" not in out
    assert "LEGACY" in out["systemMessage"]
    assert "no owner recorded" in out["systemMessage"]
    assert "loopgraph adopt" in out["systemMessage"]


def test_another_sessions_open_goal_is_named_not_enforced(env, monkeypatch):
    conn = env.conn()
    add_criterion(conn, "THEIRS", "their goal", "false", {})
    coord.set_node_flags(conn, "THEIRS", session="session-A")
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    out = env.stop(stop_hook_active=False)
    assert "decision" not in out
    assert "owned by session-A" in out["systemMessage"]


def test_adopting_a_loose_criterion_makes_it_bind(env, monkeypatch):
    conn = env.conn()
    add_criterion(conn, "LEGACY", "old goal", "false", {})
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    assert coord.adopt(conn, "LEGACY") is True
    assert env.stop(stop_hook_active=False)["decision"] == "block"


def test_a_global_criterion_binds_every_session(env, monkeypatch):
    conn = env.conn()
    add_criterion(conn, "ALLOFUS", "everyone's goal", "false", {})
    coord.set_node_flags(conn, "ALLOFUS", session="session-A", **{"global": True})
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    assert env.stop(stop_hook_active=False)["decision"] == "block"


def test_without_a_session_identity_everything_still_binds(env, monkeypatch):
    """A gate that quietly stops gating because an env var went missing is
    the failure this whole tool exists to prevent."""
    conn = env.conn()
    add_criterion(conn, "THEIRS", "their goal", "false", {})
    coord.set_node_flags(conn, "THEIRS", session="session-A")
    monkeypatch.delenv("LOOPGRAPH_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_BRIDGE_SESSION_ID", raising=False)
    assert env.stop(stop_hook_active=False)["decision"] == "block"


def test_a_closed_loose_criterion_is_not_nagged_about(env, monkeypatch):
    conn = env.conn()
    add_criterion(conn, "DONE", "their finished goal", "true", {})
    coord.set_node_flags(conn, "DONE", session="session-A")
    from loopgraph.evidence import run_evidence
    from loopgraph.state import record_status
    run_evidence(conn, "DONE")
    record_status(conn, "DONE")
    monkeypatch.setenv("LOOPGRAPH_SESSION", "session-B")
    assert env.stop(stop_hook_active=False) == {}


def test_green_guards_are_not_success_while_the_goal_is_unspecified(env):
    """`check` exiting 0 here would report a met specification that nobody
    ever wrote."""
    from loopgraph.evidence import run_evidence
    from loopgraph.rules import terminal_state
    from loopgraph.state import record_status
    conn = env.conn()
    env.submit()
    add_criterion(conn, "G-tests", "suite passes", "true", {})
    coord.set_node_flags(conn, "G-tests", guard=True)
    run_evidence(conn, "G-tests")
    record_status(conn, "G-tests")
    assert terminal_state(conn, {}) is None
    coord.clear_goal(conn, "waived")
    assert terminal_state(conn, {}) == "success"
