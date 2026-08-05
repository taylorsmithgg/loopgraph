---
title: Mechanism benchmarks
description: Clock versus content-addressed invalidation over 788 days of commit history.
---

# Mechanism benchmarks

Three claims in the coordination spec tested against real data, 2026-08-03.

## 1. CAS base-hash vs bare clock — CONFIRMED, modest

Method: the git history of a large private GitOps monorepo (`infra-gitops` below), 3,233 file-changing commits spanning 788 days. For 400 sampled windows per size, an agent scope of 6 files was drawn from the files touched in the preceding 7 days. Clock invalidation = any commit in the window touched a scoped file. CAS invalidation = `git diff` between window endpoints over the scoped files is non-empty.

| window | trials | clock invalidations | CAS invalidations | false | reduction |
|---|---|---|---|---|---|
| 1h | 351 | 94 | 86 | 8 | 8.5% |
| 4h | 359 | 122 | 102 | 20 | 16.4% |
| 24h | 389 | 222 | 190 | 32 | 14.4% |
| 7d | 400 | 334 | 299 | 35 | 10.5% |

**8.5–16.4% of clock-triggered invalidations are false** — the file was touched and reverted, or changed and changed back. The mechanism is real and worth keeping, but it is a refinement, not a transformation. It also does not address the larger category of *semantically irrelevant* changes, which are content changes that do not affect the agent's conclusion.

## 2. Staleness risk by agent lifetime — the more useful output

The same measurement, read as a risk curve rather than a comparison:

| agent lifetime | P(premises genuinely changed) |
|---|---|
| 1 hour | 24.5% |
| 4 hours | 28.4% |
| 24 hours | 48.8% |
| 7 days | **74.8%** |

A seven-day agent has roughly a three-in-four chance its premises moved. This is the quantitative argument for bounding agent lifetime, and it is stronger than any argument for detecting staleness after the fact.

## 3. Semantic key derivation on K8s — CONTRADICTS the spec's generalization

863 K8s objects with `kind` + `name` across two repositories.

| identity space | distinct | collision |
|---|---|---|
| filenames | 352 | 59.2% |
| `kind/name` | 797 | **7.6%** |
| vocabulary-stripped | 784 | **9.2%** |

**Stripping made it worse.** `ServiceAccount/search-workflows` → `ServiceAccount/workflows` merged distinct objects. Most residual `kind/name` collisions are the same object defined per environment (`Role/default` ×5), which is legitimate rather than a conflict.

Derivation has now helped in exactly **one of four namespaces tested**:

| namespace | derivation |
|---|---|
| Go exported symbols | not needed — 0.0% collision |
| TypeScript exports | not needed — 0.0% collision |
| K8s `kind/name` | **harmful** — 7.6% → 9.2% |
| SQL detection views | **helps** — 11.7% → 4.4% |

§8.6 currently implies derivation is generally required wherever nothing enforces semantic uniqueness. That is too broad. Derivation helps only in the narrow case where **the same logical thing is deliberately implemented once per vendor** — detection rules across azure/entra/duo/sysmon. Elsewhere the declared identifier is the key and should be left alone.

*Measurement caveat:* the K8s extraction took the first `name:` within four spaces of indentation, which can capture container names, and it omitted `namespace`. The spec's own identity for K8s is `kind/namespace/name`, so the 7.6% figure is an upper bound on true collision.

## 4. Relay threshold sizing — argues against building push at all

Combining the risk curve (§2) with the measured lifetime distribution (`2026-08-03-corpus-baseline.md`, n=2,674):

| cohort | agents | share | P(stale) | expected stale |
|---|---|---|---|---|
| <10m | 952 | 35.6% | 10.0% | 95 |
| 10–60m | 1,200 | 44.9% | 22.2% | 267 |
| 1–4h | 483 | 18.1% | 26.4% | 128 |
| 4–24h | 22 | 0.8% | 36.6% | 8 |
| >24h | 17 | 0.6% | 59.5% | 10 |
| **total** | **2,674** | | | **508 — 19.0%** |

Relay threshold trade-off:

| relay if lifetime > | agents relayed | % of fleet | stale caught | precision | recall |
|---|---|---|---|---|---|
| 10m | 1,722 | 64.4% | 413 | 24.0% | 81.3% |
| 1h | 522 | 19.5% | 146 | 28.0% | 28.7% |
| 4h | 39 | 1.5% | 18 | 46.6% | 3.6% |
| 24h | 17 | 0.6% | 10 | 59.5% | 2.0% |

**No threshold is good.** Precision never exceeds 60%. Catching most staleness (81% recall at a 10-minute threshold) requires relaying to 64% of the fleet at 24% precision — three wasted relays for every useful one, each spending tokens in an agent's context.

**And the pull path already catches 100% of it for free.** §5.1 validates every returning agent with an indexed query and zero tokens. Push does not improve detection at all; its only value is letting an agent *abort early* and save its remaining work.

That value is small where staleness is likeliest. The >24h cohort — the highest-risk group at 59.5% — averages 208 tool calls and 331k tokens, *fewer* than the 1–4h cohort. Those agents are outstanding across suspensions, not working. Aborting one early saves little, because little remains to be done.

**Recommendation: do not build §5.2 push relay in the first implementation.** Ship pull-only validation, which is complete, free, and has no delivery hole (the probe showed queued relays are silently lost when an agent stops before its next tool round). Revisit push only if measurement shows agents doing substantial work *after* their premises break.
