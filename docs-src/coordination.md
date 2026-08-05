---
title: Coordination design
description: Scope claims, artifact deduplication and premise validation across parallel agents, with the benchmarks behind each.
---

# Agent coordination graph — Design

**Date:** 2026-08-03
**Status:** Approved direction, pre-plan
**Supersedes in scope:** nothing. Extends the P0+P1 substrate to a second entity family.

---

## 1. Problem

From `docs/evidence/2026-08-03-subagent-failure-taxonomy.md`, measured over a 10,920-line session with 35 agents and 26.5 agent-days of wall clock:

| category | count | mechanism |
|---|---|---|
| A. Expired conclusions | 4 | premises decay while an agent is outstanding |
| B. Duplicate artifacts | 1 | a sibling's conclusion is reached but not reachable |
| C. Namespace collisions | 2 | no identifier reservation |
| D. Kills losing in-flight work | 4 | no completion record before death |
| E. Rediscovered facts | 3+ | main-loop context does not reach agents |

The dominant failure is A. A 9.6-day agent returned a plan for a retirement that had completed seven days earlier. Its measurements were sound when taken; the conclusions drawn from them expired.

**This is a stale-read problem.** That is the framing the rest of the design follows.

## 2. What already exists

P0+P1 shipped a general graph substrate, currently used only for `criterion` nodes:

- `nodes(id, type, …)` — `type` is free text; only `add_criterion` hardcodes `'criterion'`.
- `edges(src, dst, rel_type)` — PK on the triple; `ALLOWED_REL_TYPES` is an extensible frozenset.
- `deltas(entity_id, change_type, old_value, new_value, wall_time, logical_clock)` — append-only, transactionally written with the clock bump.
- A monotonic logical clock in `meta`, incremented under `BEGIN IMMEDIATE`.
- Measured cost: derivation is ~0.09 ms per node, roughly linear.

Nothing here needs replacing. The clock is a version counter and the delta log is a change stream — which is precisely what the coordination problem needs.

## 3. Design principles

1. **Staleness is a version check, never a judgment.** No model participates in deciding whether an agent's conclusions expired.
2. **Pull is free; push is expensive.** Validation costs one indexed query in the controller's context. A relay costs tokens in the agent's context. Push is an optimization to be justified, not a default.
3. **Publish the diff, not the world.** A relay carries `entity, old→new, clock, one line why`.
4. **Structural indexes may be cached; derived verdicts may not.** The P0 invariant exists because a stored *status* can lie about evidence. A subscription index cannot lie — SQLite maintains it transactionally. Criterion status stays uncached; graph structure is indexed freely.
5. **Everything is one substrate.** Coordination reuses the same nodes, edges, deltas and clock as criteria. No second store.
6. **Agents propose; a neutral steward applies.** No agent holds write authority over a shared surface. Adopted from ATM (arXiv 2607.00041) — see §3.1.
7. **Invalidate on content, not on activity.** A read is stale only when the content it depended on changed, not when anything nearby moved. See §5.1.

### 3.1 Relation to prior art

This design is not novel in kind. See `docs/evidence/2026-08-03-prior-art-review.md` for the full survey; the load-bearing points:

- **ATM** (arXiv 2607.00041) governs pre-write admission within a single worktree or filesystem domain, using a Task Contract `⟨g, A, F, S, D, V, E, ε⟩`, atom maps, content identifiers and a neutral steward. Its `V`/`D`/`E` plane — validation commands, deliverables, evidence obligations — is what loopgraph P0+P1 already implements. **We adopt three of its mechanisms (§3.1a) and decline the rest**, because ATM explicitly excludes "cross-machine clones, remote branches, or PR-level distributed coordination", which is where every failure in our taxonomy occurred.
- **CodeCRDT** (arXiv 2510.18893) absorbs conflicts through commutative replicated types rather than preventing them. Not adopted: every failure in our taxonomy is semantic, not textual. `azure_impossible_travel` and `entra_impossible_travel` converge perfectly and remain a duplicate.
- **Blackboard architecture** (Hearsay-II, Linda tuple spaces) is the correct name for what earlier drafts of this document called pub/sub: agents coordinate indirectly through shared structured state rather than by messaging. Used throughout.
- **AgenticFlict**, cited by ATM, reports a **27.67% merge-conflict rate across 142,000 AI-agent pull requests**. External evidence the problem is real at a scale our own 2,674-completion corpus cannot establish.

