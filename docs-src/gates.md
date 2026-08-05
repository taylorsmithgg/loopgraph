---
title: Gates
description: The scope gate on agent dispatch and the loop gate on turn end -- what makes them reachable, and what stops them trapping a session.
---

# Gates

Two gates. One stops two agents writing the same files. The other stops a turn
ending while the work is unfinished.

Both are on once the hooks are installed, and both are **inert until you give
them something**. The scope gate says nothing unless a dispatch prompt carries
a `SCOPE:` line. The loop gate says nothing unless criteria are declared. A
repo that never opted in behaves exactly as it did before.

Every gate also fails open. If one breaks, work continues — a gate that blocks
on its own bug is worse than no gate at all.

| | |
|---|---|
| Turn both off | `loopgraph off` |
| Turn one off | `loopgraph off --only scope` (or `loop`) |
| Force off without touching state | `LOOPGRAPH_COORD=0`, `LOOPGRAPH_LOOP=0` |
| Where state lives | `~/.loopgraph/<sha of repo root>.db` |

Nothing is ever written into your project.

## The scope gate

A `PreToolUse` hook refuses to dispatch an agent whose scope another agent
already holds. You declare the scope in the dispatch prompt:

```
SCOPE: charts/values.yaml sql/57 rules/registry.yaml
```

That is the whole interface. Undeclared scope is not blocked, non-Agent tools
pass straight through, and a claim conflict exits 3 rather than merging two
agents into the same files.

## The loop gate

A `Stop` hook refuses to end the turn while declared criteria are unmet.

It checks the cheap subset every turn — only criteria that are not yet closed.
Before it will declare success, it runs a **full sweep**, so a criterion that
closed early and broke later still gets caught.

### It cannot trap a session

Two separate limits guarantee that, and the smaller one governs.

**`STAGNATION_TURNS` (8)** ends the drive as `stalled` when nothing has closed
in that many turns. Give the gate an unsatisfiable criterion and this is what
stops it, at 7 blocks. Raising `LOOPGRAPH_MAX_BLOCKS` on its own changes
nothing.

**`LOOPGRAPH_MAX_BLOCKS` (default 7)** covers the other case. A session that
keeps closing criteria keeps resetting stagnation, and could otherwise drive
forever.

Claude Code enforces its own ceiling at `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`
(default 8). Keep loopgraph's limit under it. Otherwise the harness cuts the
drive off mid-flight with its own message instead of yours.

A run that ends `exhausted`, `stalled` or `blocked` allows the stop and says
which. An error is never reported as success.

## What makes it self-starting

The loop gate is inert with no criteria. So "declare nothing" was always the
cheapest way past it, and for the tool's whole life that is exactly what
happened, everywhere.

Three deterministic pieces close that hole. None of them is a model call.

**A stated goal is recorded.** `UserPromptSubmit` files the request as pending
and injects the contract.

**A pending goal has to be answered.** The `Stop` hook refuses a turn that
declared nothing — twice at most, then it allows the stop while saying plainly
that the work is UNVERIFIED rather than verified. Two answers count:
`loopgraph add ...` declares a criterion, and `loopgraph noop --reason "..."`
waives one. A waiver is perfectly legitimate for a question or a lookup. It
just has to be an answer.

**The repo's own suite gets fenced automatically.** `loopgraph baseline`
detects pytest, npm, cargo, go or make, runs it, and installs it as a
`--guard` only if it comes back green. Guards bind every session. A fence that
was already down is never installed, so pre-existing breakage cannot hold your
turn open. It runs detached, so no prompt ever waits on it.

Criteria carry their origin. `status` prints `[guard]` and `[auto]`, and
`loopgraph drop <id>` withdraws anything that misread the request.

## Weakness, not brevity

`add` runs the check immediately and **refuses one that already passes**.

The reason is simple: a check that is green before the work cannot tell done
from not-done. A graph full of those is what "fully specified, nothing
happening" looks like. `--guard` is the deliberate exception, because a fence
is supposed to be green and its job is to stay that way. `--allow-green` is the
manual override.

Among checks that do discriminate, the widest one wins.

