#!/usr/bin/env python3
"""SessionStart: inject recorded traps once per session so they are not
rediscovered. 'glab mr merge lies' cost three rediscoveries in one day."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

def main() -> int:
    try:
        from loopgraph import coord
        from loopgraph.db import open_db
        conn = open_db(coord.default_db_path())
        if not coord.is_enabled(conn):
            return 0
        text = coord.brief(conn)
        if not text:
            return 0
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": text}}))
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
