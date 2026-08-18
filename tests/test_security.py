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
    assert "3 item(s)" in security.report(q, m)


def test_clear_marks_everything_reviewed(tmp_path):
    q, m = str(tmp_path / "q.jsonl"), str(tmp_path / "m")
    security.queue("withheld", "id-1", "an IP address", path=q)
    assert security.clear(q, m) == 1
    assert security.pending(q, m) == []
    assert "nothing outstanding" in security.report(q, m)


def test_items_queued_after_a_review_still_surface(tmp_path):
    q, m = str(tmp_path / "q.jsonl"), str(tmp_path / "m")
    security.queue("withheld", "old", "an IP address", path=q)
    security.clear(q, m)
    time.sleep(0.01)
    security.queue("withheld", "new", "an email address", path=q)
    assert [r["subject"] for r in security.pending(q, m)] == ["new"]


def test_a_broken_queue_never_fails_the_work(tmp_path):
    security.queue("withheld", "x", "y", path="/nonexistent/dir/q.jsonl")
