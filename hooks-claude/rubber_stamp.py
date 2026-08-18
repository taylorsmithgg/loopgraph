#!/usr/bin/env python3
"""UserPromptSubmit: catch the gate that should not have been asked.

A one-word approval is evidence, not input. If the honest reply to a turn is
"lgtm" or "do it", that turn ended on a question whose answer was already
known, and it spent a full context window at ~250k tokens to learn nothing.
Measured across 2042 sessions: 340 of 1972 prompts Taylor typed were pure
rubber stamps -- 17% of everything he wrote -- against 501 approval gates.

Why a hook and not just the AGENTS.md rule: AGENTS.md is read into context
once, at session start. Eight sessions were live when that rule was written
and not one of them could see it; the oldest had been running for four days.
A rule that only reaches sessions started after it was written cannot fix the
sessions currently doing the thing. This fires on the next prompt of every
session, running or not.

Silent on every other prompt, so it costs nothing until the defect occurs.
"""
import json
import re
import sys

# Deliberately narrow: only prompts that carry no information beyond assent.
# "yes, but check X first" is a real answer and must not trip this.
STAMP = re.compile(
    r"^\s*(?:lgtm|ok(?:ay)?|yes|yep|yeah|sure|do it|go|go ahead|proceed|continue|"
    r"approved?|sounds good|looks good|ship it|merge it?|please do|make it so|"
    r"[1-9]|y)\s*[.!]*\s*$",
    re.I,
)

NOTE = (
    "The reply to your last turn was a one-word approval. That is the signal "
    "that the turn ended on a gate you could have predicted: the answer was "
    "already implied by your own reasoning, and asking cost a full turn. "
    "Do not ask that class of question again this session -- act on the "
    "default and report. If a genuinely destructive or irreversible step "
    "still needs sign-off, finish everything it does not gate first, then "
    "state the recommendation so one word settles it."
)


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0                      # never block a prompt on this
    prompt = (ev.get("prompt") or "").strip()
    if not prompt or not STAMP.match(prompt):
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit", "additionalContext": NOTE}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
