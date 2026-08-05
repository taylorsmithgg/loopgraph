# loopgraph

Deterministic goal-state substrate for agent loops. Criteria live in a context
graph; "done" is computed from evidence, never claimed by an agent. Harness
hooks read the graph and refuse to let a turn end while the specification is
unmet.

**Documentation: <https://taylorsmithgg.github.io/loopgraph/>**

An agent that grades its own work will tell you it is finished. That is not
dishonesty, it is the absence of a fact to check against — so loopgraph
supplies one. A criterion is a statement plus a command plus an expectation.
The command runs, the expectation holds or it does not, and the loop's exit
condition is derived from that rather than asserted in prose.

Everything is deterministic: SQLite, subprocesses, exit codes. No model sits in
the path of any decision the gates make.

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
root>.db`. Both gates are inert until you give them something — the scope gate
is silent without a `SCOPE:` line, the loop gate silent without criteria — so
installing loopgraph changes no behaviour until you opt in.

```console
$ loopgraph add C1 "the queue drains under restart" \
    --cmd 'for i in $(seq 50); do ./restart; done; [ $(in) -eq $(out) ]'
C1 added   (unproven)

$ loopgraph check; echo "exit=$?"
C1  unproven  the queue drains under restart
exit=1
```

## What is here

| | |
|---|---|
| [CLI and exit codes](https://taylorsmithgg.github.io/loopgraph/cli.html) | Three separate exit-code contracts. Confusing them in a hook is a real hazard. |
| [Gates](https://taylorsmithgg.github.io/loopgraph/gates.html) | Scope gate on dispatch, loop gate on turn end, and the two limits that stop either trapping a session. |
| [Memory](https://taylorsmithgg.github.io/loopgraph/memory.html) | `retain` / `recall` / `supersede` on the same graph, with a default-deny recall scope you configure with your own terms. |
| [Audit and routing](https://taylorsmithgg.github.io/loopgraph/audit.html) | A second vendor asking whether a check can be satisfied without doing the work. |
| [Design](https://taylorsmithgg.github.io/loopgraph/design.html) | The specification this was built from, including what was rejected. |
| [Evidence](https://taylorsmithgg.github.io/loopgraph/evidence/index.html) | The measurements behind the design — including the ones that came out against it. |

Sources for the site are plain markdown in [`docs-src/`](docs-src); `docs/` is
generated and committed so that what is served is what was reviewed:

```sh
uv run --with markdown --with pygments python tools/build_docs.py
```

## Redaction, and why there is no client list in this repo

Recall is default-deny across harnesses, and the shipped classifier only knows
patterns that identify anyone's private work: IP addresses, emails, AWS ARNs
and account ids, credential material, in-cluster DNS, private URLs. It
deliberately ships no list of employers, clients or clusters — those differ per
operator, and a list of them committed to a public repository is the leak it
was written to prevent. Yours live in `~/.loopgraph/sensitive.toml`, on your
machine. See [Memory](https://taylorsmithgg.github.io/loopgraph/memory.html).

## License

MIT. See [LICENSE](LICENSE).