#### 3.1a Mechanisms adopted from ATM

| mechanism | replaces | why |
|---|---|---|
| Neutral steward | agents writing directly | separates who proposes from who applies; removes write authority from every agent |
| CAS base-hash | bare logical-clock comparison | invalidates on content change, not on any adjacent delta |
| Direction epoch | nothing — this was a gap | a goal or scope change must issue a new epoch; an agent may not silently continue against a superseded intent |

## 4. Entity model

New node types on the existing table:

| type | represents | key property |
|---|---|---|
| `artifact` | a rule, SQL file, MR, chart | semantic key for duplicate detection |
| `slot` | a claimable identifier (`sql/57`, a rule name) | claimed or free |
| `finding` | a conclusion an agent reached | the clock at which it was derived |
| `fact` | a durable environmental truth | e.g. "`glab mr merge` reports success while `state` stays `opened`" |
| `agent` | a dispatched agent | dispatch clock, declared scope, lifetime, `heartbeat_at`, **`epoch`** |
| `epoch` | a direction generation for a goal | incremented whenever goal or scope changes |

New edge types:

| rel_type | meaning |
|---|---|
| `scope_of` | agent → the nodes it may touch. **Doubles as its subscription set** — no separate subscription table. |
| `claims` | agent → slot. Atomic; a second claim fails. |
| `derived_from` | finding → the nodes it was computed from |
| `refused` | agent → artifact-class it declined, with the reason in the delta |
| `duplicates` | artifact → the artifact it duplicates |

New change types: `SCOPE_CLAIMED`, `SCOPE_CONFLICT`, `RELAY_SENT`, `STALE_ON_RETURN`, `EPOCH_ADVANCED`, `WRITE_PROPOSED`, `WRITE_APPLIED`.

### 4.1 The neutral steward

Agents never apply a governed write. An agent emits a `WRITE_PROPOSED` delta describing the intended change; the steward validates it against the proposer's scope, epoch and base hash, and only then applies it and emits `WRITE_APPLIED`.

This separates *who proposes a change* from *who performs it*. The practical consequence for our taxonomy: a stale or out-of-scope proposal is rejected at apply time by a party that has current state, rather than discovered after an agent has already written and opened an MR — which is how !515 reached review with four duplicate rules.

**Scope of the steward.** It governs *shared surfaces only* — artifacts, slots and identifiers declared in `scope_of`. An agent's private work-in-progress inside its own worktree is untouched; requiring every local edit to traverse a broker would be unusable. The steward is the apply authority at the boundary where work becomes shared.

**Degradation.** Where a steward cannot mediate — an agent pushing directly to a remote, an external actor merging — the write is observed after the fact via §9 external ingestion and recorded as `WRITE_APPLIED` with no matching `WRITE_PROPOSED`. That asymmetry is itself a detectable signal: ungoverned writes are visible as such rather than silently indistinguishable.

### 4.2 Direction epochs

Every goal carries an epoch. If the goal statement or the scope set changes, the epoch advances and an `EPOCH_ADVANCED` delta is written.

An agent records the epoch it was dispatched under. A proposal carrying a superseded epoch is refused by the steward, and the agent is culled or relayed per §7.1. This closes a gap our earlier drafts had no answer for: !515's MR was rejected partly because "its premise may now be wrong" — the runbook asked eight tenants to enable a setting the data showed was already enabled. That is direction drift, not staleness of a specific read, and a clock comparison would not have caught it.

