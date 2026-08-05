---
title: Subagent failure taxonomy
description: One long session classified into the failure modes the gates are aimed at.
---

# Subagent failure taxonomy — one long infrastructure session, 2026-08-03

Evidence base: `2026-08-03-151432-this-session-is-being-continued-from-a-previous-c.txt`, 10,920 lines, 567 KB. Line references are to that file.

**Population:** 39 dispatch sites, 35 distinct agents with a completion record, **26.5 agent-days** of summed wall clock.

| lifetime | agents |
|---|---|
| > 24 h | 5 |
| 1–4 h | 20 |
| < 1 h | 10 |

The five long-runners: `Inventory a legacy pipeline for decom` 9d 14h (L3724), `Fix and extend event normalization` 3d 18h (L4519), `Cloud sign-in coverage` 3d 17h (L4119), `Chat and mail detections` 3d 17h (L3858), `Surface normalized events in the console` 3d 17h (L3907). The four clustered at ~3d 17h returned together, which suggests they were *outstanding* across a suspension rather than computing continuously. The effect is identical: their premises are days stale on return.

---

## A. Expired conclusions from long-outstanding agents

**Confirmed, 4 instances, all among the 5 agents that ran > 24 h.**

- **L3724–3736** — `Inventory a legacy pipeline for decom` ran 9.6 days. Its cluster evidence was timestamped 2026-07-24, ten days stale. The retirement it was planning **had already been completed on 2026-07-27**, seven days before it returned, archived at `archive/infrastructure/legacy-pipeline/2026-07-27/` using the very convention the agent recommended. Every recommendation was obsolete on arrival.
- **L3858–3865** — `Chat and mail detections` ran 3.7 days; the rules it was building had been built by others mid-flight. It declined to open an MR.
- **L3907–3910** — `Surface normalized events in the console`, same pattern, also declined.
- **L4119–4145** — `Cloud sign-in coverage` ran 3.7 days and **did** ship (!515). See B.

The session diagnosed this itself at **L3895–3900**:

> "Two long-running agents have now reported on work that no longer exists… the measurements were sound when taken, and the conclusions drawn from them expired."

**Mechanism:** not context loss inside the agent. The agent's world model is correct at dispatch and decays while main moves. Failure occurs at *return* time.

**What contained it:** agent honesty — two of four declined to ship. That is a behaviour, not a system property, and it cannot be relied on.

## B. Duplicate artifacts shipped

**Confirmed, 1 instance.**

- **L4131–4137** — !515 added `azure_impossible_travel`, `azure_mfa_fatigue`, `azure_risky_signin`, `azure_new_asn`, each a one-to-one duplicate of `entra_*` rules already shipped in `sql/52`. For the four tenants delivering `azure.signinlogs`, every sign-in would have alerted twice.
- **L4180–4181** — "the exact double-alerting **two other agents refused to ship this week**." The refusals existed. They were not reachable by the third agent.
- **L4142–4173** — the MR's premise was also already false: it concluded eight tenants must enable an Entra diagnostic setting, but the sign-in data was already arriving nested under `azure.activitylogs`. Shipping it would have sent eight clients on an unnecessary errand.

**Caught by:** the main loop reviewing the MR on return. No automated guard existed.

## C. Namespace collisions between parallel agents

**Confirmed, 2 instances.**

- **L2271–2280** — !506 `sql/57_source_host_hourly.sql` vs !510 `sql/57_win_service_control.sql`. Different filenames, so git reported no conflict, but "duplicate numbering in a repo where file execution order is load-bearing is exactly what caused the LC_COLLATE bug and a two-day bootstrap outage." Both also edited `values.yaml`.
- **L4139–4140** — !515 `60_azure_signin_rollup.sql` collides with !506 `60_telemetry_advisories.sql`.

**Mechanism:** nothing reserves an identifier. Two agents independently pick the same slot, and git cannot detect it because the filenames differ.

