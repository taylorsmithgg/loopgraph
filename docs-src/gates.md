---
title: Gates
description: The scope gate on agent dispatch and the loop gate on turn end -- what makes them reachable, and what stops them trapping a session.
---

# Gates


**On by default.** This is safe because both gates are inert until you give
them something: the scope gate is silent unless a dispatch prompt carries a
`SCOPE:` line, and the loop gate is silent unless criteria are declared. A repo
that never opted in is unaffected.

`loopgraph off` disables both, `--only scope|loop` disables one, and
`LOOPGRAPH_COORD=0` / `LOOPGRAPH_LOOP=0` force them off without touching state. State lives in `~/.loopgraph/<sha of repo root>.db`
— nothing is ever written into your project.

A `PreToolUse` hook blocks an `Agent` dispatch whose scope is already claimed.
Declare scope in the dispatch prompt:

```
SCOPE: charts/values.yaml sql/57 rules/registry.yaml
```

Undeclared scope is not blocked, non-Agent tools pass through, and a broken
gate allows work rather than blocking it.

## Loop gate

Enabled with `loopgraph on --only loop`. A `Stop` hook refuses to end
the turn while declared criteria are unmet.

- Silent when off, or when no criteria are declared.
- Cheap subset each turn (only non-closed criteria), but a **full sweep before
  declaring success**, so a criterion that closed earlier and broke later is
  still caught.
- Cannot trap a session, and **two separate limits do that job — the smaller
  one governs**:
  - **`STAGNATION_TURNS` (8)** — R-01 fires when nothing has closed in that
    many turns, ending the drive as `stalled`. With an unsatisfiable
    criterion this is what stops it, at 7 blocks. Raising
    `LOOPGRAPH_MAX_BLOCKS` alone changes nothing.
  - **`LOOPGRAPH_MAX_BLOCKS` (default 7)** — the backstop for the other case:
    a session that keeps closing criteria resets stagnation and could
    otherwise drive forever.
  Claude Code enforces its own ceiling at `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`
  (default 8); keep loopgraph's under it, or the harness cuts the drive off
  mid-flight with its own message instead.
- `exhausted` / `stalled` / `blocked` allow the stop and say so — an error is
  never reported as success.
- `LOOPGRAPH_LOOP=0` forces it off.

## What makes it self-starting

The gate is inert with no criteria, so "declare nothing" was always the
cheapest way past it — and for the tool's whole life, that is what happened
everywhere. Three deterministic pieces close that, none of them a model call:

- **A stated goal is recorded.** `UserPromptSubmit` files the request as
  pending and injects the contract.
- **A pending goal must be answered.** The `Stop` hook refuses a turn that
  declared nothing, twice at most, then allows the stop while saying plainly
  that the work is **UNVERIFIED, not verified**. The way out is either
  `loopgraph add ...` or `loopgraph noop --reason "..."` — a waiver is a
  legitimate answer for a question or a lookup, it just has to be an answer.
- **The repo's own suite is fenced automatically.** `loopgraph baseline`
  detects pytest / npm / cargo / go / make, runs it, and installs it as a
  `--guard` if it is observed green. Guards bind every session; a fence that
  was already down is never installed, so pre-existing breakage cannot hold
  a turn open. Runs detached, so no prompt ever waits on it.

Criteria carry their origin: `status` prints `[guard]` and `[auto]`, and
`loopgraph drop <id>` withdraws anything that misreads the request.

## Weakness, not brevity

`add` is an entailment gate. It runs the check immediately and **refuses one
that already passes** — a check that is green before the work cannot tell done
from not-done, and a graph full of those is what "fully specified, nothing
happening" looks like. `--guard` is the deliberate exception (a fence is
supposed to be green; its job is to stay that way) and `--allow-green` is the
manual override.

