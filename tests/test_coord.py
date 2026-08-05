from datetime import datetime, timedelta, timezone

import pytest

from loopgraph.db import open_db
from loopgraph.coord import (
    agent_check, agent_done, agent_start, conflict_classes, fact_add, fact_list,
    heartbeat, live_claims, live_holder,
)


@pytest.fixture
def conn(tmp_path):
    return open_db(tmp_path / "g.db")


# --- atomic claims -----------------------------------------------------------

def test_claim_succeeds_on_free_scope(conn):
    r = agent_start(conn, "a1", ["sql/57", "values.yaml"], base_ref="abc123")
    assert r["ok"] is True
    assert r["claimed"] == ["sql/57", "values.yaml"]
    assert live_holder(conn, "sql/57") == "a1"


def test_second_agent_is_refused_the_same_slot(conn):
    agent_start(conn, "a1", ["sql/57"])
    r = agent_start(conn, "a2", ["sql/57"])
    assert r["ok"] is False
    assert r["conflicts"] == [{"slot": "sql/57", "holder": "a1"}]


def test_claim_is_all_or_nothing(conn):
    """The real !506/!510 shape: partial overlap must claim nothing."""
    agent_start(conn, "a1", ["sql/57"])
    r = agent_start(conn, "a2", ["sql/58", "sql/57", "sql/59"])
    assert r["ok"] is False
    # a2 must hold NOTHING - not even the two free slots
    assert live_holder(conn, "sql/58") is None
    assert live_holder(conn, "sql/59") is None
    assert r["claimed"] == []


def test_disjoint_scopes_both_succeed(conn):
    assert agent_start(conn, "a1", ["sql/57"])["ok"] is True
    assert agent_start(conn, "a2", ["sql/58"])["ok"] is True
    assert live_claims(conn) == {"sql/57": "a1", "sql/58": "a2"}


def test_release_frees_the_slot(conn):
    agent_start(conn, "a1", ["sql/57"])
    assert agent_done(conn, "a1") == ["sql/57"]
    assert live_holder(conn, "sql/57") is None
    assert agent_start(conn, "a2", ["sql/57"])["ok"] is True


def test_expired_lease_frees_the_slot_lazily(conn):
    """A dead agent must not poison a slot forever."""
    agent_start(conn, "a1", ["sql/57"])
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert live_holder(conn, "sql/57", lease_s=1800, now=later) is None
    assert agent_start(conn, "a2", ["sql/57"], lease_s=1800, now=later)["ok"] is True


def test_heartbeat_keeps_a_long_agent_alive(conn):
    """The brake must not fire on an agent that is genuinely still working."""
    agent_start(conn, "a1", ["sql/57"])
    t1 = datetime.now(timezone.utc) + timedelta(minutes=25)
    heartbeat(conn, "a1", now=t1)
    t2 = t1 + timedelta(minutes=25)
    assert live_holder(conn, "sql/57", lease_s=1800, now=t2) == "a1"


def test_reclaiming_own_slot_is_not_a_conflict(conn):
    agent_start(conn, "a1", ["sql/57"])
    assert agent_start(conn, "a1", ["sql/57", "sql/58"])["ok"] is True


# --- staleness validation ----------------------------------------------------

def test_check_clean_when_nothing_in_scope_moved(conn):
    agent_start(conn, "a1", ["charts/a.yaml"], base_ref="sha0")
    r = agent_check(conn, "a1", changed=["docs/readme.md", "charts/b.yaml"])
    assert r["verdict"] == "clean"
    assert r["changed_in_scope"] == []


def test_check_stale_when_scope_moved(conn):
    """The 9.6-day agent: its premise changed while it ran."""
    agent_start(conn, "a1", ["apps/checkout"], base_ref="sha0")
    r = agent_check(conn, "a1", changed=["apps/checkout", "unrelated.py"])
    assert r["verdict"] == "stale"
    assert r["changed_in_scope"] == ["apps/checkout"]


def test_check_stale_on_superseded_epoch(conn):
    """!515: the goal moved, not the files."""
    agent_start(conn, "a1", ["x.sql"], epoch=3)
    r = agent_check(conn, "a1", changed=[], current_epoch=4)
    assert r["verdict"] == "stale"
    assert r["epoch_stale"] is True
    assert r["changed_in_scope"] == []


def test_check_unknown_agent_raises(conn):
    with pytest.raises(ValueError):
        agent_check(conn, "nope", changed=[])


# --- conflict classes --------------------------------------------------------

