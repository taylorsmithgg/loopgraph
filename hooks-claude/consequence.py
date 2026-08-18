#!/usr/bin/env python3
"""Refuse to end a turn that changed shared state without checking its effect.

Measured over one long session: every time Taylor asked "more?", the answer
was a consequence of a change I had just made and not followed up --

  a deny-hook installed into five running sessions without checking what it
  would deny (2.10% of all commands, replayed after the fact);
  drive-loop caps lowered on a premise later proved false, the premise
  corrected and the decision left standing;
  a correction log collected nightly and shown to nobody;
  the CLI and the gate resolving different graphs, twice, because each half
  was fixed and tested alone.

None of that needed more diligence in the abstract. Each one needed the same
question asked once: this changes something other sessions use -- what did I
check? The loop gate could not ask it, because it only holds a turn to
criteria that were declared, and criteria kept describing the deliverable
rather than its reach.

So: touching shared configuration records a debt, and the debt has to be
answered before the turn ends. Answering is one command and accepts any
sentence, including "nothing to check" -- the point is that the question gets
asked at the moment the change is made, not that it always has an interesting
answer.

Scope is deliberately narrow. Only files that other sessions load: settings,
the hooks themselves, and the global instructions. Editing project code is not
shared state and never triggers this.

Detection is by MTIME, not by intercepting the write. The first version hooked
PostToolUse(Write|Edit) and recorded nothing at all, because this agent edits
config with `python3 - <<PY` inside Bash -- the guard could not see the very
edits it existed to catch, which is the third instance today of a guard blind
to its own subject. Watching the file is unbypassable: however the change was
made, the mtime moved.
"""
import json
import os
import sys
import time

PENDING = os.path.expanduser("~/.loopgraph/consequence.jsonl")
ANSWERED_DIR = os.path.expanduser("~/.loopgraph/consequence-seen")

SHARED = (
    os.path.expanduser("~/.claude/settings.json"),
    os.path.expanduser("~/.claude/hooks/"),
    os.path.expanduser("~/AGENTS.md"),
    os.path.expanduser("~/.claude/CLAUDE.md"),
)


def _session() -> str:
    return (os.environ.get("CLAUDE_CODE_BRIDGE_SESSION_ID")
            or os.environ.get("LOOPGRAPH_SESSION") or "unknown")


def _mark_path(session: str = "") -> str:
    s = session or _session()
    safe = "".join(c for c in s if c.isalnum() or c in "-_") or "unknown"
    return os.path.join(ANSWERED_DIR, safe)


def _answered_at(session: str = "") -> float:
    """Per SESSION. A single machine-wide mark meant one session answering
    cleared the debt for all five -- the guard failing open for everyone
    except whoever happened to answer first, which is a worse property than
    not existing."""
    try:
        return float(open(_mark_path(session), encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return 0.0


def _files() -> list[str]:
    out = []
    for s in SHARED:
        if os.path.isdir(s):
            for name in sorted(os.listdir(s)):
                if name.endswith((".py", ".sh")):
                    out.append(os.path.join(s, name))
        elif os.path.isfile(s):
            out.append(s)
    return out


def outstanding(_session: str = "") -> list[str]:
    """Shared files changed since the last answer. State, not events."""
    since = _answered_at()
    if since <= 0:
        # First run: adopt the current state rather than reporting every
        # config file on the machine as an unanswered change.
        answer("baseline adopted on first run")
        return []
    changed = []
    for f in _files():
        try:
            if os.path.getmtime(f) > since:
                changed.append(f)
        except OSError:
            continue
    return sorted(changed)


def answer(text: str) -> None:
    os.makedirs(ANSWERED_DIR, exist_ok=True)
    with open(_mark_path(), "w", encoding="utf-8") as fh:
        fh.write(str(time.time()))
    try:
        with open(PENDING + ".log", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "answer": text[:500]}) + "\n")
    except OSError:
        pass


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    event = ev.get("hook_event_name", "")

    if event == "Stop":
        if ev.get("stop_hook_active"):
            return 0                    # already driving; do not stack blocks
        paths = outstanding()
        if not paths:
            return 0
        listed = "\n".join(f"  {p}" for p in paths[:6])
        print(json.dumps({"decision": "block", "reason":
            "This turn changed configuration that OTHER sessions load:\n"
            f"{listed}\n\n"
            "Five sessions run on this machine, two of them unattended crons. "
            "Say what you checked about the effect -- who it reaches, what it "
            "would have done to work already in flight, whether it rests on a "
            "premise you have actually tested. Then:\n"
            "  python3 ~/.claude/hooks/consequence.py --answered \"<what you checked>\"\n"
            "\"Nothing to check, it only affects this session\" is a valid "
            "answer, and so is \"another session changed this, reviewed and "
            "it does not affect my work\" -- five sessions share these files, "
            "so a change here is not necessarily yours. Asking is the point; "
            "every time this was skipped, the thing it would have caught was "
            "found later by being asked \"more?\"."}))
        return 0
    return 0


if __name__ == "__main__":
    if "--answered" in sys.argv:
        i = sys.argv.index("--answered")
        answer(sys.argv[i + 1] if len(sys.argv) > i + 1 else "")
        print("consequence: recorded")
        raise SystemExit(0)
    raise SystemExit(main())
