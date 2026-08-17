"""Agent coordination: atomic scope claims and staleness validation.

Used by the orchestrator at dispatch and at return. Requires nothing from the
agents themselves.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .db import emit_delta, meta_get, meta_set, utcnow
from .graph import get_node

AGENT = "agent"
SLOT = "slot"
FACT = "fact"
# p90 agent lifetime is 89 min and p99 is ~12 h (corpus baseline, n=2674).
# A 30-minute lease would expire under a live agent and hand its slot away.
DEFAULT_LEASE_S = 14400  # 4h: covers ~98.6% of observed agent lifetimes


def default_db_path(cwd: str | None = None) -> str:
    """Per-project database, stored OUTSIDE the project.

    Keyed by git toplevel (or cwd). Nothing is ever written into the repo,
    so there is nothing to gitignore, commit or clean up.

    Hooks MUST pass the cwd from their event payload rather than relying on
    os.getcwd(). The agent's shell keeps its working directory between tool
    calls, so a single `cd` into some other repo -- to run its tests, say --
    silently repoints this function for the rest of the session. Observed
    live: spec_prompt recorded a goal under ~, the agent cd'd into another
    repo to run pytest, and the Stop hook then demanded that goal out of the
    OTHER repo's database. The CLI, resolving from the real cwd, could not
    see it, so no `loopgraph add` or `noop` could ever satisfy the gate and
    it simply blocked until the cap. The payload cwd is the session's, and
    does not drift.
    """
    import hashlib
    import os
    import subprocess
    root = cwd or os.getcwd()
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            root = out.stdout.strip()
    except Exception:
        pass
    h = hashlib.sha256(root.encode()).hexdigest()[:16]
    d = os.path.join(os.path.expanduser("~"), ".loopgraph")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{h}.db")


def is_enabled(conn: sqlite3.Connection) -> bool:
    """On by default. Safe because the gate is inert until a dispatch
    declares a SCOPE: line -- it cannot affect work that never opted in.
    `loopgraph off` or LOOPGRAPH_COORD=0 disables it."""
    import os
    if os.environ.get("LOOPGRAPH_COORD", "") == "0":
        return False
    return meta_get(conn, "coord_enabled", "1") == "1"


def set_enabled(conn: sqlite3.Connection, on: bool) -> None:
    meta_set(conn, "coord_enabled", "1" if on else "0")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Add the coordination column if an older database lacks it."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(nodes)")}
    if "meta_json" not in cols:
        conn.execute("ALTER TABLE nodes ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}'")


def _meta(conn: sqlite3.Connection, node_id: str) -> dict:
    row = conn.execute("SELECT meta_json FROM nodes WHERE id = ?", (node_id,)).fetchone()
    return json.loads(row["meta_json"]) if row and row["meta_json"] else {}


def _set_meta(conn: sqlite3.Connection, node_id: str, meta: dict) -> None:
    conn.execute(
        "UPDATE nodes SET meta_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(meta, sort_keys=True), utcnow(), node_id),
    )


def set_node_flags(conn: sqlite3.Connection, node_id: str, **flags) -> None:
    """Record where a criterion came from and what kind it is.

    Derived criteria have to be legible as derived: a gate holding a turn
    open against a check the user never wrote, with no way to see that, is
    indistinguishable from the tool malfunctioning.
    """
    ensure_schema(conn)
    m = _meta(conn, node_id)
    m.update(flags)
    _set_meta(conn, node_id, m)


def node_flags(conn: sqlite3.Connection, node_id: str) -> dict:
    ensure_schema(conn)
    return _meta(conn, node_id)


def _upsert(conn: sqlite3.Connection, node_id: str, type_: str, statement: str = "") -> None:
    now = utcnow()
    conn.execute(
        "INSERT INTO nodes (id, type, statement, expect_json, timeout_s, is_goal, "
        "created_at, updated_at, meta_json) VALUES (?, ?, ?, '{}', 120, 0, ?, ?, '{}') "
        "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at",
        (node_id, type_, statement, now, now),
    )


def live_holder(
    conn: sqlite3.Connection, slot: str, lease_s: int = DEFAULT_LEASE_S,
    now: datetime | None = None,
) -> str | None:
    """Return the agent holding `slot`, or None. Expiry is evaluated lazily."""
    now = now or datetime.now(timezone.utc)
    row = conn.execute(
        "SELECT src FROM edges WHERE dst = ? AND rel_type = 'claims'", (slot,)
    ).fetchone()
    if row is None:
        return None
    holder = row["src"]
    meta = _meta(conn, holder)
    if meta.get("state") in ("done", "released"):
        return None
    hb = meta.get("heartbeat_at")
    if hb and (now - datetime.fromisoformat(hb)).total_seconds() > lease_s:
        return None
    return holder


def agent_start(
    conn: sqlite3.Connection,
    agent_id: str,
    scope: list[str],
    base_ref: str = "",
    epoch: int = 0,
    lease_s: int = DEFAULT_LEASE_S,
    now: datetime | None = None,
) -> dict:
    """Claim the whole scope atomically. All slots or none.

    Returns {"ok": True, "claimed": [...]} or
            {"ok": False, "conflicts": [{"slot":..., "holder":...}]}.
    """
    ensure_schema(conn)
    now = now or datetime.now(timezone.utc)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conflicts = []
        for slot in scope:
            holder = live_holder(conn, slot, lease_s=lease_s, now=now)
            if holder is not None and holder != agent_id:
                conflicts.append({"slot": slot, "holder": holder})
        if conflicts:
            conn.execute("ROLLBACK")
            return {"ok": False, "conflicts": conflicts, "claimed": []}

        _upsert(conn, agent_id, AGENT)
        _set_meta(conn, agent_id, {
            "state": "live",
            "base_ref": base_ref,
            "epoch": epoch,
            "dispatched_at": now.isoformat(),
            "heartbeat_at": now.isoformat(),
            "scope": sorted(scope),
        })
        for slot in scope:
            _upsert(conn, slot, SLOT)
            conn.execute(
                "INSERT OR REPLACE INTO edges (src, dst, rel_type, created_at) "
                "VALUES (?, ?, 'claims', ?)",
                (agent_id, slot, utcnow()),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    for slot in scope:
        emit_delta(conn, slot, "OWNERSHIP_CHANGE", None, agent_id)
    return {"ok": True, "conflicts": [], "claimed": sorted(scope)}


def heartbeat(conn: sqlite3.Connection, agent_id: str, now: datetime | None = None) -> None:
    meta = _meta(conn, agent_id)
    meta["heartbeat_at"] = (now or datetime.now(timezone.utc)).isoformat()
    _set_meta(conn, agent_id, meta)


def agent_check(
    conn: sqlite3.Connection,
    agent_id: str,
    changed: list[str],
    current_epoch: int | None = None,
) -> dict:
    """Validate a returning agent against what actually changed.

    `changed` is the set of paths/identifiers that moved since the agent's
    base_ref — the caller supplies it (git diff, an artifact query, whatever).
    """
    node = get_node(conn, agent_id)
    if node is None:
        raise ValueError(f"no such agent: {agent_id}")
    meta = _meta(conn, agent_id)
    scope = set(meta.get("scope") or [])
    hits = sorted(scope & set(changed))
    epoch_stale = (
        current_epoch is not None and current_epoch != meta.get("epoch", 0)
    )
    verdict = "stale" if (hits or epoch_stale) else "clean"
    return {
        "agent": agent_id,
        "verdict": verdict,
        "changed_in_scope": hits,
        "epoch_stale": epoch_stale,
        "base_ref": meta.get("base_ref", ""),
        "scope_size": len(scope),
    }


def agent_done(
    conn: sqlite3.Connection, agent_id: str, outcome: str = "done"
) -> list[str]:
    """Release every claim held by this agent. Returns the released slots."""
    meta = _meta(conn, agent_id)
    meta["state"] = "released"
    meta["outcome"] = outcome
    meta["released_at"] = utcnow()
    _set_meta(conn, agent_id, meta)
    rows = list(conn.execute(
        "SELECT dst FROM edges WHERE src = ? AND rel_type = 'claims'", (agent_id,)
    ))
    slots = sorted(r["dst"] for r in rows)
    conn.execute("DELETE FROM edges WHERE src = ? AND rel_type = 'claims'", (agent_id,))
    for s in slots:
        emit_delta(conn, s, "OWNERSHIP_CHANGE", agent_id, None)
    return slots


def live_claims(
    conn: sqlite3.Connection, lease_s: int = DEFAULT_LEASE_S, now: datetime | None = None
) -> dict[str, str]:
    """slot -> holding agent, for every claim still live."""
    out = {}
    for r in conn.execute("SELECT dst FROM edges WHERE rel_type='claims' ORDER BY dst"):
        h = live_holder(conn, r["dst"], lease_s=lease_s, now=now)
        if h:
            out[r["dst"]] = h
    return out


def conflict_classes(scopes: dict[str, list[str]]) -> list[list[str]]:
    """Partition agents into classes by write-set intersection.

    Agents in one class must serialise; different classes may run parallel.
    """
    names = sorted(scopes)
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if set(scopes[a]) & set(scopes[b]):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)
    return sorted((sorted(v) for v in groups.values()), key=lambda g: g[0])


def fact_add(conn: sqlite3.Connection, fact_id: str, text: str, tags: str = "") -> None:
    ensure_schema(conn)
    _upsert(conn, fact_id, FACT, text)
    _set_meta(conn, fact_id, {"tags": [t for t in tags.split(",") if t]})
    emit_delta(conn, fact_id, "STATE_TRANSITION", None, "recorded")


def fact_list(conn: sqlite3.Connection, tag: str = "") -> list[dict]:
    out = []
    for r in conn.execute(
        "SELECT id, statement FROM nodes WHERE type='fact' ORDER BY id"
    ):
        tags = _meta(conn, r["id"]).get("tags", [])
        if tag and tag not in tags:
            continue
        out.append({"id": r["id"], "text": r["statement"], "tags": tags})
    return out


ARTIFACT = "artifact"


def semantic_key(name: str, vocab: set[str] | None = None) -> str:
    """Derive a comparison key. Strips a leading token only if it is a known
    vendor — measured: blind stripping made K8s collisions worse (9.2% vs
    7.6%), while vocabulary-gated stripping took SQL detections from 11.7%
    to 4.4% with zero false groups."""
    vocab = vocab if vocab is not None else default_vocab()
    leaf = name.rsplit(".", 1)[-1]
    parts = leaf.split("_")
    if len(parts) > 1 and parts[0].lower() in vocab:
        return "_".join(parts[1:])
    return leaf


def default_vocab() -> set[str]:
    return {
        "azure", "entra", "duo", "okta", "aws", "gcp", "o365", "m365", "gsuite",
        "sysmon", "win", "windows", "linux", "sentinelone", "s1", "crowdstrike",
        "defender", "fortigate", "cisco", "huntress", "sonicwall",
    }


def artifact_add(conn, artifact_id: str, key: str = "", kind: str = "") -> str:
    ensure_schema(conn)
    k = key or semantic_key(artifact_id)
    _upsert(conn, artifact_id, ARTIFACT, kind)
    _set_meta(conn, artifact_id, {"key": k, "kind": kind})
    emit_delta(conn, artifact_id, "STATE_TRANSITION", None, "created")
    return k


def refuse(conn, key: str, reason: str, by: str = "") -> None:
    """Record that a class of artifact was deliberately NOT built.

    !515 shipped four duplicate rules because two other agents had already
    refused that exact design and their decision was unreachable.
    """
    ensure_schema(conn)
    node = f"refusal:{key}"
    _upsert(conn, node, ARTIFACT, reason)
    _set_meta(conn, node, {"key": key, "refusal": True, "reason": reason, "by": by})
    emit_delta(conn, node, "STATE_TRANSITION", None, "refused")


def artifact_check(conn, name: str, key: str = "") -> dict:
    """Would creating `name` duplicate something, or repeat a refusal?"""
    ensure_schema(conn)
    k = key or semantic_key(name)
    existing, refusals = [], []
    for r in conn.execute("SELECT id FROM nodes WHERE type='artifact' ORDER BY id"):
        meta = _meta(conn, r["id"])
        if meta.get("key") != k:
            continue
        if meta.get("refusal"):
            refusals.append({"reason": meta.get("reason", ""), "by": meta.get("by", "")})
        elif r["id"] != name:
            existing.append(r["id"])
    return {
        "name": name, "key": k, "duplicates": existing, "refusals": refusals,
        "verdict": "conflict" if (existing or refusals) else "clear",
    }


def sweep_expired(conn, lease_s: int = DEFAULT_LEASE_S, now: datetime | None = None) -> list[str]:
    """Release claims whose holder's lease has lapsed. Returns freed slots."""
    now = now or datetime.now(timezone.utc)
    freed = []
    for r in list(conn.execute("SELECT DISTINCT src FROM edges WHERE rel_type='claims'")):
        agent = r["src"]
        meta = _meta(conn, agent)
        hb = meta.get("heartbeat_at")
        expired = hb and (now - datetime.fromisoformat(hb)).total_seconds() > lease_s
        if expired or meta.get("state") in ("done", "released"):
            freed += agent_done(conn, agent, outcome="lease-expired" if expired else "done")
    return sorted(freed)


