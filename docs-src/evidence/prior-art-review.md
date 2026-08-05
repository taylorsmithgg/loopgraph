---
title: Prior art review
description: What each surveyed memory and coordination system requires, against what the graph already had.
---

# Prior art review — multi-agent write coordination

Conducted 2026-08-03 before committing to an implementation plan.

## Headline

**We are not first.** ATM (arXiv 2607.00041, Jun 2026) occupies almost exactly the niche of our §8 design, is more rigorously specified, and has a working Apache-2.0 implementation. It also has a scope boundary that excludes the failures we actually measured — which is the reason not to adopt it wholesale.

## External validation that the problem is real

ATM cites the **AgenticFlict** dataset: **142,000 AI-agent pull requests**, 107,000 deterministic merge simulations, a **27.67% merge-conflict rate**, and 336,000 fine-grained conflict regions. That is a far stronger evidence base for the problem's existence than our own corpus, which found 17 long-running agents in 2,674 completions.

## ATM — the closest analog

**Model.** A Task Contract `T = ⟨g, A, F, S, D, V, E, ε⟩`: approved intent, allowed resources, forbidden predicates, governed scope paths, required deliverables, validation commands, evidence obligations, and a direction epoch. Three planes — task contract, mutation admission, evidence closure — with a CID broker as the admission subsystem and a **neutral steward** as the only actor permitted to apply governed writes.

**Mapping to our two projects:**

| ATM | ours |
|---|---|
| Task Contract `A`, `S` (allowed files, scope paths) | scope declaration at dispatch (§8) |
| Atom / atom map | `artifact` and `slot` nodes; the graph |
| Candidate CID + ConflictKey | atomic claim (§8.5) |
| CAS base-hash, `readAtoms`, active registry | OCC against the logical clock (§5.1) |
| Task Contract `V`, `D`, `E` (validation, deliverables, evidence) | **loopgraph P0+P1 criteria** |
| Neutral steward | *absent* |
| Direction epoch `ε` (G2) | *absent* |

The `V`/`D`/`E` correspondence is exact: loopgraph's criteria engine is ATM's evidence-closure plane, built independently. Our two projects together approximate ATM's three planes, minus the steward.

**ATM's drift taxonomy** distinguishes epistemic, specification, scope, evidence and **state drift** — the last defined as "an intent is built on a base state or read dependency that has changed," addressed by a CAS base-hash. That is our failure category A, with a more precise mechanism than ours.

### Three mechanisms worth taking

1. **Neutral steward.** Agents *propose* writes; a neutral party applies them. This separates "who proposes a change" from "who performs the write," and no agent ever holds write authority on a shared surface. Structurally stronger than our detect-after-the-fact approach.
2. **CAS base-hash instead of a bare version counter.** Our OCC query fires when *any* delta touches an entity in scope. A content hash fires only when the content actually depended upon changed — fewer false invalidations, which matters because a design that cries stale on every return trains people to ignore it.
3. **Direction epoch (G2).** If the goal or scope set changes, a new epoch must be issued; an agent may not silently change direction. We have no equivalent, and "the runbook's premise was already false" (!515) is a direction-drift failure.

### Why not adopt ATM wholesale

**Wrong boundary.** ATM states plainly that it "does not address cross-machine clones, remote branches, or PR-level distributed coordination" and operates "within a single controlled filesystem, worktree, or service domain." Our measured failures are precisely cross-worktree and cross-MR:

- !506 vs !510 both claiming `sql/57` — different agent worktrees, different MRs.
- !515's four duplicate rules — a different worktree entirely, no file overlap at all.
- !535/!539/!543 — three separate MRs sharing three files.

ATM's broker would not have seen any of these. The closer analog for that boundary is CAID, which isolates agents in separate Git worktrees and reconciles afterward — which is what already happens here, and is where the cost lands.

**Maturity.** v0.9.0-alpha.1, single author, 2 stars, 1 fork. The paper's own abstract limits its claim to "feasibility within the observed single-domain settings, but not broad comparative superiority over alternative concurrency-control systems."

**Complexity.** A seven-layer admission gate, per-language adapters, atom maps, virtual atoms and two CID tiers is a large surface for a failure population of 17 agents.

## CodeCRDT — the opposite paradigm

CodeCRDT (arXiv 2510.18893) does not prevent conflicts; it absorbs them. Agents hold local replicas and apply commutative, idempotent, causally-tracked operations, so concurrent edits to different regions converge without coordination. Reported **2–3× speedup with 4 concurrent agents**.

Limits, as reported: semantic conflicts survive syntactic convergence (incompatible signatures), convergence overhead, harder debugging, degradation at high agent counts. ATM's related-work section notes CodeCRDT "reports residual semantic conflicts despite character-level convergence."

This is a complementary layer, not a competitor. CRDTs solve textual convergence; none of our five failure categories is textual. `azure_impossible_travel` and `entra_impossible_travel` converge perfectly and are still a duplicate.

## Others surveyed

- **PatchBoard** (arXiv 2605.29313) — schema-validated state mutations, logged and reversible. Our delta log with a schema check. Confirms the append-only-audit approach is conventional.
- **Blackboard architectures / Linda tuple spaces** — the classical ancestor of what we called pub/sub: agents coordinate indirectly through shared structured state rather than messaging. Our graph is a blackboard; worth using the established name.
- **Git worktrees** — the industry consensus isolation primitive, already in use here. Cost is deferred to merge time, which is exactly where it lands for us.
- **Merge trains / merge queues** — the implemented answer to serial rebase-on-result. Already delegated in §8.3.
- **LangGraph / CrewAI / AutoGen** — orchestration frameworks. LangGraph has checkpointing and explicit typed shared state; CrewAI and AutoGen have neither persistence nor checkpointing. None addresses write-set conflict or staleness of an outstanding agent's premises.

## Recommendation

1. **Do not adopt ATM.** Wrong boundary for our measured failures, alpha maturity, and disproportionate complexity.
2. **Take three mechanisms from it**: the neutral steward, the CAS base-hash in place of a bare clock, and the direction epoch. Cite it rather than reinventing its vocabulary.
3. **Rename honestly.** What we called pub/sub is a blackboard architecture; use the established term.
4. **Keep the cross-worktree boundary as our differentiator.** It is the gap ATM explicitly declines and where every failure we measured actually occurred.
5. **Do not build CRDT convergence.** None of our failures is textual.
