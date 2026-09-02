import json
import os
import pytest
from loopgraph.cli import main
from loopgraph.db import open_db


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
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green", "--goal")
    run(db, "run")
    assert run(db, "check") == 0


def test_unproven_criterion_keeps_check_nonzero(db):
    """Never-run evidence must not read as met."""
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green", "--goal")
    assert run(db, "check") == 1


def test_status_json_shape(db, capsys):
    run(db, "add", "C1", "--statement", "lake has rows", "--cmd", "echo 5", "--allow-green",
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
    assert run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green",
               "--expect", "not json") == 2


# --- I3: rel_type is validated; a typo is a usage error, not silent -----


def test_link_bad_rel_type_is_usage_error(db, capsys):
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green")
    run(db, "add", "C2", "--statement", "s", "--cmd", "true", "--allow-green")
    assert run(db, "link", "C2", "C1", "--rel", "depends-on") == 2
    assert capsys.readouterr().err.strip()


# --- I4: bad --expect values (wrong type, not just malformed JSON) ------


def test_add_bad_expect_value_is_usage_error(db, capsys):
    assert run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green",
               "--expect", '{"exit_zero": "false"}') == 2
    assert capsys.readouterr().err.strip()


# --- I5: _report threads one `now` reading through every sub-call -------


def test_report_computes_now_once(db):
    """Before this fix, `_report` called statuses/workable/blocked/
    evaluate_rules/terminal_state each without `now`, and terminal_state
    recomputes statuses()/evaluate_rules() internally -- five (really
    twelve, for one criterion) independent datetime.now() reads. Patch
    both cli.py's and state.py's `datetime` name (not the builtin type,
    which cannot be monkeypatched) to prove exactly one reading is taken
    at the cli layer, and none at all downstream once it is threaded
    through.

    The criterion MUST carry a staleness_window_s: derive_status() returns
    "closed" immediately when the window is None, before ever reaching its
    own `now = now or datetime.now(...)` line -- so a criterion without a
    window would make the "0 downstream calls" assertion trivially true
    even if `now` were never threaded at all.
    """
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green",
        "--staleness", "3600", "--goal")
    run(db, "run")

    import loopgraph.cli as cli_mod
    import loopgraph.state as state_mod
    from datetime import datetime as real_datetime

    cli_calls = []

    class CountingCliDatetime:
        @staticmethod
        def now(tz=None):
            cli_calls.append(1)
            return real_datetime.now(tz)

    state_calls = []

    class CountingStateDatetime:
        @staticmethod
        def now(tz=None):
            state_calls.append(1)
            return real_datetime.now(tz)

        fromisoformat = staticmethod(real_datetime.fromisoformat)

    original_cli_datetime = cli_mod.datetime
    original_state_datetime = state_mod.datetime
    cli_mod.datetime = CountingCliDatetime
    state_mod.datetime = CountingStateDatetime
    try:
        conn = open_db(db)
        report = cli_mod._report(conn, {})
    finally:
        cli_mod.datetime = original_cli_datetime
        state_mod.datetime = original_state_datetime

    assert report["terminal_state"] == "success"
    assert len(cli_calls) == 1
    assert len(state_calls) == 0


# The tests above are the brief's given tests, verbatim. The tests below
# were added during mutation testing because the corresponding mutant
# survived every test above.


def test_add_returns_zero_on_success(db):
    """`add`/`link`/`tick`/`spend` are ordinary authoring/bookkeeping
    commands: 0 means the command itself succeeded (so they can be
    chained with `&&` in a script), distinct from check/run's "0 means
    the specification is met" contract.

    Originally pinned the opposite behaviour (`add` returning 1 on
    success) to catch a mutant where `add` returned 0. The brief's own
    Step 3 code returned 1 from add/link/tick/spend on success, which
    the coordinator ruled a defect inherited from the brief: it makes
    `add C1 ... && link C2 C1` never run the second command. Inverted
    here per that ruling; this test is not one of the brief's 7 given
    tests, so changing it is in scope."""
    assert run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green") == 0


def test_run_records_status_so_recent_close_prevents_stagnation(db):
    """A mutant that dropped `record_status` after `run_evidence` in the
    `run` subcommand slipped past every test above. `record_status` is
    what emits the STATE_TRANSITION delta that rules.py's stagnation
    tracker (R-01) uses to know a criterion closed recently. Without it,
    a criterion that in fact just closed reads as having made no
    progress, R-01 fires, and terminal_state becomes "stalled" instead
    of "success" even though every criterion is closed."""
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green", "--goal")
    run(db, "tick")
    run(db, "tick")
    run(db, "run")
    run(db, "tick")
    assert run(db, "check") == 0


