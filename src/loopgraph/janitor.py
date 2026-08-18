"""Cross-project sweep for loose ends.

Every other view in this tool is scoped to one graph, because the db is keyed
by repo root. That is right for enforcement -- another project's criteria are
not this turn's business -- and it is exactly why nothing gets finished: on
this machine the per-project design had produced 78 separate graphs holding 15
never-closed criteria and 12 stated goals that were never resolved, and no
command in the tool could show them at once. Work was not being dropped
because it was forgotten; it was being dropped because it was unreachable from
wherever you happened to be standing.

The digest is deliberately bounded. A janitor that prints everything it finds
is read once and then skipped, and a report nobody reads is the same as no
report -- this whole file exists because of a note that repeated until it
became wallpaper. Oldest first, one line each, hard cap, and an honest
"+N more" instead of a wall.
"""
from __future__ import annotations

import datetime
import glob
import hashlib
import json
import os
import sqlite3

DEFAULT_MAX_LINES = 20
STALE_DAYS = 3


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _age_days(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        t = datetime.datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return max(0, (_now() - t).days)


def _candidate_roots(home: str | None = None) -> list[str]:
    """Directories a graph could be keyed on.

    The db filename is a truncated sha256 of the root, which does not invert,
    so the only way back to a human-readable name is to hash the plausible
    roots and look for a match. Depth-limited on purpose: this runs on every
    session start and must not walk the disk.
    """
    home = home or os.path.expanduser("~")
    roots = [home]
    for depth in ("*", "*/*", "*/*/*"):
        # .git repos AND plain container directories. Guessing only at git
        # repos left 17 graphs unattributable: ~/projects/clearwater is a
        # container, not a repo, and it keys a graph holding nine criteria.
        for g in glob.glob(os.path.join(home, "projects", depth, ".git")):
            roots.append(os.path.dirname(g))
        for d in glob.glob(os.path.join(home, "projects", depth)):
            if os.path.isdir(d):
                roots.append(d)
    return roots


def _root_index(home: str | None = None) -> dict[str, str]:
    return {hashlib.sha256(r.encode()).hexdigest()[:16]: r
            for r in _candidate_roots(home)}


def scan(loopgraph_dir: str | None = None, home: str | None = None) -> dict:
    """Every loose end on this machine, as data. No printing, no truncation."""
    d = loopgraph_dir or os.path.join(os.path.expanduser("~"), ".loopgraph")
    index = _root_index(home)
    goals, crits, unreadable, empty, graphs = [], [], [], [], 0

    for path in sorted(glob.glob(os.path.join(d, "*.db"))):
        name = os.path.basename(path)
        if name == "memory.db":
            continue
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            continue
        key = name[:-3]
        try:
            meta = {r["key"]: r["value"] for r in conn.execute("select key, value from meta")}
        except sqlite3.Error:
            conn.close()
            continue
        graphs += 1
        # Stamped root first, guess second. A graph whose directory is gone
        # holds criteria that can never be satisfied again -- scratch dirs,
        # deleted worktrees -- and saying so is what makes drop-or-keep
        # decidable. Unknown is NOT the same as gone, and is reported apart.
        root = meta.get("root") or index.get(key)
        gone = bool(root) and not os.path.isdir(root)
        where = root or key[:8]

        goal = (meta.get("goal_pending") or "").strip()
        if goal:
            goals.append({"where": where, "goal": goal, "gone": gone,
                          "age": _age_days(meta.get("goal_pending_at"))})

        # `meta_json` is added lazily by coord.ensure_schema, so a graph that
        # no coordination call has touched does not have it. Selecting it
        # unconditionally raised, and the except-branch below used to swallow
        # that and return no rows -- a sweeper reporting "nothing loose"
        # because its own query failed, which is precisely the defect it was
        # written to find. Ask the schema instead, and let a genuinely
        # unreadable graph be counted and named rather than skipped.
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes)")}
            select = "id, statement, created_at" + (
                ", meta_json" if "meta_json" in cols else "")
            rows = list(conn.execute(
                f"select {select} from nodes where type = 'criterion'"))
            has_meta = "meta_json" in cols
        except sqlite3.Error as exc:
            unreadable.append(f"{where}: {exc}")
            conn.close()
            continue
        for r in rows:
            try:
                run = conn.execute(
                    "select exit_code from runs where criterion_id = ? "
                    "order by id desc limit 1", (r["id"],)).fetchone()
            except sqlite3.Error:
                run = None
            if run is not None and run["exit_code"] == 0:
                continue                       # closed; not a loose end
            try:
                flags = json.loads(r["meta_json"] or "{}") if has_meta else {}
            except (ValueError, IndexError, KeyError):
                flags = {}
            crits.append({
                "where": where, "gone": gone,
                "root_known": bool(root), "id": r["id"],
                "statement": (r["statement"] or "").strip(),
                "age": _age_days(r["created_at"]),
                "state": "never-run" if run is None else "failing",
                "owner": flags.get("session", ""),
            })
        if not rows and not goal:
            try:
                if conn.execute("select count(*) from nodes").fetchone()[0] == 0:
                    empty.append(path)
            except sqlite3.Error:
                pass
        conn.close()

    key = lambda x: -(x["age"] if x["age"] is not None else -1)
    goals.sort(key=key)
    crits.sort(key=key)
    return {"graphs": graphs, "goals": goals, "criteria": crits,
            "unreadable": unreadable, "empty": empty,
            "memory": memory_health()}


