---
title: Memory
description: Retain, recall, supersede on the same graph -- deterministic BM25 retrieval, and a default-deny recall scope you configure with your own terms.
---

# Memory

`loopgraph mem` (or the `mem` shim) is memory on the same graph: `retain`,
`recall`, `supersede`, `history`, `forget`, `import`.

It exists because of what the alternatives ask for. Hindsight, Supermemory,
Graphiti, Mem0 and LangMem each want their own store, sitting beside a graph
that already has typed nodes, typed edges and an append-only delta log with a
logical clock. The store was never the missing piece. Retrieval was.

## The two places a memory lives

Each memory is a markdown file you can read and edit, and an entry in a
search index built from those files. The files are the real thing. The index
only makes them findable, and `mem reindex` rebuilds it from the files at any
time.

Both halves matter when you delete a memory. `mem forget` clears the file and
the search entry together, and it tells you when it only found one:

```console
$ mem forget the-edge-collector-is-32-bit
Forgot the-edge-collector-is-32-bit. Its search entry had already been
deleted, so only the other half needed clearing up.
```

Nothing is wrong there. One half had already gone and the command tidied up
the rest. You only get an error when neither half exists:

```console
$ mem forget somehing-mistyped
There is no memory called somehing-mistyped. Search for the right name with:
mem recall "<a few words>"
```

## Security notes, collected for one review

When you save a memory that contains something private -- a password, an
internal address, a client name from your own term list -- loopgraph keeps
that memory and quietly writes itself a note about it. It does not interrupt
you. An earlier version announced every one, printed 98 times in a single
session, and a running commentary about security teaches you to skim the one
category you should never skim.

Read the notes when it suits you:

```console
$ loopgraph security
134 security notes are waiting for you. The oldest is 16 days old.

    61  credential material or its location
        opensearch-tenant-sso-redirect-loop-2026-08-19
        clearwater-tenant-opensearch-oidc-the-client-sec
        and 59 more

Run `loopgraph security --clear` once you have handled them.
```

Two housekeeping commands go with it. `--clear` marks everything currently
listed as reviewed. `--prune` drops notes about memories you have since
forgotten, and it leaves everything else alone -- notes you filed by hand
about an account or a host are never touched by it.

## How it behaves

**Global, not per-repo.** One file, `~/.loopgraph/memory.db`. A trap learned in
one repo is worth the most in a different one.

**Three kinds**, after Hindsight's pathways: `world` (how things are),
`experience` (what happened to us), `model` (what we concluded).

**Recall is deterministic.** BM25 over FTS5 with a recency half-life, and no
model in the path. Recall runs at session start and on demand, so a model call
there would tax every session for a lookup that ranking already does.

**Extraction runs in the session, not a subprocess.** Every surveyed system
calls an LLM at retain time. A nested `claude -p` takes minutes or dies on
auth, and it would ship memory content somewhere the conversation had not
already been. The agent holding the conversation already knows what happened,
so it calls `retain` itself.

**Superseding keeps the old belief** and links it to the one that replaced it.
Delete it instead and the graph ends up asserting we always knew the new thing.

One tuning note: recall gates on **term coverage, not a BM25 floor**. An
absolute score floor is a function of corpus size, so a floor tuned against 75
memories silences a store with five — which is exactly when a new install gets
judged.

## Recall scope

One store is reachable from every harness on the machine, and those harnesses
do not all run the same vendor's model.

So recall is **default-deny**. A memory classified sensitive is withheld unless
`LOOPGRAPH_MEM_SCOPE=full`, and the withholding is announced — "3 withheld at
scope=safe" — rather than returned as an empty list that reads like "nothing is
known."

The shipped classifier knows only patterns that identify anyone's private work:
IP addresses, emails, AWS ARNs and account ids, credential material, in-cluster
DNS, private URLs.

What it deliberately does **not** ship is a list of employers, clients or
clusters. Those differ per operator, and a list of them committed to a public
repository is the leak it was written to prevent.

## Your own terms

Yours go in `~/.loopgraph/sensitive.toml`, which loopgraph reads at
classification time and never writes to. Override the location with
`LOOPGRAPH_SENSITIVE_CONFIG`.

```toml
# Literal terms, matched case-insensitively on word boundaries.
terms = ["acme", "acme-console", "orion"]
terms_why = "an internal system or client name"

# Regexes, for shapes a word list cannot express.
[[pattern]]
regex = '\b(?:ACME|GLBX)[-_][A-Z0-9-]{2,}\b'
why = "a client host or tenant code"
```

Note that this is an explicit term list rather than a rule like "any three
capitals." A classifier that fires on `AWS`, `SQL` and `CPU` marks everything,
and one that marks everything teaches its operator to run `--scope full` by
reflex. That is worse than having no classifier at all.

Edit the file and the change takes effect immediately. There is no restart,
because a term added after a leak scare should not have to wait for one.

A config that cannot be parsed prints to stderr and is skipped. It is never
silently ignored — a redactor that quietly stops redacting looks exactly like
one with nothing to redact.
