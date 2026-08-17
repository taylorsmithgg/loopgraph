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
        for g in glob.glob(os.path.join(home, "projects", depth, ".git")):
            roots.append(os.path.dirname(g))
    return roots


def _root_index(home: str | None = None) -> dict[str, str]:
    return {hashlib.sha256(r.encode()).hexdigest()[:16]: r
            for r in _candidate_roots(home)}


def scan(loopgraph_dir: str | None = None, home: str | None = None) -> dict:
    """Every loose end on this machine, as data. No printing, no truncation."""
    d = loopgraph_dir or os.path.join(os.path.expanduser("~"), ".loopgraph")
    index = _root_index(home)
    goals, crits, unreadable, graphs = [], [], [], 0

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
        where = index.get(key, key[:8])
        try:
            meta = {r["key"]: r["value"] for r in conn.execute("select key, value from meta")}
        except sqlite3.Error:
            conn.close()
            continue
        graphs += 1

        goal = (meta.get("goal_pending") or "").strip()
        if goal:
            goals.append({"where": where, "goal": goal,
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
                "where": where, "id": r["id"],
                "statement": (r["statement"] or "").strip(),
                "age": _age_days(r["created_at"]),
                "state": "never-run" if run is None else "failing",
                "owner": flags.get("session", ""),
            })
        conn.close()

    key = lambda x: -(x["age"] if x["age"] is not None else -1)
    goals.sort(key=key)
    crits.sort(key=key)
    return {"graphs": graphs, "goals": goals, "criteria": crits,
            "unreadable": unreadable}


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
    if not goals and not crits and not bad:
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
            lines.append(f"  {age:>4} {_short(c['where'], home)} {c['id']}: "
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
