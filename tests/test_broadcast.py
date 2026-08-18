"""The bus between live sessions.

Nightly distillation is the wrong cadence for a signal that exists the instant
it is created. Five interactive sessions run on this machine at once; when one
learns a belief is wrong, the others keep acting on the old one until someone
re-reads the corpus tomorrow.
"""
import importlib.util
import json
import os
import time

import pytest


@pytest.fixture
def bus(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "bcast", os.path.expanduser("~/.claude/hooks/broadcast.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "BUS", str(tmp_path / "bus.jsonl"))
    monkeypatch.setattr(m, "SEEN_DIR", str(tmp_path / "seen"))
    return m


def test_a_session_receives_what_another_published(bus):
    bus.publish("belief corrected", "the old fact was wrong", "sess-A")
    assert [r["text"] for r in bus.unseen("sess-B")] == ["the old fact was wrong"]


def test_a_session_never_receives_its_own(bus):
    """Otherwise every publish echoes back at the author on their next
    prompt, and the bus becomes a mirror."""
    bus.publish("belief corrected", "mine", "sess-A")
    assert bus.unseen("sess-A") == []


def test_reading_twice_delivers_once(bus):
    bus.publish("trap", "something bites", "sess-A")
    assert len(bus.unseen("sess-B")) == 1
    bus.mark_seen("sess-B")
    assert bus.unseen("sess-B") == []


def test_an_idle_session_still_gets_the_backlog(bus):
    """Pull, not push: a session idle for hours picks up what it missed when
    it wakes, rather than having been interrupted while it slept."""
    for i in range(3):
        bus.publish("note", f"event {i}", "sess-A")
    assert len(bus.unseen("sess-idle")) == 3


def test_stale_entries_expire(bus):
    bus.publish("note", "ancient history", "sess-A")
    rows = [json.loads(l) for l in open(bus.BUS)]
    rows[0]["ts"] = time.time() - 72 * 3600
    open(bus.BUS, "w").write(json.dumps(rows[0]) + "\n")
    assert bus.unseen("sess-B") == []


def test_a_corrupt_bus_never_breaks_a_prompt(bus):
    os.makedirs(os.path.dirname(bus.BUS), exist_ok=True)
    open(bus.BUS, "w").write("{ not json\n")
    assert bus.unseen("sess-B") == []


def test_publisher_identity_matches_reader_identity(bus, monkeypatch):
    """Publish tagged with the bridge id while read used session_id, so three
    self-published entries came back as though a peer had sent them. Same
    mismatch class as the gate's false MISMATCH, one component over."""
    monkeypatch.setenv("CLAUDE_CODE_BRIDGE_SESSION_ID", "sess-A")
    bus.publish("note", "mine", "sess-A")
    assert bus.unseen("sess-A") == []
    assert len(bus.unseen("sess-B")) == 1
