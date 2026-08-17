"""The UserPromptSubmit contract injector: it must ask exactly when there is
nothing declared, and shut up otherwise."""

import importlib.util
import io
import json
import os
import sys

import pytest

from loopgraph import coord
from loopgraph.db import open_db
from loopgraph.graph import add_criterion

HOOK = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "hooks", "spec_prompt.py",
)
GOAL = "make the ingest pipeline stop dropping events on restart"


@pytest.fixture
def hook(tmp_path, monkeypatch):
    db = str(tmp_path / "spec.db")
    monkeypatch.setattr(coord, "default_db_path", lambda *a, **k: db)
    spec = importlib.util.spec_from_file_location("spec_prompt", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def run(prompt):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"hook_event_name": "UserPromptSubmit", "prompt": prompt})))
        buf = io.StringIO()
        real, sys.stdout = sys.stdout, buf
        try:
            mod.main()
        finally:
            sys.stdout = real
        out = buf.getvalue().strip()
        return json.loads(out) if out else {}

    run.db = db
    return run


def test_asks_for_an_end_state_when_none_is_declared(hook):
    ctx = hook(GOAL)["hookSpecificOutput"]["additionalContext"]
    assert "loopgraph add C1" in ctx


def test_silent_once_a_criterion_exists(hook):
    conn = open_db(hook.db)
    add_criterion(conn, "C1", "holds", "true", {})
    assert hook(GOAL) == {}


def test_silent_on_chatter_and_slash_commands(hook):
    assert hook("thanks") == {}
    assert hook("/loopgraph status and then tell me what it means for us") == {}


def test_silent_when_the_loop_is_off(hook, monkeypatch):
    monkeypatch.setenv("LOOPGRAPH_LOOP", "0")
    assert hook(GOAL) == {}


def test_opt_out_env(hook, monkeypatch):
    monkeypatch.setenv("LOOPGRAPH_SPEC_PROMPT", "0")
    assert hook(GOAL) == {}


def test_never_blocks_a_prompt_when_broken(hook, monkeypatch):
    monkeypatch.setattr(coord, "default_db_path",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert hook(GOAL) == {}