That follows Bennett,
[*The Optimal Choice of Hypothesis Is the Weakest, Not the Shortest*](https://arxiv.org/abs/2301.12987)
(arXiv:2301.12987): among hypotheses that entail the observations, maximising
extension generalises 1.1–5× better than minimising description length. A
criterion is a hypothesis about done-ness, and its extension is the set of
world-states where its check passes.

In practice that means:

| check | extension | what it does to the loop |
|---|---|---|
| `test -f done.txt` | everything | proves nothing; refused unless red |
| `grep -q "persistent = true" config.yml` | one implementation | drives toward one guessed fix, fails on a better one |
| `for i in $(seq 50); do ./restart; done; [ $(in) -eq $(out) ]` | every working implementation | holds the outcome, leaves the route open |

`loopgraph.weakness` scores this structurally, and `add` warns when a check is
narrower than the goal it stands in for.

The order is the whole idea: **entailment first, weakness second.** Rank by
weakness alone and you select `true` every time. Brevity survives only as a
tie-break.

One thing that was built and then dropped: letting a model derive criteria. It
put a multi-second model call on every first prompt and executed generated
shell nobody had read. `weakness.is_safe()` survives as the deny-list for any
future attempt, refusing `rm -rf`, `git push`, `sudo`, `curl | sh` and friends.

## One database, many sessions

The database is keyed by git root, falling back to cwd. Every session working
**outside** a repo therefore shares one graph with every other session outside
a repo.

That is not hypothetical. Observed live: three sessions in `$HOME`, one of them
declaring a goal mid-work while the other two sat held open on criteria they
had never heard of.

So a goal belongs to whoever stated it. `add` stamps the authoring session
(`CLAUDE_CODE_BRIDGE_SESSION_ID`, override `LOOPGRAPH_SESSION`):

| criterion | binds |
|---|---|
| goal, stamped with your session | you |
| goal, stamped with another session | them — not you |
| goal, no owner recorded (pre-upgrade, or a session that died) | nobody |
| `--guard` | everyone; a broken suite is not a private matter |
| `--global` | everyone, deliberately and out loud |
| anything, when no session identity is available at all | everyone; a gate that quietly stops gating is the one failure never worth risking |

**Not enforced is never unmentioned.** Anything open that this session is not
held to still gets listed by `status`, and the Stop hook names it on the way
past, alongside `loopgraph adopt <id>` to take it on and `loopgraph drop <id>`
to remove it. `loopgraph adopt --all` takes every loose one. Staying quiet here
would just be the original bug wearing a new hat.

`status` shows the whole board. `check` answers only for this session's
specification — otherwise a neighbour's open goal would make it report failure
forever. `status` also prints the session identity and shouts `MISMATCH` if the
Stop hook ever sees a different one from the CLI, because that disagreement
would disarm the gate invisibly.

### `stop_hook_active` is not a permission bit

It is `true` only while the harness is already continuing *because* a stop hook
blocked. On every ordinary first stop it is `false` — which is the exact moment
a block works.

The gate shipped reading `false` as "cannot block", and was therefore a no-op
on every normal turn: zero blocks across 15 project databases. It is now used
for one thing only, resetting the consecutive-block count when a fresh stop
chain starts.

## The hooks

| event | script | effect |
|---|---|---|
| UserPromptSubmit | `spec_prompt.py` | ask for an end-state while none is declared |
| UserPromptSubmit | `recall_prompt.py` | surface relevant memories, or stay silent |
| PreToolUse (Agent) | `coord_gate.py` | refuse dispatch whose `SCOPE:` is claimed |
| PostToolUse (Agent) | `coord_release.py` | release a foreground agent's claims on return |
| SessionStart | `session_brief.py` | inject recorded traps once per session |
| Stop | `loop_gate.py` | refuse to finish while the specification is unmet |

Installing them is opt-in: they do nothing until you wire them into your
harness settings.

`spec_prompt.py` is what makes the loop gate reachable at all. The gate is
inert until criteria exist, so this asks once, on goal-shaped prompts, and
silences itself the moment one criterion is declared. `LOOPGRAPH_SPEC_PROMPT=0`
turns it off.
