---
title: Overview
description: Deterministic goal-state substrate for agent loops. Criteria live in a context graph; done is computed from evidence, never claimed by an agent.
---

# loopgraph

<p class="lede">Criteria live in a context graph. "Done" is computed from
evidence, never claimed by an agent. Harness hooks read the graph and refuse to
let a turn end while the specification is unmet.</p>

An agent that grades its own work will tell you it is finished. That is not
dishonesty. It is the absence of a fact to check against, so loopgraph supplies
one.

A criterion is a statement, a command and an expectation. The command runs, the
expectation holds or it does not, and the loop's exit condition follows from
that rather than from a sentence of prose.

Everything here is deterministic: SQLite, subprocesses, exit codes. No model
sits in the path of any decision the gates make. A second model does appear in
one place — [auditing a check for spec-gaming](audit.md) — and it runs at
authoring time, gates nothing, and hands its verdict to a human.

<div class="cards">
  <a class="card" href="cli.html"><b>CLI &amp; exit codes</b><span>Three different exit-code contracts. Getting them confused in a hook is a real hazard.</span></a>
  <a class="card" href="gates.html"><b>Gates</b><span>The scope gate on dispatch, the loop gate on turn end, and the limits that stop either trapping a session.</span></a>
  <a class="card" href="memory.html"><b>Memory</b><span>Retain, recall, supersede on the same graph — with a default-deny recall scope.</span></a>
  <a class="card" href="audit.html"><b>Audit &amp; routing</b><span>A second vendor asking whether a check can be satisfied without doing the work.</span></a>
  <a class="card" href="design.html"><b>Design</b><span>The specification this was built from, including what was rejected.</span></a>
  <a class="card" href="evidence/index.html"><b>Evidence</b><span>The measurements behind the design decisions, including the ones that came out against the design.</span></a>
</div>

## Install

Python 3.12+, no runtime dependencies. [`uv`](https://docs.astral.sh/uv/) is
used for the venv and the console script.

```sh
git clone https://github.com/taylorsmithgg/loopgraph ~/src/loopgraph
cd ~/src/loopgraph
uv sync
uv run pytest -q                      # 402 tests, ~14s

# 'loopgraph' from any directory
export LOOPGRAPH_HOME=~/src/loopgraph          # add to your shell profile
install -m 755 dist/loopgraph-shim.sh ~/.local/bin/loopgraph

# optional: /loopgraph slash command in Claude Code
cp dist/slash-command.md ~/.claude/commands/loopgraph.md
```

Nothing is written into your project: state lives in `~/.loopgraph/<sha of repo
root>.db`. Both gates are inert until you give them something, so installing
loopgraph changes no behaviour until a `SCOPE:` line or a criterion appears.

## A first specification

```console
$ loopgraph add C1 "the queue drains under restart" \
    --cmd 'for i in $(seq 50); do ./restart; done; [ $(in) -eq $(out) ]'
C1 added   (unproven)

$ loopgraph check; echo "exit=$?"
C1  unproven  the queue drains under restart
exit=1

$ loopgraph run
C1  closed
exit=0
```

`add` refuses a check that already passes, because one that is green before the
work cannot tell done from not-done. Among checks that do discriminate, the
widest one wins — see [weakness, not brevity](gates.html#weakness-not-brevity).

## What it is not

- **Not a planner.** It holds the definition of done. How to get there is the
  agent's job.
- **Not a judge.** No model scores anything. Measured judges over-reject
  conformant work by 35–45%, which is why nothing here gates on one.
- **Not a hosted service.** One SQLite file per repository, on your machine.

## Hooks

Six hooks read the graph: two on `UserPromptSubmit`, one each on `PreToolUse`,
`PostToolUse`, `SessionStart` and `Stop`. Wiring them into your harness is
opt-in, and every one of them fails open — a gate that blocks on its own bug is
worse than no gate.

[Gates](gates.md) covers what each hook does, and why the loop gate needs
`spec_prompt.py` to be reachable at all.
