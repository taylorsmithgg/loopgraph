---
title: CLI and exit codes
description: Three separate exit-code contracts, the command surface, and the three ways to invoke the same CLI.
---

# CLI and exit codes

## Exit codes

There is no single exit-code convention across these subcommands. There are
three, and confusing them in a script or a hook is a real hazard.

| Subcommand | Exit 0 | Exit 1 | Exit 2 |
|---|---|---|---|
| `check` | specification met (`terminal_state == "success"`) | specification not (yet) met | — |
| `run` | specification met after this run | not met yet, but every targeted criterion was at least evaluated | at least one criterion **could not be evaluated at all** |
| `add`, `link`, `tick`, `spend` | the write went through | — | the write was rejected |
| `next` | there is **nothing** workable right now | there is at least one workable criterion | — |

Read that table twice, because `0` means three different things in it:

- for `check` and `run`, `0` means "spec met"
- for `next`, `0` means the *opposite* of "there's work"
- for the write commands, `0` means only "the write went through" — ordinary
  Unix success, safe to chain with `&&`

None of these generalises to the others.

Two cells deserve their own note. `run` exit 2 covers a bad evidence command or
a corrupt stored `expect`, and it **outranks** the success check: a batch
containing one unevaluable criterion never reports 0, even if every other
criterion closed. The write commands' exit 2 covers bad `--expect` JSON or
value, an unknown `rel_type`, a duplicate id, an unknown foreign key.

## "Keep working" vs. "stop and escalate"

An exit code cannot tell you *why* `check` or `run` came back non-zero. Two
very different situations share the same code:

- **`None`** — the specification isn't met yet, but nothing is wrong. Keep
  working.
- **A terminal state** — `stalled`, `exhausted`, `blocked` or `no-op`. The work
  has hit something it cannot get out of on its own, and needs a human or a
  different strategy rather than another turn. (`no-op` means there was never
  any work to do.)

So: **anything wiring `check` into a `Stop` hook must read `terminal_state`
from `check --json` or `status --json`, not the exit code.**

Treat non-zero as "keep going" and you spin forever on a genuinely `stalled`
graph. Treat it as "stop" and you bail out of ordinary in-progress work. Only
`terminal_state` tells them apart:

```console
$ loopgraph check --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["terminal_state"])'
stalled
```

Its values are `null` (keep working), `"success"`, `"stalled"`, `"exhausted"`,
`"blocked"` and `"no-op"`.

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

Memory and the security notes it collects have their own commands:

```
mem retain "<the fact>" --kind world|experience|model   # save something
mem recall "<a few words>"                # search; exit 1 when nothing matches
mem forget <name>                         # delete the file and the search entry
mem reindex                               # rebuild the search index from the files
loopgraph security                        # what is waiting to be reviewed
loopgraph security --clear                # mark everything listed as reviewed
loopgraph security --prune                # drop notes about forgotten memories
```

`mem recall` is the one exception to reading exit codes as success or failure:
it exits 1 when it finds nothing, so a script can tell "nothing is known"
from "here is what is known" without reading the text. `mem forget` exits 2
only when the name exists in neither the files nor the search index. Finding
it in just one is normal, and the command says so and exits 0.

## Invoking it

Three ways, all the same CLI:

```
loopgraph status              # PATH shim -> dist/loopgraph-shim.sh in ~/.local/bin
/loopgraph status             # slash command -> dist/slash-command.md in ~/.claude/commands
uv run --project <repo> loopgraph status
```

`/loopgraph` with no arguments shows status.

The database is resolved from the **current** git repo, not from wherever
loopgraph is installed — so the same command reports different state per
project. Installable copies of the shim and the slash command both live in
`dist/`.
