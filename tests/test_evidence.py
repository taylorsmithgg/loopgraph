import pytest
from loopgraph.db import open_db
from loopgraph.evidence import evaluate, run_evidence, validate_expect
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


# --- C1: stdout_int_gte must not pick digits out of decimals/versions ----


@pytest.mark.parametrize(
    "stdout,want",
    [
        # "last integer" was really "last run of digits anywhere", so a
        # decimal's fractional part or a dotted version number could read
        # as the extracted value. None of these contain a real integer
        # >= 1 as a standalone token, so all must fail (stay open).
        ("0 rows in set (0.02 sec)", False),
        ("0 rows\ntook 1.7s\n", False),
        ("matches: 0 (v1.2)", False),
    ],
)
def test_stdout_int_gte_ignores_digits_embedded_in_decimals(stdout, want):
    assert evaluate({"stdout_int_gte": 1}, 0, stdout) is want


def test_stdout_int_gte_ignores_sentence_final_period_after_zero():
    """Regression pin: a sentence-final '.' is not proof of a decimal. The
    lookaround-based fix that shipped for C1 over-anchored -- it treated
    ANY adjacent '.' as decimal evidence, so "Matches found: 0." dropped
    the 0 entirely and the earlier, larger "3" (from "3 candidates
    scanned") became the last integer, wrongly closing the criterion."""
    assert evaluate({"stdout_int_gte": 1}, 0, "3 candidates scanned. Matches found: 0.") is False


@pytest.mark.parametrize(
    "stdout,want",
    [
        ("Scanned 12 files. Violations: 0.\n", False),
        ("9 total\nremaining: 0.", False),
    ],
)
def test_stdout_int_gte_sentence_final_period_variants(stdout, want):
    assert evaluate({"stdout_int_gte": 1}, 0, stdout) is want


def test_stdout_int_gte_trailing_period_with_no_larger_earlier_int():
    """"count=0." has a single digit-run followed only by a sentence-final
    '.'. The under-report failure mode: over-anchoring on any adjacent '.'
    discarded this token entirely, so _last_int returned None and a
    threshold of 0 (which the lone "0" satisfies) wrongly failed."""
    assert evaluate({"stdout_int_gte": 0}, 0, "count=0.") is True


def test_stdout_int_gte_boundary_is_inclusive():
    """The operator is >=: a value exactly equal to the threshold must
    PASS. (Mutation check: changing >= to > flips this to False.)"""
    assert evaluate({"stdout_int_gte": 5}, 0, "5") is True
    assert evaluate({"stdout_int_gte": 5}, 0, "4") is False


def test_stdout_int_gte_multiline_last_int_wins():
    """Pins that scanning the FULL (multi-line) stdout for the last
    integer token is retained. "9\\nrows: 0" must yield 0 (the last
    token), not 9 (the first) -- a threshold of 5 tells them apart:
    last-wins fails (0 < 5), first-wins would wrongly pass (9 >= 5)."""
    assert evaluate({"stdout_int_gte": 5}, 0, "9\nrows: 0") is False
    assert evaluate({"stdout_int_gte": 5}, 0, "0\nrows: 9") is True


# --- C2: exit_zero:True is implied ALWAYS unless explicitly overridden ---


def test_nonzero_exit_with_matching_stdout_does_not_close():
    """`echo PASS; exit 2` with {"stdout_contains": "PASS"} must not
    close -- exit 127 ("command not found") with matching stdout text is
    exactly the case that must never read as met."""
    assert evaluate({"stdout_contains": "PASS"}, 2, "PASS") is False
    assert evaluate({"stdout_matches": "[0-9]+ rows"}, 127, "3 rows") is False


def test_explicit_exit_zero_false_is_honoured():
    """An author who explicitly writes {"exit_zero": False} has opted out
    of the exit-code check deliberately; a non-zero exit must then be
    allowed to close (given the rest of expect is satisfied)."""
    assert evaluate({"exit_zero": False}, 1, "") is True
    assert evaluate({"exit_zero": False, "stdout_contains": "PASS"}, 2, "PASS") is True


