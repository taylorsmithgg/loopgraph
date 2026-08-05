# loopgraph

**An agent will tell you it is finished. Ask the repository instead.**

Criteria live in a context graph. A criterion is a statement, a command and an
expectation — so "done" is computed by running the command, never claimed in
prose. A `Stop` hook reads the graph and refuses to end the turn while one of
them is red.

```
$ loopgraph check
C1        closed     the queue drains under restart
C2        failing    no duplicate rows after replay
G-pytest  closed     the repo's own suite still passes   [guard]
C3        unproven   restart is idempotent under load

terminal_state null — keep working · exit 1
```

Nothing in that table is an opinion. An agent that grades its own work is not
being dishonest when it reports success; it just has no fact to check against.
This supplies one.

**Documentation → <https://taylorsmithgg.github.io/loopgraph/>**

## Install

Python 3.12+, no runtime dependencies. [`uv`](https://docs.astral.sh/uv/) runs
the venv and the console script.

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

State lives in `~/.loopgraph/<sha of repo root>.db`. Nothing is written into
your project, and both gates stay inert until you give them something — the
scope gate is silent without a `SCOPE:` line, the loop gate silent without
criteria.

## Declaring one

```console
$ loopgraph add C1 "the queue drains under restart" \
    --cmd 'for i in $(seq 50); do ./restart; done; [ $(in) -eq $(out) ]'
C1 added   (unproven)
```

`add` runs the check immediately and **refuses one that already passes**,
because a check that is green before the work cannot tell done from not-done.
Among checks that do discriminate, the widest one wins — a criterion that holds
the outcome admits every implementation that works, not just the one currently
in mind.

## What is here

| | |
|---|---|
| [CLI and exit codes](https://taylorsmithgg.github.io/loopgraph/cli.html) | Three separate exit-code contracts. `0` means three different things, and confusing them in a hook is a real hazard. |
| [Gates](https://taylorsmithgg.github.io/loopgraph/gates.html) | The scope gate on dispatch, the loop gate on turn end, and the two limits that stop either trapping a session. |
| [Memory](https://taylorsmithgg.github.io/loopgraph/memory.html) | `retain` / `recall` / `supersede` on the same graph. Deterministic BM25 retrieval, and a default-deny recall scope. |
| [Audit and routing](https://taylorsmithgg.github.io/loopgraph/audit.html) | A second vendor asking whether a check can be satisfied without doing the work. |
| [Design](https://taylorsmithgg.github.io/loopgraph/design.html) | The specification this was built from, including what was rejected. |
| [Evidence](https://taylorsmithgg.github.io/loopgraph/evidence/index.html) | The measurements behind it — including the ones that came out against the design. |

## What it is not

- **Not a planner.** It holds the definition of done. How to get there is the
  agent's job.
- **Not a judge.** No model scores anything. Measured judges over-reject
  conformant work by 35–45%, which is why nothing here gates on one.
- **Not a service.** One SQLite file per repository, on your machine.

## No client list ships in this repo

Recall is default-deny across harnesses, and the classifier that ships knows
only patterns identifying *anyone's* private work: IP addresses, emails, AWS
ARNs and account ids, credential material, in-cluster DNS, private URLs.

It deliberately ships no list of employers, clients or clusters. Those differ
per operator, and a list of them committed to a public repository is the leak
it was written to prevent. Yours live in `~/.loopgraph/sensitive.toml`, on your
machine.

## Working on it

```sh
uv run pytest -q                                                   # tests
uv run --with markdown --with pygments python tools/build_docs.py  # docs site
```

Documentation sources are markdown in `docs-src/`. The rendered site in `docs/`
is committed on purpose, so what GitHub Pages serves is what was reviewed
locally rather than the output of a remote build nobody watched.

MIT — see [LICENSE](LICENSE).
