"""The cross-project sweep.

Enforcement is scoped to one graph on purpose. Visibility was too, and that is
why work went unfinished: 78 graphs on this machine held 15 never-closed
criteria and 12 unresolved goals that no single command could show.
"""
import os
import sqlite3

import pytest

from loopgraph import janitor
from loopgraph.db import open_db, meta_set
from loopgraph.graph import add_criterion


def _graph(d, name, criteria=(), goal=None, goal_at=None):
    conn = open_db(os.path.join(d, f"{name}.db"))
    for cid, stmt, cmd in criteria:
        add_criterion(conn, cid, stmt, cmd, {})
    if goal is not None:
        meta_set(conn, "goal_pending", goal)
        if goal_at:
            meta_set(conn, "goal_pending_at", goal_at)
    conn.commit()
    return conn


def test_scan_reaches_every_graph_not_just_the_current_one(tmp_path):
    d = str(tmp_path)
    _graph(d, "a" * 16, [("C1", "thing a holds", "false")])
    _graph(d, "b" * 16, [("C2", "thing b holds", "false")])
    data = janitor.scan(loopgraph_dir=d, home=str(tmp_path))
    assert data["graphs"] == 2
    assert {c["id"] for c in data["criteria"]} == {"C1", "C2"}


def test_closed_criteria_are_not_loose_ends(tmp_path):
    d = str(tmp_path)
    conn = _graph(d, "c" * 16, [("C1", "already true", "true")])
    conn.execute("INSERT INTO runs (criterion_id, exit_code, stdout, stderr, "
                 "started_at, ok) VALUES ('C1', 0, '', '', '2026-01-01', 1)")
    conn.commit()
    data = janitor.scan(loopgraph_dir=d, home=str(tmp_path))
    assert data["criteria"] == []


def test_digest_is_bounded_and_says_what_it_omitted(tmp_path):
    d = str(tmp_path)
    _graph(d, "d" * 16, [(f"C{i}", f"statement number {i}", "false")
                         for i in range(40)])
    out = janitor.digest(max_lines=10, stale_days=0,
                         loopgraph_dir=d, home=str(tmp_path))
    assert len(out.splitlines()) <= 10, out
    assert "more" in out, "truncation must be admitted, not silent"


def test_digest_is_silent_when_nothing_is_loose(tmp_path):
    """It runs on every session start. A janitor that always speaks is one
    the reader learns to skip, which is how the last warning died."""
    d = str(tmp_path)
    _graph(d, "e" * 16, [])
    assert janitor.digest(loopgraph_dir=d, home=str(tmp_path)) == ""


def test_reap_clears_stale_goals_but_never_criteria(tmp_path):
    """An unresolved goal blocks a gate for a session that is long gone. An
    unfinished criterion is somebody's work, and sweeping it away silently
    would defeat the entire point of the tool."""
    d = str(tmp_path)
    _graph(d, "f" * 16, [("C1", "real unfinished work", "false")],
           goal="something nobody ever answered",
           goal_at="2020-01-01T00:00:00+00:00")
    assert janitor.reap(loopgraph_dir=d, home=str(tmp_path), dry_run=True)
    janitor.reap(loopgraph_dir=d, home=str(tmp_path), dry_run=False)
    data = janitor.scan(loopgraph_dir=d, home=str(tmp_path))
    assert data["goals"] == []
    assert len(data["criteria"]) == 1, "reap must not touch unfinished work"


def test_reap_leaves_a_fresh_goal_alone(tmp_path):
    import datetime
    d = str(tmp_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _graph(d, "1" * 16, goal="asked a moment ago", goal_at=now)
    assert janitor.reap(loopgraph_dir=d, home=str(tmp_path), dry_run=True) == []


def test_memory_db_is_not_a_project_graph(tmp_path):
    d = str(tmp_path)
    _graph(d, "memory", [("C1", "should be ignored", "false")])
    assert janitor.scan(loopgraph_dir=d, home=str(tmp_path))["graphs"] == 0


def test_a_graph_it_cannot_read_is_named_not_counted_clean(tmp_path):
    """The failure this file exists to catch, found in this file: meta_json is
    added lazily, so on an untouched graph the node query raised and the
    except-branch returned no rows -- "nothing loose" because the query broke.
    """
    d = str(tmp_path)
    p = os.path.join(d, "9" * 16 + ".db")
    sqlite3.connect(p).executescript(
        "create table meta(key text primary key, value text);"
        "create table nodes(id text, type text);"
        "insert into meta values('goal_pending','x');")
    out = janitor.digest(stale_days=0, loopgraph_dir=d, home=d)
    data = janitor.scan(loopgraph_dir=d, home=d)
    assert data["unreadable"], "an unreadable graph must be reported"
    assert "UNREADABLE" in out


def test_criteria_are_found_on_a_graph_with_no_meta_json_column(tmp_path):
    d = str(tmp_path)
    p = os.path.join(d, "8" * 16 + ".db")
    c = sqlite3.connect(p)
    c.executescript(
        "create table meta(key text primary key, value text);"
        "create table nodes(id text primary key, type text, statement text,"
        " created_at text);"
        "create table runs(id integer primary key, criterion_id text,"
        " exit_code integer);"
        "insert into nodes values('C1','criterion','no meta column here','2026-01-01');")
    c.commit()
    data = janitor.scan(loopgraph_dir=d, home=d)
    assert [x["id"] for x in data["criteria"]] == ["C1"]
