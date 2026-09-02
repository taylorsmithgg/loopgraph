"""Command line entry point. Exit 0 means the specification is met."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

from . import coord, memory
from .db import meta_get, open_db
from .evidence import run_evidence
from .graph import add_criterion, all_criteria, drop_criterion, link
from .rules import add_spend, evaluate_rules, terminal_state, tick
from .state import blocked, record_status, statuses, workable
from .weakness import BASE_SCORE, weakness


def _report(conn, cfg) -> dict:
    # One clock reading threaded through every call below. Each of these
    # independently defaults to datetime.now(timezone.utc) when `now` is
    # omitted, and terminal_state() recomputes statuses()/evaluate_rules()
    # internally -- five (really twelve, for a 2-criterion check)
    # independent readings otherwise, any one of which could land on the
    # opposite side of a criterion's staleness boundary from the others
    # within the same report. This is a single consistent clock, not
    # caching: status is still recomputed on every read.
    now = datetime.now(timezone.utc)
    return {
        "statuses": statuses(conn, now=now),
        "workable": workable(conn, now=now),
        "blocked": blocked(conn, now=now),
        "rules": evaluate_rules(conn, cfg, now=now),
        "terminal_state": terminal_state(conn, cfg, now=now),
    }


def _gate_line(conn, db) -> str:
    import os
    sc = "ON " if coord.is_enabled(conn) else "off"
    lp = "ON " if coord.loop_enabled(conn) else "off"
    forced = [n for n, v in (("LOOPGRAPH_COORD", "scope"), ("LOOPGRAPH_LOOP", "loop"))
              if os.environ.get(n, "") == "0"]
    note = f"  (forced off by {', '.join(forced)})" if forced else ""
    mine = coord.session_key() or "(none)"
    gate_saw = meta_get(conn, "last_gate_session", None)
    # A disagreement here means everything the CLI stamps looks foreign to
    # the Stop hook, i.e. the gate quietly stops gating. Say it, loudly.
    #
    # But `last_gate_session` is one slot and $HOME is not a git repo, so
    # every session working there shares this db: the slot holds whichever
    # SIBLING stopped most recently, not evidence about us. Comparing
    # against it alone cried MISMATCH -- "criteria added here will not bind
    # it" -- at a session whose identity was in fact perfectly consistent,
    # and sent a reader hunting an identity bug that did not exist. A
    # diagnostic that lies costs more than one that is silent.
    #
    # `gate_seen:<key>` is per-session, so our own gate having run is proof
    # the two agree, whoever stamped the shared slot afterwards.
    proven = meta_get(conn, "gate_seen:" + (coord.session_key() or "-"), None)
    if proven is not None or gate_saw is None or gate_saw == coord.session_key():
        mismatch = ""
    elif meta_get(conn, "gate_seen_any", None) is None:
        mismatch = ""                      # no gate has ever run here; nothing to compare
    else:
        # Unverified, not broken: another session's gate ran here and ours
        # has not stopped yet, so we cannot yet know whether the keys agree.
        mismatch = (f"  (a sibling session's gate ran here last: {gate_saw}; "
                    "this session has not stopped yet, so key agreement is "
                    "unverified - re-check after one stop)")
    return (f"gates: scope={sc} loop={lp}{note}\n"
            f"session: {mine}{mismatch}\ndb: {db}")


def _print_human(conn, report) -> None:
    st = report["statuses"]
    counts = {s: sum(1 for v in st.values() if v == s) for s in set(st.values())}
    print(" ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty")
    for cid, status in sorted(st.items()):
        if status == "closed":
            continue
        run = conn.execute(
            "SELECT * FROM runs WHERE criterion_id=? ORDER BY id DESC LIMIT 1",
            (cid,),
        ).fetchone()
        detail = ""
        if run is not None:
            tail = (run["stdout"] or "").strip().splitlines()[-3:]
            detail = f" exit={run['exit_code']} {' | '.join(tail)}"
        flags = coord.node_flags(conn, cid)
        mark = "".join(
            f" [{k}]" for k in ("auto", "guard") if flags.get(k)
        ) + (f" [{flags['origin']}]" if flags.get("origin") else "")
        print(f"{cid}{mark} {status}:{detail}")
    for cid, deps in sorted(report["blocked"].items()):
        print(f"{cid} blocked by {', '.join(deps)}")
    for rule in report["rules"]:
        print(f"{rule['rule']} {rule['detail']}")
    aud = coord.audit_state(conn)
    if aud["gameable"]:
        print(f"GAMEABLE checks: {', '.join(aud['gameable'])}")
    if aud.get("sabotage_only"):
        print(f"gameable only by sabotaging the environment (PATH shim, "
              f"conftest, ...): {', '.join(aud['sabotage_only'])} - this "
              "defeats any check, so it is not a reason to rewrite one")
    if aud["unaudited"]:
        print(f"unaudited checks: {len(aud['unaudited'])} "
              f"({', '.join(aud['unaudited'][:5])}) - run `loopgraph game`")
    # Not enforced here must never mean not mentioned: an open criterion that
    # quietly stopped counting is the same bug in a new coat.
    for u in coord.unenforced_criteria(conn):
        print(f"{u['id']} open but NOT enforced in this session ({u['why']}): "
              f"{u['statement']} - `loopgraph adopt {u['id']}` to take it on, "
              f"`loopgraph drop {u['id']}` to remove it")
    pending = coord.goal_pending(conn)
    if pending:
        print(f"goal stated, nothing declared yet: {pending}")
    print(f"terminal_state={report['terminal_state']}")
    print(_gate_line(conn, report.get('_db', '')))


def _mem(args) -> int:
    """The one surface every harness calls. Text in, ranked text out.

    Deliberately plain stdout: Pi does not speak MCP, Codex and opencode
    reach it through a shell, and a hook can pipe it. Anything that needs
    structure asks for --json.
    """
    conn = memory.open_memory(os.environ.get("LOOPGRAPH_MEMORY_DB"))

    if args.mem_cmd == "retain":
        text = args.text or sys.stdin.read()
        tags = tuple(t for t in args.tags.split(",") if t.strip())
        try:
            if args.supersedes:
                mid = memory.supersede(conn, args.supersedes, text,
                                       kind=args.kind, tags=tags,
                                       source=args.source)
            else:
                mid = memory.retain(conn, text, kind=args.kind, tags=tags,
                                    source=args.source)
        except ValueError as exc:
            print(f"mem retain: {exc}", file=sys.stderr)
            return 2
        # The corpus is the writer. A memory that reaches sqlite but not
        # MEMORY.md is invisible to the next session, which loads the corpus
        # and not this database.
        if not args.no_file:
            memory.write_markdown(args.corpus, mid, text, args.kind,
                                  tags=tags, source=args.source)
        # A corrected belief is the one thing worth interrupting other
        # sessions for: five run at once here, and until this existed they
        # would keep acting on the superseded version until someone re-read
        # the corpus the next day.
        if args.supersedes:
            try:
                import importlib.util as _ilu
                _s = _ilu.spec_from_file_location("bcast", os.path.expanduser(
                    "~/.claude/hooks/broadcast.py"))
                _m = _ilu.module_from_spec(_s); _s.loader.exec_module(_m)
                _m.publish("belief corrected",
                           f"{args.supersedes} is superseded: {text[:220]}",
                           os.environ.get("CLAUDE_CODE_BRIDGE_SESSION_ID", ""))
            except Exception:
                pass                    # broadcasting must never fail a retain
        why = memory.sensitivity(text)
        if why:
            # Queued, not announced. This printed 98 times mid-task, and each
            # one interrupts the work to report something that needs no
            # decision at that moment: the memory is stored either way and the
            # scope rule already applies. Security findings are worth one
            # deliberate pass, not a running commentary -- `loopgraph security`.
            from .security import MEMORY_WITHHELD as _WITHHELD
            from .security import queue as _sec_queue
            _sec_queue(_WITHHELD, mid, "; ".join(why))
        print(mid)
        return 0

    if args.mem_cmd == "recall":
        hits = memory.recall(conn, " ".join(args.query), k=args.k,
                             kind=args.kind, scope=args.scope)
        if args.json:
            print(json.dumps(hits, indent=2))
        else:
            for h in hits:
                stale = (f"  [superseded by {h['superseded_by']}]"
                         if h["superseded_by"] else "")
                print(f"{h['id']}  ({h['kind']}, {h['created_at'][:10]}){stale}\n"
                      f"  {h['text'].strip().splitlines()[0][:300]}")
        # Exit 1 on no hits: a caller piping this needs to tell "nothing
        # known" from "here is what is known" without parsing prose.
        return 0 if hits else 1

    if args.mem_cmd == "history":
        rows = memory.history(conn, args.id)
        if not rows:
            print(f"mem history: nothing recorded for {args.id}", file=sys.stderr)
            return 2
        for r in rows:
            print(f"{r['logical_clock']:>4} {r['wall_time'][:19]} "
                  f"{r['change_type']} {r['old_value'] or ''} -> {r['new_value'] or ''}")
        return 0

    if args.mem_cmd == "forget":
        # Two stores, so three outcomes, and the old code collapsed them into
        # one: it removed the file, ignored whether that had done anything,
        # then reported only on the node. Forgetting a memory whose node was
        # already gone deleted the markdown and still exited 2 "no such
        # memory" -- which reads as "nothing was touched" while a file has in
        # fact just been deleted. Only an id absent from BOTH stores is
        # unknown; an id in one of them is a divergence being repaired, and
        # saying which store held it is the difference between a scary
        # message and a diagnosis.
        from . import security as _sec
        missing, repaired = [], []
        for i in args.id:
            had_file = memory.remove_markdown(args.corpus, i)
            had_node = memory.forget(conn, i)
            if not (had_file or had_node):
                missing.append(i)
                continue
            # The finding outlives the memory otherwise. The queue is
            # append-only and knows nothing about deletion, so a forgotten
            # memory left a row naming an id that exists in neither store.
            _sec.retract(i)
            if had_file != had_node:
                repaired.append((i, "the index" if had_node else "the corpus"))
        for i, where in repaired:
            print(f"mem forget: {i} was only in {where} -- the other store had "
                  "already lost it, now consistent", file=sys.stderr)
        if missing:
            print(f"mem forget: no such memory: {', '.join(missing)}",
                  file=sys.stderr)
            return 2
        return 0

    if args.mem_cmd == "relate":
        try:
            memory.relate(conn, args.src, args.dst, args.rel)
        except (ValueError, sqlite3.IntegrityError) as exc:
            print(f"mem relate: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.mem_cmd == "doctor":
        # Which harnesses have actually invoked the hooks. Installing a hook
        # whose payload contract was inferred rather than tested is
        # indistinguishable from installing nothing: it simply never fires.
        from .db import meta_get
        rows = list(conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE 'hook_seen:%' ORDER BY key"))
        known = {"claude-code", "codex", "opencode", "pi", "cursor", "gemini"}
        seen = {r["key"].split(":", 1)[1]: r["value"] for r in rows}
        for h in sorted(known):
            when = seen.get(h)
            print(f"  {h:<14} {'last recall hook ' + when[:19] if when else 'NEVER fired'}")
        print(f"\nscope here: {memory.scope_default()}   "
              f"corpus: {args.corpus}")
        missing = sorted(known - set(seen))
        if missing:
            print(f"\n{len(missing)} harness(es) have never invoked the hook. "
                  "That is either uninstalled, an unmatched payload contract, "
                  "or simply unused since install - not proof it works.")
        return 0

    if args.mem_cmd == "reflect":
        groups = memory.reflect(conn, min_cluster=args.min_cluster)
        if args.json:
            print(json.dumps(groups, indent=2))
            return 0
        if not groups:
            print("nothing to reflect on: every cluster already has a "
                  "conclusion sitting on it")
            return 0
        print(f"{len(groups)} clusters of related memories that nobody has "
              "drawn a conclusion from:\n")
        for g in groups:
            print(f"  about: {', '.join(g['shared']) or '(linked, no shared terms)'}")
            for m in g["members"]:
                print(f"    - {m}")
            print("    -> if there is a lesson here, write it:")
            print('       mem retain "<what this means>" --kind model\n')
        return 0

    if args.mem_cmd == "reindex":
        got = memory.reindex(conn, args.corpus)
        print(f"rebuilt from {args.corpus}: {got['imported']} memories, "
              f"{got['linked']} links")
        return 0

    if args.mem_cmd == "harvest":
        from .harvest import mine, transcripts, undistilled
        paths = []
        for d in args.directory:
            paths += transcripts(d, since_days=args.since_days)
        got = mine(paths, min_sessions=args.min_sessions)
        # Drop what memory already answers, so re-running gets quieter.
        known = {r["example"] for r in got["recurring_errors"]}
        fresh = set(undistilled(conn, sorted(known)))
        got["recurring_errors"] = [r for r in got["recurring_errors"]
                                   if r["example"] in fresh]
        if args.json:
            print(json.dumps(got, indent=2))
            return 0
        print(f"scanned {got['scanned']} transcripts")
        print(f"\nrediscovered across sessions (>= {args.min_sessions}):")
        for r in got["recurring_errors"]:
            print(f"  [{r['sessions']:>3} sessions] {r['example'][:160]}")
        print("\ncorrections (most recent):")
        for c in got["corrections"][-15:]:
            print(f"  {c['text'][:160]}")
        print("\nThese are candidates, not memories. Distil the ones worth "
              "keeping:\n  mem retain \"<the fact>\" --kind world --tags harvested")
        return 0

    if args.mem_cmd == "import":
        got = memory.import_markdown(conn, args.directory)
        print(f"imported={got['imported']} skipped={got['skipped']} "
              f"linked={got['linked']}")
        for p in got["pending_links"]:
            print(f"  pending link (target not written yet): {p}")
        return 0

    s = memory.stats(conn)
    print(f"memories={s['memories']} edges={s['edges']} "
          + " ".join(f"{k}={v}" for k, v in sorted(s["by_kind"].items())))
    print(f"db: {memory.default_memory_db()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="loopgraph")
    p.add_argument("--db", default=None,
               help="default: ~/.loopgraph/<hash of repo root>.db")
    p.add_argument("--stagnation-turns", type=int, default=3)
    p.add_argument("--budget-tokens", type=int, default=None)
    sub = p.add_subparsers(dest="cmd", required=False)

    sub.add_parser("init")

    a = sub.add_parser("add")
    a.add_argument("id")
    a.add_argument("--statement", required=True)
    # dest="evidence_cmd": the top-level subparsers action already uses
    # dest="cmd" to record which subcommand ran. "--cmd" would default to
    # dest="cmd" too and silently clobber that value (e.g. `add C1 --cmd
    # false` would leave args.cmd == "false" instead of "add"), so the
    # dest is renamed here to avoid the collision.
    a.add_argument("--cmd", dest="evidence_cmd", required=True)
    a.add_argument("--expect", default="{}")
    a.add_argument("--staleness", type=int, default=None)
    a.add_argument("--timeout", type=int, default=120)
    a.add_argument("--goal", action="store_true")
    a.add_argument("--audit", action="store_true",
                   help="run the audit inline and wait (~22s)")
    a.add_argument("--no-audit", action="store_true",
                   help="skip the background audit that runs by default")
    a.add_argument("--allow-green", action="store_true",
                   help="accept a check that already passes (guards, "
                        "regression fences); normally that is refused")
    a.add_argument("--guard", action="store_true",
                   help="a check that must KEEP passing rather than start "
                        "passing (implies --allow-green)")

    a.add_argument("--global", dest="is_global", action="store_true",
                   help="bind every session, not just this one")

    dr = sub.add_parser("drop")
    dr.add_argument("id", nargs="+")

    ad = sub.add_parser("adopt")
    ad.add_argument("id", nargs="*",
                    help="omit with --all to adopt every unenforced criterion")
    ad.add_argument("--all", action="store_true")

    np = sub.add_parser("noop")
    np.add_argument("--reason", required=True)

    bl = sub.add_parser("baseline")
    bl.add_argument("--timeout", type=int, default=None)

    # --- memory: global, cross-repo, cross-harness --------------------------
    me = sub.add_parser("mem")
    me.add_argument("--corpus", default=memory.DEFAULT_CORPUS,
                    help="the markdown corpus that is the source of truth")
    me_sub = me.add_subparsers(dest="mem_cmd", required=True)
    me_r = me_sub.add_parser("retain")
    me_r.add_argument("text", nargs="?", default="", help="omit to read stdin")
    me_r.add_argument("--kind", default="world", choices=list(memory.KINDS))
    me_r.add_argument("--tags", default="")
    me_r.add_argument("--source", default="")
    me_r.add_argument("--supersedes", default="")
    me_r.add_argument("--no-file", action="store_true",
                      help="index only, do not write to the corpus")
    me_c = me_sub.add_parser("recall")
    me_c.add_argument("query", nargs="+")
    me_c.add_argument("-k", type=int, default=8)
    me_c.add_argument("--kind", default=None, choices=list(memory.KINDS))
    me_c.add_argument("--json", action="store_true")
    me_c.add_argument("--scope", default=None, choices=list(memory.SCOPES),
                      help="default: LOOPGRAPH_MEM_SCOPE, else safe")
    me_h = me_sub.add_parser("history")
    me_h.add_argument("id")
    me_f = me_sub.add_parser("forget")
    me_f.add_argument("id", nargs="+")
    me_l = me_sub.add_parser("relate")
    me_l.add_argument("src")
    me_l.add_argument("dst")
    me_l.add_argument("--rel", default="relates_to")
    me_i = me_sub.add_parser("import")
    me_i.add_argument("directory")
    me_hv = me_sub.add_parser("harvest")
    me_hv.add_argument("directory", nargs="+")
    me_hv.add_argument("--min-sessions", type=int, default=3,
                       help="how many separate sessions must have hit it")
    me_hv.add_argument("--since-days", type=float, default=None)
    me_hv.add_argument("--json", action="store_true")
    me_sub.add_parser("stats")
    me_sub.add_parser("reindex")
    me_sub.add_parser("doctor")
    me_rf = me_sub.add_parser("reflect")
    me_rf.add_argument("--min-cluster", type=int, default=3)
    me_rf.add_argument("--json", action="store_true")

    lk = sub.add_parser("link")
    lk.add_argument("src")
    lk.add_argument("dst")
    lk.add_argument("--rel", default="depends_on")

    r = sub.add_parser("run")
    r.add_argument("id", nargs="?")

    for name in ("status", "check"):
        s = sub.add_parser(name)
        s.add_argument("--json", action="store_true")

    # --- coordination: used by the orchestrator, needs nothing from agents ---
    cl = sub.add_parser("claim")
    cl.add_argument("agent")
    cl.add_argument("--scope", nargs="+", required=True)
    cl.add_argument("--base-ref", default="")
    cl.add_argument("--epoch", type=int, default=0)
    cl.add_argument("--lease", type=int, default=coord.DEFAULT_LEASE_S)
    cl.add_argument("--model", default="",
                    help="LABEL ONLY, for the route table. This does not "
                         "dispatch anywhere or select a model; it records who "
                         "you say did the work.")
    cl.add_argument("--kind", default="",
                    help="plan|implement|audit|investigate|review")

    va = sub.add_parser("validate")
    va.add_argument("agent")
    va.add_argument("--changed", nargs="*", default=None,
                    help="explicit changed set; default derives it from git")
    va.add_argument("--epoch", type=int, default=None)

    rl = sub.add_parser("release")
    rl.add_argument("agent")
    rl.add_argument("--outcome", default="done")
    rl.add_argument("--spend", type=int, default=0, help="tokens this agent used")
    rl.add_argument("--seconds", type=int, default=0)

    for _n in ("on", "off"):
        _g = sub.add_parser(_n)
        _g.add_argument("--only", choices=["scope", "loop"], default=None,
                        help="default: both gates")
    cf = sub.add_parser("coord")  # deprecated alias
    cf.add_argument("action", choices=["on", "off", "status"])
    cf.add_argument("--loop", action="store_true")

    ar = sub.add_parser("artifact")
    ar.add_argument("action", choices=["add", "check"])
    ar.add_argument("name")
    ar.add_argument("--key", default="")
    ar.add_argument("--kind", default="")

    rf = sub.add_parser("refuse")
    rf.add_argument("key")
    rf.add_argument("--reason", required=True)
    rf.add_argument("--by", default="")

    sw = sub.add_parser("sweep")
    sw.add_argument("--lease", type=int, default=coord.DEFAULT_LEASE_S)

    tc = sub.add_parser("touch")
    tc.add_argument("agent")

    br = sub.add_parser("brief")
    br.add_argument("--tags", default="")

    fr = sub.add_parser("frontier")
    fr.add_argument("agent")

    gm = sub.add_parser("game")
    gm.add_argument("id", nargs="?", help="criterion id; default all")
    gm.add_argument("--model", default="")
    gm.add_argument("--sandbox", default="",
                    help="read-only|workspace-write|danger-full-access; "
                         "default inherits ~/.codex/config.toml")
    gm.add_argument("--approval", default="",
                    help="override; default inherits ~/.codex/config.toml. "
                         "sandbox is always read-only")

    ex = sub.add_parser("exec")
    ex.add_argument("agent")
    ex.add_argument("--plan", required=True, help="plan file, or - for stdin")
    ex.add_argument("--scope", nargs="+", required=True)
    ex.add_argument("--model", default="")
    ex.add_argument("--sandbox", default="")
    ex.add_argument("--approval", default="")
    ex.add_argument("--timeout", type=int, default=3600)
    ex.add_argument("--kind", default="implement")
    ex.add_argument("--plan-format", default="",
                    choices=["", "narrative", "checklist", "pseudocode"],
                    help="recorded for the route table. arXiv 2605.29927 found "
                         "plan representation is model-specific and moves "
                         "results substantially; this makes that testable here.")

    sub.add_parser("route")

    sub.add_parser("claims")

    cc = sub.add_parser("classes")
    cc.add_argument("--agent", action="append", default=[],
                    metavar="NAME=path,path", help="repeatable")

    fa = sub.add_parser("fact")
    fa.add_argument("action", choices=["add", "list"])
    fa.add_argument("id", nargs="?")
    fa.add_argument("--text", default="")
    fa.add_argument("--tags", default="")

    sec = sub.add_parser("security")
    sec.add_argument("--clear", action="store_true",
                     help="mark everything reviewed (after the review pass)")
    sec.add_argument("--prune", action="store_true",
                     help="retract findings about memories that no longer exist")
    sec.add_argument("--json", action="store_true")

    ds = sub.add_parser("distill")
    ds.add_argument("--run", action="store_true",
                    help="mine now and write the candidate file (what the schedule calls)")
    ds.add_argument("--json", action="store_true")
    ds.add_argument("--min-sessions", type=int, default=5)
    ds.add_argument("--since-days", type=float, default=30.0)

    jn = sub.add_parser("janitor")
    jn.add_argument("--max-lines", type=int, default=20)
    jn.add_argument("--stale-days", type=int, default=3)
    jn.add_argument("--json", action="store_true")
    jn.add_argument("--find", metavar="QUERY",
                    help="search criteria across every graph")
    jn.add_argument("--open-only", action="store_true")
    jn.add_argument("--reap", action="store_true",
                    help="clear stated goals nobody turned into criteria")
    jn.add_argument("--apply", action="store_true", help="with --reap, actually write")
    sub.add_parser("next")
    sub.add_parser("tick")
    sp = sub.add_parser("spend")
    sp.add_argument("tokens", type=int)

    args = p.parse_args(argv)
    if args.cmd is None:            # bare `loopgraph` reports status
        args.cmd, args.json = "status", False
    cfg = {
        "stagnation_turns": args.stagnation_turns,
        "budget_tokens": args.budget_tokens,
    }
    if args.db is None:
        # Prefer the graph THIS session's Stop hook is using. The hooks resolve
        # the project from the transcript, which does not move; the CLI has no
        # transcript and resolved from cwd, so the moment the hooks stopped
        # drifting the two disagreed -- `add` from a shell parked in another
        # repo wrote a criterion the gate would never see, and reported
        # success. Same CLI/hook split as this morning, roles reversed,
        # because only half the system had been fixed.
        args.db = coord.session_graph_path() or coord.default_db_path()
    conn = open_db(args.db)

    if args.cmd == "security":
        from . import security as _sec
        if args.prune:
            # Only findings that name a memory, and only where the memory is
            # gone from the index. The queue carries hand-filed findings too,
            # whose subject is an account or a host and never was a memory
            # id -- an open account compromise was sitting two rows below
            # three of these stale ones, so pruning by "subject not found"
            # alone would have retracted it.
            # `conn` above is the per-repo criteria graph, not this. Resolving
            # memory ids against it would find none of them and retract the
            # whole queue.
            mconn = memory.open_memory(os.environ.get("LOOPGRAPH_MEMORY_DB"))
            live = {r["id"] for r in mconn.execute(
                "SELECT id FROM nodes WHERE type='memory'")}
            stale = sorted({r["subject"] for r in _sec.pending()
                            if r.get("kind") == _sec.MEMORY_WITHHELD
                            and r.get("subject") not in live})
            for s in stale:
                _sec.retract(s)
            print(f"security: retracted {len(stale)} finding(s) about "
                  "forgotten memories")
            for s in stale:
                print(f"  {s}")
            return 0
        if args.clear:
            n = _sec.clear()
            print(f"security: {n} item(s) marked reviewed")
            return 0
        if args.json:
            print(json.dumps(_sec.pending(), indent=2))
            return 0
        print(_sec.report())
        return 0

    if args.cmd == "distill":
        from . import distill as _dist
        if args.run:
            got = _dist.run(min_sessions=args.min_sessions,
                            since_days=args.since_days)
            print(f"distilled: scanned {got['scanned']} transcripts, "
                  f"{len(got['recurring'])} recurring, "
                  f"{len(got['unconcluded'])} unconcluded clusters, "
                  f"{len(got['corrections'])} corrections")
            return 0
        if args.json:
            print(json.dumps(_dist.load(), indent=2))
            return 0
        print(_dist.digest() or "distill: nothing new")
        return 0

    if args.cmd == "janitor":
        from . import janitor as _jan
        if args.find:
            hits = _jan.find(args.find, include_closed=not args.open_only)
            if args.json:
                print(json.dumps(hits, indent=2))
                return 0
            for h in hits:
                age = "?" if h["age"] is None else f"{h['age']}d"
                print(f"{h['state']:9} {age:>4} {_jan._short(h['where'])} "
                      f"{h['id']}: {h['statement'][:90]}")
            if not hits:
                print(f"janitor: no criterion mentions {args.find!r}")
            return 0
        if args.reap:
            done = _jan.reap(dry_run=not args.apply)
            for line in done:
                print(("cleared " if args.apply else "would clear ") + line)
            if not done:
                print("janitor: no stale goals to clear")
            return 0
        data = _jan.scan()
        if args.json:
            print(json.dumps(data, indent=2))
            return 0
        out = _jan.digest(max_lines=args.max_lines, stale_days=args.stale_days,
                          data=data)
        print(out or "janitor: nothing loose")
        return 0

    if args.cmd == "init":
        return 0

    # add/link/tick/spend are ordinary authoring/bookkeeping commands: 0
    # means the command itself succeeded, in the normal Unix sense (so
    # they can be chained with `&&` in a script). This is a different
    # contract from check/run, where 0 means and only means the
    # SPECIFICATION is met (terminal_state == "success").
    if args.cmd == "add":
        try:
            expect = json.loads(args.expect)
        except json.JSONDecodeError as exc:
            print(f"--expect is not valid JSON: {exc}", file=sys.stderr)
            return 2
        try:
            add_criterion(
                conn, args.id, args.statement, args.evidence_cmd, expect,
                staleness_window_s=args.staleness, timeout_s=args.timeout,
                is_goal=args.goal,
            )
        except (sqlite3.IntegrityError, ValueError) as exc:
            # IntegrityError: duplicate id. ValueError: validate_expect
            # rejected a malformed --expect (wrong type, unknown key,
            # uncompilable stdout_matches regex) -- see evidence.py.
            print(f"add: cannot add {args.id!r}: {exc}", file=sys.stderr)
            return 2

        if args.is_global:
            coord.set_node_flags(conn, args.id, **{"global": True})
        if args.guard:
            coord.set_node_flags(conn, args.id, guard=True)
        elif coord.session_key() and not args.is_global:
            # Stamp the author so a second session sharing this database is
            # not held to a goal it never stated.
            coord.set_node_flags(conn, args.id, session=coord.session_key())

        # Entailment gate (weakness.py, arXiv:2301.12987). A check that is
        # green before the work has not been shown to distinguish done from
        # not-done, so it cannot hold a turn open and cannot report success
        # honestly -- it just makes the gate look armed. Run it now, keep the
        # run as the criterion's first evidence, and refuse if it passes.
        # --guard is the deliberate exception: a regression fence is supposed
        # to be green already, and its job is to stay that way.
        if not (args.allow_green or args.guard):
            r = run_evidence(conn, args.id)
            record_status(conn, args.id)
            if r["ok"]:
                drop_criterion(conn, args.id)
                print(
                    f"add: refused {args.id!r}: the check already passes, so it "
                    "cannot tell done from not-done.\n"
                    f"  cmd: {args.evidence_cmd}\n"
                    "  Write the check that is red right now and green when the "
                    "work is finished.\n"
                    "  If it is a regression fence that must simply stay green, "
                    "add it with --guard.",
                    file=sys.stderr,
                )
                return 2
            w = weakness(args.evidence_cmd)
            if w["score"] < BASE_SCORE:
                worst = w["reasons"][0][1] if w["reasons"] else ""
                print(
                    f"add: {args.id} accepted, but this check is narrow "
                    f"(weakness {w['score']}): {worst}.\n"
                    "  A narrower check than the goal makes the loop drive "
                    "toward one guessed implementation instead of the outcome.",
                    file=sys.stderr,
                )
        if not args.audit and not args.no_audit \
                and os.environ.get("LOOPGRAPH_AUDIT", "") != "0":
            # Audit by default WITHOUT blocking: the reasoning is ~20s and
            # irreducible, so detach it rather than make authoring wait.
            # The verdict lands in the db and shows up in `status`.
            from . import gaming
            if gaming.codex_available():
                try:
                    subprocess.Popen(
                        [sys.executable, "-m", "loopgraph.cli", "--db", args.db,
                         "game", args.id],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL, start_new_session=True)
                except Exception:
                    pass                       # never block authoring
        if args.audit:
            from . import gaming
            r = (gaming.check_gameable(args.statement, args.evidence_cmd, expect)
                 if gaming.codex_available() else {"ok": False, "error": "codex absent"})
            if r.get("ok"):
                coord.record_audit(conn, args.id, r)
                if r.get("gameable"):
                    print(f"GAMEABLE: {r.get('explanation','')}", file=sys.stderr)
                    if r.get("cheat"):
                        print(f"  cheat: {r['cheat']}", file=sys.stderr)
                    if r.get("suggested_check"):
                        print(f"  harder: {r['suggested_check']}", file=sys.stderr)
                    return 1
            elif r.get("error") != "codex absent":
                print(f"audit skipped: {r.get('error')}", file=sys.stderr)
        return 0

    if args.cmd == "mem":
        # Memory is global and lives in its own store, so it deliberately
        # does not use `conn` (the per-repo criteria graph).
        return _mem(args)

    if args.cmd == "noop":
        # The waiver is the point: "this has no checkable end-state" is a
        # legitimate answer that must be said out loud once, not assumed by
        # default forever.
        coord.clear_goal(conn, args.reason)
        return 0

    if args.cmd == "baseline":
        from .baseline import DEFAULT_TIMEOUT, install
        # cwd, for the same reason default_db_path() keys on it: the fence
        # belongs to the tree being worked in.
        for r in install(conn, os.getcwd(),
                         timeout=args.timeout or DEFAULT_TIMEOUT):
            print(f"{r['id']} {'installed' if r['installed'] else 'skipped'}: "
                  f"{r['why']} ({r['cmd']})")
        return 0

    if args.cmd == "drop":
        missing = [i for i in args.id if not drop_criterion(conn, i)]
        if missing:
            print(f"drop: no such criterion: {', '.join(missing)}", file=sys.stderr)
            return 2
        return 0

    if args.cmd == "adopt":
        targets = ([c["id"] for c in coord.unenforced_criteria(conn)]
                   if args.all else args.id)
        if not targets:
            print("adopt: nothing to adopt", file=sys.stderr)
            return 2
        missing = [i for i in targets if not coord.adopt(conn, i)]
        if missing:
            print(f"adopt: no such criterion: {', '.join(missing)}", file=sys.stderr)
            return 2
        print(f"adopted: {', '.join(targets)}")
        return 0

    if args.cmd == "link":
        try:
            link(conn, args.src, args.dst, args.rel)
        except (sqlite3.IntegrityError, ValueError) as exc:
            # IntegrityError: unknown src/dst id. ValueError: --rel is not
            # one of the allowed relation types (graph.ALLOWED_REL_TYPES).
            print(
                f"link: cannot link {args.src!r} -> {args.dst!r}: {exc}",
                file=sys.stderr,
            )
            return 2
        return 0

    if args.cmd == "run":
        targets = [args.id] if args.id else [c["id"] for c in all_criteria(conn)]
        failures = []
        for cid in targets:
            try:
                run_evidence(conn, cid)
                record_status(conn, cid)
            except Exception as exc:
                # A single bad criterion (typo'd --expect key, missing
                # evidence_cmd, ...) must not abort the whole run: every
                # other criterion in `targets` still has to be evaluated.
                # evidence.run_evidence already finalises the run row
                # with ok=NULL on error, so this criterion still derives
                # as `unproven` -- it just must not silently drop the
                # rest of the batch.
                failures.append((cid, exc))
        if failures:
            for cid, exc in failures:
                print(f"run: {cid} failed to evaluate: {exc}", file=sys.stderr)
            # A criterion that could not be evaluated must never
            # contribute to a success verdict, so this outranks the
            # terminal_state check below.
            return 2
        return 0 if terminal_state(conn, cfg) == "success" else 1

    if args.cmd == "claim":
        r = coord.agent_start(conn, args.agent, args.scope, base_ref=args.base_ref,
                              epoch=args.epoch, lease_s=args.lease)
        if r["ok"]:
            coord.agent_meta_set(conn, args.agent, model=args.model, kind=args.kind)
            print(f"claimed {len(r['claimed'])}: {' '.join(r['claimed'])}")
            return 0
        for c in r["conflicts"]:
            print(f"CONFLICT {c['slot']} held by {c['holder']}", file=sys.stderr)
        print("claimed nothing (all-or-nothing)", file=sys.stderr)
        return 3

    if args.cmd == "validate":
        changed = args.changed
        if changed is None:
            base = coord._meta(conn, args.agent).get("base_ref", "")
            if not base:
                print("no base_ref recorded; pass --changed", file=sys.stderr)
                return 2
            out = subprocess.run(["git", "diff", "--name-only", f"{base}..HEAD"],
                                 capture_output=True, text=True)
            if out.returncode != 0:
                print(f"git diff failed: {out.stderr.strip()}", file=sys.stderr)
                return 2
            changed = [l for l in out.stdout.splitlines() if l.strip()]
        try:
            r = coord.agent_check(conn, args.agent, changed, current_epoch=args.epoch)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(r, indent=2, sort_keys=True))
        return 0 if r["verdict"] == "clean" else 1

    if args.cmd == "release":
        coord.agent_meta_set(conn, args.agent, spend=args.spend, seconds=args.seconds)
        att = coord.attribute(conn, args.agent)
        freed = coord.agent_done(conn, args.agent, outcome=args.outcome)
        print(f"released {len(freed)}: {' '.join(freed)}"
              f"  | model={att['model']} kind={att['kind']} closed={att['closes']}")
        return 0

    if args.cmd in ("on", "off"):
        want = args.cmd == "on"
        if args.only in (None, "scope"):
            coord.set_enabled(conn, want)
        if args.only in (None, "loop"):
            coord.set_loop_enabled(conn, want)
        print(_gate_line(conn, args.db))
        return 0

    if args.cmd == "coord":
        print("note: `loopgraph coord` is deprecated, use `loopgraph on|off`",
              file=sys.stderr)
        if args.loop:
            if args.action == "status":
                print(f"loop gate: {'ON' if coord.loop_enabled(conn) else 'OFF'}")
                return 0
            coord.set_loop_enabled(conn, args.action == "on")
            print(f"loop gate: {args.action.upper()}")
            return 0
        if args.action == "status":
            on = coord.is_enabled(conn)
            forced = os.environ.get("LOOPGRAPH_COORD", "") == "0"
            print(f"coordination: {'ON' if on else 'OFF'}"
                  + ("  (forced off by LOOPGRAPH_COORD=0)" if forced else ""))
            print(f"db: {args.db}")
            return 0
        coord.set_enabled(conn, args.action == "on")
        print(f"coordination: {args.action.upper()}")
        return 0

    if args.cmd == "artifact":
        if args.action == "add":
            k = coord.artifact_add(conn, args.name, key=args.key, kind=args.kind)
            print(f"added {args.name} key={k}")
            return 0
        r = coord.artifact_check(conn, args.name, key=args.key)
        print(json.dumps(r, indent=2, sort_keys=True))
        return 1 if r["verdict"] == "conflict" else 0

    if args.cmd == "refuse":
        coord.refuse(conn, args.key, args.reason, by=args.by)
        print(f"recorded refusal for key={args.key}")
        return 0

    if args.cmd == "sweep":
        freed = coord.sweep_expired(conn, lease_s=args.lease)
        print(f"freed {len(freed)}: {' '.join(freed)}" if freed else "nothing expired")
        return 0

    if args.cmd == "touch":
        coord.heartbeat(conn, args.agent)
        return 0

    if args.cmd == "brief":
        out = coord.brief(conn, tags=args.tags)
        if out:
            print(out)
        return 0

    if args.cmd == "frontier":
        for e in coord.frontier(conn, args.agent):
            print(f"{e['wall_time']}\t{e['entity_id']}\t{e['change_type']}\t{e['new_value']}")
        return 0

    if args.cmd == "game":
        from . import gaming
        if not gaming.codex_available():
            print("codex not found on PATH", file=sys.stderr)
            return 2
        targets = ([c for c in all_criteria(conn) if c["id"] == args.id]
                   if args.id else all_criteria(conn))
        if not targets:
            print("no criteria to audit", file=sys.stderr)
            return 2
        worst = 0
        for c in targets:
            r = gaming.check_gameable(c["statement"], c["evidence_cmd"],
                                      json.loads(c["expect_json"] or "{}"),
                                      model=args.model, approval=args.approval,
                                      sandbox=args.sandbox)
            if not r.get("ok"):
                print(f"{c['id']}\tERROR\t{r.get('error')} {r.get('detail','')}",
                      file=sys.stderr)
                worst = max(worst, 2)
                continue
            coord.record_audit(conn, c["id"], r)
            if r.get("gameable"):
                worst = max(worst, 1)
                mark = "GAMEABLE(demonstrated)" if r.get("demonstrated") else "GAMEABLE(asserted)"
                print(f"{c['id']}\t{mark}\t{r.get('explanation','')}")
                if r.get("evidence"):
                    print(f"  observed: {r['evidence'][:160]}")
                if r.get("cheat"):
                    print(f"  cheat: {r['cheat']}")
                if r.get("suggested_check"):
                    print(f"  harder: {r['suggested_check']}")
            else:
                print(f"{c['id']}\tsound\t{r.get('explanation','')[:90]}")
        return worst

    if args.cmd == "exec":
        from . import gaming
        plan = sys.stdin.read() if args.plan == "-" else open(args.plan).read()
        # 1. claim the scope atomically - refuse rather than collide
        r = coord.agent_start(conn, args.agent, args.scope, base_ref="")
        if not r["ok"]:
            for c in r["conflicts"]:
                print(f"CONFLICT {c['slot']} held by {c['holder']}", file=sys.stderr)
            return 3
        coord.agent_meta_set(conn, args.agent, model=args.model or "codex",
                             kind=args.kind, plan_format=args.plan_format)
        crits = [dict(c) for c in all_criteria(conn)]
        coord.agent_meta_set(conn, args.agent,
                             criteria=[c["id"] for c in crits])
        # 2. hand the plan to codex to implement
        res = gaming.implement(plan, crits, args.scope, cwd=os.getcwd(),
                               model=args.model, timeout=args.timeout,
                               approval=args.approval, sandbox=args.sandbox)
        if not res.get("ok") and res.get("error"):
            print(f"exec failed: {res['error']}", file=sys.stderr)
            coord.agent_done(conn, args.agent, outcome="error")
            return 2
        # 3. verify against the criteria - the implementer does not grade itself
        for c in crits:
            try:
                run_evidence(conn, c["id"])
                record_status(conn, c["id"])
            except Exception:
                pass
        coord.agent_meta_set(conn, args.agent, spend=res.get("tokens", 0))
        att = coord.attribute(conn, args.agent)
        coord.agent_done(conn, args.agent, outcome="done")
        ts = terminal_state(conn, cfg)
        print(f"model={att['model']} closed={att['closes']} "
              f"tokens={att['spend']} sandbox={res.get('sandbox')} "
              f"terminal_state={ts}")
        if res.get("stderr_tail"):
            print(res["stderr_tail"], file=sys.stderr)
        return 0 if ts == "success" else 1

    if args.cmd == "route":
        rows = coord.route_table(conn)
        if not rows:
            print("no attributed agents yet - claim with --model/--kind")
            return 0
        print(f"{'kind':<12}{'model':<12}{'plan_fmt':<11}{'agents':>7}"
              f"{'closes':>7}{'rate':>7}{'spend':>10}{'cost/accepted':>15}")
        for r in rows:
            cpa = r["cost_per_accepted"]
            print(f"{r['kind']:<12}{r['model']:<12}{r['plan_format']:<11}"
                  f"{r['agents']:>7}{r['closes']:>7}{r['close_rate']:>7}"
                  f"{r['spend']:>10}{(cpa if cpa is not None else '-'):>15}")
        return 0

    if args.cmd == "claims":
        held = coord.live_claims(conn)
        for slot, holder in held.items():
            print(f"{slot}\t{holder}")
        return 0

    if args.cmd == "classes":
        scopes = {}
        if args.agent:
            for spec in args.agent:
                if "=" not in spec:
                    print(f"bad --agent {spec!r}, expected NAME=path,path", file=sys.stderr)
                    return 2
                name, paths = spec.split("=", 1)
                scopes[name] = [p for p in paths.split(",") if p]
        else:
            for slot, holder in coord.live_claims(conn).items():
                scopes.setdefault(holder, []).append(slot)
        for group in coord.conflict_classes(scopes):
            marker = "SERIAL" if len(group) > 1 else "parallel"
            print(f"{marker}\t{' '.join(group)}")
        return 0

    if args.cmd == "fact":
        if args.action == "add":
            if not args.id or not args.text:
                print("fact add needs an id and --text", file=sys.stderr)
                return 2
            coord.fact_add(conn, args.id, args.text, args.tags)
            return 0
        for f in coord.fact_list(conn, tag=args.tags):
            tags = ",".join(f["tags"])
            print(f"{f['id']}\t{f['text']}\t[{tags}]")
        return 0

    if args.cmd == "next":
        for cid in workable(conn):
            print(cid)
        # 0 here means "nothing workable" (e.g. everything closed, or
        # everything remaining is blocked) -- it is NOT the exit-0-means-
        # specification-met contract. That contract belongs to `check`
        # and `run` only, which compare terminal_state to "success".
        return 0 if not workable(conn) else 1

    if args.cmd == "tick":
        print(tick(conn))
        return 0

    if args.cmd == "spend":
        print(add_spend(conn, args.tokens))
        return 0

    report = _report(conn, cfg)
    report["_db"] = args.db
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(conn, report)
    # `status` is a report and always succeeds. Only `check` (and `run`)
    # carry the contract that exit 0 means the SPECIFICATION is met -- a
    # reporting command that exits 1 on a healthy empty project breaks
    # every caller that treats non-zero as failure.
    if args.cmd == "status":
        return 0
    # `status` shows the whole board, including criteria this session is not
    # held to. `check` is the machine contract, and the specification it
    # answers for is this session's -- otherwise a neighbouring session's
    # open goal makes every caller here report failure forever.
    everything = all_criteria(conn)
    mine = {c["id"] for c in everything if coord.owned_here(conn, c["id"])}
    if not mine and everything:
        # Exiting 1 without a word here reads as "the work failed" when it
        # actually means "none of this is yours". Name it.
        print(f"check: no criteria are owned by this session "
              f"({len(everything)} in the graph belong to another session or "
              f"to nobody) - `loopgraph adopt <id>` to answer for one",
              file=sys.stderr)
    return 0 if terminal_state(conn, cfg, only=mine) == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