def frontier(conn, agent_id: str) -> list[dict]:
    """What a dead agent actually completed, so a successor resumes rather
    than restarts. Reads the delta log, which survives the kill."""
    rows = conn.execute(
        "SELECT entity_id, change_type, new_value, wall_time FROM deltas "
        "WHERE entity_id IN (SELECT dst FROM edges WHERE src=? AND rel_type='claims') "
        "OR entity_id = ? ORDER BY id", (agent_id, agent_id),
    )
    return [dict(r) for r in rows]


def brief(conn, tags: str = "") -> str:
    """Facts block to paste into a dispatch prompt, so traps are not
    rediscovered. 'glab mr merge lies' cost three rediscoveries in one day."""
    facts = fact_list(conn, tag=tags)
    if not facts:
        return ""
    lines = ["KNOWN TRAPS (do not rediscover these):"]
    lines += [f"- {f['text']}" for f in facts]
    return "\n".join(lines)


# --- loop gating (the criteria half, wired) ----------------------------------

# Claude Code overrides a Stop hook that blocks more than
# CLAUDE_CODE_STOP_HOOK_BLOCK_CAP times in a row (default 8) and ends the turn
# with its own message. Staying one under that default means loopgraph names
# the terminal state itself instead of being cut off mid-drive. Raise both
# together: LOOPGRAPH_MAX_BLOCKS here, CLAUDE_CODE_STOP_HOOK_BLOCK_CAP there.
DEFAULT_MAX_BLOCKS = 7
MAX_CONSECUTIVE_BLOCKS = DEFAULT_MAX_BLOCKS  # legacy alias; prefer max_blocks()