## 5. The two paths

### 5.1 Pull — validate on return (always)

When an agent stops, before its output is accepted, validation runs in two stages. Both are indexed queries; neither involves a model.

**Stage 1 — cheap filter on the clock.** Find the scope entities that saw *any* delta while the agent ran:

```sql
SELECT DISTINCT entity_id FROM deltas
WHERE entity_id IN (SELECT dst FROM edges WHERE src = :agent AND rel_type = 'scope_of')
  AND logical_clock > :dispatch_clock;
```

**Stage 2 — CAS base-hash on the survivors.** A clock advance is not proof the agent's premise changed: a delta may have touched an entity's ownership, or changed and reverted it. Each `scope_of` edge records the **content hash of the entity as the agent read it**. Stage 2 recomputes that hash for the candidates from stage 1 and compares:

```
stale ⟺ ∃ e ∈ candidates : hash_now(e) ≠ hash_at_dispatch(e)
```

Only a genuine content difference invalidates. This is compare-and-swap validation, adopted from ATM's base-hash mechanism, and it exists to keep false invalidations low — a gate that reports stale on every return is one people learn to override.

**Epoch check.** Independently of content, an agent dispatched under a superseded epoch (§4.2) is invalid regardless of hashes: its goal changed underneath it.

Conclusions failing either check are quarantined rather than merged (§7.3). This path runs for **every** agent regardless of lifetime, because it is effectively free — stage 1 is one indexed query, and stage 2 touches only its output.

**Cost note.** Stage 2 requires reading current content for candidate entities, which is the one place validation is not purely a database operation. Bounding it is why stage 1 exists: in a quiet session the candidate set is empty and no content is read at all.

### 5.2 Push — relay while the agent can still act — DEFERRED, not in first implementation

**Measurement changed this decision.** See `docs/evidence/2026-08-03-mechanism-benchmarks.md` §4. Summary of why push is not built first:

- Pull (§5.1) already detects **100%** of staleness, for one indexed query and zero tokens. Push adds no detection.
- Push's only value is letting an agent abort early and save remaining work. In the >24h cohort — highest staleness risk at 59.5% — agents average 208 tool calls, *fewer* than the 1–4h cohort. They are outstanding across suspensions, not working, so little remains to save.
- No threshold is defensible. Relaying above 10 minutes covers 64.4% of the fleet at 24% precision to reach 81% recall: three wasted relays per useful one, each spending tokens in an agent's context. Raising the threshold to 24h gives 59.5% precision at 2% recall.
- The delivery hole is real: the probe showed a queued relay is silently lost if the agent stops before its next tool round.

Revisit only if measurement shows agents doing substantial work *after* their premises break. The design below is retained for that case.

#### 5.2a Design, if revisited

Measured delivery semantics (probe, this session):

| agent state | delivery |
|---|---|
| running, calling tools | works, ~1 tool round |
| blocked inside one long call | delayed until that call returns |
| stopped before its next round | **silently lost** |

So push is best-effort and §5.1 is the backstop, not an optimization of it.

**When to push.** Relay only when `agent_lifetime_so_far` exceeds a threshold, because a short agent will finish before the relay changes anything and the tokens are wasted. Default threshold: 10 minutes. Below it, rely on pull.

**What to push.** On each publish cycle, resolve affected agents with a single batched query rather than one per delta:

```sql
SELECT e.src AS agent, d.entity_id, d.old_value, d.new_value, d.logical_clock
FROM deltas d
JOIN edges e ON e.dst = d.entity_id AND e.rel_type = 'scope_of'
WHERE d.logical_clock > :last_published_clock;
```

Requires an index on `edges(dst, rel_type)` — the only schema addition.

Then **coalesce before sending**: group by agent, dedupe by `entity_id`, keep the latest state per entity. Twenty deltas during one agent's run become one message listing the entities that moved. This is log compaction, and it is what keeps relay cost bounded as fan-out grows.

