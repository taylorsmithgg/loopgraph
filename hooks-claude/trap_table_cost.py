#!/usr/bin/env python3
"""What does the trap table cost, measured against real history?

A guard is worth its interruption only when the failure it prevents is worse
than being interrupted. That criterion is easy to state and easy to drift
away from: this table reached 2.10% of every command ever run here before
anyone measured it, one plausible rule at a time.

Exit 1 if the table would deny more than CEILING of historical commands.
"""
import collections
import glob
import importlib.util
import json
import os
import sys

CEILING = 0.001                      # 0.1% of all commands

spec = importlib.util.spec_from_file_location(
    "ct", os.path.join(os.path.dirname(os.path.abspath(__file__)), "command_trap.py"))
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)

cmds = []
for path in glob.glob(os.path.expanduser(
        "~/.claude/projects/**/*.jsonl"), recursive=True):
    for line in open(path, errors="replace"):
        if '"Bash"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "assistant":
            continue
        for b in (d.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" \
                    and b.get("name") == "Bash":
                c = (b.get("input") or {}).get("command", "")
                if c:
                    cmds.append(c)

if len(cmds) < 1000:
    print("trap-cost: not enough history to measure; skipping")
    raise SystemExit(0)

hits = collections.Counter()
for raw in cmds:
    if "command_trap" in raw or "mem retain" in raw:
        continue
    stripped = ct.strip_inert(raw)
    for test, mid, _ in ct.TRAPS:
        if test.search(stripped):
            hits[mid] += 1

total = sum(hits.values())
rate = total / len(cmds)
print(f"trap table denies {total} of {len(cmds):,} historical commands "
      f"= {100*rate:.4f}%  (ceiling {100*CEILING:.2f}%)")
for mid, n in hits.most_common():
    print(f"   {n:5}  {mid}")
raise SystemExit(0 if rate <= CEILING else 1)