def test_conflict_classes_partition_by_write_set():
    """!535/!539/!543 share files; !541/!542 are independent."""
    scopes = {
        "mr535": ["a.sql", "b.yaml", "c.py"],
        "mr539": ["b.yaml"],
        "mr543": ["c.py", "d.md"],
        "mr541": ["x.go"],
        "mr542": ["y.go"],
    }
    assert conflict_classes(scopes) == [
        ["mr535", "mr539", "mr543"], ["mr541"], ["mr542"],
    ]


def test_conflict_classes_are_transitive():
    """A shares with B, B shares with C, so all three serialise."""
    assert conflict_classes({"a": ["1"], "b": ["1", "2"], "c": ["2"]}) == [["a", "b", "c"]]


def test_conflict_classes_all_disjoint():
    assert conflict_classes({"a": ["1"], "b": ["2"]}) == [["a"], ["b"]]


# --- facts -------------------------------------------------------------------

def test_facts_roundtrip_and_filter(conn):
    fact_add(conn, "glab-merge-lies", "glab mr merge prints Merged! while state stays opened", "gitlab,ci")
    fact_add(conn, "shellcheck-split", "shellcheck version differs between runner images", "ci")
    assert [f["id"] for f in fact_list(conn)] == ["glab-merge-lies", "shellcheck-split"]
    assert [f["id"] for f in fact_list(conn, tag="gitlab")] == ["glab-merge-lies"]
    assert fact_list(conn, tag="nope") == []


# --- category B: duplicate artifacts ------------------------------------------

from loopgraph.coord import (
    artifact_add, artifact_check, brief, frontier, refuse, semantic_key, sweep_expired,
)


def test_semantic_key_strips_only_known_vendors():
    assert semantic_key("azure_impossible_travel") == "impossible_travel"
    assert semantic_key("entra_impossible_travel") == "impossible_travel"
    # not a vendor -> first token is the discriminator, must NOT be stripped
    assert semantic_key("egress_baseline") == "egress_baseline"
    assert semantic_key("detections.duo_mfa_fatigue") == "mfa_fatigue"


def test_artifact_check_catches_the_515_duplicate(conn):
    artifact_add(conn, "entra_impossible_travel")
    r = artifact_check(conn, "azure_impossible_travel")
    assert r["verdict"] == "conflict"
    assert r["duplicates"] == ["entra_impossible_travel"]


def test_artifact_check_clear_when_genuinely_new(conn):
    artifact_add(conn, "entra_impossible_travel")
    assert artifact_check(conn, "win_jar_from_user_writable")["verdict"] == "clear"


def test_non_vendor_prefixes_do_not_collide(conn):
    """egress/outage/throughput_baseline are distinct, not duplicates."""
    artifact_add(conn, "egress_baseline")
    assert artifact_check(conn, "outage_baseline")["verdict"] == "clear"


def test_refusal_is_reachable_by_a_later_agent(conn):
    """Two agents refused this design; the third could not see that."""
    refuse(conn, "impossible_travel", "would double-alert; entra_* already ships", by="a1")
    r = artifact_check(conn, "azure_impossible_travel")
    assert r["verdict"] == "conflict"
    assert "double-alert" in r["refusals"][0]["reason"]


# --- sweep, frontier, brief ---------------------------------------------------

def test_sweep_frees_only_expired_holders(conn):
    agent_start(conn, "dead", ["s1"])
    agent_start(conn, "alive", ["s2"])
    later = datetime.now(timezone.utc) + timedelta(hours=9)
    heartbeat(conn, "alive", now=later)
    freed = sweep_expired(conn, lease_s=14400, now=later)
    assert freed == ["s1"]
    assert live_claims(conn, now=later) == {"s2": "alive"}


def test_frontier_shows_what_a_killed_agent_completed(conn):
    agent_start(conn, "killed", ["apps/x"])
    events = frontier(conn, "killed")
    assert any(e["entity_id"] == "apps/x" for e in events)


def test_brief_is_empty_without_facts(conn):
    assert brief(conn) == ""


def test_brief_renders_traps(conn):
    fact_add(conn, "glab", "glab mr merge prints Merged while state stays opened", "gitlab")
    out = brief(conn)
    assert "KNOWN TRAPS" in out and "glab mr merge" in out


# --- loop gate state ---------------------------------------------------------

from loopgraph.coord import (
    blocks_so_far, clear_blocks, loop_enabled, note_block, set_loop_enabled,
)


def test_loop_gate_is_on_by_default(conn):
    """Safe as a default because the gate returns silently when no criteria
    are declared; a repo that never declared one is unaffected."""
    assert loop_enabled(conn) is True


