"""Auto-installed regression fences.

The dangerous version of this feature installs a fence that was already down
and then blocks every turn on breakage that predates the request.
"""

import pytest

from loopgraph import coord
from loopgraph.baseline import detect, install
from loopgraph.db import open_db
from loopgraph.graph import all_criteria


@pytest.fixture
def conn(tmp_path):
    return open_db(str(tmp_path / "g.db"))


def _python_repo(root, passing=True):
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "tests").mkdir()
    (root / "runner").write_text("")
    return root


def test_detects_the_python_suite(tmp_path):
    _python_repo(tmp_path)
    assert [c["id"] for c in detect(str(tmp_path))] == ["G-tests"]


def test_prefers_uv_when_the_lock_is_there(tmp_path):
    _python_repo(tmp_path)
    assert "uv run" not in detect(str(tmp_path))[0]["cmd"]
    (tmp_path / "uv.lock").write_text("")
    assert detect(str(tmp_path))[0]["cmd"].startswith("uv run")


def test_detects_node_only_when_a_test_script_exists(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "x"}}')
    assert detect(str(tmp_path)) == []
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    assert [c["id"] for c in detect(str(tmp_path))] == ["G-npm"]


def test_detects_nothing_in_an_empty_directory(tmp_path):
    assert detect(str(tmp_path)) == []


def test_installs_a_green_fence_and_marks_it(conn, tmp_path, monkeypatch):
    _python_repo(tmp_path)
    monkeypatch.setattr("loopgraph.baseline.detect", lambda root: [
        {"id": "G-tests", "cmd": "true", "statement": "suite passes"}])
    got = install(conn, str(tmp_path))
    assert got[0]["installed"] is True
    flags = coord.node_flags(conn, "G-tests")
    assert flags["guard"] is True and flags["origin"] == "auto"


def test_refuses_to_fence_an_already_failing_suite(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("loopgraph.baseline.detect", lambda root: [
        {"id": "G-tests", "cmd": "false", "statement": "suite passes"}])
    got = install(conn, str(tmp_path))
    assert got[0]["installed"] is False
    assert "already failing" in got[0]["why"]
    assert all_criteria(conn) == []


def test_a_suite_that_never_finishes_is_not_fenced(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("loopgraph.baseline.detect", lambda root: [
        {"id": "G-tests", "cmd": "sleep 5", "statement": "suite passes"}])
    got = install(conn, str(tmp_path), timeout=1)
    assert got[0]["installed"] is False and "finish" in got[0]["why"]


def test_installing_twice_does_not_duplicate(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("loopgraph.baseline.detect", lambda root: [
        {"id": "G-tests", "cmd": "true", "statement": "suite passes"}])
    install(conn, str(tmp_path))
    second = install(conn, str(tmp_path))
    assert second[0]["installed"] is False
    assert len(all_criteria(conn)) == 1


def test_a_fence_starts_with_real_evidence_not_unproven(conn, tmp_path, monkeypatch):
    """`unproven` for a check that was just run is the tool lying quietly."""
    from loopgraph.state import derive_status
    monkeypatch.setattr("loopgraph.baseline.detect", lambda root: [
        {"id": "G-tests", "cmd": "true", "statement": "suite passes"}])
    install(conn, str(tmp_path))
    assert derive_status(conn, "G-tests") == "closed"


def test_a_guard_is_not_reported_as_an_orphan(conn, tmp_path, monkeypatch):
    from loopgraph.rules import evaluate_rules
    monkeypatch.setattr("loopgraph.baseline.detect", lambda root: [
        {"id": "G-tests", "cmd": "false", "statement": "suite passes"}])
    install(conn, str(tmp_path))          # refused, so declare it by hand
    from loopgraph import coord
    from loopgraph.graph import add_criterion
    add_criterion(conn, "G2", "fence", "false", {})
    coord.set_node_flags(conn, "G2", guard=True)
    assert not [r for r in evaluate_rules(conn, {}) if r["rule"] == "R-06"]
