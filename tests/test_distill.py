"""Scheduled distillation.

Every mechanism here already existed and had been invoked seven times in the
life of the corpus, because each was a thing a person had to think of doing.
These tests are about the part that was missing: it running without anyone,
and saying so when it stops.
"""
import json
import os
import time

import pytest

from loopgraph import distill


def _write(p, **kw):
    d = {"ran_at": time.time(), "scanned": 10, "recurring": [],
         "corrections": [], "unconcluded": [], "kinds": {}}
    d.update(kw)
    open(p, "w").write(json.dumps(d))
    return p


def test_digest_says_it_has_never_run(tmp_path):
    out = distill.digest(state_path=str(tmp_path / "nope.json"))
    assert "never run" in out


def test_a_dead_schedule_is_announced_before_its_findings(tmp_path):
    """Stale candidates presented as current is the failure this replaces.
    The warning has to come FIRST, or the findings are read as today's."""
    p = _write(tmp_path / "d.json", ran_at=time.time() - 9 * 86400,
               recurring=[{"sessions": 9, "example": "a real recurring failure here"}])
    out = distill.digest(state_path=str(p))
    assert out.splitlines()[0].startswith("loopgraph distill: last ran")
    assert "schedule may be dead" in out


def test_fresh_and_empty_says_nothing(tmp_path):
    p = _write(tmp_path / "d.json")
    assert distill.digest(state_path=str(p)) == ""


def test_generic_prefixes_are_not_findings():
    """"Traceback (most recent call last):" topped the real list at 307
    sessions and names no failure. A big number on an empty string is worse
    than silence, because the number makes it look important."""
    rows = [{"sessions": 307, "example": "Traceback (most recent call last):"},
            {"sessions": 9, "example": "clickhouse-local not found on this host"}]
    kept = distill._informative(rows)
    assert [r["sessions"] for r in kept] == [9]


def test_near_duplicates_collapse():
    """Two OTEL lines differing only in a retry counter arrived as separate
    findings at 123 and 118 sessions -- one fact, two of the few lines a
    session start can afford."""
    rows = [
        {"sessions": 123, "example": "Transient error exporting traces to localhost:9, retry 4"},
        {"sessions": 118, "example": "Transient error exporting traces to localhost:9, retry 7"},
    ]
    assert len(distill._informative(rows)) == 1


def test_run_writes_a_readable_file_and_load_ages_it(tmp_path):
    p = str(tmp_path / "d.json")
    got = distill.run(corpus_roots=[str(tmp_path / "empty")], state_path=p)
    assert got["scanned"] == 0
    back = distill.load(state_path=p)
    assert back["age_hours"] < 1
    assert "recurring" in back and "unconcluded" in back


def test_digest_never_raises_on_a_corrupt_file(tmp_path):
    p = tmp_path / "d.json"
    p.write_text("{ this is not json")
    assert "never run" in distill.digest(state_path=str(p))