def max_blocks() -> int:
    import os
    try:
        n = int(os.environ.get("LOOPGRAPH_MAX_BLOCKS", "") or DEFAULT_MAX_BLOCKS)
    except ValueError:
        n = DEFAULT_MAX_BLOCKS
    return max(1, n)


def loop_enabled(conn) -> bool:
    """On by default. Safe because the gate returns silently when no
    criteria are declared -- a repo that never declared one is unaffected.
    `loopgraph off --only loop` or LOOPGRAPH_LOOP=0 disables it."""
    import os
    if os.environ.get("LOOPGRAPH_LOOP", "") == "0":
        return False
    return meta_get(conn, "loop_enabled", "1") == "1"


def set_loop_enabled(conn, on: bool) -> None:
    meta_set(conn, "loop_enabled", "1" if on else "0")


# A stated goal that produced no criteria is the failure mode that made the
# loop half worthless: the gate is inert when nothing is declared, so "declare
# nothing" was always the cheapest way past it. These three functions make
# declaring nothing a decision that has to be taken rather than a default that
# happens.
MAX_SPEC_BLOCKS = 2


def session_key() -> str:
    """Which session authored a criterion.

    The database is keyed by repo root, so every session working outside a
    git repo shares one graph with every other -- observed live: two
    unrelated sessions in $HOME, each holding the other's turn open on
    criteria it had never heard of. A goal belongs to the session that
    stated it. Guards do not: a broken test suite is everyone's problem.

    Empty when the harness tells us nothing, which restores the old
    everyone-enforces-everything behaviour rather than silently enforcing
    nothing.
    """
    import os
    return (os.environ.get("LOOPGRAPH_SESSION")
            or os.environ.get("CLAUDE_CODE_BRIDGE_SESSION_ID") or "")


