#!/usr/bin/env python3
"""A bus between live sessions, read on the next prompt.

Nightly distillation was the wrong cadence for a signal that exists the
instant it is created. Five interactive sessions run on this machine at once;
when one of them learns that a belief is wrong, the other four keep acting on
it until someone re-reads the corpus tomorrow. Today three memories were
superseded -- including one saying `grep` fails because of ugrep, which is
false -- and no running session would have found out.

Pull, not push. Entries are appended to a file and each session picks up what
it has not seen on its NEXT prompt. Nobody is interrupted mid-task, there is
no protocol to keep working, and a session that is idle for a day simply gets
the backlog when it wakes.

Deliberately narrow about what is worth broadcasting:
  - a belief was CORRECTED (a supersede): other sessions may be acting on the
    old one right now, which is the whole point;
  - a trap was discovered that will bite anyone.
Not every memory. Five sessions times every retain is spam, and spam here
means the next real correction arrives inside noise.
"""
import json
import os
import sys
import time

# Overridable, because the end-to-end check for this file published a test
# entry onto the REAL bus and every session on the machine received it for
# hours. A verification that cannot be run without polluting production will
# either pollute production or not be run.
BUS = os.environ.get("LOOPGRAPH_BROADCAST_BUS") or os.path.expanduser(
    "~/.loopgraph/broadcast.jsonl")
SEEN_DIR = os.environ.get("LOOPGRAPH_BROADCAST_SEEN") or os.path.expanduser(
    "~/.loopgraph/broadcast-seen")
MAX_AGE_H = 24
MAX_SHOW = 3


def publish(kind: str, text: str, session: str = "") -> None:
    os.makedirs(os.path.dirname(BUS), exist_ok=True)
    with open(BUS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.time(), "kind": kind,
                             "text": text[:400], "session": session}) + "\n")


def _seen_path(session: str) -> str:
    safe = "".join(c for c in (session or "unknown") if c.isalnum() or c in "-_")
    return os.path.join(SEEN_DIR, safe or "unknown")


def unseen(session: str) -> list[dict]:
    """Entries this session has not read, newest last. Never its own."""
    try:
        with open(BUS, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
    except (OSError, ValueError):
        return []
    try:
        mark = float(open(_seen_path(session), encoding="utf-8").read().strip())
    except (OSError, ValueError):
        mark = 0.0
    cutoff = time.time() - MAX_AGE_H * 3600
    return [r for r in rows
            if r.get("ts", 0) > mark and r.get("ts", 0) > cutoff
            and r.get("session") != session]


def mark_seen(session: str) -> None:
    os.makedirs(SEEN_DIR, exist_ok=True)
    tmp = _seen_path(session) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(str(time.time()))
    os.replace(tmp, _seen_path(session))


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    # Identity must match the PUBLISHER's, or a session reads back its own
    # messages -- observed immediately: publish tagged with the bridge id
    # while read used session_id, so three self-published entries arrived as
    # though another session had sent them. Same mismatch class as the gate's
    # false MISMATCH earlier today, one component over.
    session = (os.environ.get("CLAUDE_CODE_BRIDGE_SESSION_ID")
               or ev.get("session_id") or "")
    rows = unseen(session)
    # Mark regardless of whether anything is shown, so a backlog older than
    # the window does not re-arrive forever.
    mark_seen(session)
    if not rows:
        return 0
    lines = ["Another session on this machine learned something since your "
             "last prompt:"]
    for r in rows[-MAX_SHOW:]:
        age_m = int((time.time() - r.get("ts", 0)) / 60)
        lines.append(f"  [{r.get('kind', 'note')}, {age_m}m ago] {r.get('text', '')}")
    if len(rows) > MAX_SHOW:
        lines.append(f"  (+{len(rows) - MAX_SHOW} more; `loopgraph mem recall` "
                     f"for the full record)")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
