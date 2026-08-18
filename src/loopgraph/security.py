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


def pending(path: str | None = None, mark_path: str | None = None) -> list[dict]:
    since = _mark(mark_path)
    try:
        with open(path or QUEUE, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
    except (OSError, ValueError):
        return []
    return [r for r in rows if r.get("ts", 0) > since]


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