def owned_here(conn, node_id: str) -> bool:
    """True if this session may be held to that criterion.

    A goal belongs to whoever stated it. Another session's goal is that
    session's business, and an unowned one is nobody's -- it was authored
    before ownership was recorded, or by a session that is long gone, and
    there is no way to tell which. Enforcing those was tried and it is the
    worse failure: two sessions in $HOME, each blocked forever on the
    other's criteria, and a machine that accumulates permanent hostages
    every time a session dies mid-goal.

    Not enforced is NOT the same as not shown. `status` lists them, the gate
    names them on the way past, and `loopgraph adopt <id>` takes one on
    deliberately. Silence would just be the old bug wearing a new hat.

    Two things still bind everyone: guards (a broken suite is not a private
    matter) and `--global` criteria (said to be everyone's, out loud). And
    when the harness gives us no session identity at all, everything binds
    -- a gate that quietly stops gating is the one outcome never worth
    risking.
    """
    flags = node_flags(conn, node_id)
    if flags.get("guard") or flags.get("global"):
        return True
    if not session_key():
        return True
    return flags.get("session", "") == session_key()


def _age_days(created_at: str) -> int | None:
    """Whole days since a node was created, or None if unparseable."""
    if not created_at:
        return None
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - t).days)


def unenforced_criteria(conn) -> list[dict]:
    """Open criteria this session is not held to, and why.

    The age is part of the answer, not decoration. Every one of these is a
    choice between `adopt` and `drop`, and that choice is undecidable from
    the id alone: a criterion stated an hour ago by a live sibling session
    means something different from one left behind three weeks ago by a
    session that is never coming back. Without the age the cheapest move is
    to ignore the line, which is what happened - the same two ids were named
    618 times across this machine and neither was ever adopted or dropped.
    """
    from .graph import all_criteria
    from .state import derive_status
    out = []
    for c in all_criteria(conn):
        if owned_here(conn, c["id"]) or derive_status(conn, c["id"]) == "closed":
            continue
        owner = node_flags(conn, c["id"]).get("session", "")
        why = f"owned by {owner}" if owner else "no owner recorded"
        try:
            created = c["created_at"]          # sqlite3.Row, not a dict
        except (IndexError, KeyError):
            created = ""
        age = _age_days(created or "")
        if age is not None:
            why += f", {age}d old"
        out.append({"id": c["id"], "statement": c["statement"], "why": why})
    return out


