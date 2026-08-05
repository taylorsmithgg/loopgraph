---
title: Corpus baseline
description: Agent session lifetimes and artifact-name collision rates across a real corpus.
---

# Corpus baseline — agent population, 2026-08-03

**This is a baseline, not a result.** The coordination mechanisms in `2026-08-03-agent-coordination-graph-design.md` §7–§8 are unimplemented. There is no treatment arm, so no improvement is claimed or measurable yet. This document exists so a later comparison has something honest to compare against.

## Corpus

3,233 session transcripts, 2,092 MB, under `~/.claude/projects`. 45 sessions used subagents. **2,674 agent completions** with full accounting.

Totals: **143.2 agent-days** of wall clock, **691.5M subagent tokens**, median 192,767 tokens per agent.

## Lifetime and cost distribution

| cohort | agents | % agents | agent-days | % time | tokens | % tokens | tok/agent | tools/agent |
|---|---|---|---|---|---|---|---|---|
| <10m | 952 | 35.6% | 3.4 | 2.4% | 120.8M | 17.5% | 126,858 | 32 |
| 10–60m | 1,200 | 44.9% | 22.1 | 15.5% | 352.3M | 50.9% | 293,583 | 115 |
| 1–4h | 483 | 18.1% | 32.5 | 22.7% | 205.0M | 29.6% | 424,417 | 296 |
| 4–24h | 22 | 0.8% | 9.9 | 6.9% | 7.8M | 1.1% | 355,789 | 246 |
| >24h | 17 | 0.6% | 75.3 | 52.6% | 5.6M | 0.8% | 331,546 | 208 |

Median lifetime 16.9 min; p75 44.9 min; p90 88.6 min; p99 11.87 h; max **9.61 days** — which reproduces that session's 9d 14h outlier exactly, confirming the corpus captures it.

## The finding that changes the design's justification

**17 agents (0.6%) hold 52.6% of all wall-clock and 0.8% of tokens.**

They average *fewer* tool calls than the 1–4h cohort (208 vs 296) and similar token spend per agent (331k vs 424k). They are not computing for days — they are **outstanding** for days across session suspensions while doing roughly an hour's work.

Consequences:

1. **Culling long agents is not a cost saving.** Eliminating every agent over 24 h would recover 0.8% of token spend. Any justification of §7 culling on efficiency grounds is refuted by this table.
2. **The prize is correctness.** The value of staleness detection is preventing an agent from acting on a ten-day-old world model — as happened when a 9.6-day agent returned a retirement plan for work completed seven days earlier. That is a correctness argument, not an economic one.
3. **Token mass is elsewhere.** 50.9% of spend sits in the 10–60 min cohort, whose lifetimes are short enough that staleness rarely bites. Mechanisms aimed at long agents will not move total spend.
4. **Wall-clock and token cost are nearly uncorrelated** across cohorts. Optimising one does not optimise the other, and they need separate justification.

## Secondary observations

- **25 agents (0.9%) completed with zero tool calls**, consuming 0.7M tokens. This is the `tool_uses == 0` no-work signal, now quantified at population scale.
- Model split across 1,403 dispatches sampled at one directory level: sonnet 679, inherited 474, opus 144, haiku 104.
- Peak observed concurrency is 7 simultaneous agents in one session; most sessions peak at 1.

## Method corrections

Two errors were made and corrected while producing this table. Both are recorded because they changed the answer materially.

1. **Worktree contamination.** The first artifact-name measurement walked a large private monorepo including `.claude/worktrees/agent-*`, which are full repository copies per agent. That inflated the tree from 4,214 files to 94,479 and produced collision groups of ~5,000 files. Excluding them changed the basename collision rate from 92% to 40.7%.
2. **Wrong lifetime source.** Pairing each `Agent` tool_use to its `tool_result` by `tool_use_id` measures time-to-acknowledge-backgrounding, not agent lifetime — a backgrounded agent returns a result immediately and completes later via a separate notification. That method reported median 1.0 min and **max 43 min**, with zero agents over an hour. Reading `<duration_ms>` from `task-notification` records instead gives median 16.9 min and max 9.61 days. A survivorship-bias hypothesis was also tested and rejected: 99.9% of dispatches did return.

## What a conclusive test would require

An A/B with the mechanisms live: the same task set run with and without scope claims, OCC staleness validation, conflict-class partitioning and relay. Metrics: duplicate artifacts shipped, collisions requiring rebase, agents acting on invalidated premises, and cost per accepted change.

Not runnable today. The nearest available substitute is a backtest of the conflict-class partition against recorded collisions with known ground truth (!506 vs !510 on `sql/57`; !535/!539/!543 sharing three files), which requires reconstructing per-agent write-sets. **Subagent tool calls are not present in these transcripts** — `isSidechain` records number zero — so write-sets cannot currently be reconstructed from history. Capturing them is a prerequisite for that backtest.
