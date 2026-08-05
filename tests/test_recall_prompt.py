"""The recall hook. Its job is to stay quiet.

A block of three plausible-looking memories injected into a prompt they have
nothing to do with teaches the reader to skim past it -- and then the one time
it matters, it gets skimmed too.
"""

import importlib.util
import io
import json
import os
import sys

import pytest

from loopgraph import memory

HOOK = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "hooks", "recall_prompt.py")

EDGE = ("EDGE-LOG-01 was silently cut over to EDGE-LOG-02 on the same IP "
        "192.0.2.10; EDGE-LOG-02 nginx never started, so the edge went blind")


@pytest.fixture
def hook(tmp_path, monkeypatch):
    db = str(tmp_path / "memory.db")
    monkeypatch.setenv("LOOPGRAPH_MEMORY_DB", db)
    monkeypatch.setenv("LOOPGRAPH_MEM_SCOPE", "full")
    monkeypatch.setattr(memory, "default_memory_db", lambda: db)
    spec = importlib.util.spec_from_file_location("recall_prompt", HOOK)
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
        if not out:
            return ""
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    run.conn = memory.open_memory(db)
    return run


def test_a_matching_prompt_gets_the_memory(hook):
    memory.retain(hook.conn, EDGE, kind="experience")
    ctx = hook("why did the edge go blind after the logger cutover?")
    assert "EDGE-LOG-02" in ctx and "possibly relevant" in ctx.lower()


def test_an_unrelated_prompt_gets_nothing(hook):
    memory.retain(hook.conn, EDGE, kind="experience")
    assert hook("write me a haiku about parsers") == ""


def test_pleasantries_get_nothing(hook):
    memory.retain(hook.conn, EDGE, kind="experience")
    assert hook("hello, can you help me please?") == ""


def test_an_empty_store_is_silent(hook):
    assert hook("why did the edge go blind after the logger cutover?") == ""


def test_slash_commands_and_stubs_are_skipped(hook):
    memory.retain(hook.conn, EDGE, kind="experience")
    assert hook("/status edge nginx cutover blind") == ""
    assert hook("edge?") == ""


def test_a_superseded_memory_is_flagged_not_hidden(hook):
    old = memory.retain(hook.conn, "the staging gateway is not in the live path")
    new = memory.supersede(hook.conn, old,
                           "the staging gateway IS in the live path since August")
    ctx = hook("is the staging gateway in the live path?")
    assert f"SUPERSEDED BY {new}" in ctx and "prefer that one" in ctx


def test_the_hook_says_how_to_correct_the_record(hook):
    memory.retain(hook.conn, EDGE, kind="experience")
    assert "--supersedes" in hook("why did the edge go blind after the cutover?")


def test_it_never_costs_a_prompt_when_the_store_is_broken(hook, monkeypatch):
    monkeypatch.setattr(memory, "open_memory",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert hook("why did the edge go blind after the logger cutover?") == ""


def test_opt_out(hook, monkeypatch):
    memory.retain(hook.conn, EDGE, kind="experience")
    monkeypatch.setenv("LOOPGRAPH_RECALL", "0")
    assert hook("why did the edge go blind after the logger cutover?") == ""


def test_the_breadcrumb_records_that_the_hook_ran_at_all(hook):
    """"NEVER fired" must mean not installed, not "installed and skipped a
    short prompt" -- telling those apart is the whole job of `mem doctor`."""
    from loopgraph.db import meta_get
    hook("hi")                                    # too short to recall on
    assert meta_get(hook.conn, "hook_seen:claude-code")


def test_a_slash_command_does_not_pretend_the_hook_worked(hook, monkeypatch):
    monkeypatch.setenv("LOOPGRAPH_HARNESS", "probe")
    hook("/status something or other")
    from loopgraph.db import meta_get
    assert meta_get(hook.conn, "hook_seen:probe") is None