def test_empty_expect_still_means_exit_zero_only():
    """An empty dict continues to mean exit_zero: True and nothing else."""
    assert evaluate({}, 0, "") is True
    assert evaluate({}, 1, "") is False


def test_run_evidence_nonzero_exit_with_matching_stdout_stays_open(conn):
    add_criterion(conn, "C1", "s", "echo PASS; exit 2", {"stdout_contains": "PASS"})
    run = run_evidence(conn, "C1")
    assert run["ok"] == 0


def test_run_evidence_explicit_exit_zero_false_closes(conn):
    add_criterion(
        conn, "C1", "s", "echo PASS; exit 2",
        {"exit_zero": False, "stdout_contains": "PASS"},
    )
    run = run_evidence(conn, "C1")
    assert run["ok"] == 1


# --- I4: expect values are validated, not silently coerced ---------------


@pytest.mark.parametrize(
    "bad_expect",
    [
        {"exit_zero": "false"},  # bool("false") is True: opposite meaning
        {"exit_zero": 0},
        {"exit_zero": 1},
        {"stdout_int_gte": "abc"},
        {"stdout_int_gte": True},
        {"stdout_int_gte": 1.5},
        {"stdout_contains": ["a", "b"]},
        {"stdout_matches": 123},
        {"stdout_matches": "(unclosed"},
        {"nonsense_key": 1},
    ],
)
def test_validate_expect_rejects_bad_values(bad_expect):
    with pytest.raises(ValueError):
        validate_expect(bad_expect)


def test_validate_expect_accepts_well_formed_values():
    for good in (
        {},
        {"exit_zero": True},
        {"exit_zero": False},
        {"stdout_int_gte": 5},
        {"stdout_contains": "ok"},
        {"stdout_matches": r"^\d+ rows$"},
    ):
        validate_expect(good)  # must not raise


def test_add_criterion_rejects_bad_expect_at_authoring_time(conn):
    with pytest.raises(ValueError):
        add_criterion(conn, "C1", "s", "true", {"exit_zero": "false"})


def test_evaluate_rejects_invalid_expect_rather_than_reading_as_a_failure():
    """A stored-but-invalid expect (e.g. reaching evaluate() via a stored
    row that bypassed add_criterion's validation) must still fail loudly.
    Before validate_expect existed, {"stdout_int_gte": "abc"} would raise
    only when stdout happened to contain a digit (int("abc") TypeError
    inside the comparison); otherwise `_last_int` found nothing and the
    expectation silently read as a decided failure (ok=0) instead of an
    error (ok=NULL/unproven)."""
    with pytest.raises(ValueError):
        evaluate({"stdout_int_gte": "abc"}, 0, "no digits here")


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


def test_timeout_with_stderr_output_does_not_crash(conn):
    """A command that logs to stderr before hanging must still finalise
    cleanly as an undecided (not failed, not crashed) run."""
    add_criterion(
        conn, "C1", "s", "echo err-msg >&2; sleep 5", {}, timeout_s=1
    )
    run = run_evidence(conn, "C1")
    assert run["timed_out"] == 1
    assert run["ok"] is None
    assert run["ended_at"] is not None
    assert "err-msg" in run["stderr"]
    assert "timed out" in run["stderr"]


def test_run_evidence_finalizes_row_on_unexpected_error(conn):
    """A failure after the run row is inserted (e.g. corrupt expect_json)
    must still finalise the row as undecided, then re-raise, never as
    ok=0."""
    add_criterion(conn, "C1", "s", "echo hi", {})
    conn.execute(
        "UPDATE nodes SET expect_json = ? WHERE id = ?",
        ("{not valid json", "C1"),
    )
    with pytest.raises(ValueError):
        run_evidence(conn, "C1")

    row = conn.execute(
        "SELECT * FROM runs WHERE criterion_id = ? ORDER BY id DESC LIMIT 1",
        ("C1",),
    ).fetchone()
    assert row["ended_at"] is not None
    assert row["timed_out"] == 0
    assert row["ok"] is None
