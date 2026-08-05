---
title: Audit and routing
description: A second vendor auditing criteria for spec-gaming, Codex as implementer, and measuring which model earns its tokens.
---

# Audit and routing

## The spec-gaming audit

`loopgraph game [<criterion>]` asks Codex, read-only, whether a criterion's
evidence command can be satisfied without achieving its intent.

Here it is on this repo's own test criterion:

```
$ loopgraph game C1
C1  GAMEABLE  "grep -q done f.txt" matches a substring, so "undone" passes
  cheat:  printf 'undone\n' > f.txt
  harder: grep -qx 'done' f.txt
```

Exit 0 means sound, 1 gameable, 2 error.

It is on by default and never blocks. `loopgraph add` returns in about 0.2s and
detaches the audit; the verdict lands roughly 20s later and shows up in
`status` as `GAMEABLE checks:`. Use `--audit` to wait for it inline, `--no-audit`
to skip it, and `LOOPGRAPH_AUDIT=0` to disable it entirely.

This is the only cross-model role the evidence supports. Ensembling models for
detection measured *worse* (F1 0.365 → 0.333), and judges over-reject
conformant work by 35–45%. So no model gates anything here. The audit runs at
authoring time and a human decides.

### Why it takes ~20 seconds

Codex startup is 2.6–4.2s, which turned out not to be the cost. Two of our own
choices were:

| change | effect |
|---|---|
| `--ignore-user-config` dropped the configured model, falling back to codex's default (`gpt-5.6-sol`) instead of `gpt-5.3-codex-spark` | 2× slower |
| reasoning effort pinned to `medium` | 1.5× slower than `low`, same verdict |

Measured on one audit: low 21.8s, medium 32.8s, high 40.0s, all reaching the
same verdict. `minimal` fails outright. The configured model is now forwarded
explicitly and effort is pinned to `low`.

What remains is the model actually reasoning about the question, which is why
the audit is detached rather than optimised further.

### Sandbox policy

`--ignore-user-config` buys startup speed and tool isolation (300s → 23s), but
it also drops `approval_policy` — and without that forwarded, codex reverts to
prompting and blocks forever on a pipe. Both `approval_policy` and
`sandbox_mode` are therefore read from `~/.codex/config.toml` and passed
through. `--approval` and `--sandbox` override them.

**The elevated sandbox is the point, not a compromise.** With write access the
auditor executes the candidate cheat and confirms it passes, so the verdict is
`GAMEABLE(demonstrated)` with observed output rather than `GAMEABLE(asserted)`.
That is the difference between evidence and opinion, which is the distinction
the rest of this system is built on.

The cost is real: the auditor can write to the tree it audits. It is told to
work in a scratch directory and clean up, and it has been observed doing so,
but that is behaviour rather than a guarantee. Run it on a clean tree, or pass
`--sandbox read-only` when that matters more than proof.

### Sabotage is not a criterion problem

Audited against four real criteria, the verdict came back gameable on **all
four** — including `uv run pytest -q`, cheated by dropping a `conftest.py` that
skips every test, and three others via `PATH` shims.

A verdict that fires on every input carries no information, and gets silenced
rather than heeded. So verdicts now carry a `cheat_class`:

| class | what it means | worth acting on |
|---|---|---|
| `shortcut` | satisfies the letter of the check by doing less work, no tampering | **yes** — rewrite the check |
| `sabotage` | subverts the environment (`PATH` shim, `conftest.py`, alias) | no — this defeats *every* possible check |

`status` lists them separately for that reason, and reports `unaudited checks:`
alongside `GAMEABLE checks:` — an un-audited check is one you do not know is
gameable, surfaced the same way `unproven` is.

### What weakness does not tell you

The gaming experiment was meant to test whether a low weakness score predicts a
gameable check. With the verdict saturated at 100%, there was no signal to
correlate against.

So weakness stands on Bennett's argument and on what it does to the loop — it
drives toward outcomes rather than one guessed implementation. It is **not**
validated as a predictor of spec-gaming, and nothing here should be read as
claiming otherwise.

## Codex as implementer

```
loopgraph exec impl-1 --plan plan.md --scope util.py --sandbox workspace-write
model=codex closed=1 tokens=26952 sandbox=workspace-write terminal_state=success
```

`exec` claims the scope atomically, refusing on conflict, hands the plan and
the acceptance criteria to `codex exec`, then re-runs the criteria itself. The
implementer never grades its own work. Its prompt forbids editing or weakening
the criteria, so the only way to satisfy a check is to do the work.

### What the evidence actually supports

Primary sources only, and they do not say what the popular version says.

**Plan-then-implement is well supported.**
[Self-planning code generation](https://arxiv.org/pdf/2303.06689) reports up to
25.4% relative Pass@1 over direct generation, and 11.9% over chain-of-thought.
But that is *self*-planning: the same model plans, then implements. It argues
for planning first. It does not argue for splitting roles across models.

**Cross-model planner/executor splitting has thin evidence.** The one arXiv
study that ablates the pairing —
[Does The Way You Plan Matter?](https://arxiv.org/html/2605.29927v1) — finds
mixed pairs beat homogeneous ones. But it studies web agents, not code
(WebArena, 158 hard tasks, N=5); it uses GPT-4.1-mini, Qwen-2.5-VL and Gemini
2.5 Flash, none of them Codex or Opus; and its best configuration puts GPT in
the *planner* seat, not the executor seat. Its own stated limits are three
backends, one benchmark, five runs.

**Nothing establishes Codex-as-implementer over Opus-as-implementer.** Claims
to that effect circulate through blog and podcast coverage of a private
benchmark. They are not primary sources and are not treated as evidence here.
Our own measurement points the other way: Opus 5 leads SWE-bench Pro by 14.6
points on long-horizon repo work.

So the case for Codex implementing is cost, latency and parallelism — not
demonstrated capability. The honest way to settle it is `loopgraph route` on
your own tasks.

One finding does transfer and is worth testing: plan representation is
model-specific and moves results substantially (GPT best with narrative, Qwen
with checklists, Gemini with pseudocode). `loopgraph exec --plan-format
narrative|checklist|pseudocode` records which you used, and `route` groups by
it, so the question becomes measurable rather than arguable.

## Which model for which task

Published benchmarks will not answer this for your workload. Opus 5 and GPT-5.6
Sol sit within a point of each other on SWE-bench Verified and tie on
Terminal-Bench.

What answers it is measurement on your own tasks — and criteria make that free.
The same task, scored by the same deterministic checks, with no judge and no
human grading.

```
loopgraph claim a1 --scope ... --model opus  --kind implement
loopgraph release a1 --spend 900
loopgraph route
```

```
kind          model          agents closes   rate     spend  cost/accepted
implement     codex               1      1    1.0       400            400
implement     opus                1      3    3.0       900            300
plan          codex               1      0    0.0      5000              -
```

`--model` is a **label**, not a router. It records who you say did the work so
the table can group by it. It dispatches nowhere and selects nothing.

`cost/accepted` is tokens per criterion that actually closed — the metric the
loop-engineering paper proposes and notes is almost never measured. A `-` means
the agent spent and landed nothing, which is the number worth seeing.

### What attribution is and is not

A close is counted when a criterion inside that agent's scope transitions to
closed during its run, derived from the delta log.

That is correlation, not proof of causation. A criterion can close for an
unrelated reason. It is measured rather than self-reported, which is the whole
point, but do not read a single row as a verdict. Accumulate rows before you
route anything on them.
