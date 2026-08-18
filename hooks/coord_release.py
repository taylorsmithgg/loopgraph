#!/usr/bin/env python3
"""PostToolUse: release a foreground agent's claim the moment it returns.

A backgrounded agent's tool_result is only an acknowledgement, not a
completion, so those are left to the lease. Fails open.
"""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    if ev.get("tool_name") != "Agent":
        return 0
    resp = json.dumps(ev.get("tool_response") or "")
    if "ackgrounded" in resp or "background" in resp.lower():
        return 0                       # not finished; lease governs it
    desc = ((ev.get("tool_input") or {}).get("description") or "").strip()
    if not desc:
        return 0
    agent = re.sub(r"[^a-z0-9]+", "-", desc.lower())[:48]
    try:
        from loopgraph import coord
        from loopgraph.db import open_db
        conn = coord.open_project_db(ev.get("cwd"))
        if not coord.is_enabled(conn):
            return 0
        freed = coord.agent_done(conn, agent, outcome="returned")
        if freed:
            print(json.dumps({"systemMessage":
                f"loopgraph: released {len(freed)} claim(s) from {agent}"}))
    except Exception as exc:
        print(f"[loopgraph] release skipped: {exc}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
