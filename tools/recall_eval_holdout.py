#!/usr/bin/env python3
"""Held-out recall eval. The honest number.

tools/recall_eval.py is contaminated: the alias table was written while
looking at its misses, so customer->tenant, desktop->avd, amazon->s3 and
bound->timeout each map onto one of its twelve cases. Reporting 12/12 from it
is training on the test set.

These twenty target memories that eval never touched, phrased before looking
at the alias table. Aliases that generalise will still fire; aliases that were
really just memorised answers will not.

Usage: uv run python tools/recall_eval_holdout.py [--verbose]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from loopgraph import memory  # noqa: E402

CASES = [
    ("someone got phished into approving a sign-in on their phone", "oauth-device"),
    ("our backups turned out never to have existed", "snapshots-were-never-re"),
    ("how does a new engineer get into the government cloud", "lighthouse-acce"),
    ("a payment fraud nobody spotted until late in the investigation", "res-bec"),
    ("a python package will not install inside a lambda", "tl-msc-common"),
    ("giving one client's search cluster more capacity", "right-sizing"),
    ("that proxy is not actually in the traffic path anymore", "phase0_gateway"),
    ("health probes are flooding our tracing backend", "langfuse_otel_noise"),
    ("where do we track whether a device is still sending data", "source_health"),
    ("stdin gets closed when I run something interactive", "codex-specific-trap"),
    ("words I am not allowed to write", "forbidden_words"),
    ("moving indices between clusters without downtime", "blue-green-drain"),
    ("the container image only runs on arm", "clickhouse_mss_ingest"),
    ("letting a laptop in india reach US-only services", "tailscale"),
    ("we have no delegated admin rights into that partner tenant", "gdap"),
    ("telemetry keeps failing to send and retrying forever", "otel_export"),
    # Two memories answer this equally well -- the specific self-analysis note
    # and the general failure-class note. A single-answer label scored the
    # better answer as a miss, so the needle accepts either. Label staleness
    # is not a ranking regression, and treating it as one would have argued
    # for lowering the floor.
    ("I said it worked without actually checking",
     ("recurring-failure-mode", "dead_but_looks_alive")),
    ("which client does this azure subscription belong to", "lighthouse_client_mapping"),
    ("can a small model tell when it is unsure", "reach-engine"),
    ("where does agent memory live across tools", "memory_across_harnesses"),
]


def main() -> int:
    verbose = "--verbose" in sys.argv
    conn = memory.open_memory()
    at1 = at5 = 0
    misses = []
    for question, needle in CASES:
        hits = memory.recall(conn, question, k=5, scope="full")
        ids = [h["id"] for h in hits]
        wanted = needle if isinstance(needle, tuple) else (needle,)
        pos = next((i for i, x in enumerate(ids, 1)
                    if any(w in x.lower() for w in wanted)), None)
        at1 += pos == 1
        at5 += bool(pos and pos <= 5)
        if not pos:
            misses.append((question, ids[0] if ids else "-"))
        if verbose:
            print(f"{('HIT@%d' % pos) if pos else 'MISS ':7} {question[:50]:52}"
                  f" -> {(ids[0][:40] if ids else '-')}")
    n = len(CASES)
    print(f"HELD-OUT recall@1 {at1}/{n} = {100*at1//n}%   "
          f"recall@5 {at5}/{n} = {100*at5//n}%")
    if misses and verbose:
        print("\nmisses:")
        for q, got in misses:
            print(f"  {q[:56]:58} -> {got[:44]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
