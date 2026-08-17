#!/usr/bin/env python3
"""Stop hook: refuse to end the turn while the specification is unmet.

On by default, inert until criteria are declared. Fails open on any error.
Cannot trap a session: it gives up after coord.max_blocks() consecutive blocks
and names the terminal state instead.
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

STAGNATION_TURNS = 8

SPEC_DEMAND = """\
loopgraph: this turn is about to end with no end-state on record.

Stated goal: %s

Do one of these two things, then stop:

1. Declare what would make it true, and prove it:
     loopgraph add C1 --statement "<what must be true>" --cmd "<check>" --goal
   The check has to be RED right now and GREEN when the work is done - `add`
   runs it and refuses it if it already passes, because a check that is
   already green cannot tell done from not-done. Prefer a check that runs the
   system over one that greps the source: it admits every implementation that
   works instead of the one you happen to have in mind.

2. Say there is nothing to check, on the record:
     loopgraph noop --reason "<why this has no checkable end-state>"
   Questions, lookups and judgement calls belong here. This is not a defeat
   and costs nothing - it just has to be a decision rather than a silence."""

def _loose_note(loose) -> str:
    return ("loopgraph: open criteria NOT enforced in this session - "
            + "; ".join(f"{u['id']} ({u['why']})" for u in loose)
            + ". `loopgraph adopt <id>` to take one on, `loopgraph drop <id>` "
              "to remove it.")


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        from loopgraph import coord
        from loopgraph.db import open_db
        from loopgraph.evidence import run_evidence
        from loopgraph.graph import all_criteria
        from loopgraph.rules import terminal_state, tick
        from loopgraph.state import derive_status, record_status

        conn = open_db(coord.default_db_path(ev.get("cwd")))
        if not coord.loop_enabled(conn):
            return 0
        # Breadcrumb: if the hook and the CLI ever disagree about who this
        # session is, every criterion the CLI stamps looks foreign to the
        # gate. `status` prints both so that shows up as a mismatch instead
        # of as a gate that mysteriously stopped gating.
        from loopgraph.db import meta_set
        meta_set(conn, "last_gate_session", coord.session_key())
        # Per-session proof that the hook and the CLI compute the same key.
        # The shared slot above is last-writer-wins across every session in
        # this db, so on its own it cannot tell "our gate disagrees" from
        # "a sibling stopped after us".
        meta_set(conn, "gate_seen:" + (coord.session_key() or "-"), "1")
        meta_set(conn, "gate_seen_any", "1")

        # `stop_hook_active` is true only while the harness is ALREADY
        # continuing because a stop hook blocked - it is the loop guard, not
        # a capability flag. It is false on every ordinary first stop, which
        # is exactly when blocking works. Reading false as "blocking
        # impossible" made this gate a no-op on every normal turn: the drive
        # loop never ran once, in any repo. False just means a fresh stop
        # chain, so the consecutive-block count starts over here.
        if not ev.get("stop_hook_active", False):
            coord.clear_blocks(conn)

        # Another session's goals are not this turn's business. The database
        # is keyed by repo root, so every session outside a git repo shares
        # one graph; without this, two unrelated sessions in $HOME hold each
        # other's turns open forever. Guards stay in force for everyone.
        crits = [c for c in all_criteria(conn) if coord.owned_here(conn, c["id"])]

        # A goal was stated and nothing was declared to hold it to. The gate
        # is inert with no criteria, so declaring none was always the cheapest
        # way past it - which is how a whole tool sat armed and idle. Guards
        # do not count: a green test suite is not a statement of what this
        # request means. Ask once, take an explicit waiver, then get out of
        # the way; a nag that cannot be ended is worse than no nag.
        pending = coord.goal_pending(conn)
        goalish = [c for c in crits
                   if not coord.node_flags(conn, c["id"]).get("guard")]
        if pending and not goalish:
            n = coord.note_spec_block(conn)
            if n > coord.MAX_SPEC_BLOCKS:
                coord.clear_goal(conn, "asked twice, no criteria declared")
                print(json.dumps({"systemMessage":
                    "loopgraph: no criteria were declared for this goal - "
                    "the turn is ending UNVERIFIED, not verified."}))
                return 0
            print(json.dumps({"decision": "block", "reason": SPEC_DEMAND % pending}))
            return 0
        if pending and goalish:
            coord.clear_goal(conn)                 # the contract is now real

        # Anything open that this session is not held to gets named on the
        # way past. An unenforced criterion that nobody mentions is the
        # original bug with better manners.
        loose = coord.unenforced_criteria(conn)

        if not crits:
            # Once per set, not once per stop. This is the path a long
            # session with no criteria of its own takes on EVERY turn, and
            # unsuppressed it is where 432 of one session's repeats came
            # from. `loose_note_due` speaks again as soon as the set changes.
            if loose and coord.loose_note_due(conn, loose):
                print(json.dumps({"systemMessage": _loose_note(loose)}))
            return 0                               # nothing of ours; not silent
        tick(conn)                                 # turn counter feeds R-01

        def sweep(ids):
            for cid in ids:
                try:
                    run_evidence(conn, cid)
                    record_status(conn, cid)
                except Exception:
                    pass                           # stays unproven, never closed

        # Cheap subset every turn: only what is not already closed.
        sweep([c["id"] for c in crits if derive_status(conn, c["id"]) != "closed"])
        cfg = {"stagnation_turns": STAGNATION_TURNS, "budget_tokens": None}
        mine = {c["id"] for c in crits}
        ts = terminal_state(conn, cfg, only=mine)

        # Before declaring success, re-verify EVERYTHING. Without this a
        # criterion that closed earlier can be broken later and never be
        # re-checked - success would be asserted on stale evidence.
        if ts == "success":
            sweep([c["id"] for c in crits])
            ts = terminal_state(conn, cfg, only=mine)
    except Exception as exc:
        print(json.dumps({"systemMessage":
            f"loopgraph: loop gate error, allowing stop ({exc})"}))
        return 0

    if ts == "success":
        coord.clear_blocks(conn)
        if loose:
            # Deliberately NOT deduped. Success here means "everything this
            # session signed up for". Saying it while something else sits
            # open, unsaid, is how a green light starts meaning nothing --
            # and a caveat delivered thirty turns before the success it
            # qualifies has not been delivered. This is the one moment the
            # repetition buys something, and success is rare enough that it
            # cannot become the wallpaper the other paths were.
            print(json.dumps({"systemMessage": _loose_note(loose)}))
        return 0
    if ts in ("exhausted", "stalled", "blocked", "no-op"):
        coord.clear_blocks(conn)
        say_loose = loose and coord.loose_note_due(conn, loose)
        print(json.dumps({"systemMessage":
            f"loopgraph: terminal state {ts} - allowing stop, NOT success"
            + (" " + _loose_note(loose) if say_loose else "")}))
        return 0

    cap = coord.max_blocks()
    n = coord.note_block(conn)
    if n > cap:
        coord.clear_blocks(conn)
        print(json.dumps({"systemMessage":
            f"loopgraph: {n-1} consecutive blocks, giving up - treat as stalled"}))
        return 0

    open_lines = []
    for c in all_criteria(conn):
        if not coord.owned_here(conn, c["id"]):
            continue
        st = derive_status(conn, c["id"])
        if st == "closed":
            continue
        row = conn.execute(
            "SELECT exit_code, stdout FROM runs WHERE criterion_id=? ORDER BY id DESC LIMIT 1",
            (c["id"],)).fetchone()
        tail = ((row["stdout"] or "").strip().splitlines() or [""])[-1] if row else ""
        open_lines.append(f"  {c['id']} {st}: {c['statement']}"
                          + (f"\n    exit={row['exit_code']} {tail}" if row else ""))
    print(json.dumps({"decision": "block", "reason":
        "loopgraph: specification not met (block %d/%d). Do not stop and do "
        "not report success. Work the open criteria below - each line ends "
        "with the failing evidence command's own output, which is the thing "
        "that has to change. Re-run `loopgraph run <id>` to reprove one.\n%s"
        % (n, cap, "\n".join(open_lines))}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