def test_stale_criterion_appears_in_check_output(db, capsys):
    """A mutant that made `_print_human` skip "stale" criteria (in
    addition to "closed" ones) slipped past every test above, since none
    of them exercises a stale criterion. A stale criterion with an
    unmet dependency is absent from both `workable()` and `blocked()`
    (see state.py), so this per-criterion line is the only place an
    operator reading `check` output would see it at all.

    Checking bare substring "C1" is not enough: R-02's rule detail also
    contains "C1" ("stale: C1"), so a mutant that dropped the
    per-criterion line but left the rules section intact would slip
    past a bare substring check. Assert the specific per-criterion line
    format instead.
    """
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green", "--staleness", "0")
    run(db, "run")
    run(db, "check")
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("C1 stale:") for line in lines)


# Fix round 1: two Important findings, both defects inherited from the
# brief's own Step 3 code. Tests below are new; nothing above this point
# was modified except test_add_returns_zero_on_success (see its
# docstring), and none of the brief's 7 given tests were touched.


def test_run_continues_past_failing_criterion(db, capsys):
    """A criterion whose stored expect_json is invalid raises inside
    evidence.evaluate() at run time. That must not abort the batch: every
    other criterion in the run must still be evaluated, and the failure
    must be loud (including the actual exception text, not just the
    criterion id), not silent.

    Since I4 (validate_expect), a typo'd --expect key like {"bogus_key":
    1} is now rejected by `add` itself -- it can no longer reach `run` as
    valid JSON that only fails later. To still exercise the run-time
    failure path, this tampers with the stored expect_json directly,
    bypassing add_criterion's validation the same way
    test_run_evidence_finalizes_row_on_unexpected_error does in
    test_evidence.py."""
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green")
    run(db, "add", "C2", "--statement", "s", "--cmd", "true", "--allow-green")
    conn = open_db(db)
    conn.execute(
        "UPDATE nodes SET expect_json = ? WHERE id = ?",
        ('{"bogus_key": 1}', "C1"),
    )
    conn.close()

    assert run(db, "run") == 2
    err = capsys.readouterr().err
    assert "C1" in err
    assert "bogus_key" in err

    run(db, "status", "--json")
    payload = json.loads(capsys.readouterr().out)
    # C2 comes after C1 alphabetically/in evaluation order; if it has a
    # derived status other than "unproven" it was actually evaluated,
    # proving the loop didn't stop at C1.
    assert payload["statuses"]["C2"] == "closed"


def test_unevaluable_criterion_never_yields_success(db):
    """A criterion whose evidence could not be evaluated (stored-but-
    invalid expect_json, tampered in after `add`) must never be counted
    toward a success verdict, even though evidence.run_evidence leaves it
    as "unproven" rather than "open"."""
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green", "--goal")
    conn = open_db(db)
    conn.execute(
        "UPDATE nodes SET expect_json = ? WHERE id = ?",
        ('{"bogus_key": 1}', "C1"),
    )
    conn.close()

    assert run(db, "run") == 2
    assert run(db, "check") != 0


def test_add_duplicate_id_exits_two(db):
    """A duplicate id is a sqlite3.IntegrityError (nodes.id is a PRIMARY
    KEY). It must map to the documented usage/internal-error exit code,
    not crash with a raw traceback."""
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green")
    assert run(db, "add", "C1", "--statement", "s2", "--cmd", "true", "--allow-green") == 2


def test_link_unknown_dst_exits_two(db):
    """Linking to a criterion id that doesn't exist is a foreign-key
    violation (edges.dst references nodes.id, foreign_keys=ON). It must
    map to exit 2, not crash with a raw traceback."""
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green")
    assert run(db, "link", "C1", "NOPE") == 2


def test_link_tick_spend_return_zero_on_success(db):
    """Companion to test_add_returns_zero_on_success: link/tick/spend are
    also ordinary authoring/bookkeeping commands and must report 0 on
    ordinary success so they can be chained with `&&` in a script."""
    run(db, "add", "C1", "--statement", "s", "--cmd", "true", "--allow-green")
    run(db, "add", "C2", "--statement", "s", "--cmd", "true", "--allow-green")
    assert run(db, "link", "C1", "C2") == 0
    assert run(db, "tick") == 0
    assert run(db, "spend", "10") == 0


