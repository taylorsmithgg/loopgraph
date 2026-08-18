"""Held-out recall, as a guard rather than a one-off measurement.

The number this locks in was itself produced by catching a bad measurement:
the alias table was tuned against tools/recall_eval.py and then scored 12/12
on it, which is training on the test set. tools/recall_eval_holdout.py exists
because that number meant nothing, and this file exists because a measurement
nobody re-runs decays into a claim.

Floors, not exact values. A change that improves ranking should pass without
anyone editing the test; only a regression fails. And it skips rather than
fails when the corpus is not on this machine -- a guard that goes red for
everyone who is not Taylor gets deleted, and then guards nothing.
"""
import importlib.util
import os

import pytest

from loopgraph import memory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Measured 2026-08-17 with first-line weighting, a length penalty, and
# mutual/degree-capped autolinking. Raise these when a change earns it; never
# lower them silently. @1 went 9 -> 10 by REMOVING links, which is why the
# graph is guarded by this number and not by how connected it looks.
FLOOR_AT5 = 16
FLOOR_AT1 = 10
CASE_COUNT = 20


def _holdout():
    path = os.path.join(ROOT, "tools", "recall_eval_holdout.py")
    spec = importlib.util.spec_from_file_location("holdout", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _corpus_present() -> bool:
    try:
        conn = memory.open_memory()
        n = conn.execute(
            "SELECT count(*) FROM nodes WHERE type='memory'").fetchone()[0]
        return n >= 100
    except Exception:
        return False


needs_corpus = pytest.mark.skipif(
    not _corpus_present(),
    reason="needs this machine's memory corpus; nothing to measure without it")


@needs_corpus
def test_holdout_recall_has_not_regressed():
    ho = _holdout()
    assert len(ho.CASES) == CASE_COUNT, (
        "the held-out set changed size; the floors below were measured "
        "against 20 cases and no longer mean the same thing")
    conn = memory.open_memory()
    at1 = at5 = 0
    for question, needle in ho.CASES:
        ids = [h["id"] for h in memory.recall(conn, question, k=5, scope="full")]
        wanted = needle if isinstance(needle, tuple) else (needle,)
        pos = next((i for i, x in enumerate(ids, 1)
                    if any(w in x.lower() for w in wanted)), None)
        at1 += pos == 1
        at5 += bool(pos and pos <= 5)
    assert at5 >= FLOOR_AT5, f"held-out recall@5 regressed to {at5}/{CASE_COUNT}"
    assert at1 >= FLOOR_AT1, f"held-out recall@1 regressed to {at1}/{CASE_COUNT}"


@needs_corpus
def test_the_tuned_eval_is_still_labelled_as_contaminated():
    """It stays in the repo because it is a useful regression signal, and it
    stays labelled because someone will otherwise quote its number again."""
    path = os.path.join(ROOT, "tools", "recall_eval.py")
    head = open(path, encoding="utf-8").read(1200).lower()
    assert "contaminated" in head or "training on the test set" in head


@needs_corpus
def test_safe_scope_still_returns_something_for_every_question():
    """Half this corpus is withheld from harnesses not trusted with client
    content, which is inherent to the content. Returning NOTHING at all would
    be a different failure, and is the one worth guarding."""
    ho = _holdout()
    conn = memory.open_memory()
    empty = [q for q, _ in ho.CASES
             if not memory.recall(conn, q, k=5, scope="safe")]
    assert empty == [], f"safe scope returned nothing for: {empty}"
