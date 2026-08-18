#!/usr/bin/env python3
"""PreToolUse(Bash): warn when a command matches a KNOWN, repeatedly-hit trap.

Recall fires on the user's PROMPT. Traps fire on an ACTION, and those are not
the same moment. The corpus has held "macOS has no timeout binary --
rediscovered in 161 separate sessions" for weeks, and it was rediscovered
again in the session that wrote this file. Retrieval was never the problem:
nothing was asking at the moment the mistake was about to be made.

Seven memories account for 595 rediscoveries between them, and none of them
is here. Replaying 109,980 real commands showed why: frequency is not
severity. git-outside-repo, `timeout` and an unquoted glob are the three most
common failures in that history, and all three fail loudly, immediately, and
cost exactly one retry against a self-explanatory error. Intercepting 2.1% of
all work to prevent that is a worse trade than the failure.

One rule survives, at 0.0155%: a second logstash JVM on a live legacy logger
took a 40-tenant production box down for 5h20m, and nothing in the command
says so at the time. Expensive, silent, and irreversible -- which is the only
shape that earns an interruption.

The knowledge is not in this file. It is in the memory corpus, it is recalled,
and it is now correct about zsh rather than ugrep. This file is only the small
part that is worth blocking someone over.

Inferring the trap from prose was tried first and measured: ranking the whole
command against the corpus put "timeout 600 ..." on a redpanda note, and a
literal-substring-plus-trap-word version fired on `git status`, `python3
script.py` and `uv run pytest` -- 4 false positives in 10 ordinary commands,
while still attributing two of its four real hits to the wrong memory. A
warning that fires on `git status` is wallpaper within a day, and wallpaper
is the failure this whole file exists to prevent. So: a curated table, matched
exactly, and nothing clever.

Measured, because it is not obvious: a PreToolUse hook that exits 0 has BOTH
its stderr and its systemMessage discarded, and `ask` is auto-approved
silently under this machine's permission mode. `deny` is the only channel that
reaches a session at all -- which is precisely why the bar for using it has to
be this high.
"""
import json
import os
import re
import sys

# (compiled test, memory id, one-line reminder)
# Keep this SHORT. Every entry is a claim that the warning is worth reading.
#
# Entries must be PRE-EMPTABLE *and worth a block*. Both halves matter, and
# both were learned by replaying 109,369 real historical commands through
# this table:
#
#   git-outside-repo was removed at 1,896 hits (1.7% of every command ever
#   run here) despite being the most-repeated failure in the corpus. Its cost
#   when it happens is one retry against a self-explanatory error. Blocking
#   1.7% of all work to prevent that is a worse trade than the failure.
#
#   The ugrep rule was removed because its PREMISE was false: retested,
#   `grep` here is /usr/bin/grep and the unquoted --include it forbade works
#   fine. It was denying 474 commands on a stale memory, now superseded.
#
# Applied consistently, the criterion empties most of this table. git-outside-
# repo was dropped because its cost is one retry against a self-explanatory
# error -- and `timeout` (194 hits) and the unquoted glob (456) are exactly
# the same: they fail loudly, immediately, and cheaply. Keeping them was
# inconsistency, not caution. What survives is the one failure that is
# expensive and silent: a second logstash JVM on a live legacy logger took a
# 40-tenant production box down for 5h20m, and nothing about the command says
# so at the time.
#
# The knowledge did not go anywhere -- it is in the corpus, it is recalled,
# and it is now correct about zsh rather than ugrep. What is gone is the
# interception. A guard is worth its interruption only when the failure it
# prevents is worse than being interrupted. AWS SSO expiry was tried here and removed -- it is real (42
# sessions) but you meet it when a call fails, not before, so the entry would
# have fired on every aws command to tell you what the error will already
# say. That is how a table like this rots.
TRAPS = [
    (re.compile(r"\blogstash\s+-t\b|\blogstash\b[^|;]*--config\.test_and_exit"),
     "feedback_never_config_test_on_live_logger",
     "NEVER run a second logstash JVM on a live legacy MSS-LOG-* host: it "
     "hung a 40-tenant prod box for 5h20m."),
]

HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?.*?^\s*\1\s*$",
                     re.S | re.M)


def strip_inert(cmd: str) -> str:
    """Remove text that is DATA, not a command about to run.

    A heredoc body is file content: writing a script that mentions
    `grep --include=*.py` is not running it, and denying that blocked the
    very experiment meant to check whether the memory behind the rule is
    still true. A guard that prevents its own falsification is unfalsifiable,
    which is a worse property than being wrong.
    """
    return HEREDOC.sub(" ", cmd)


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    raw = ((ev.get("tool_input") or {}).get("command") or "").strip()
    if not raw:
        return 0
    cmd = strip_inert(raw)
    # Working on the trap table itself, or reading a memory about one, is not
    # walking into it.
    if "command_trap" in cmd or "mem retain" in cmd or "mem recall" in cmd:
        return 0

    hits = [(mid, note) for test, mid, note in TRAPS if test.search(cmd)]


    if not hits:
        return 0
    # `deny`, not systemMessage. Measured: a PreToolUse hook that exits 0 has
    # BOTH its stderr and its systemMessage discarded -- instrumented the hook
    # to a file and watched it fire correctly on `git log` outside a repo and
    # on `timeout`, while the warning reached nobody. An advisory that cannot
    # be seen is the failure this file was written to fix, wearing the costume
    # of a fix.
    #
    # Denying is defensible precisely because the table is small and exact
    # (measured 0 false positives in 12 ordinary commands): every entry is a
    # command that was going to fail or do harm, so the block costs a retry
    # that was owed anyway and replaces a confusing error with the reason.
    lines = "\n".join(f"{note}  ({mid})" for mid, note in hits[:2])
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "loopgraph: recorded trap -- " + lines,
    }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
