"""Security findings are queued, not announced.

"retained as sensitive (credential material or its location)" printed 98 times
mid-task. The memory is stored either way and the scope rule already applies,
so the notice bought nothing but a break in concentration -- and a stream of
security remarks during unrelated work trains a reader to skim exactly the
category that must not be skimmed.
"""
import time

from loopgraph import security


def test_queue_is_silent(capsys, tmp_path):
    security.queue("memory withheld", "some-id", "an IP address",
                   path=str(tmp_path / "q.jsonl"))
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_findings_accumulate_for_one_pass(tmp_path):
    q, m = str(tmp_path / "q.jsonl"), str(tmp_path / "m")
    for i in range(3):
        security.queue("withheld", f"id-{i}", "credential material", path=q)
    assert len(security.pending(q, m)) == 3
    assert "3 security notes are waiting" in security.report(q, m)


def test_clear_marks_everything_reviewed(tmp_path):
    q, m = str(tmp_path / "q.jsonl"), str(tmp_path / "m")
    security.queue("withheld", "id-1", "an IP address", path=q)
    assert security.clear(q, m) == 1
    assert security.pending(q, m) == []
    assert "Every security note has been dealt with" in security.report(q, m)


def test_items_queued_after_a_review_still_surface(tmp_path):
    q, m = str(tmp_path / "q.jsonl"), str(tmp_path / "m")
    security.queue("withheld", "old", "an IP address", path=q)
    security.clear(q, m)
    time.sleep(0.01)
    security.queue("withheld", "new", "an email address", path=q)
    assert [r["subject"] for r in security.pending(q, m)] == ["new"]


def test_retraction_withdraws_a_finding(tmp_path):
    q, m = str(tmp_path / "q.jsonl"), str(tmp_path / "m")
    security.queue("withheld", "gone", "credential material", path=q)
    security.queue("withheld", "kept", "an IP address", path=q)
    security.retract("gone", path=q)
    assert [r["subject"] for r in security.pending(q, m)] == ["kept"]


def test_a_tombstone_is_never_itself_a_finding(tmp_path):
    q, m = str(tmp_path / "q.jsonl"), str(tmp_path / "m")
    security.retract("never-queued", path=q)
    assert security.pending(q, m) == []
    assert "Every security note has been dealt with" in security.report(q, m)


def test_retracting_twice_is_harmless(tmp_path):
    q, m = str(tmp_path / "q.jsonl"), str(tmp_path / "m")
    security.queue("withheld", "gone", "credential material", path=q)
    security.retract("gone", path=q)
    security.retract("gone", path=q)
    assert security.pending(q, m) == []


def test_a_subject_queued_again_after_retraction_resurfaces(tmp_path):
    # Re-retaining a memory under the same id is the ordinary case: the id is
    # a slug of the text. A retraction must not deafen the queue to that
    # subject for the rest of time.
    q, m = str(tmp_path / "q.jsonl"), str(tmp_path / "m")
    security.queue("withheld", "same-id", "credential material", path=q)
    security.retract("same-id", path=q)
    time.sleep(0.01)
    security.queue("withheld", "same-id", "an email address", path=q)
    assert [r["detail"] for r in security.pending(q, m)] == ["an email address"]


def test_a_broken_queue_never_fails_a_retraction(tmp_path):
    security.retract("x", path="/nonexistent/dir/q.jsonl")


def test_a_broken_queue_never_fails_the_work(tmp_path):
    security.queue("withheld", "x", "y", path="/nonexistent/dir/q.jsonl")


def test_one_torn_line_does_not_empty_the_queue(tmp_path):
    """The parse used to sit inside a try that returned [] on ValueError, so
    a single interrupted append reported "nothing outstanding" over the top
    of an untriaged account compromise. Retraction doubles the writes into
    this file, which makes a torn line likelier, not rarer."""
    q, m = str(tmp_path / "q.jsonl"), str(tmp_path / "m")
    security.queue("withheld", "before", "an IP address", path=q)
    with open(q, "a", encoding="utf-8") as fh:
        fh.write('{"ts": 1788, "kind": "open compr\n')      # torn mid-write
    security.queue("open compromise", "real-account", "needs revocation",
                   path=q)
    assert [r["subject"] for r in security.pending(q, m)] == ["before",
                                                             "real-account"]
    assert "2 security notes are waiting" in security.report(q, m)
