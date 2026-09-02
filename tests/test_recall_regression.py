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

The measurement is taken against a fixed-size slice of the corpus, not the
whole of it. Measured against the live corpus these floors decayed on their
own: the corpus grew from 214 memories to 711, so the same twenty questions
were competing against 3.3x the documents for the same five slots, and
recall@1 halved from 10 to 5 without a line of ranking code changing. A
number that falls because the corpus grew is not a regression, and a guard
that reports one is a guard that gets re-baselined every few weeks until
nobody believes it. Pinning the size is what makes the floors comparable
from one month to the next.
"""
import hashlib
import importlib.util
import os
import tempfile

import pytest

from loopgraph import memory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The corpus every measurement below runs against: the memories that existed
# on the day the floors were recorded, selected by their own created_at.
#
# A stable hash over a growing population is not stable. Sorting every
# non-target memory by sha1 of its id and truncating still lets a newly
# retained memory whose hash sorts early enter the slice and evict one, so
# the composition churns as the corpus grows and the floors drift again on a
# longer period. Cutting on the date removes growth from the selection
# entirely: nothing retained after the baseline can enter, so the slice
# changes only when a memory inside it is forgotten, and then the size assert
# below fails loudly rather than the denominator changing in silence.
#
# On this machine the cut lands on exactly the original corpus -- 214
# memories, all 29 rows the twenty cases target, 185 distractors -- so this
# is not a sample of that corpus, it is that corpus.
BASELINE_DATE = "2026-08-17"
PINNED_SIZE = 214

# Measured 2026-08-17 with first-line weighting, a length penalty, and
# mutual/degree-capped autolinking. Raise these when a change earns it; never
# lower them silently. @1 went 9 -> 10 by REMOVING links, which is why the
# graph is guarded by this number and not by how connected it looks.
#
# Re-measured 2026-09-02 on the date-cut corpus: @1 10, @5 16, both equal to
# the values recorded above. That equality is the check on the reconstruction
# -- the live corpus had grown to 711 by then and read @1 5, @5 15, and it
# was dilution rather than any ranking change that moved it. No margin under
# the measured value: margin absorbs drift, and the date cut leaves none to
# absorb, so a floor a point low would just hide the next real regression.
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


def _cache_key(rows: list[tuple]) -> str:
    """What the built database is a function of.

    The corpus contents, and the module that ranks them. Keying on the corpus
    alone would let a cached index built by the previous autolink survive a
    change to autolink, and autolink is part of what these floors measure --
    @1 went 9 to 10 by removing links. A stale hit would report the old code's
    number as the new code's.
    """
    h = hashlib.sha256(BASELINE_DATE.encode())
    for mid, statement, created, kind, tags in rows:
        h.update(f"\0{mid}\0{statement}\0{created}\0{kind}\0{tags}".encode())
    h.update(open(memory.__file__, "rb").read())
    return h.hexdigest()[:16]


def _pinned_corpus():
    """The baseline corpus, rebuilt in a scratch database.

    Selection is by `created_at`, so growth cannot touch it: a memory
    retained after the baseline never enters, and the set changes only when
    one inside it is forgotten. Returns the connection, its size, and the ids
    present, so callers can fail on a shortfall rather than measure a quietly
    different denominator.

    Building it costs about forty-five seconds -- 214 retains, each
    autolinking against the rows already inserted, and inserting in date
    order puts topically related memories next to each other, which is the
    expensive case for the mutual-link check. So the result is cached under
    the key above and reused until either the corpus or the ranking module
    changes.
    """
    live = memory.open_memory()
    rows = []
    for r in live.execute(
            "SELECT id, statement, created_at FROM nodes WHERE type='memory' "
            "AND substr(created_at, 1, 10) <= ? ORDER BY created_at, id",
            (BASELINE_DATE,)):
        meta = memory.mem_meta(live, r["id"])
        rows.append((r["id"], r["statement"], r["created_at"],
                     meta.get("kind", "world"), tuple(meta.get("tags", []))))
    ids = {r[0] for r in rows}

    path = os.path.join(tempfile.gettempdir(),
                        f"loopgraph-pinned-{_cache_key(rows)}.db")
    if os.path.exists(path):
        return memory.open_memory(path), len(rows), ids

    building = path + ".building"
    if os.path.exists(building):
        os.remove(building)
    conn = memory.open_memory(building)
    for mid, statement, created, kind, tags in rows:
        memory.retain(conn, statement, kind=kind, tags=tags, id=mid,
                      created_at=created)
    conn.close()
    # Rename only once it is complete: an interrupted build must not be
    # picked up as a cache hit and measured as if it were the whole corpus.
    os.replace(building, path)
    return memory.open_memory(path), len(rows), ids


@pytest.fixture(scope="module")
def pinned():
    if not _corpus_present():
        pytest.skip("needs this machine's memory corpus")
    return _pinned_corpus()


def _score(conn, cases) -> tuple[int, int]:
    at1 = at5 = 0
    for question, needle in cases:
        ids = [h["id"] for h in memory.recall(conn, question, k=5, scope="full")]
        wanted = needle if isinstance(needle, tuple) else (needle,)
        pos = next((i for i, x in enumerate(ids, 1)
                    if any(w in x.lower() for w in wanted)), None)
        at1 += pos == 1
        at5 += bool(pos and pos <= 5)
    return at1, at5


@needs_corpus
def test_the_pinned_corpus_is_the_one_the_floors_were_measured_on(pinned):
    """A smaller corpus is an easier task, so a forgotten baseline memory has
    to fail here rather than quietly flatter the floors."""
    _, size, ids = pinned
    assert size == PINNED_SIZE, (
        f"the baseline corpus rebuilt to {size} memories, not {PINNED_SIZE}; "
        "one of them has been forgotten and the floors no longer compare")
    uncovered = []
    for question, needle in _holdout().CASES:
        wanted = needle if isinstance(needle, tuple) else (needle,)
        if not any(w in i.lower() for i in ids for w in wanted):
            uncovered.append(question)
    assert uncovered == [], (
        "no memory in the baseline corpus answers these, so they measure "
        f"nothing: {uncovered}")


@needs_corpus
def test_holdout_recall_has_not_regressed(pinned):
    ho = _holdout()
    assert len(ho.CASES) == CASE_COUNT, (
        "the held-out set changed size; the floors above were measured "
        "against 20 cases and no longer mean the same thing")
    conn, _, _ = pinned
    at1, at5 = _score(conn, ho.CASES)
    # Both numbers in both messages. With one assert per floor the second
    # never speaks until the first passes, and @1 sat at 5 against a floor of
    # 10 for weeks behind an @5 failure that read as the whole story.
    reading = f"@1 {at1}/{CASE_COUNT} (floor {FLOOR_AT1}), " \
              f"@5 {at5}/{CASE_COUNT} (floor {FLOOR_AT5})"
    assert at5 >= FLOOR_AT5, f"held-out recall@5 regressed: {reading}"
    assert at1 >= FLOOR_AT1, f"held-out recall@1 regressed: {reading}"


@needs_corpus
def test_the_tuned_eval_is_still_labelled_as_contaminated():
    """It stays in the repo because it is a useful regression signal, and it
    stays labelled because someone will otherwise quote its number again."""
    path = os.path.join(ROOT, "tools", "recall_eval.py")
    head = open(path, encoding="utf-8").read(1200).lower()
    assert "contaminated" in head or "training on the test set" in head


@needs_corpus
def test_safe_scope_still_returns_something_for_every_question(pinned):
    """Half this corpus is withheld from harnesses not trusted with client
    content, which is inherent to the content. Returning NOTHING at all would
    be a different failure, and is the one worth guarding."""
    conn, _, _ = pinned
    empty = [q for q, _ in _holdout().CASES
             if not memory.recall(conn, q, k=5, scope="safe")]
    assert empty == [], f"safe scope returned nothing for: {empty}"
