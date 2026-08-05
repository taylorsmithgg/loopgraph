---
title: Design
description: The specification loopgraph was built from: what a criterion is, how the gates read it, and what was rejected.
---

# loopgraph — Design

**Date:** 2026-08-03
**Status:** Approved design, pre-implementation

---

## 1. Problem

Agent loops stop before the goal is met, and fan-out produces no global movement. Stated directly by the operator:

> "sheer laziness. goals not being met. distributing to sub agents and not getting any meaningful progress globally."
>
> "looking for harness solutions that continue until they meet our specifications without stopping"

Three work shapes are in scope, and they fail differently:

| Shape | Example | Why current loops fail |
|---|---|---|
| Repo code work | feature, refactor, test coverage | agent declares done with goal partly met |
| Infra GitOps | a GitOps monorepo's manifests, MRs, Argo CD sync | no failing command exists to block on |
| Investigation | SOC log digging, ClickHouse RCA | "done" is a finding, not an exit code |

The shared defect is already recorded in operator memory as the dominant local failure class: **guards, jobs and alerts that report success while doing nothing.** Measure the effect, not the status.

## 2. Evidence base

Three sources drive this design. Each is used for what it actually supports, and its limits are stated.

### 2.1 Loop specification and its anti-patterns — arXiv 2607.00038

Position paper defining the *loop specification*: trigger, goal, verification, stopping rule, memory. Contributes:

- **Five-level verification ladder.** L1 deterministic, L2 rule/constraint, L3 delayed field truth, L4 model-as-judge, L5 human. L1–L2 are the autonomous zone. Design rule: *do not pretend level 4 is level 1.*
- **Named terminal states**, and: an error or an exhausted budget **never** counts as success.
- **Anti-patterns** this design must not reproduce: while-true around a stranger; the self-approving loop; specification gaming; pretending L4 is L1; the unattended runaway.
- **Maturity mismatch** in the 50-loop corpus: verification is mature (70% autonomous, 74% name terminal states), while automated triggers (22%), reusable skills (20%) and durable memory (32%) lag. Those three gaps are precisely what this design targets.
- **Cost per accepted change** proposed as the headline health metric — tokens spent divided by changes that survived verification.

*Limits:* position paper over a descriptive corpus study, single coder, no controlled experiment, no measured budget. Treat as vocabulary and design guidance, not as measurement.

### 2.2 Judge fragility — two empirical results

