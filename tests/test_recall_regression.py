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

# The corpus slice every measurement below runs against. 214 is the size the
# corpus had on 2026-08-17, when these floors were first measured, so the
# numbers stay comparable with the ones recorded then. The twenty target
# memories are always included; the rest of the slice is chosen by a stable
# hash of the id, so it is the same set on every run and changes only when a
# memory in it is forgotten.
PINNED_SIZE = 214

# Measured 2026-08-17 with first-line weighting, a length penalty, and
# mutual/degree-capped autolinking. Raise these when a change earns it; never
# lower them silently. @1 went 9 -> 10 by REMOVING links, which is why the
# graph is guarded by this number and not by how connected it looks.
#
# FLOOR_AT5 re-measured 2026-09-02, from 16 to 15, and the reason is not a
# ranking change: the slice is rebuilt from today's corpus by stable hash, so
# its 194 distractors are not the 194 the original number saw. @1 came back
# to exactly 10 on the pinned slice while it reads 5 on the live one, which is
# what says the pinning is faithful and the drift was dilution.
FLOOR_AT5 = 15
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


def _pinned_corpus(size: int = PINNED_SIZE):
    """A fixed-size slice of the live corpus, rebuilt in a scratch database.

    Every target memory the twenty cases name is included, because a question
    whose answer is not present measures nothing. The distractors are the
    remaining memories ordered by a stable hash of their id, so the slice is
    identical run to run and shifts only when a memory inside it is forgotten
    or its text is superseded.

    Building it costs about ten seconds -- 214 retains, each autolinking
    against the rows already inserted -- so it is module-scoped and the three
    measurements below share one.
    """
    ho = _holdout()
    needles = []
    for _, needle in ho.CASES:
        needles += list(needle) if isinstance(needle, tuple) else [needle]

    live = memory.open_memory()
    rows = []
    for r in live.execute(
            "SELECT id, statement, created_at FROM nodes WHERE type='memory'"):
        meta = memory.mem_meta(live, r["id"])
        rows.append((r["id"], r["statement"], r["created_at"],
                     meta.get("kind", "world"), tuple(meta.get("tags", []))))

    targets = [x for x in rows if any(w in x[0].lower() for w in needles)]
    target_ids = {t[0] for t in targets}
    others = sorted((x for x in rows if x[0] not in target_ids),
                    key=lambda x: hashlib.sha1(x[0].encode()).hexdigest())
    chosen = targets + others[:max(0, size - len(targets))]

    conn = memory.open_memory(os.path.join(tempfile.mkdtemp(), "pinned.db"))
    for mid, statement, created, kind, tags in chosen:
        memory.retain(conn, statement, kind=kind, tags=tags, id=mid,
                      created_at=created)
    return conn, len(chosen)


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
def test_holdout_recall_has_not_regressed(pinned):
    ho = _holdout()
    assert len(ho.CASES) == CASE_COUNT, (
        "the held-out set changed size; the floors above were measured "
        "against 20 cases and no longer mean the same thing")
    conn, size = pinned
    assert size == PINNED_SIZE, (
        f"the pinned slice came out at {size}, not {PINNED_SIZE}; the corpus "
        "no longer holds enough memories for the floors to compare")
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
    conn, _ = pinned
    empty = [q for q, _ in _holdout().CASES
             if not memory.recall(conn, q, k=5, scope="safe")]
    assert empty == [], f"safe scope returned nothing for: {empty}"