def memory_health(corpus: str | None = None, db: str | None = None) -> dict:
    """Is the memory corpus actually reachable?

    Three stores have to agree: the markdown files (the truth), MEMORY.md
    (what a session loads) and the search index (what recall queries). A file
    missing from MEMORY.md is invisible to every session while looking
    perfectly present on disk -- that is not hypothetical, it hid a global
    ban on a word from enforcement for ten days, and it was found by hand.
    Anything found by hand once should be found automatically thereafter.
    """
    import re
    corpus = corpus or os.path.join(
        os.path.expanduser("~"), ".claude", "projects",
        "-Users-taylorsmith", "memory")
    out = {"unindexed": [], "dead_links": [], "files": 0, "indexed": 0,
           "searchable": None}
    index_path = os.path.join(corpus, "MEMORY.md")
    if not os.path.isdir(corpus) or not os.path.isfile(index_path):
        return out
    try:
        listed = set(re.findall(r"^-\s*\[([^\]]+)\]",
                                open(index_path, errors="replace").read(), re.M))
        files = {os.path.basename(f) for f in glob.glob(os.path.join(corpus, "*.md"))
                 if not os.path.basename(f).startswith("MEMORY")}
    except OSError:
        return out
    out["unindexed"] = sorted(files - listed)
    out["dead_links"] = sorted(listed - files)
    out["files"], out["indexed"] = len(files), len(listed)
    db = db or os.path.join(os.path.expanduser("~"), ".loopgraph", "memory.db")
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        out["searchable"] = c.execute(
            "select count(*) from nodes where type = 'memory'").fetchone()[0]
        c.close()
    except sqlite3.Error:
        pass
    return out


def _short(where: str, home: str | None = None) -> str:
    home = home or os.path.expanduser("~")
    if where.startswith(home):
        rel = where[len(home):].lstrip("/") or "~"
        return rel.replace("projects/", "")
    return where


