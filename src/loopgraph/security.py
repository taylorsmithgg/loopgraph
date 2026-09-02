"""Security findings, accumulated for one review pass.

These were being announced as they occurred: "retained as sensitive
(credential material or its location)" printed 98 times mid-task, each one
interrupting work to report something that needed no decision at that moment.
The memory is stored either way and the scope rule already applies, so the
notice bought nothing except a break in concentration -- and a stream of
security remarks during unrelated work trains a reader to skim exactly the
category that must not be skimmed.

So: queue silently, review deliberately. `loopgraph security` shows everything
outstanding in one pass, grouped by reason, and `--clear` marks it handled.

The queue is append-only, and for a while that was the whole story -- which
meant it had no way to say that a finding no longer applies. `mem forget`
deletes the node, the index row and the markdown file, and left the finding
behind naming an id that then existed in neither store. Read from the queue
side that looks exactly like the two memory stores having diverged, and three
of them were sitting in this queue before `retract` existed. Retraction is a
tombstone rather than a rewrite: the finding stays on disk for the audit, and
`pending` stops offering it.
"""
from __future__ import annotations

import json
import os
import time

# Overridable so a guard that must exercise the retain path does not file its
# own probe as a finding. SECURITYQUIET ran a real `mem retain` on every
# evaluation and queued a sensitive-looking test string each time -- ten copies
# of it were sitting on top of an open account compromise. A guard that fills
# the queue it guards is worse than no guard.
QUEUE = os.environ.get("LOOPGRAPH_SECURITY_QUEUE") or os.path.join(
    os.path.expanduser("~"), ".loopgraph", "security.jsonl")
REVIEWED = os.path.join(os.path.expanduser("~"), ".loopgraph", "security.reviewed")

# Not a `kind` any caller files. Prefixed so it cannot collide with a real
# finding kind, and skipped by `pending` so a tombstone never reads as work.
RETRACTED = "__retracted__"

# The one finding kind whose subject IS a memory id, so the only kind that can
# be resolved against the memory index. The rest of the queue is a mixed
# namespace -- an account, a host, a certificate batch -- and an open account
# compromise was sitting two rows below three stale memory findings. Named
# here rather than spelled out at the producer and the consumer separately:
# if those two strings drift, resolution matches nothing and stale rows
# accumulate again, which is the failure this constant exists to prevent.
MEMORY_WITHHELD = "memory withheld at safe scope"


def queue(kind: str, subject: str, detail: str = "", path: str | None = None) -> None:
    """Record a finding. Never prints -- that is the entire point."""
    p = path or QUEUE
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "kind": kind,
                                 "subject": subject[:160],
                                 "detail": detail[:200]}) + "\n")
    except OSError:
        pass                       # a queue failure must not fail the work


def _mark(path: str | None = None) -> float:
    try:
        return float(open(path or REVIEWED, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return 0.0


def retract(subject: str, path: str | None = None) -> None:
    """Withdraw every finding filed against `subject` up to now.

    Idempotent, and scoped by time rather than by subject alone: a subject
    retracted today can be queued again tomorrow -- re-retaining a memory
    under the same id is the ordinary case -- and the new finding must still
    surface.
    """
    p = path or QUEUE
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "kind": RETRACTED,
                                 "subject": subject[:160], "detail": ""}) + "\n")
    except OSError:
        pass                       # a queue failure must not fail the work


def _rows(path: str | None = None) -> list[dict]:
    """Every row on disk, skipping any line that will not parse.

    The comprehension that read this file sat inside the try that catches
    ValueError, so a single truncated line -- one interrupted append, one
    concurrent writer -- returned an empty queue and reported "nothing
    outstanding" over the top of an untriaged account compromise. Retraction
    doubles the write traffic into the file, which makes a torn line likelier
    rather than less. A bad line now costs that one finding, not all of them.
    """
    out: list[dict] = []
    try:
        fh = open(path or QUEUE, encoding="utf-8")
    except OSError:
        return out
    with fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def pending(path: str | None = None, mark_path: str | None = None) -> list[dict]:
    since = _mark(mark_path)
    rows = _rows(path)
    retracted: dict[str, float] = {}
    for r in rows:
        if r.get("kind") == RETRACTED:
            s = r.get("subject", "")
            retracted[s] = max(retracted.get(s, 0.0), r.get("ts", 0.0))
    out = []
    for r in rows:
        if r.get("kind") == RETRACTED:
            continue
        ts = r.get("ts", 0)
        if ts <= since:
            continue
        if ts <= retracted.get(r.get("subject", ""), 0.0):
            continue
        out.append(r)
    return out


def clear(path: str | None = None, mark_path: str | None = None) -> int:
    n = len(pending(path, mark_path))
    p = mark_path or REVIEWED
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(str(time.time()))
    return n


def report(path: str | None = None, mark_path: str | None = None) -> str:
    rows = pending(path, mark_path)
    if not rows:
        return "security: nothing outstanding"
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("detail") or r.get("kind", "?"), []).append(r)
    oldest = min(r.get("ts", 0) for r in rows)
    age_d = (time.time() - oldest) / 86400
    out = [f"security review: {len(rows)} item(s), oldest {age_d:.1f}d old"]
    for reason, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        out.append(f"  {len(items):4}  {reason}")
        for it in items[:3]:
            out.append(f"          {it['subject']}")
        if len(items) > 3:
            out.append(f"          +{len(items) - 3} more")
    out.append("  `loopgraph security --clear` once handled.")
    return "\n".join(out)