# --- coordination CLI --------------------------------------------------------

def test_claim_then_conflict_exits_three(db, capsys):
    assert run(db, "claim", "a1", "--scope", "sql/57") == 0
    assert run(db, "claim", "a2", "--scope", "sql/58", "sql/57") == 3
    err = capsys.readouterr().err
    assert "CONFLICT sql/57 held by a1" in err
    # all-or-nothing: a2 must not hold the free slot either
    run(db, "claims")
    assert "a2" not in capsys.readouterr().out


def test_validate_uses_git_when_no_changed_given(db, capsys):
    """Exercises the git path -- would have caught the missing subprocess import."""
    run(db, "claim", "a1", "--scope", "README.md", "--base-ref", "HEAD")
    rc = run(db, "validate", "a1")
    out = capsys.readouterr().out
    assert rc in (0, 1)
    assert '"verdict"' in out


def test_validate_stale_on_explicit_changed(db, capsys):
    run(db, "claim", "a1", "--scope", "apps/checkout", "--base-ref", "sha0")
    rc = run(db, "validate", "a1", "--changed", "apps/checkout")
    assert rc == 1
    assert '"stale"' in capsys.readouterr().out


def test_validate_without_base_ref_is_usage_error(db):
    run(db, "claim", "a1", "--scope", "x")
    assert run(db, "validate", "a1") == 2


def test_release_frees_and_classes_partition(db, capsys):
    run(db, "claim", "a1", "--scope", "a.sql", "b.yaml")
    run(db, "claim", "a2", "--scope", "b.yaml")   # refused
    run(db, "release", "a1")
    assert run(db, "claim", "a2", "--scope", "b.yaml") == 0
    run(db, "classes", "--agent", "x=1,2", "--agent", "y=2,3", "--agent", "z=9")
    out = capsys.readouterr().out
    assert "SERIAL\tx y" in out and "parallel\tz" in out


def test_fact_add_and_list(db, capsys):
    run(db, "fact", "add", "glab-lies", "--text", "glab mr merge prints Merged while opened",
        "--tags", "gitlab")
    run(db, "fact", "list")
    assert "glab-lies" in capsys.readouterr().out


def test_on_off_toggles_both_gates(db, capsys):
    assert run(db, "on") == 0
    assert "scope=ON" in capsys.readouterr().out
    run(db, "off")
    assert "scope=off" in capsys.readouterr().out


def test_only_flag_toggles_one_gate(db, capsys):
    """Gates default on, so --only must be shown to move exactly one."""
    run(db, "off", "--only", "scope")
    out = capsys.readouterr().out
    assert "scope=off" in out and "loop=ON" in out


def test_status_reports_gate_state(db, capsys):
    run(db, "on")
    capsys.readouterr()
    run(db, "status")
    assert "gates:" in capsys.readouterr().out


def test_bare_invocation_reports_status_and_succeeds(db, capsys):
    """`loopgraph` with no subcommand must not be an argparse error."""
    assert run(db) == 0
    assert "gates:" in capsys.readouterr().out


def test_status_exits_zero_even_when_work_remains(db):
    """status is a report, not a gate. Exiting 1 here broke /loopgraph."""
    run(db, "add", "C1", "--statement", "s", "--cmd", "false", "--goal")
    run(db, "run")
    assert run(db, "status") == 0


def test_status_exits_zero_on_an_empty_project(db):
    assert run(db, "status") == 0


def test_check_still_carries_the_specification_contract(db):
    """The status fix must not have relaxed check."""
    run(db, "add", "C1", "--statement", "s", "--cmd", "false", "--goal")
    run(db, "run")
    assert run(db, "check") == 1
    assert run(db, "add", "C2", "--statement", "s", "--cmd", "true", "--allow-green") == 0


# --- mem forget across the two stores ---------------------------------------
#
# The corpus is the writer and sqlite is the index over it, so every forget
# touches two stores and can succeed in one while finding nothing in the
# other. Reporting only on the index made a partial delete look like a no-op.

