"""Regression fences the repo already tells us about.

The weakest useful check in any repo is the one that runs everything and
asserts it still works (weakness.py). Every project already has one and
nobody should have to type it, so detect it and install it as a guard.

A guard is the other kind of criterion: it is green now and its job is to
stay green, where a goal criterion is red now and its job is to go green.
Installing one only ever makes sense if it is *observed* green first -- a
fence that was already down fences nothing, and would hold every future turn
open on breakage that predates the request.
"""

from __future__ import annotations

import os
import subprocess

DEFAULT_TIMEOUT = 300


def _has(root: str, *names: str) -> bool:
    return any(os.path.exists(os.path.join(root, n)) for n in names)


def _json_has_test_script(root: str) -> bool:
    import json
    try:
        with open(os.path.join(root, "package.json")) as fh:
            return bool((json.load(fh).get("scripts") or {}).get("test"))
    except Exception:
        return False


def _make_has_test(root: str) -> bool:
    try:
        with open(os.path.join(root, "Makefile")) as fh:
            return any(l.startswith(("test:", "check:")) for l in fh)
    except Exception:
        return False


def detect(root: str) -> list[dict]:
    """The project's own way of asking "does it all still work?"."""
    out: list[dict] = []
    if _has(root, "pyproject.toml", "setup.cfg", "setup.py") and _has(root, "tests", "test"):
        runner = "uv run pytest -q" if _has(root, "uv.lock") else "python -m pytest -q"
        out.append({"id": "G-tests", "cmd": runner,
                    "statement": "the python suite still passes"})
    if _has(root, "package.json") and _json_has_test_script(root):
        out.append({"id": "G-npm", "cmd": "npm test --silent",
                    "statement": "the npm test script still passes"})
    if _has(root, "Cargo.toml"):
        out.append({"id": "G-cargo", "cmd": "cargo test -q",
                    "statement": "the cargo suite still passes"})
    if _has(root, "go.mod"):
        out.append({"id": "G-go", "cmd": "go test ./...",
                    "statement": "the go suite still passes"})
    if not out and _make_has_test(root):
        out.append({"id": "G-make", "cmd": "make test",
                    "statement": "make test still passes"})
    return out


def install(conn, root: str, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """Install every detected fence that is observably green right now.

    Returns one record per candidate saying what happened, because a fence
    silently not installed is exactly the failure this whole tool exists to
    stop.
    """
    from . import coord
    from .evidence import run_evidence
    from .graph import add_criterion, drop_criterion, get_node
    from .state import record_status

    results: list[dict] = []
    for cand in detect(root):
        if get_node(conn, cand["id"]) is not None:
            results.append({**cand, "installed": False, "why": "already declared"})
            continue
        # Declare first, then prove through the ordinary evidence path: one
        # run instead of two, and the fence starts out with real evidence
        # behind it rather than sitting `unproven` until something happens to
        # run it. Withdraw it again if the suite was already red.
        add_criterion(conn, cand["id"], cand["statement"], cand["cmd"], {},
                      timeout_s=timeout)
        coord.set_node_flags(conn, cand["id"], guard=True, origin="auto")
        try:
            r = run_evidence(conn, cand["id"])
            record_status(conn, cand["id"])
            green, code = bool(r["ok"]), r["exit_code"]
            why = "" if green else (
                f"did not finish in {timeout}s" if r["timed_out"] else
                f"already failing (exit {code}) - not fencing breakage that "
                "predates the request")
        except Exception as exc:
            green, why = False, f"could not run: {exc}"
        if not green:
            drop_criterion(conn, cand["id"])
            results.append({**cand, "installed": False, "why": why})
            continue
        results.append({**cand, "installed": True, "why": "green, fenced"})
    return results
