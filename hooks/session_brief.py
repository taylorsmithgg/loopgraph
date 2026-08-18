#!/usr/bin/env python3
"""SessionStart: inject recorded traps once per session so they are not
rediscovered. 'glab mr merge lies' cost three rediscoveries in one day."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        ev = {}                       # never block a session start on this
    try:
        from loopgraph import coord
        from loopgraph.db import open_db
        # Payload cwd, not os.getcwd(): the agent's shell keeps its directory
        # between tool calls, so the process cwd drifts to whatever repo was
        # last cd'd into and the brief would come from that project's graph.
        conn = coord.open_project_db(ev.get("cwd"))
        if not coord.is_enabled(conn):
            return 0
        parts = [coord.brief(conn)]
        # Loose ends from EVERY graph, not just this one. Enforcement is
        # rightly scoped to one project; visibility must not be, or work
        # stays unfinished simply because you are standing in a different
        # repo than the criterion. Bounded on purpose -- this runs on every
        # session start, and a brief that costs real context gets switched
        # off, which is the same as never having written it.
        try:
            from loopgraph import janitor
            parts.append(janitor.digest(max_lines=12))
        except Exception:
            pass                      # a janitor must never break a session
        # What the scheduled distillation found. Reads a file written by
        # launchd overnight -- mining 1,476 transcripts here would put seconds
        # on every session start, and a brief that slows the session down gets
        # removed, which returns the whole thing to being manual.
        try:
            from loopgraph import distill
            parts.append(distill.digest(max_lines=8))
        except Exception:
            pass
        text = "\n\n".join(p for p in parts if p and p.strip())
        if not text:
            return 0
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": text}}))
    except Exception:
        return 0
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