## 6. How each failure closes

| failure | mechanism |
|---|---|
| **A** expired conclusions | §5.1 two-stage validation (clock filter, then CAS base-hash) on every return; §5.2 relay for long agents, so the 9.6-day agent learns on day 3 rather than day 10; §4.2 epoch check catches the case where the *goal* moved rather than the data |
| **B** duplicate artifacts | query `artifact` by semantic key before create (§8.6); `refused` edges make a sibling's decision reachable; §4.1 steward rejects the proposal at apply time rather than letting it reach MR review |
| **C** namespace collisions | `claims` is an atomic all-or-nothing insert on the edges PK (§8.5); the loser is told at claim time, not at rebase |
| **D** kills losing work | deltas are written as work completes and survive the kill; the successor resumes from the frontier (§7.4) |
| **E** rediscovered facts | `fact` nodes injected into the dispatch prompt |

Two mechanisms carry more than one category. The **steward** (§4.1) is the apply-time gate for B and the enforcement point for the epoch check in A. The **epoch** (§4.2) covers a failure mode no clock or hash detects: !515's premise was invalidated not because its reads changed but because the goal it was pursuing had been shown unnecessary.

## 7. Agent lifecycle: culling and replacement

The evidence demands this section. Nine agents were killed simultaneously by a weekly quota; three background tasks were orphaned with **no completion record** ("these leave no transcript marker"); one agent "died mid-reconciliation"; five ran past 24 hours. Death is common, unobservable, and currently loses everything in flight.

### 7.1 Cull triggers

All six are decided by query. No model participates.

| trigger | test |
|---|---|
| stale premise | §5.1 OCC query returns rows |
| lifetime exceeded | `now - dispatch_time > max_lifetime` |
| no progress | no closing delta from this agent in N — the existing stagnation rule, scoped per agent |
| scope conflict | two live agents hold `scope_of` on the same write target |
| external death | lease lapsed (§7.2) |
| superseded | the artifact the agent is building already exists |

Order of response for a stale premise: **relay first, cull second.** A long-running agent that can still act is told what moved and may abort itself, which preserves the independent-convergence effect the taxonomy records as genuine validation. Culling is for agents that are unreachable, unresponsive, or whose premise is fatally invalidated.

### 7.2 Claims are leases, not locks

An agent that dies holding `sql/57` must not poison that slot forever. Claims are **leases with a TTL**, the same reason ZooKeeper and Chubby use ephemeral nodes.

**Lease validity is derived, not stored.** The `claims` edge carries no expiry column. An agent node has `heartbeat_at`; a claim is valid exactly while `now - heartbeat_at < lease_ttl`. This needs no schema change to `edges`, and it keeps the design's rule that state is derived rather than cached.

**Expiry is lazy.** There is no background sweeper and no periodic scan. A lapsed lease is discovered when someone next tries to claim that slot — evaluated at claim time, like lazy TTL eviction. The reclaim is written as a `SCOPE_RECLAIMED` delta so it is auditable rather than silent.

**Heartbeat is explicit**, not inferred from work. Inferring liveness from delta writes would falsely cull an agent legitimately blocked in a forty-minute build. The agent touches its own node; the TTL is generous and renewable. Default `lease_ttl` 30 minutes.

### 7.3 Quarantine

A culled agent's output is marked `quarantined` and **never auto-merges**. This is precisely what !515 needed and did not have: four duplicate rules reached MR state and were caught only by human review on return.

Quarantine is a state on the artifacts, recorded by delta. Nothing is deleted — the work is preserved for triage.

### 7.4 Succession

A replacement is generation N+1, linked by a `succeeds` edge, and inherits:

- **Scope** — `scope_of` edges retargeted to the successor.
- **Claims** — transferred directly, never released and re-raced. Releasing first would let a third agent take the slot in between.
- **Findings, individually revalidated.** Each inherited `finding` is checked with the §5.1 query against its own `derived_from` set. Survivors are kept; the rest are dropped with a delta recording why.
- **The completion frontier.** The successor reads the delta log to see what its predecessor actually finished, and starts from there.

That last point is the whole value. The nine quota-killed agents were relaunched from scratch because nothing recorded partial completion. With deltas written as work lands, a successor resumes instead of restarting.

### 7.5 Cost

| operation | cost |
|---|---|
| evaluate all six cull triggers | one query each, indexed |
| lease validity | derived from `heartbeat_at`; no sweeper, no scan |
| succession | edge retargeting plus one revalidation query per inherited finding |
| quarantine | one delta per artifact |

## 8. Write-set conflict classes — parallel vs serial, decided before dispatch

### 8.1 The case

Observed live, 2026-08-03: three MRs — !535 (API Activity), !539 (inventory), !543 (Process/Script) — all touch the same three files, so every merge re-conflicts the others. Two more, !541 and !542, are independent and run clean.

The manual workaround was an explicit one-at-a-time handshake: the agent rebases one, reports, the controller merges, the controller tells it the new `main` SHA, repeat. Without it, "rebasing all three against one base means two are stale before I can land them — which already happened once."

Both halves of that are computable rather than discovered.

### 8.2 Conflict classes

Each agent declares a **write-set** at dispatch: the paths and artifacts it intends to modify. A conflict class is a connected component over write-set intersection.

- Agents in the same class **must serialize** — one holds the class's merge token at a time.
- Agents in different classes **run parallel** with no handshake.

For the case above: `{!535, !539, !543}` is one class; `{!541}` and `{!542}` are singletons. The partition is a set intersection over declared write-sets — no model, no heuristics, and computed once at dispatch rather than rediscovered at each rebase.

This is the scheduling decision the taxonomy's category C failures were paying for at the wrong time: !506 vs !510 both claiming `sql/57` was a write-set intersection nobody computed.

### 8.3 Landing order is not ours to build

Serial rebase-on-result is a merge queue, and GitLab implements it as Merge Trains (availability is tier-dependent). Rebuilding it would repeat the mistake of reimplementing pytest.

| layer | responsibility |
|---|---|
| this graph | conflict-class partition; parallel-vs-serial decision before dispatch |
| merge train | landing order, rebase against the previous result |
| relay (§5.2) | tell live agents in an affected class that their base moved |

Where a merge train is unavailable, the class's merge token degrades to exactly the manual handshake — but driven by the graph rather than by a human relaying SHAs.

### 8.4 What the relay removes

"I tell it the new main SHA" becomes: `main` is a node, a merge writes a delta, and every agent whose `scope_of` intersects the affected class receives it at its next tool round (measured: one tool round). The controller stops being a message bus for SHAs and remains the authority on what merges.

**Honest limit:** if three MRs genuinely must touch the same three files, they still serialize. The graph does not remove the conflict. It removes paying to discover it once per MR, and it stops the queued agents working against a base that is already dead.

### 8.5 Acquisition is atomic, and scope is three-level

**All-or-nothing.** An agent acquires its entire scope in a single `BEGIN IMMEDIATE` transaction: every `claims` edge is inserted, or none is. Partial acquisition is never observable.

This is not tidiness. Incremental acquisition permits hold-and-wait — agent A holds file 1 and waits for file 2 while agent B holds file 2 and waits for file 1. Hold-and-wait is one of the four conditions deadlock requires, so removing it makes deadlock **impossible by construction**. No lock-ordering protocol, no deadlock detector, no timeout-and-retry storms, no backoff tuning. A failed acquisition rolls back whole and the agent is simply not dispatched yet.

**Three levels, claimed together.** Textual overlap is not the same as similarity, and the taxonomy has one failure of each kind:

| level | catches | evidence |
|---|---|---|
| `path` | git conflicts | !535/!539/!543 share three files (§8.1) |
| `identifier` | ordering and namespace slots | `sql/57_source_host_hourly.sql` vs `sql/57_win_service_control.sql` — different paths, same slot, precedent is a two-day bootstrap outage |
| `semantic_key` | duplicate work | `azure_impossible_travel` vs `entra_impossible_travel` — **zero path overlap**, same detection |

A path-only partition would have cleared !515 to run in parallel, and it would still have shipped four duplicate rules. The semantic level is the only one that catches category B.

Each level is a `slot` node; a scope is the union across levels; acquisition is atomic over that union.

### 8.6 Where semantic keys come from — measured

Measured 2026-08-03 across 9 repositories and 6 project types (Go, TypeScript, Python, YAML/Helm, Terraform, SQL).

| identity space | objects | collision rate | groups |
|---|---|---|---|
| filenames, blind first-token strip | 9,154 | **63.1 %** | noise |
| filenames, raw basename | 9,154 | 40.7 % | noise |
| SQL `detections.*`, blind first-token strip | 179 | 11.7 % | 5 true, 6 false |
| SQL `detections.*`, **vendor vocabulary** | 136 | **4.4 %** | **5 true, 0 false** |
| Go exported symbols (ALB controller) | 730 | **0.0 %** | — |
| Go exported symbols (a large OSS service) | 1,520 | **0.0 %** | — |
| TS exports (google-cleaner) | 131 | **0.0 %** | — |
| TS exports (pokemonchampions) | 495 | **0.0 %** | — |

**Filenames are never the identity space.** A 40–63 % collision rate would place thousands of unrelated files in one conflict class and serialise everything. In Helm and GitOps trees the same basename repeats per chart — 5,045 files named `values.yaml` in one earlier measurement — and they are all different artifacts.

**In compiled and typed languages, no derivation is needed.** Go and TypeScript show 0.0 % collision because the language already enforces uniqueness in its declaration namespace. The identifier *is* the semantic key, and a collision there is a genuine duplicate by definition.

**Derivation is the exception, not the rule — corrected by measurement.** A later K8s test (`docs/evidence/2026-08-03-mechanism-benchmarks.md` §3) contradicted the broader claim originally made here. Across four namespaces tested, derivation helped in exactly one:

| namespace | collision | derivation |
|---|---|---|
| Go exported symbols | 0.0% | not needed |
| TypeScript exports | 0.0% | not needed |
| K8s `kind/name` | 7.6% → 9.2% | **harmful** — stripping merged `ServiceAccount/search-workflows` into `ServiceAccount/workflows` |
| SQL detection views | 11.7% → 4.4% | helps |

Derivation earns its place only where **the same logical thing is deliberately implemented once per vendor** — detection rules across azure/entra/duo/sysmon. Everywhere else the declared identifier is the key and must be left alone. Default to no derivation; enable it per namespace, never globally.

Where it does apply, strip a leading token **only when it appears in a declared vendor vocabulary** (`azure`, `entra`, `duo`, `sysmon`, `win`, …). Blind first-token stripping produced six false groups (`baseline` ← device/egress/exfil/source/throughput, `alert_state`, `risk`, `catalog`) where the first token *is* the discriminator. Requiring vocabulary membership removed all six and kept every true group, including two nobody had flagged: `jar_from_user_writable` and `java_runtime_user_writable`, the same detection implemented twice over sysmon and win.

**Identity space per project type:**

| type | space | derivation |
|---|---|---|
| Go, TypeScript, Python | exported symbols | none — already unique |
| SQL | `schema.object` from `CREATE` | vendor vocabulary |
| Terraform | resource address `type.name` | none — already unique |
| K8s / Helm | `kind/namespace/name` from manifest content | vendor vocabulary |
| migrations | numeric prefix | identifier level, not semantic |