def test_loop_toggle_is_independent_of_coordination(conn):
    from loopgraph.coord import is_enabled, set_enabled
    set_loop_enabled(conn, False)
    assert loop_enabled(conn) is False
    assert is_enabled(conn) is True         # separate switches
    set_enabled(conn, False)
    set_loop_enabled(conn, True)
    assert is_enabled(conn) is False and loop_enabled(conn) is True


def test_env_var_forces_loop_gate_off(conn, monkeypatch):
    set_loop_enabled(conn, True)
    monkeypatch.setenv("LOOPGRAPH_LOOP", "0")
    assert loop_enabled(conn) is False


def test_block_counter_increments_and_clears(conn):
    assert blocks_so_far(conn) == 0
    assert note_block(conn) == 1 and note_block(conn) == 2
    clear_blocks(conn)
    assert blocks_so_far(conn) == 0


def test_audit_state_flags_unaudited_and_gameable(conn):
    from loopgraph.coord import audit_state, record_audit
    from loopgraph.graph import add_criterion
    add_criterion(conn, "C1", "s", "true", {})
    add_criterion(conn, "C2", "s", "true", {})
    assert audit_state(conn)["unaudited"] == ["C1", "C2"]
    record_audit(conn, "C1", {"gameable": True, "cheat": "touch f"})
    record_audit(conn, "C2", {"gameable": False})
    st = audit_state(conn)
    assert st["gameable"] == ["C1"] and st["unaudited"] == []


# --- routing evidence ---------------------------------------------------------

def test_attribution_counts_only_closes_in_scope(conn):
    from loopgraph.coord import agent_meta_set, attribute
    from loopgraph.db import emit_delta
    agent_start(conn, "a1", ["mine.sql"])
    agent_meta_set(conn, "a1", model="opus", kind="implement", spend=1000)
    emit_delta(conn, "mine.sql", "STATE_TRANSITION", "open", "closed")
    emit_delta(conn, "someone-elses.sql", "STATE_TRANSITION", "open", "closed")
    a = attribute(conn, "a1")
    assert a["closes"] == 1 and a["model"] == "opus" and a["kind"] == "implement"


def test_route_table_computes_cost_per_accepted(conn):
    from loopgraph.coord import agent_meta_set, route_table
    from loopgraph.db import emit_delta
    for name, model, spend, closes in (("a1", "opus", 900, 3), ("a2", "codex", 400, 1)):
        agent_start(conn, name, [f"{name}.sql"])
        agent_meta_set(conn, name, model=model, kind="implement", spend=spend)
        for _ in range(closes):
            emit_delta(conn, f"{name}.sql", "STATE_TRANSITION", "open", "closed")
    rows = {r["model"]: r for r in route_table(conn)}
    assert rows["opus"]["cost_per_accepted"] == 300
    assert rows["codex"]["cost_per_accepted"] == 400


def test_route_table_reports_none_when_nothing_landed(conn):
    from loopgraph.coord import agent_meta_set, route_table
    agent_start(conn, "a1", ["x"])
    agent_meta_set(conn, "a1", model="codex", kind="plan", spend=5000)
    r = route_table(conn)[0]
    assert r["closes"] == 0 and r["cost_per_accepted"] is None


def test_attribution_counts_criteria_not_only_scope(conn):
    """Criteria have ids, scope holds paths. Matching closes against scope
    alone reported closed=0 while the work had landed."""
    from loopgraph.coord import agent_meta_set, attribute
    from loopgraph.db import emit_delta
    agent_start(conn, "a1", ["util.py"])
    agent_meta_set(conn, "a1", model="codex", kind="implement", criteria=["C1"])
    emit_delta(conn, "C1", "STATE_TRANSITION", "open", "closed")
    assert attribute(conn, "a1")["closes"] == 1


def test_gates_are_on_by_default(conn):
    """Default-on is safe: both gates are inert until something is declared."""
    from loopgraph.coord import is_enabled, loop_enabled
    assert is_enabled(conn) is True and loop_enabled(conn) is True


def test_explicit_off_survives_the_default(conn):
    from loopgraph.coord import is_enabled, loop_enabled, set_enabled, set_loop_enabled
    set_enabled(conn, False)
    set_loop_enabled(conn, False)
    assert is_enabled(conn) is False and loop_enabled(conn) is False


def test_env_still_forces_off_when_default_is_on(conn, monkeypatch):
    from loopgraph.coord import is_enabled, loop_enabled
    monkeypatch.setenv("LOOPGRAPH_COORD", "0")
    monkeypatch.setenv("LOOPGRAPH_LOOP", "0")
    assert is_enabled(conn) is False and loop_enabled(conn) is False