@pytest.fixture
def mem_env(tmp_path, monkeypatch):
    """Isolate every store `mem` touches.

    `QUEUE` and `REVIEWED` are module constants resolved at import, so
    setenv is too late for them and a test would append tombstones to the
    live queue -- the one holding an untriaged account compromise. The
    memory DB is the other way round, read per call, so setenv works there.
    """
    from loopgraph import security
    monkeypatch.setenv("LOOPGRAPH_MEMORY_DB", str(tmp_path / "mem.db"))
    monkeypatch.setattr(security, "QUEUE", str(tmp_path / "sec.jsonl"))
    monkeypatch.setattr(security, "REVIEWED", str(tmp_path / "sec.reviewed"))
    return str(tmp_path / "corpus")


def mem(db, corpus, *args):
    """`main` resolves and opens the criteria graph before dispatching to
    `mem`, so without --db every one of these opens a real DB for the cwd."""
    return main(["--db", db, "mem", "--corpus", corpus, *args])


def test_forget_confirms_on_stdout(db, mem_env, capsys):
    """Silence after a delete leaves the reader guessing, and the one message
    there was went to stderr, which wrappers and CI paint red."""
    mem(db, mem_env, "retain", "the edge collector is 32-bit")
    mid = capsys.readouterr().out.strip()
    assert mem(db, mem_env, "forget", mid) == 0
    seen = capsys.readouterr()
    assert seen.out.strip() == f"Forgot {mid}."
    assert seen.err == ""
    assert not os.path.exists(os.path.join(mem_env, f"{mid}.md"))
    assert f"- [{mid}.md]" not in open(
        os.path.join(mem_env, "MEMORY.md"), encoding="utf-8").read()


def test_forget_of_a_file_only_memory_succeeds_and_says_so(db, mem_env, capsys):
    """The search index is disposable, so a note file can outlive its entry.
    Deleting that file is work done, and exiting 2 'no such memory' denied
    it. The message names the half that was already missing, in words a
    reader does not have to know our storage layout to follow."""
    from loopgraph.memory import write_markdown
    write_markdown(mem_env, "orphan-file", "a fact", "world")
    assert mem(db, mem_env, "forget", "orphan-file") == 0
    assert "Its search entry had already been deleted" in capsys.readouterr().out


def test_forget_of_an_index_only_memory_succeeds_and_says_so(db, mem_env, capsys):
    mem(db, mem_env, "retain", "index only", "--no-file")
    mid = capsys.readouterr().out.strip()
    assert mem(db, mem_env, "forget", mid) == 0
    assert "Its note file had already been deleted" in capsys.readouterr().out


def test_forget_of_an_unknown_id_still_fails(db, mem_env, capsys):
    """Absent from both places is the only real error, and the message says
    how to find the right name rather than only that this one was wrong."""
    assert mem(db, mem_env, "forget", "never-existed") == 2
    err = capsys.readouterr().err
    assert "There is no memory called never-existed" in err
    assert "mem recall" in err


def test_forget_retracts_the_finding_it_filed(db, mem_env, capsys):
    """A sensitive retain queues a finding naming the memory id. Forgetting
    the memory left that finding pointing at an id in neither store, which
    reads from the queue side as the two stores having diverged."""
    from loopgraph import security
    mem(db, mem_env, "retain", "the api key for the collector lives in vault")
    mid = capsys.readouterr().out.strip()
    assert [r["subject"] for r in security.pending()] == [mid]
    mem(db, mem_env, "forget", mid)
    assert security.pending() == []


def test_prune_retracts_only_findings_whose_memory_is_gone(db, mem_env, capsys):
    """The queue is a mixed namespace. Hand-filed findings name an account or
    a host and never were memory ids, so pruning on "subject not in nodes"
    alone retracts an open account compromise -- one was sitting two rows
    below three stale memory findings."""
    from loopgraph import security
    mem(db, mem_env, "retain", "the api key for the collector lives in vault")
    live = capsys.readouterr().out.strip()
    security.queue(security.MEMORY_WITHHELD, "forgotten-long-ago",
                   "credential material or its location")
    security.queue("open compromise, untriaged 7d", "someone@example.com",
                   "needs session revocation")

    assert main(["--db", db, "security", "--prune"]) == 0
    left = {r["subject"] for r in security.pending()}
    assert left == {live, "someone@example.com"}
    assert "forgotten-long-ago" in capsys.readouterr().out


def test_prune_on_a_clean_queue_retracts_nothing(db, mem_env, capsys):
    from loopgraph import security
    mem(db, mem_env, "retain", "the api key for the collector lives in vault")
    mid = capsys.readouterr().out.strip()
    assert main(["--db", db, "security", "--prune"]) == 0
    assert [r["subject"] for r in security.pending()] == [mid]