## D. Infrastructure kills losing in-flight work

**Confirmed, 4 distinct events.**

- **L1093–1158** — nine agents killed simultaneously by the weekly API limit. "All nine agents died on the same thing: weekly API limit, resets 2pm Pacific." Relaunched from scratch at L1270–1299; nothing recorded what they had completed before dying.
- **L1302–1308, L6913–6920** — `Subagent spawn limit reached (200 of 200)`. Session-lifetime budget, not concurrency; finished agents do not return slots. Blocked further dispatch entirely and forced a restart to clear.
- **L7015–7021** — three background tasks from the previous session with **no completion record**: "may have been stopped… these leave no transcript marker." State unknown, marked stopped.
- **L10838–10839** — !540 held because "its agent died mid-reconciliation."

## E. Rediscovered facts

**Confirmed, at least 1 recurring instance.**

- **L10795–10805** — `glab mr merge` printed "✓ Merged!" while `state` remained `opened` and `has_conflicts: True`. "That's the **third time today** that trap has mattered."
- **L10833–10834** names three such facts explicitly — the `--broken` exit code, the shellcheck version split, and that `glab mr merge` lies — as things "I'd otherwise have let it rediscover."

**Mechanism:** hard-won environmental facts live in the main loop's context and do not reach dispatched agents.

---

## NOT failures — redundancy working

Recording these so the fix does not suppress them.

- **L2447–2448** — "Three agents independently hit that failure. Two correctly declined to claim it was theirs; one fixed it. **That's the system working.**" Three agents encountering one pre-existing bug is not waste.
- **L3867–3872** — the o365 agent "independently converged on the shipped design's load-bearing decisions from scratch… Independent convergence is real validation of those choices."
- **L2171** — an agent "declined to re-run rather than add contention, and said so."

Any mitigation that eliminates overlap entirely would also eliminate independent convergence, which this session valued.

---

## The mitigation the session found empirically

**L10830–10834**, after the collisions had already happened:

> "One agent, not five, handling the four conflicted MRs serially — with the mechanisms from your question actually applied this time: a namespaced scratchpad, an explicit 'do not merge,' the exact write-set named per MR, and the three facts I'd otherwise have let it rediscover."

Five mechanisms, each mapping to a category above:

| mechanism | addresses |
|---|---|
| serial, not parallel, where writes overlap | C |
| namespaced scratchpad | C |
| explicit "do not merge" (bounded authority) | B, D |
| **the exact write-set named per agent** | C |
| **pre-supplied facts, so they are not rediscovered** | E |

Missing from that list, and unaddressed by it: **A** (expired premises) and the reachability half of **B** (a sibling's refusal being invisible).

---

## Summary

| category | instances | mechanism | currently caught by |
|---|---|---|---|
| A. Expired conclusions | 4 | premises decay while agent is outstanding | agent honesty (2 of 4) |
| B. Duplicate artifacts | 1 | sibling conclusions unreachable | main-loop MR review |
| C. Namespace collisions | 2 | no identifier reservation | luck, then manual renumber |
| D. Kills losing in-flight work | 4 | no completion record before death | nothing |
| E. Rediscovered facts | 3+ | main-loop context does not reach agents | repetition |

**The dominant failure is A, and its root cause is agent lifetime.** No shared-state mechanism survives a nine-day snapshot — by the time such an agent returns, anything recorded at dispatch is itself stale. Bounding lifetime addresses A directly and shrinks the window for C and D. It requires no new system.

**The loopgraph criteria model as designed addresses none of these.** It gates session termination on goal completion; every failure here is an agent returning *too late* or *colliding*, not a session ending too early. The engine could express C and E as repo invariants — "no two SQL files share a number", "no rule name duplicates a shipped rule" — checked every turn, which would have caught both instances of C at introduction rather than on return. That is a real but partial fit, and it is not what the engine was built for.
