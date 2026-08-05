---
title: Memory
description: Retain, recall, supersede on the same graph -- deterministic BM25 retrieval, and a default-deny recall scope you configure with your own terms.
---

# Memory

`loopgraph mem` (or the `mem` shim) is memory on the same graph: `retain`,
`recall`, `supersede`, `history`, `forget`, `import`. It exists because the
surveyed options — Hindsight, Supermemory, Graphiti, Mem0, LangMem — each want
their own store beside a graph that already has typed nodes, typed edges and an
append-only delta log with a logical clock. What was actually missing was
retrieval.

- **Global, not per-repo.** `~/.loopgraph/memory.db`. A trap learned in one repo
  is worth most in a different one.
- **Three kinds**, after Hindsight's pathways: `world` (how things are),
  `experience` (what happened to us), `model` (what we concluded).
- **Recall is deterministic** — BM25 over FTS5 with a recency half-life, no
  model in the path. A model call at recall time taxes every session for a
  lookup that ranking already does.
- **Extraction runs in the session, not a subprocess.** Every surveyed system
  calls an LLM at retain time; a nested `claude -p` here takes minutes or dies
  on auth, and would ship memory content somewhere the conversation had not.
  The agent holding the conversation already knows what happened.
- **Superseding keeps the old belief** and links it. Deleting it would leave the
  graph asserting we always knew the new thing.

Recall gates on **term coverage, not a BM25 floor**: an absolute score floor is
a function of corpus size, so one tuned against 75 memories silences a store
with five — exactly when a new install gets judged.

#### Recall scope, and your own terms

One store is reachable from every harness on the machine, and those harnesses
do not all run the same vendor's model. So recall is **default-deny**: a memory
classified sensitive is withheld unless `LOOPGRAPH_MEM_SCOPE=full`, and the
withholding is *announced* — "3 withheld at scope=safe" — rather than returning
an empty list that reads as "nothing is known."

The shipped classifier only knows patterns that identify anyone's private work:
IP addresses, emails, AWS ARNs and account ids, credential material, in-cluster
DNS, private URLs. It deliberately does **not** ship a list of employers,
clients or clusters. Those differ per operator, and a list of them committed to
a public repository is the leak it was written to prevent.

Yours go in `~/.loopgraph/sensitive.toml` (override with
`LOOPGRAPH_SENSITIVE_CONFIG`), which is read at classification time and never
written to by loopgraph:

```toml
# Literal terms, matched case-insensitively on word boundaries.
terms = ["acme", "acme-console", "orion"]
terms_why = "an internal system or client name"

# Regexes, for shapes a word list cannot express.
[[pattern]]
regex = '\b(?:ACME|GLBX)[-_][A-Z0-9-]{2,}\b'
why = "a client host or tenant code"
```

An explicit term list, not "any three capitals" — a classifier that fires on
`AWS`, `SQL` and `CPU` marks everything, and teaches its operator to run
`--scope full` by reflex, which is worse than having no classifier at all. A
config that cannot be parsed prints to stderr and is skipped; it is never
silently ignored, because a redactor that quietly stops redacting looks exactly
like one with nothing to redact.

