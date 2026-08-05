---
title: Overview
description: Deterministic goal-state substrate for agent loops. Criteria live in a context graph; done is computed from evidence, never claimed by an agent.
---

<div class="hero">
<div class="eyebrow">Goal state, computed</div>
<h1>loopgraph</h1>

<p class="lede">An agent will tell you it is finished. <strong>Ask the
repository instead.</strong> Criteria live in a context graph, and a turn
cannot end while one of them is red.</p>

<div class="ledger">
  <div class="bar"><span class="dot"></span>loopgraph check</div>
  <div class="row"><span class="id">C1</span>
    <span class="verdict pass">closed</span>
    <span class="stmt">the queue drains under restart</span></div>
  <div class="row"><span class="id">C2</span>
    <span class="verdict fail">failing</span>
    <span class="stmt">no duplicate rows after replay</span></div>
  <div class="row"><span class="id">G-pytest</span>
    <span class="verdict pass">closed</span>
    <span class="stmt">the repo's own suite still passes <em>[guard]</em></span></div>
  <div class="row"><span class="id">C3</span>
    <span class="verdict wait">unproven</span>
    <span class="stmt">restart is idempotent under load</span></div>
  <div class="exit">terminal_state <b>null</b> &mdash; keep working &middot; exit <b>1</b></div>
</div>
</div>

That table is the whole idea. Nothing in it is an opinion.

An agent that grades its own work is not being dishonest when it reports
success. It simply has no fact to check against, so loopgraph supplies one. A
criterion is a statement, a command and an expectation: the command runs, the
expectation holds or it does not, and the loop's exit condition follows from
that rather than from a sentence of prose.

Everything here is deterministic — SQLite, subprocesses, exit codes. No model
sits in the path of any decision the gates make. A second model appears in
exactly one place, [auditing a check for spec-gaming](audit.md), where it runs
at authoring time, gates nothing, and hands its verdict to a human.

<figure class="loopfig">
<svg viewBox="0 0 760 136" role="img"
     aria-label="A turn runs, the evidence runner re-runs every check, and the stop gate either blocks the turn or lets it end.">
  <g class="stroke" fill="none" stroke-width="1.25">
    <rect class="fill-panel" x="1" y="34" width="132" height="46" rx="2"/>
    <rect class="fill-panel" x="205" y="34" width="150" height="46" rx="2"/>
    <rect class="fill-panel" x="427" y="34" width="128" height="46" rx="2"/>
    <rect class="fill-paper" x="627" y="34" width="132" height="46" rx="2"/>
    <path d="M133 57h60M355 57h60M555 57h60"/>
  </g>
  <g class="fail" fill="none" stroke-width="1.25">
    <path d="M491 80v26H67V86" stroke-dasharray="4 4"/>
  </g>
  <g class="stroke" fill="none" stroke-width="1.25">
    <path d="M187 52l8 5-8 5M409 52l8 5-8 5M609 52l8 5-8 5"/>
  </g>
  <g class="fail" fill="none" stroke-width="1.4">
    <path d="M61 92l6-7 6 7"/>
  </g>
  <text class="label" x="20" y="54">the turn</text>
  <text class="sub" x="20" y="70">an agent works</text>
  <text class="label" x="224" y="54">evidence runner</text>
  <text class="sub" x="224" y="70">every check re-run</text>
  <text class="label" x="446" y="54">stop gate</text>
  <text class="sub" x="446" y="70">reads the graph</text>
  <text class="label" x="646" y="54">turn ends</text>
  <text class="sub" x="646" y="70">spec met, or stalled</text>
  <text class="fail-t" x="86" y="124">a red criterion sends the turn back, and names which one</text>
  <text class="pass-t" x="583" y="28">all green</text>
</svg>
<figcaption>The gate sits at turn end, not at dispatch. It re-runs the checks
itself, so what closes a criterion is the command's exit status rather than the
agent's account of it.</figcaption>
</figure>

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
