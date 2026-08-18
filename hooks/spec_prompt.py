#!/usr/bin/env python3
"""UserPromptSubmit: put a stated goal on the record before the work starts.

The loop gate is inert until criteria exist, and nothing was ever asking for
them -- 26 agents dispatched in the busiest repo, zero criteria declared. So
the gate had nothing to hold the turn open with.

Two things happen here, both cheap and neither blocking:

- The goal is recorded as pending. The Stop hook will ask for criteria once
  and take an explicit `loopgraph noop` waiver, so "declare nothing" stops
  being the free default it always was.
- The repo's own test command is installed as a regression fence, in a
  detached process so the prompt never waits on it (same pattern as the
  authoring audit). It is only installed if observed green first.

Deriving criteria from the prompt with a model was tried and dropped: it put
a multi-second model call on every first prompt, and executed generated shell
that nobody had read. The agent reading this already knows the request better
than a summariser of it would.
"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

MIN_PROMPT_CHARS = 40          # below this it is chatter, not a goal

CONTRACT = """\
loopgraph: no end-state is on record for this repo.

Before finishing, declare what would make this request true:
  loopgraph add C1 --statement "<what must be true when this is done>" \\
      --cmd "<shell check>" --goal
The check must be RED now and GREEN when the work is done; `add` runs it and
refuses it if it already passes. Prefer a check that runs the system over one
that greps the source - it admits every implementation that works, not just
the one currently in mind. Add one per independent claim (C1, C2, ...), and
`loopgraph link C2 C1` where C2 depends on C1.

If this request has no checkable end-state - a question, a lookup, a
judgement call - say so now and carry on:
  loopgraph noop --reason "<why>"

The Stop hook re-runs every check and will not let the turn end while one
fails, which is what makes the session drive to done instead of stopping at
the first plausible resting point. `loopgraph drop <id>` withdraws a
criterion that turns out to misread the request."""


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = (ev.get("prompt") or "").strip()
    # A harness envelope is not a request. `<task-notification>` blocks were
    # captured as stated goals three times on this machine and then sat
    # unresolved forever: the gate demanded an end-state for a string the
    # user never typed, and no one could answer for it.
    if len(prompt) < MIN_PROMPT_CHARS or prompt.startswith(("/", "<")):
        return 0
    if os.environ.get("LOOPGRAPH_SPEC_PROMPT", "") == "0":
        return 0
    try:
        from loopgraph import coord
        from loopgraph.db import open_db
        from loopgraph.graph import all_criteria

        conn = coord.open_project_db(ev.get("cwd"), ev.get("transcript_path"))
        if not coord.loop_enabled(conn):
            return 0
        goalish = [c for c in all_criteria(conn)
                   if not coord.node_flags(conn, c["id"]).get("guard")]
        if goalish:
            return 0               # already under contract; say nothing
        coord.note_goal(conn, prompt.splitlines()[0])
        _fence_in_background()
    except Exception:
        return 0                   # never block a prompt on this
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit", "additionalContext": CONTRACT}}))
    return 0


def _fence_in_background() -> None:
    """Detached, because running a test suite inline would make every first
    prompt wait on it. Failure here is silent by design: a missing fence is
    reported by `loopgraph status`, and a hung prompt is not."""
    if os.environ.get("LOOPGRAPH_BASELINE", "") == "0":
        return
    try:
        subprocess.Popen(
            [sys.executable, "-m", "loopgraph.cli", "baseline"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
            env={**os.environ, "LOOPGRAPH_SPEC_PROMPT": "0",
                 "LOOPGRAPH_LOOP": "0"},
        )
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