Among checks that do discriminate, the widest wins. Following Bennett,
[*The Optimal Choice of Hypothesis Is the Weakest, Not the Shortest*](https://arxiv.org/abs/2301.12987)
(arXiv:2301.12987): among hypotheses that entail the observations, maximising
extension generalises 1.1–5× better than minimising description length. A
criterion is a hypothesis about done-ness and its extension is the set of
world-states where its check passes, so:

| check | extension | what it does to the loop |
|---|---|---|
| `test -f done.txt` | everything | proves nothing; refused unless red |
| `grep -q "persistent = true" config.yml` | one implementation | drives toward one guessed fix, fails on a better one |
| `for i in $(seq 50); do ./restart; done; [ $(in) -eq $(out) ]` | every working implementation | holds the outcome, leaves the route open |

`loopgraph.weakness` scores this structurally and `add` warns when a check is
narrower than the goal it is standing in for. The order matters and is the
whole idea: **entailment first, weakness second.** Ranking by weakness alone
selects `true` every time. Brevity survives only as a tie-break.

Auto-derivation of criteria by a model was built and dropped: it put a
multi-second model call on every first prompt and executed generated shell
nobody had read. `weakness.is_safe()` remains as the deny-list for any future
derivation, and refuses `rm -rf`, `git push`, `sudo`, `curl | sh` and friends.

## One database, many sessions

`~/.loopgraph/<hash>.db` is keyed by git root, falling back to cwd — so every
session working **outside** a repo shares one graph with every other. Observed
live: three sessions in `$HOME`, one of them declaring a goal mid-work while
the others were held open on criteria they had never heard of.

A goal belongs to whoever stated it. `add` stamps the authoring session
(`CLAUDE_CODE_BRIDGE_SESSION_ID`, override `LOOPGRAPH_SESSION`), and:

| criterion | binds |
|---|---|
| goal, stamped with your session | you |
| goal, stamped with another session | them — not you |
| goal, no owner recorded (pre-upgrade, or a session that died) | nobody |
| `--guard` | everyone — a broken suite is not a private matter |
| `--global` | everyone, deliberately and out loud |
| anything, when no session identity is available at all | everyone — a gate that quietly stops gating is the one failure never worth risking |

**Not enforced is never unmentioned.** Anything open that this session is not
held to is listed by `status` and named by the Stop hook on its way past, with
`loopgraph adopt <id>` (take it on) and `loopgraph drop <id>` (remove it)
alongside it. `loopgraph adopt --all` takes on every loose one. Silence here
would just be the original bug wearing a new hat.

`status` shows the whole board; `check` answers only for this session's
specification, or a neighbour's open goal would make it report failure
forever. `status` also prints the session identity and shouts `MISMATCH` if
the Stop hook ever sees a different one from the CLI — that disagreement would
otherwise disarm the gate invisibly.

**`stop_hook_active` is not a permission bit.** It is `true` only while the
harness is already continuing *because* a stop hook blocked, and `false` on
every ordinary first stop — the exact moment a block works. The gate shipped
reading `false` as "cannot block" and was therefore a no-op on every normal
turn: zero blocks across 15 project databases. It is now used only to reset the
consecutive-block count when a fresh stop chain starts.

## Hooks installed

| event | script | effect |
|---|---|---|
| UserPromptSubmit | `spec_prompt.py` | ask for an end-state while none is declared |
| UserPromptSubmit | `recall_prompt.py` | surface relevant memories, or stay silent |
| PreToolUse (Agent) | `coord_gate.py` | refuse dispatch whose `SCOPE:` is claimed |
| PostToolUse (Agent) | `coord_release.py` | release a foreground agent's claims on return |
| SessionStart | `session_brief.py` | inject recorded traps once per session |
| Stop | `loop_gate.py` | refuse to finish while the specification is unmet |

The loop gate is inert until criteria exist, so `spec_prompt.py` is what makes
it reachable at all: it asks once, on goal-shaped prompts, and self-silences
the moment one criterion is declared. `LOOPGRAPH_SPEC_PROMPT=0` turns it off.

Every one is off by default and fails open.