**Ensembling reviewers hurts** ([arXiv 2606.15689](https://arxiv.org/html/2606.15689v1), 150 samples): Haiku 4.5 alone F1 **0.365**; Haiku + Sonnet union **0.333**. Models detect largely the same bugs, so a second reviewer adds its false positives without adding true positives. 19/150 samples were a shared blind spot. Bigger is not better either — Haiku 4.5 beat Sonnet 4.6 on F1 and recall (+18%) at 3.2× lower cost. The same paper's authors used Opus 4.6 as judge and flag bias toward Anthropic output style.

**Judges over-reject conformant work** ([arXiv 2603.00539](https://arxiv.org/pdf/2603.00539)): systematic overcorrection, with false rejection of conformant code in the ~35–45% range; chain-of-thought recovers only ~5–10%.

*Consequence, and it is the hinge of this design:* **a model-as-judge inside a blocking gate produces a loop that cannot terminate.** Not a tuning problem. It is the unattended-runaway anti-pattern wearing a lab coat.

*Limits:* the overcorrection figures come from a GPT-4/Llama-era setup and are **not** measured on Opus 5 or GPT-5.6. Treat the direction as solid and the exact percentage as unestablished.

### 2.3 Context graph and delta events — arXiv 2607.07721

Live, attributed, time-stamped multigraph of entities with `state`, `owner`, and typed edges (`depends_on`, `blocks`, `owned_by`, `escalates_to`). Every state-modifying operation emits a **delta event** onto an immutable log. A Delta Detection Engine evaluates deterministic threshold rules over the graph.

The architectural move this design adopts, from its §7:

> "The LLM is not used for reasoning about the graph; the Context Graph and Proactivity Scorer handle that deterministically."

*Limits:* single-author paper; the demo graph is 3 persons / 3 tasks / 1 asset; P@5 0.83, FPR 0.11 and 47 min → <30 s come from three self-authored case studies, not a controlled benchmark. **Adopt the substrate, not the numbers.**

*Not adopted:* the Proactivity Scorer and notification layer. That machinery ranks insights for human attention (urgency, relevance, persona-fit via an admittedly unlearned lookup table, dedup, cooldown). This system does not rank notifications; it selects the next action, which dependency edges already determine.

### 2.4 Model capability — routing evidence

| Signal | Opus 5 | GPT-5.6 Sol | Read |
|---|---|---|---|
| SWE-bench Verified | 97.0% | 96.2% | tie; not a routing signal |
| SWE-bench Pro (long-horizon repo) | 79.2%, +14.6 pts | — | real gap → repo work to Opus 5 |
| Terminal-Bench 2.1 | 89.1% | 88.8% | tie; leaderboard warns results "combine vendor and leaderboard harnesses" |
| codex-spark | — | ≈ full Codex on SWE-bench Pro (~56%) in 2–3 min vs 15–17 | strongest available lever |
| Output cost | $25/1M | $30/1M (Terra $15, Luna $6) | checker economics |

**Read:** frontier-vs-frontier capability is effectively a coin flip. The exploitable asymmetries are **cost, latency and vendor-independence** — not intelligence. Routing is therefore a cost knob, not an architectural decision.

## 3. Design principles

1. **State is deterministic. Models never vote on it.** The graph and the rules decide what is open, closed, stale or blocked. This is what makes the overcorrection result irrelevant rather than something to engineer around.
2. **Done is computed, never claimed.** A criterion closes when its evidence command runs and satisfies its expectation. No agent writes `status`.
3. **Measure effect, not status.** Progress is delta events that closed criteria, not counts reported by workers.
4. **The next action is derived, not chosen.** Dependency edges determine what is workable. Removing the choice removes the option to pick the easy item.
5. **Green-but-stale is not green.** Verification decays; a criterion carries `verified_at` and a staleness window.
6. **Model judgment moves to authoring time.** A human approves the criteria once, rather than an unattended judge adjudicating every turn.
7. **Every brake fails loudly.** A rule that matches nothing, an evidence command that never ran, a subagent that changed nothing — each is an explicit state, never silence.

## 4. Architecture

```
   sources of truth            deterministic core                harness
  ┌────────────────┐        ┌──────────────────────┐        ┌──────────────┐
  │ repo / tests   │        │  Context Graph       │        │ Stop hook    │
  │ GitLab / Argo  │──────▶ │  (SQLite)            │──────▶ │ gate         │
  │ ClickHouse     │  evid. │  nodes, edges, state │  read  │ block/allow  │
  │ kubectl        │  cmds  ├──────────────────────┤        └──────────────┘
  └────────────────┘        │  Delta Log (append)  │        ┌──────────────┐
                            ├──────────────────────┤──────▶ │ SubagentStop │
                            │  Threshold Rules     │        │ ownership    │
                            └──────────────────────┘        └──────────────┘
                                       ▲
                                       │ work only (no state writes)
                            ┌──────────┴───────────┐
                            │ workers: CC / Codex  │
                            └──────────────────────┘
```

**Five components.**

- **Context Graph** — durable state. Criteria, artifacts, owners, dependencies.
- **Delta Log** — append-only record of every state transition. The loop's memory and its progress signal.
- **Evidence Runner** — executes each criterion's command, compares to expectation, writes the resulting delta. The only writer of `status`.
- **Threshold Rules** — deterministic predicates producing brakes and terminal states.
- **Gate** — `Stop` / `SubagentStop` hooks that read the graph and block or allow.

**Storage: SQLite, not NetworkX.** The source paper uses in-memory NetworkX for a demo. The gate runs as a separate short-lived process on every turn, concurrently with subagents. It needs durability, transactions and concurrent readers. Tables: `nodes`, `edges`, `deltas`, `runs`.

## 5. Data model

### 5.1 Criterion node

```yaml
id: C7
type: criterion
statement: "Linux hosts appear in the lake"
evidence:
  cmd: "clickhouse-client -q \"SELECT count() FROM lake WHERE source_type='host.linux' AND ts>now()-INTERVAL 1 DAY\""
  expect: { stdout_int_gte: 1 }
staleness_window: 24h
owner: null                # set when assigned to a worker
status: derived            # open | closed | stale | blocked | unproven
verified_at: null
```

`status` is **never** written by an agent. It is computed by the Evidence Runner.

**`unproven` is a distinct state, not a flavour of `open`.** A criterion whose evidence command has never completed successfully cannot be closed, and cannot be silently treated as failing either — it means the check itself is unbuilt. This is the schema form of the local dominant defect: a guard that has never been watched to fail is not a guard. It is derived from the source paper's confidence term `K(c) = 1 − missing_props/total_props`.

### 5.2 Edges

| Edge | Meaning |
|---|---|
| `depends_on` | C7 depends_on C3 — C7 not workable until C3 closed |
| `blocks` | inverse, for traversal |
| `owned_by` | criterion → worker (agent id) |
| `evidenced_by` | criterion → artifact (commit, MR, query result) |
| `escalates_to` | worker → human |

### 5.3 Delta event

```
δ = (entity_id, change_type, old, new, wall_time, logical_clock)
change_type ∈ { STATE_TRANSITION, THRESHOLD_BREACH, STALENESS,
                DEPENDENCY_RISK, OWNERSHIP_CHANGE }
```

Append-only. Replayable. **This log is the durable memory** the loop paper names as the least-developed element of current practice (32% of corpus) — and because it is derived from evidence runs rather than appended by agents, it is curated by construction, which is the condition under which accumulated memory helps rather than degrades performance.

## 6. The gate

### 6.1 Stop hook contract

On every turn end:

1. Run the Evidence Runner over all criteria (or the stale/dirty subset).
2. Evaluate threshold rules.
3. Decide.

```json
{
  "decision": "block",
  "reason": "3 criteria open, 1 stale.\nC7 open: expected rows>=1, got 0\n  cmd: clickhouse-client -q \"...\"\n  last run: 2026-08-03T14:02:11Z\nC9 open: pytest tests/test_ingest.py::test_relay — 2 failed\nC11 stale: verified 31h ago, window 24h\nNext workable (deps satisfied): C7, C11. C9 blocked by C7."
}
```

The `reason` carries **actual command output**, not a summary. Advisory-only findings use `hookSpecificOutput.additionalContext` instead, leaving `decision` unset.

### 6.2 Termination

The loop stops when, and only when:

- zero `open` criteria, **and**
- zero `stale` criteria, **and**
- zero `unproven` criteria, **and**
- no threshold rule firing.

All four are deterministic. Nothing here consults a model.

### 6.3 Gate safety

- **`stop_hook_active` is the harness's loop guard, not a capability flag.** It is `true` only while Claude Code is *already continuing because a stop hook blocked*; it is `false` on every ordinary first stop — which is exactly when a block lands. Reading `false` as "cannot block" disarms the gate on every normal turn (it did, for the gate's whole life: zero blocks in 15 project databases). Use it only to reset the consecutive-block count at the start of a fresh stop chain.
- **The harness caps blocks too.** `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` (default 8) overrides the hook and ends the turn with its own message. Keep `LOOPGRAPH_MAX_BLOCKS` under it so loopgraph names the terminal state itself; raise both together to drive longer.
- **Max consecutive blocks.** A hard ceiling on unbroken blocks, independent of budget. On breach → `stalled`, allow stop, escalate loudly. Guarantees the gate cannot itself become the runaway.
- **Gate errors do not block.** An exception in the gate exits non-blocking with a loud `systemMessage`. A broken gate must never masquerade as a failing goal — and must never silently pass either.
- **Timeout.** Evidence commands carry per-criterion timeouts; a timeout is `unproven`, not `closed`.

### 6.4 Criterion selection: weakest, not shortest

Added after the design proved inert in practice. Bennett, [arXiv:2301.12987](https://arxiv.org/abs/2301.12987): among hypotheses that entail the observations, maximising **extension** beats minimising description length by 1.1–5× on generalisation. A criterion is a hypothesis about done-ness; its extension is the set of world-states where its check passes.

- **Entailment gate, first.** `add` runs the check at authoring time and refuses one that already passes. A green-at-authoring check does not entail "the goal is unmet" — it explains nothing, and a graph full of them is what a fully-specified-and-idle system looks like. `--guard` is the exception (fences are green by definition), `--allow-green` the override.
- **Weakness, second.** Among discriminating candidates prefer the widest: a check that runs the system admits every implementation that works; a check that greps for one literal admits one. `weakness.py` scores this structurally and warns below `BASE_SCORE`.
- **Order is load-bearing.** Maximising weakness without the gate selects `true`, which is the maximal-extension command and the worst possible check. Brevity survives only as a tie-break.
- **Never trust a derived command blindly.** `weakness.is_safe()` refuses `rm -rf`, `git push`, `sudo`, `curl | sh` and friends. Model-derived criteria were prototyped and dropped — a multi-second call on every prompt, executing shell nobody read.

### 6.5 Self-starting: declaring nothing is a decision, not a default

The gate is inert without criteria, so the cheapest path was always to declare none — and everywhere, that is what happened. `UserPromptSubmit` records the stated goal as pending; the `Stop` hook refuses a turn that answered it with nothing, at most `MAX_SPEC_BLOCKS` times, then allows the stop while naming the result **UNVERIFIED, not verified**. `loopgraph noop --reason` is a first-class answer. Guards do not satisfy the demand: a green suite is not a statement of what this request meant, and `terminal_state` must not report `success` on guards alone while a goal is pending.

### 6.6 One database, many sessions

The db is keyed by git root and falls back to cwd, so every session outside a repo shares one graph. Observed live: three sessions in `$HOME`, one declaring a goal mid-work while the others sat blocked on criteria they had never heard of.

A goal belongs to whoever stated it. `add` stamps the authoring session; the gate enforces a goal only for its owner. Guards and `--global` criteria bind everyone; so does everything when no session identity is available, because a gate that quietly stops gating is the one failure never worth risking.

**Unowned criteria bind nobody.** Enforcing them was tried first and is the worse failure: every session that dies mid-goal leaves a permanent hostage in the shared graph. The compensating control is that not-enforced is never unmentioned — `status` lists them, the Stop hook names them on the way past, and `adopt`/`drop` are offered inline. `check` answers for the calling session's specification; `status` shows the whole board and shouts `MISMATCH` if the hook and the CLI ever disagree about session identity.

## 7. Fan-out

The unaddressed half of the problem. A subagent is assigned **criterion IDs**, not prose.

1. `SubagentStop` fires with `agent_id`, `agent_type`.
2. Re-run evidence for exactly the criteria that agent owned.
3. Compare delta log before/after.
4. **No closing delta on any owned criterion → the agent produced nothing**, regardless of how confident its summary reads. Discard the claim, record `OWNERSHIP_CHANGE` back to unassigned, do not count it as progress.

This generalises the operator's existing `tool_uses == 0` heuristic from *status* to *effect*. A subagent that made many tool calls and moved nothing is caught identically to one that made none.

**Global progress** = closing deltas across the whole graph per unit spend. Derived from artifacts. It is not an aggregation of N self-reports, which is what made previous fan-out unmeasurable.

**Worktree isolation is mandatory, not preferential,** for any two workers that write. Sharing one checkout has already landed a commit on another agent's branch here (!202).

## 8. Threshold rules and terminal states

| Rule | Predicate | Fires |
|---|---|---|
| R-01 stagnation | no closing delta in N turns | `stalled` |
| R-02 staleness | `verified_at < now() − staleness_window` | `STALENESS` |
| R-03 blocked | criterion open ∧ all deps open ∧ age > threshold | `blocked` |
| R-04 budget | spend > ceiling | `exhausted` |
| R-05 unproven | evidence never completed successfully | `unproven` |
| R-06 dependency risk | closing C would not unblock anything workable | `DEPENDENCY_RISK` |

**Terminal states:** `success`, `no-op`, `blocked`, `stalled`, `exhausted`, `contested`.
**An error or an exhausted budget is never success.**

## 9. Model roles

Models are workers. They hold three jobs and no others.

| Job | Who | Effort | Note |
|---|---|---|---|
| Draft criteria from a goal | either | high | **human approves once** — this is where judgment lives |
| Do the work | Opus 5 for long-horizon repo; either elsewhere | high/max | tie on benchmarks → route by tooling and cost |
| Render explanations | cheap tier | low | no state authority |
| **Adversarial gaming pass** | **Codex, cross-vendor** | medium | see below |

**The one place Codex is load-bearing.** A deterministic graph cannot detect that a criterion is *gameable* — that its evidence command can be satisfied without solving the problem (edit the test, hardcode the expected output, satisfy the letter). That is the specification-gaming anti-pattern, and it is a property of the spec, not of the state.

So: at criteria-authoring time, a cross-vendor pass attempts to **cheat each criterion** and reports how.

```bash
codex exec -s read-only -m gpt-5.3-codex-spark \
  --output-schema gaming-verdict.json -o out.json \
  "For each criterion, produce the cheapest change that satisfies its evidence
   command without achieving its stated intent. Cite file:line."
```

This runs **once per criterion at authoring time**, human-reviewed — not per turn, not unattended, not in the blocking path. Vendor split here mitigates the documented own-style judge bias; it is explicitly **not** claimed to add coverage, since ensembling was shown to reduce it.

**Effort routing by stakes, not habit.** The current `~/.codex/config.toml` default of `xhigh` globally is waste: overcorrection barely moves with reasoning effort (~5–10%). Low/medium for mechanical checks; high/max reserved for the hard maker turn and contested adjudication.

## 10. Security

The current Codex configuration is:

```toml
approval_policy = "never"
sandbox_mode    = "danger-full-access"
network_access  = true
```

An unattended loop invoking that is erring unattended. Non-negotiable:

- Every non-maker Codex invocation passes **`-s read-only` explicitly.** The config default will not do it, and `approval_policy = "never"` means nothing will stop a write.
- The gaming pass never writes. It reports.
- Irreversible actions (deploy, merge to protected branch, delete, anything customer-facing) sit behind explicit human approval as graph nodes requiring an `escalates_to` traversal — the human checkpoint falls on the irreversible step, not the routine one.
- **Never write to the ticketing system** — a standing operator constraint; those records are client-facing and read-only.

## 11. Metrics

**Cost per accepted change** — spend divided by closing deltas that survived staleness re-verification. The loop paper's proposed headline metric, and the number that reveals a loop burning budget while looking busy.

Recorded per turn in `runs`. Reported per route (model × effort × work shape), which is what makes the routing table falsifiable against local data rather than published benchmarks.

Secondary: open-criteria trajectory, unproven count, gate block/allow ratio, subagent no-op rate.

## 12. Non-goals

- Not a benchmark harness. Published scores inform the initial routing table and nothing else.
- Not multi-agent debate or judge ensembling — measured to reduce review quality.
- Not the Proactivity Scorer or notification layer from 2607.07721.
- Not a replacement for prompting. Loop and prompt are distinct tools.
- No loop where feedback does not change the next action. That is a scheduled one-shot; the authoring path must refuse to wrap it in machinery.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Criteria authored badly → gate enforces the wrong thing | human approval gate + cross-vendor gaming pass |
| Evidence commands slow → per-turn cost | dirty-subset re-runs; full sweep only at termination check |
| Gate becomes the runaway | max consecutive blocks, independent of budget |
| Investigation criteria hard to make deterministic | criterion = a query returning rows, or a named artifact existing; if neither can be written, the goal is not loopable and the authoring path says so |
| Graph drifts from reality | staleness window forces re-verification; `unproven` is loud |
| SQLite contention under fan-out | WAL mode; gate reads are short; writers serialise through the Evidence Runner |

## 14. Implementation phasing

Too large for one plan. Each phase is independently useful and independently verifiable — and each is checked with its own criteria, so the system bootstraps on itself from P1 onward.

| Phase | Delivers | Done when |
|---|---|---|
| **P0** | SQLite schema, graph + delta log, `loopgraph` CLI (`add`, `status`, `check`, `next`) | criteria can be authored, evidence run, deltas recorded — by hand, no hooks |
| **P1** | Evidence Runner + threshold rules R-01..R-06 | terminal states computed correctly against a fixture graph, each rule watched to fire |
| **P2** | `Stop` hook gate + gate safety (§6.3) | a live session cannot end with open criteria; the hook is tested against a real event payload — a gate whose only test is on `coord.loop_enabled` is not tested at all |
| **P3** | `SubagentStop` ownership check + worktree isolation | a deliberately no-op subagent is caught and discarded |
| **P4** | Authoring path: criteria drafting, triage off-ramp, Codex gaming pass | non-loopable goal is refused; gameable criterion is caught before approval |
| **P5** | Metrics: cost per accepted change, per-route reporting | routing table becomes falsifiable against local data |

**Every rule and guard must be watched to fail before it is trusted.** A brake that has never been observed firing is `unproven`, and the system treats its own guards exactly as it treats criteria.

## 15. Open questions

1. Staleness window defaults per work shape — repo tests vs Argo sync vs ClickHouse freshness differ by orders of magnitude.
2. Whether the Evidence Runner should be invokable by workers mid-turn (fast feedback) or only at gate time (stricter separation). Leaning mid-turn read-only, gate-time authoritative.
3. Budget ceiling units — turns, tokens, or wall-clock. Likely tokens, to align with cost per accepted change.