**Honest limits.** The 4.4 % figure is one corpus of 136 objects, and the vendor vocabulary used was written against that corpus, so it fits it by construction. The vocabulary is a small declared list per project — cheap to maintain, but its quality bounds the result. Where no vocabulary exists, treat semantic keys as **absent rather than guessed**: the partition degrades to paths and identifiers, and says so.

**No model participates.** Vocabulary membership is a set lookup. This keeps an unreliable verdict out of a blocking mechanism, for the same reason model judgement stays out of staleness.

## 9. External change ingestion

The legacy-pipeline retirement was performed outside any agent, so nothing would have published it. External sources are polled incrementally against a stored **watermark**, never rescanned:

- GitLab: `updated_after=<last_seen>` per project, watermark in `meta`.
- Git: `git log <last_seen_sha>..HEAD --name-only`, watermark is the SHA.

Each poll writes deltas for the artifacts it touched, which then flow through §5 unchanged. Polling is incremental by construction; cost is proportional to what changed, not to repository size.

## 10. Efficiency budget

| operation | cost | notes |
|---|---|---|
| validate a returning agent, stage 1 | one indexed query | zero tokens; empty result in a quiet session ends validation here |
| validate a returning agent, stage 2 | content hash per candidate only | the only non-database step; bounded by stage 1's output |
| epoch check | one column comparison | independent of content |
| steward apply | scope + epoch + hash check, then one write | shared surfaces only; private worktree edits are untouched |
| resolve subscribers for a publish cycle | one batched join | never per-delta; the N+1 lesson from the P0 review |
| relay to one agent | one coalesced message | diffs only, no subgraph dumps |
| external poll | proportional to changes | watermarked, not rescanned |
| slot claim | one insert, PK-enforced | no coordination protocol |

**The rule that governs all of it:** a mechanism that spends agent context tokens must be justified against the work it saves. Everything that can be answered with a query is answered with a query.

## 11. Non-goals

- No Proactivity Scorer or ranking. Relevance is exact set membership in `scope_of`, not a weighted score.
- No second datastore, no message broker. The delta log is the stream; edges are the routing table.
- No suppression of independent overlap. The taxonomy's NOT-failures section records that three agents hitting one bug was "the system working" and that independent convergence validated design decisions. Only *collisions* and *stale premises* are targets.
- No model in the staleness path.

## 12. Open questions

1. **Vendor vocabulary coverage.** §8.6 measured 4.4 % collision with 0 false groups on a 136-object SQL corpus, but the vocabulary was written against that corpus. Measure against a second project type — K8s manifests are the obvious next one — before treating the number as general.
2. **Merge train availability.** §8.3 delegates landing order to GitLab Merge Trains, which are tier-dependent. Confirm availability on the target projects; where absent, the class merge token degrades to a graph-driven handshake.
3. Whether `finding` nodes are worth their write cost, or whether `derived_from` on artifacts suffices.
4. **Lease TTL default.** 30 minutes is a guess. It must exceed the longest legitimate single tool call — a slow CI wait or a large build — or a live agent gets culled mid-work.
5. **What content the base hash covers.** For a file, the file. For a `slot` or a semantic key, there is no obvious content to hash — a slot's "content" is whether it is claimed. Those entities may need clock comparison only, with the hash applying to `artifact` nodes. Decide before implementing §5.1 stage 2.
6. **Steward boundary in practice.** §4.1 governs shared surfaces and leaves private worktree edits alone, but the line between them is a judgement in a repo where a worktree edit becomes shared the moment an MR opens. Likely rule: the boundary is the push, not the write. Confirm against the !535/!539/!543 workflow before building.

**Resolved during design:**

- *Relay or kill* — §7.1: relay first, cull second. A reachable long-running agent is told what moved and may abort itself, preserving the independent-convergence effect the taxonomy records as real validation.
- *Culled work* — §7.3: quarantined, never auto-merged, triaged by the successor.
