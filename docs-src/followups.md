---
title: Open questions
description: What is unresolved, what was measured and left alone, and the costs that turned out to matter.
---

# Follow-ups

Findings raised during P0+P1 that were reviewed, judged real, and deliberately not fixed in that branch. Each carries the evidence and the ruling so a later phase does not have to rediscover it.

## 1. A criterion keeps a stale green verdict when its latest run fails

**Where:** `src/loopgraph/state.py`, `_latest_completed_run` / `derive_status`.

`_latest_completed_run` filters `ok IS NOT NULL`, so a run that timed out or errored is skipped and an **older passing run still wins**. Reproduced: a criterion closes, its next run errors, and it still reports `closed` with `terminal_state: success` and `check` exit 0. With `staleness_window_s` unset — the default — this persists indefinitely. `run` exits 2 at the time of the failure, but a later standalone `check` exits 0.

This is spec-mandated behaviour (design §5.1, §6.2 treat a non-completing run as leaving the *previous* verdict intact), not an implementation slip. It is nonetheless the "dead but looks alive" pattern sitting in the module the Stop hook will read.

**Ruling:** deferred to the P2 hooks plan, where it actually bites. Decide there whether a criterion whose latest run failed should drop to `unproven`, or whether the hook should require a bounded `staleness_window_s` on every criterion.

## 2. `stdout_int_gte` sign-flip on `<digit>.-<digit>`

**Where:** `src/loopgraph/evidence.py`, `_INT_TOKEN`.

The shipped pattern is

```python
_INT_TOKEN = re.compile(r"(?<!\d)(?<!\d\.)-?\d+(?!\d)(?!\.\d)")
```

The `(?<!\d\.)` lookbehind is evaluated at the optional `-`, so it blocks the sign and the bare digits then match as positive: `"Errors: 0.-1"` yields `1` instead of `-1`. A 176,425-case differential fuzz found 183 such disagreements, **all pure sign flips with no magnitude inflation**, and **zero occurrences across 30,000 realistic evidence-command outputs**. 89% of them pre-dated the current pattern.

A verified drop-in replacement moves the decimal lookbehinds off the optional sign and onto the digit run:

```python
_INT_TOKEN = re.compile(r"(?:(?<!\d)-)?(?<!\d)(?<!\d\.)\d+(?!\d)(?!\.\d)")
```

It scored **0 disagreements across 192,191 fuzz cases** (vs. 568 for the shipped pattern), passes the required behaviour table, and passes the full suite 130/130.

**Ruling:** parked rather than landed, because the final gate had already passed and the reachable bug was fixed. Fold in with a regression test pinning `evaluate({"stdout_int_gte": 1}, 0, "Errors: 0.-1") is False`.

**Note for whoever touches this line:** it has now had three regexes and two of them shipped a hole that let `check` exit 0 on an unmet specification. Do not change it without a differential fuzz against an independently written reference.

## 3. Design spec R-03 is unimplemented

Design §8 specifies R-03 as `criterion open ∧ all deps open ∧ age > threshold` → `blocked`. The plan superseded it with dependency-derived blocking, and R-07 (cycle detection) now occupies the `blocked` terminal state. Age-based escalation was deferred to the hooks plan. Either implement it there or amend the spec.

## 4. Measured: derivation is cheap, evidence execution is not

Latency probe against the merged tree, chain-shaped dependency graph, 9 repetitions after warm-up:

| criteria | median `check` | min | max |
|---|---|---|---|
| 25 | 2.2 ms | 2.1 | 3.0 |
| 50 | 5.7 ms | 4.8 | 68.8 |
| 100 | 9.6 ms | 8.9 | 10.5 |
| 200 | 21.0 ms | 18.4 | 128.5 |
| 300 | 26.3 ms | 25.6 | 27.4 |

Roughly linear at ~0.09 ms per criterion. **The N+1 query patterns deferred as YAGNI were correctly deferred** — this is measured, not assumed. Occasional cold-cache spikes to ~130 ms appear in the max column; they do not compound.

The cost that matters is elsewhere. Executing evidence commands took ~49 ms per criterion even with `true` as the command, dominated by subprocess spawn. Design §6.1 has the Stop hook "run the Evidence Runner over all criteria (or the stale/dirty subset)" — so a 50-criterion spec costs ~2.5 s per turn end before any real command runs. Actual evidence commands are database queries, `pytest`, `kubectl get`, `argocd app get`: seconds apiece.

**The parenthetical is load-bearing and unspecified.** Evidence selection is a P2 design problem, not an optimisation to defer — without it the gate is unaffordable on exactly the infra and investigation work the spec puts in scope.

## 5. Smaller items

- `derive_status` raises `TypeError` when handed a naive `datetime` — undocumented, untested, unreachable from the CLI.
- `record_status` now takes a write lock (`BEGIN IMMEDIATE`) on every call including the no-op case, so it will fail against a read-only database file where it previously succeeded.
- `validate_expect` rejects values that previously worked via coercion (`{"stdout_int_gte": "5"}`, `{"exit_zero": 0}`). Safe direction, but there is no migration for an existing database holding such a value — the criterion becomes permanently `unproven`.
- `state.py` imports the private `db._emit_delta_in_transaction` across a module boundary.
- `cli.py` calls `workable(conn)` twice without a shared `now` in the `next` branch — the same defect class fixed in `_report`.
- `has_cycle`, `blocked` and `workable` issue N+1 queries. Fine at current scale; revisit only if gate latency is measured.
- README lists exit 2 for `tick`/`spend`, neither of which has a failure path; `status` and `init` are absent from the table.
- `shell=True` executes command strings stored in the database, so write access to `.loopgraph.db` is code execution the next time `run` fires. Inherent to the design and local-only, but P2 auto-executes these every turn.
