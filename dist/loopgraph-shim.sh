#!/bin/sh
# Wrapper so 'loopgraph' works from any directory. The db is resolved from
# the CURRENT git repo, not from where loopgraph is installed.
#
# Install:
#   install -m 755 dist/loopgraph-shim.sh ~/.local/bin/loopgraph
#   export LOOPGRAPH_HOME=/path/to/this/checkout   # in your shell profile
#
# LOOPGRAPH_HOME is where this repository lives; ~/src/loopgraph is only a
# default, and a wrong one fails loudly here rather than silently running a
# different copy.
: "${LOOPGRAPH_HOME:=$HOME/src/loopgraph}"
if [ ! -f "$LOOPGRAPH_HOME/pyproject.toml" ]; then
    echo "loopgraph: no checkout at $LOOPGRAPH_HOME -- set LOOPGRAPH_HOME" >&2
    exit 127
fi
exec uv run --quiet --project "$LOOPGRAPH_HOME" loopgraph "$@"
