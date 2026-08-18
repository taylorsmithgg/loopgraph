#!/usr/bin/env python3
"""Does recall find things when you do NOT already know their wording?

Querying a memory with its own description scores 100% and proves nothing --
the description shares the body's vocabulary. These queries deliberately avoid
it, the way a person actually asks: "customer" for a corpus that says
"tenant", "remote desktop" for one that says "AVD", "amazon" for "S3".

Baseline when written (BM25 only, no link expansion, memories only):
    recall@1 5/12, recall@5 8/12.

Usage: uv run python tools/recall_eval.py [--verbose]
Exit 0 if recall@5 meets THRESHOLD.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from loopgraph import memory  # noqa: E402

THRESHOLD = 10          # of len(CASES) at k=5

# (question, substring that must appear in a hit id)
CASES = [
    ("why would a network switch have restarted", "cisco_switch"),
    ("all our remote desktop users pile onto one machine", "avd"),
    ("the alert said fine but nothing was actually running", "dead_but_looks_alive"),
    ("where do we keep customer log archives in amazon", "tenant_log_lake"),
    ("two agents clobbered each other's work", "worktree"),
    ("no way to bound how long a shell command runs on mac", "timeout"),
    ("price per gigabyte for microsoft's siem", "azure_cost"),
    ("disk filled up on an old log server", "var"),
    ("git complains it isn't a repository", "git"),
    ("why is every turn so expensive in context", "ceremony"),
    ("the checker passed because it never checked anything", "dead_but_looks_alive"),
    ("which bucket belongs to a given customer", "tenant_log_lake"),
]


def main() -> int:
    verbose = "--verbose" in sys.argv
    conn = memory.open_memory()
    at1 = at5 = 0
    for question, needle in CASES:
        hits = memory.recall(conn, question, k=5, scope="full")
        ids = [h["id"] for h in hits]
        pos = next((i for i, x in enumerate(ids, 1) if needle in x.lower()), None)
        at1 += pos == 1
        at5 += bool(pos and pos <= 5)
        if verbose:
            mark = f"HIT@{pos}" if pos else "MISS "
            print(f"{mark:7} {question[:46]:48} -> {(ids[0][:44] if ids else '-')}")
    n = len(CASES)
    print(f"paraphrase recall@1 {at1}/{n}  recall@5 {at5}/{n}  (threshold @5 >= {THRESHOLD})")
    return 0 if at5 >= THRESHOLD else 1


if __name__ == "__main__":
    raise SystemExit(main())
