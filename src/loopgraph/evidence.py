"""Executes evidence commands. The only writer of run results."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess

from .db import utcnow
from .graph import get_node

# Boundary-anchored: a digit-run is rejected only when it is genuinely
# part of a decimal or dotted version number -- preceded by a '.' that
# itself follows a digit ("0.02" rejects "02"), or followed by a '.' that
# precedes a digit ("v1.2" rejects "1"). A bare trailing '.' (sentence-
# final punctuation, as in "Matches found: 0.") is NOT proof of a decimal
# and must not swallow the digit before it. No re.S/re.M needed -- \d and
# the lookaround classes match across newlines unconditionally; re.findall
# scans the whole string regardless, so the LAST token in a multi-line
# stdout is still the one that wins.
_INT_TOKEN = re.compile(r"(?<!\d)(?<!\d\.)-?\d+(?!\d)(?!\.\d)")


def _last_int(stdout: str) -> int | None:
    matches = _INT_TOKEN.findall(stdout)
    return int(matches[-1]) if matches else None


def validate_expect(expect: dict) -> None:
    """Raise ValueError if `expect` is not a well-formed expectation dict.

    Called both at authoring time (graph.add_criterion) and at evaluation
    time (evaluate), so a bad expectation is rejected loudly rather than
    coercing to an unintended meaning (e.g. bool("false") is True) or
    silently reading as a decided failure.
    """
    if not isinstance(expect, dict):
        raise ValueError(f"expect must be a dict, got {type(expect).__name__}")
    for key, value in expect.items():
        if key == "exit_zero":
            if not isinstance(value, bool):
                raise ValueError(
                    f"exit_zero must be a bool, got {value!r}"
                )
        elif key == "stdout_int_gte":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"stdout_int_gte must be an int, got {value!r}"
                )
        elif key in ("stdout_contains", "stdout_matches"):
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string, got {value!r}")
            if key == "stdout_matches":
                try:
                    re.compile(value)
                except re.error as exc:
                    raise ValueError(
                        f"stdout_matches is not a valid regex: {exc}"
                    ) from exc
        else:
            raise ValueError(f"unknown expectation key: {key}")


def evaluate(expect: dict, exit_code: int, stdout: str) -> bool:
    validate_expect(expect)
    # exit_zero: True is implied ALWAYS -- unless the author explicitly
    # supplies an exit_zero key, in which case the author's value wins.
    # An empty dict still means exit_zero: True and nothing else.
    if (exit_code == 0) is not bool(expect.get("exit_zero", True)):
        return False
    for key, want in expect.items():
        if key == "exit_zero":
            continue
        elif key == "stdout_int_gte":
            n = _last_int(stdout)
            if n is None or n < int(want):
                return False
        elif key == "stdout_contains":
            if str(want) not in stdout:
                return False
        elif key == "stdout_matches":
            if not re.search(str(want), stdout, re.M):
                return False
    return True


def run_evidence(conn: sqlite3.Connection, criterion_id: str) -> sqlite3.Row:
    node = get_node(conn, criterion_id)
    if node is None:
        raise ValueError(f"no such criterion: {criterion_id}")
    cmd = node["evidence_cmd"]
    if not cmd:
        raise ValueError(f"criterion {criterion_id} has no evidence command")

    started = utcnow()
    cur = conn.execute(
        "INSERT INTO runs (criterion_id, started_at) VALUES (?, ?)",
        (criterion_id, started),
    )
    run_id = cur.lastrowid

    timed_out = 0
    exit_code = None
    stdout = stderr = ""
    try:
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=node["timeout_s"],
            )
        except subprocess.TimeoutExpired as exc:
            timed_out = 1
            exc_stdout = exc.stdout or ""
            exc_stderr = exc.stderr or ""
            # TimeoutExpired.stdout/.stderr can be raw bytes even when
            # text=True was passed to subprocess.run — decode BEFORE any
            # string operation (e.g. the marker concat below).
            if isinstance(exc_stdout, bytes):
                exc_stdout = exc_stdout.decode(errors="replace")
            if isinstance(exc_stderr, bytes):
                exc_stderr = exc_stderr.decode(errors="replace")
            stdout = exc_stdout
            stderr = exc_stderr + "\n[loopgraph] timed out"
        else:
            exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr

        ok = None if timed_out else int(
            evaluate(json.loads(node["expect_json"]), exit_code, stdout)
        )

        conn.execute(
            "UPDATE runs SET ended_at=?, exit_code=?, stdout=?, stderr=?, "
            "timed_out=?, ok=? WHERE id=?",
            (utcnow(), exit_code, stdout, stderr, timed_out, ok, run_id),
        )
    except Exception as exc:
        # Any failure after the run row was inserted (subprocess launch
        # errors, malformed expect_json, a broken evaluate() call, ...)
        # must still finalise the row. An error is neither success nor a
        # decided failure: ok stays NULL so the criterion reads as
        # `unproven`, never `open`, in the status derivation. Never
        # record an error as ok=0.
        note = f"[loopgraph] error: {exc}"
        conn.execute(
            "UPDATE runs SET ended_at=?, exit_code=?, stdout=?, stderr=?, "
            "timed_out=?, ok=? WHERE id=?",
            (
                utcnow(),
                None,
                stdout,
                f"{stderr}\n{note}" if stderr else note,
                0,
                None,
                run_id,
            ),
        )
        raise

    return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