def loose_note_due(conn, loose) -> bool:
    """True the first time this session sees this exact set of loose ids.

    Naming unenforced criteria is right; naming them on every stop is not.
    Unsuppressed, this note repeated 432 times in one real session and 998
    times across the machine, always the same two ids, and it changed
    nothing either time. A warning that arrives on every turn is read as
    wallpaper, so the one that actually matters arrives pre-ignored -- the
    same way a nag that could not be ended made the spec demand worthless.

    Keyed by the set, so it speaks again the moment the set changes: a NEW
    unenforced criterion is news even if an old one was already mentioned.
    """
    key = "loose_said:" + (session_key() or "-")
    sig = ",".join(sorted(u["id"] for u in loose))
    if meta_get(conn, key, "") == sig:
        return False
    meta_set(conn, key, sig)
    return True


def adopt(conn, node_id: str) -> bool:
    """Take on someone else's (or nobody's) criterion as this session's."""
    from .graph import get_node
    if get_node(conn, node_id) is None:
        return False
    set_node_flags(conn, node_id, session=session_key())
    return True


def note_goal(conn, text: str) -> None:
    if meta_get(conn, "goal_pending", ""):
        return                                   # first statement of it wins
    meta_set(conn, "goal_pending", (text or "").strip()[:200])
    meta_set(conn, "spec_blocks", "0")
    # Stamped so the janitor can age it. An unresolved goal with no date is
    # indistinguishable from one stated a minute ago, which is how twelve of
    # them sat unanswered across this machine without anyone being able to
    # say which were dead.
    import datetime as _dt
    meta_set(conn, "goal_pending_at",
             _dt.datetime.now(_dt.timezone.utc).isoformat())


def goal_pending(conn) -> str:
    return meta_get(conn, "goal_pending", "") or ""


def clear_goal(conn, reason: str = "") -> None:
    meta_set(conn, "goal_pending", "")
    meta_set(conn, "spec_blocks", "0")
    if reason:
        meta_set(conn, "goal_waived_reason", reason[:200])


def note_spec_block(conn) -> int:
    n = int(meta_get(conn, "spec_blocks", "0")) + 1
    meta_set(conn, "spec_blocks", str(n))
    return n


def blocks_so_far(conn) -> int:
    return int(meta_get(conn, "consecutive_blocks", "0"))


def note_block(conn) -> int:
    n = blocks_so_far(conn) + 1
    meta_set(conn, "consecutive_blocks", str(n))
    return n


def clear_blocks(conn) -> None:
    meta_set(conn, "consecutive_blocks", "0")


