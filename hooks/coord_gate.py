#!/usr/bin/env python3
"""PreToolUse gate: refuse an Agent dispatch whose scope is already claimed.

Install on PreToolUse. Silent no-op unless coordination is explicitly ON.
Exit 2 blocks the tool call and feeds stderr back to the model.

Declare scope in the dispatch prompt with a line:
    SCOPE: path/one path/two some/identifier
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    if ev.get("tool_name") != "Agent":
        return 0
    inp = ev.get("tool_input") or {}
    prompt = inp.get("prompt") or ""
    m = re.search(r"^\s*SCOPE:\s*(.+)$", prompt, re.M)
    if not m:
        return 0                      # undeclared scope is not blocked
    scope = [t for t in m.group(1).split() if t]
    if not scope:
        return 0
    try:
        from loopgraph import coord
        from loopgraph.db import open_db
        conn = coord.open_project_db(ev.get("cwd"))
        if not coord.is_enabled(conn):
            return 0
        coord.sweep_expired(conn)
        desc = (inp.get("description") or "agent").strip()
        agent = re.sub(r"[^a-z0-9]+", "-", desc.lower())[:48] or "agent"
        r = coord.agent_start(conn, agent, scope)
    except Exception as exc:          # a broken gate must never block work
        print(f"[loopgraph] gate error, allowing: {exc}", file=sys.stderr)
        return 0
    if r["ok"]:
        return 0
    lines = [f"  {c['slot']} held by {c['holder']}" for c in r["conflicts"]]
    print(
        "loopgraph: dispatch refused - scope already claimed:\n"
        + "\n".join(lines)
        + "\n\nThese agents would collide on the same write-set. Either wait for the\n"
          "holder to finish, narrow this agent's SCOPE to disjoint paths, or run\n"
          "`loopgraph release <holder>` if it is known dead.",
        file=sys.stderr,
    )
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
