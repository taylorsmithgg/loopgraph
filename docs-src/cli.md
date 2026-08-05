---
title: CLI and exit codes
description: Three separate exit-code contracts, the command surface, and the three ways to invoke the same CLI.
---

# CLI and exit codes

## Exit codes

The `loopgraph` CLI does **not** use one exit-code convention across all subcommands. There are three separate contracts, and mixing them up in a script or a hook is a real hazard:

| Subcommand | Exit 0 | Exit 1 | Exit 2 |
|---|---|---|---|
| `check` | specification met (`terminal_state == "success"`) | specification not (yet) met | — |
| `run` | specification met after this run | specification not (yet) met, but every targeted criterion was at least evaluated | at least one criterion **could not be evaluated at all** (bad evidence command, corrupt stored `expect`, ...) — this outranks the success check, so a batch with one unevaluable criterion never reports 0 even if every other criterion closed |
| `add`, `link`, `tick`, `spend` | the write went through — ordinary Unix "command succeeded," safe to chain with `&&` | — | the write was rejected: bad `--expect` JSON/value, unknown `rel_type`, duplicate id, unknown foreign key, etc. |
| `next` | there is **nothing** workable right now | there is at least one workable criterion | — |

Three different subcommands, three different meanings for `0` — `check`/`run`'s 0 means "spec met," `next`'s 0 means the opposite of "there's work," and `add`/`link`/`tick`/`spend`'s 0 means only "the write went through." Do not assume any one of these generalizes to the others.

### "Keep working" vs. "stop and escalate"

The exit code alone also cannot tell a caller *why* `check`/`run` returned non-zero. A non-zero exit covers both:

- **`None`** — the specification isn't met yet, but there's nothing wrong; keep working.
- **A terminal state** — `stalled`, `exhausted`, `blocked`, or `no-op` — work has hit a condition it cannot get out of on its own (or, for `no-op`, there was never any work to do) and needs a human or a different strategy, not another turn.

Both cases produce the same non-zero exit code. **Any consumer that needs to distinguish "keep working" from a terminal condition — in particular anything wiring `check`'s exit code into a `Stop` hook — MUST read `terminal_state` from `check --json` (or `status --json`), not the exit code.** A hook author who naively treats "non-zero" as "keep going" will spin forever on a genuinely `stalled` or `exhausted` graph; one who treats it as "stop" will bail out on ordinary in-progress work. Only `terminal_state` tells them apart:

```console
$ loopgraph check --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["terminal_state"])'
stalled
```

`terminal_state` is one of: `null` (keep working), `"success"`, `"stalled"`, `"exhausted"`, `"blocked"`, `"no-op"`.

## Command surface

```
loopgraph on|off [--only scope|loop]     # both gates by default
loopgraph status                         # criteria + gate state
loopgraph claim <agent> --scope <path|id>...   # atomic, exit 3 on conflict
loopgraph validate <agent> [--changed ...]     # exit 1 if premises moved
loopgraph release <agent> | sweep | touch <agent>
loopgraph classes --agent NAME=a,b ...         # SERIAL vs parallel, pre-dispatch
loopgraph artifact check <name>                # exit 1 if it duplicates
loopgraph drop <id>                            # withdraw a criterion
loopgraph noop --reason "..."                  # no checkable end-state, on the record
loopgraph baseline                             # fence the repo's own suite as a guard
loopgraph refuse <key> --reason "..."          # make a decision reachable
loopgraph fact add <id> --text "..." | brief   # traps to paste into dispatch
loopgraph frontier <agent>                     # what a killed agent finished
```
## Invoking it

Three ways, all the same CLI:

```
loopgraph status              # PATH shim -> dist/loopgraph-shim.sh in ~/.local/bin
/loopgraph status             # slash command -> dist/slash-command.md in ~/.claude/commands
uv run --project <repo> loopgraph status
```

`/loopgraph` with no arguments shows status. The database is resolved from the
**current** git repo, not from wherever loopgraph is installed, so the same
command reports different state per project.

Installable copies of both live in `dist/`.