def record_audit(conn, criterion_id: str, result: dict) -> None:
    """Store a spec-gaming verdict on the criterion."""
    ensure_schema(conn)
    m = _meta(conn, criterion_id)
    from .gaming import classify_cheat
    gameable = bool(result.get("gameable"))
    # `sabotage` vs `shortcut`: only the latter is a criterion problem. See
    # gaming.SABOTAGE -- every check audited came back gameable via PATH
    # shims and conftest injection, which says nothing about the check.
    cheat_class = (classify_cheat(result.get("cheat", ""),
                                  result.get("explanation", ""))
                   if gameable else "")
    m["audit"] = {
        "gameable": gameable,
        "cheat_class": cheat_class,
        "cheat": result.get("cheat", ""),
        "suggested_check": result.get("suggested_check", ""),
        "at": utcnow(),
    }
    _set_meta(conn, criterion_id, m)
    emit_delta(conn, criterion_id, "STATE_TRANSITION", None,
               "gameable" if result.get("gameable") else "audited")


def audit_state(conn) -> dict:
    """unaudited / gameable criteria, for status output.

    Called from the status path, which must never fail because of an
    older database, so the migration runs here too.
    """
    from .graph import all_criteria
    ensure_schema(conn)
    unaudited, gameable, sabotage = [], [], []
    for c in all_criteria(conn):
        a = _meta(conn, c["id"]).get("audit")
        if not a:
            unaudited.append(c["id"])
        elif a.get("gameable"):
            # Only a `shortcut` is a criterion problem. Sabotage (PATH shims,
            # conftest injection) defeats every possible check, so reporting
            # it beside real findings is what makes the alarm meaningless.
            (sabotage if a.get("cheat_class") == "sabotage" else gameable
             ).append(c["id"])
    return {"unaudited": sorted(unaudited), "gameable": sorted(gameable),
            "sabotage_only": sorted(sabotage)}


# --- routing evidence: which model succeeds at which kind of task ------------

def agent_meta_set(conn, agent_id: str, **kw) -> None:
    m = _meta(conn, agent_id)
    m.update({k: v for k, v in kw.items() if v not in (None, "")})
    _set_meta(conn, agent_id, m)


def attribute(conn, agent_id: str) -> dict:
    """Count criteria that closed inside this agent's scope during its run.

    Attribution is by scope + time window, derived from the delta log. It is
    not proof of causation -- a criterion may close for an unrelated reason --
    but it is measured rather than self-reported, which is the point.
    """
    m = _meta(conn, agent_id)
    # Criteria are NOT scope entities: scope holds paths and identifiers,
    # criteria have ids like C1. Matching closes against scope alone can
    # never attribute anything, which silently reported closed=0 while the
    # work had actually landed.
    scope = set(m.get("scope") or []) | set(m.get("criteria") or [])
    start = m.get("dispatched_at") or ""
    end = m.get("released_at") or utcnow()
    closes = 0
    for r in conn.execute(
        "SELECT entity_id, wall_time FROM deltas "
        "WHERE change_type='STATE_TRANSITION' AND new_value='closed'"
    ):
        if r["entity_id"] in scope and start <= r["wall_time"] <= end:
            closes += 1
    return {
        "agent": agent_id,
        "model": m.get("model", "unknown"),
        "kind": m.get("kind", "unspecified"),
        "plan_format": m.get("plan_format", "-"),
        "closes": closes,
        "spend": int(m.get("spend") or 0),
        "seconds": int(m.get("seconds") or 0),
    }


def route_table(conn) -> list[dict]:
    """cost per accepted change, per model x kind. The paper's headline
    metric, measured on this workload rather than a benchmark."""
    rows = {}
    for r in conn.execute("SELECT id FROM nodes WHERE type='agent' ORDER BY id"):
        a = attribute(conn, r["id"])
        k = (a["model"], a["kind"], a["plan_format"])
        agg = rows.setdefault(k, {"model": a["model"], "kind": a["kind"],
                                  "plan_format": a["plan_format"],
                                  "agents": 0, "closes": 0, "spend": 0, "seconds": 0})
        agg["agents"] += 1
        for f in ("closes", "spend", "seconds"):
            agg[f] += a[f]
    out = []
    for agg in rows.values():
        agg["close_rate"] = round(agg["closes"] / agg["agents"], 2) if agg["agents"] else 0
        agg["cost_per_accepted"] = (round(agg["spend"] / agg["closes"])
                                    if agg["closes"] else None)
        out.append(agg)
    return sorted(out, key=lambda r: (r["kind"], r["model"], r["plan_format"]))