def digest(max_lines: int = DEFAULT_MAX_LINES, stale_days: int = STALE_DAYS,
           loopgraph_dir: str | None = None, home: str | None = None,
           data: dict | None = None) -> str:
    """A bounded report. Empty string when there is nothing to say.

    Silence is a real answer here: this is wired into session start, and a
    janitor that greets every session with a paragraph gets filtered out by
    the reader within a day.
    """
    data = data or scan(loopgraph_dir, home)
    goals = [g for g in data["goals"]
             if g["age"] is None or g["age"] >= stale_days]
    crits = [c for c in data["criteria"]
             if c["age"] is None or c["age"] >= stale_days]
    bad = data.get("unreadable") or []
    mem = data.get("memory") or {}
    mem_bad = bool(mem.get("unindexed") or mem.get("dead_links"))
    if not goals and not crits and not bad and not mem_bad:
        return ""

    lines = [f"loopgraph janitor: {len(crits)} open criteria and {len(goals)} "
             f"stale goals across {data['graphs']} graphs. "
             f"`loopgraph janitor` for the full list."]
    budget = max(1, max_lines - 1)
    half = max(1, budget // 2)

    shown = 0
    if crits:
        lines.append("open criteria (never closed, oldest first):")
        for c in crits[:half]:
            age = "?" if c["age"] is None else f"{c['age']}d"
            tag = " [dir gone]" if c.get("gone") else ""
            lines.append(f"  {age:>4} {_short(c['where'], home)} {c['id']}{tag}: "
                         f"{c['statement'][:70]}")
            shown += 1
        if len(crits) > half:
            lines.append(f"  +{len(crits) - half} more")
    if bad:
        lines.append(f"UNREADABLE graphs ({len(bad)}) - these are not 'clean', "
                     "they could not be checked:")
        for b in bad[:3]:
            lines.append(f"  {b}")
    rest = max(1, budget - shown - 2)
    if goals:
        lines.append("stated goals never resolved:")
        for g in goals[:rest]:
            age = "?" if g["age"] is None else f"{g['age']}d"
            lines.append(f"  {age:>4} {_short(g['where'], home)}: {g['goal'][:70]}")
        if len(goals) > rest:
            lines.append(f"  +{len(goals) - rest} more")
    if mem_bad:
        lines.append("memory corpus:")
        if mem.get("unindexed"):
            lines.append(f"  {len(mem['unindexed'])} file(s) missing from MEMORY.md "
                         f"- invisible to every session: "
                         f"{', '.join(mem['unindexed'][:3])}")
        if mem.get("dead_links"):
            lines.append(f"  {len(mem['dead_links'])} index entr(ies) with no file: "
                         f"{', '.join(mem['dead_links'][:3])}")
    n_empty = len(data.get("empty") or [])
    if n_empty:
        lines.append(f"{n_empty} empty graphs hold nothing "
                     f"(`loopgraph janitor --reap --apply` removes them)")
    return "\n".join(lines)


def reap(stale_days: int = 14, loopgraph_dir: str | None = None,
         home: str | None = None, dry_run: bool = True) -> list[str]:
    """Clear stated goals nobody ever turned into criteria.

    Goals only. A never-closed criterion is somebody's unfinished work and an
    automatic sweep that deletes it is worse than the mess -- the point of
    this tool is that unfinished work stays visible. Goals are different: an
    unanswered one blocks the gate on a request whose session is long gone.
    """
    d = loopgraph_dir or os.path.join(os.path.expanduser("~"), ".loopgraph")
    index = _root_index(home)
    done = []
    for path in (scan(loopgraph_dir=d, home=home).get("empty") or []):
        done.append(f"empty graph {os.path.basename(path)}")
        if not dry_run:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except OSError:
                    pass
    for path in sorted(glob.glob(os.path.join(d, "*.db"))):
        if os.path.basename(path) == "memory.db":
            continue
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            continue
        try:
            row = conn.execute(
                "select value from meta where key = 'goal_pending'").fetchone()
            at = conn.execute(
                "select value from meta where key = 'goal_pending_at'").fetchone()
        except sqlite3.Error:
            conn.close()
            continue
        goal = (row["value"] if row else "") or ""
        age = _age_days(at["value"] if at else None)
        if goal.strip() and (age is None or age >= stale_days):
            key = os.path.basename(path)[:-3]
            where = _short(index.get(key, key[:8]), home)
            done.append(f"{where}: {goal.strip()[:60]}")
            if not dry_run:
                conn.execute(
                    "insert or replace into meta(key, value) values('goal_pending','')")
                conn.execute(
                    "insert or replace into meta(key, value) "
                    "values('goal_waived_reason', ?)",
                    (f"janitor: unresolved for {age if age is not None else '?'}d",))
                conn.commit()
        conn.close()
    return done
