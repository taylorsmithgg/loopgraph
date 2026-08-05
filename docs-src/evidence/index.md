---
title: What was measured
description: The measurements behind the design, including the ones that came out against it.
---

# What was measured

Four measurement passes, run before the mechanisms they justify were built.
They are published as they were written, including the parts that argued
against the design.

Two conventions hold throughout. Numbers come from a real corpus of long
agent sessions and a large private GitOps monorepo — names of employers,
clients and clusters are replaced with placeholders, the measurements are
not. And a result that contradicted the plan is reported rather than
retired: the coordination design's own benchmark found symbol-stripping
*harmful*, and it says so.

<div class="cards">
  <a class="card" href="corpus-baseline.html"><b>Corpus baseline</b><span>Agent session lifetimes and artifact-name collision rates, plus the worktree contamination that inflated the first attempt.</span></a>
  <a class="card" href="mechanism-benchmarks.html"><b>Mechanism benchmarks</b><span>Clock versus content-addressed invalidation over 788 days of commit history, and where identifier normalization backfires.</span></a>
  <a class="card" href="subagent-failure-taxonomy.html"><b>Subagent failure taxonomy</b><span>One long session, classified: stale premises, duplicated artifacts, and traps rediscovered three times in a day.</span></a>
  <a class="card" href="prior-art-review.html"><b>Prior art review</b><span>What the surveyed memory and coordination systems each require, and what was already present in the graph.</span></a>
</div>

The single most consequential finding is the cheapest to state: an agent
that reports success is reporting on its own behaviour, not on the world.
The [failure taxonomy](subagent-failure-taxonomy.md) has a subagent
finishing a 9.6-day investigation into a retirement that had already been
completed a week earlier, in the exact convention it went on to recommend.
Nothing was wrong with its reasoning. Its premises had simply expired, and
nothing in the loop was positioned to notice.
