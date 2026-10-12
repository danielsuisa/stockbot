# Recommendation algorithm: the blind run's report (spec 6.4)

`ensemble-blind-report-2026-10-12.json` next to this file is the only artifact of the blind run, committed byte for
byte as downloaded (sha256 `0432e10b757a332f151bd7dac325459df388d1cdd779b0407b918c5b4f00e7d1`). This note restates its
contents and the run's printed lines. It adds no interpretation. The verdict run is not approved.

## The run

| Item | Value |
|---|---|
| Workflow run | `38137946310` (Ensemble blind run), all five jobs succeeded |
| Commit | `64a8faf5fc552c1623f5fa2d65dd1b8f7f75ad02` (merge of PR #17) on `ensemble-run-blind` |
| Input fingerprint (prefetch 1-4 and blind) | `e0464cacaf105656` |
| Input cache kept for the verdict | `ensemble-inputs-38137946310-4` (2,800,280,820 bytes); caches 2 and 3 were deleted on the owner's instruction |

The printed lines of the prefetch jobs:

| Job | Quotes wanted | Quotes read | Index pages missing |
|---|---|---|---|
| prefetch1 | 115,639 | 38,755 | 0 |
| prefetch2 | 115,639 | 91,756 | 0 |
| prefetch3 | 115,639 | 115,639 | 0 |
| prefetch4 (`--require-complete`) | 115,639 | 115,639 | 0 |

The blind job printed one line: `blind report written: ensemble-blind-report.json; inputs e0464cacaf105656`.

## Check 1 and comparability

| Number | Value |
|---|---|
| n | 35,083 |
| SE, T10 | 0.00361751 |
| SE, T5 | 0.00345086 |
| μ_min (4.1 × the larger SE) | 0.0148318 |
| b (K × 0.0041 R) | 0.00506515 |
| μ_min + b, against 0.10 R | 0.0198970 ≤ 0.10 |
| check_1 | true |
| Quote refusal share (limit 5%) | 0.831% |
| Small-pool share (limit 2%) | 0.0% |
| comparable | true |
| continue | true |

Also in the report: sd_c 0.677576, design effect 0.85171, largest absolute deviation 10.2224.

## K, r_s, w_s

K = 1.2354.

| Signal | r_s | w_s |
|---|---|---|
| S1 | 0.438723 | 0.0436964 |
| S2 | 0.759005 | 0.224610 |
| S3 | 1.63024 | 0.373514 |
| S4 | 0.670854 | 0.358179 |

## Counts

| Signal | Firings | Issued | Refusals |
|---|---|---|---|
| S1 | 1,558 | 1,533 | 25 |
| S2 | 8,015 | 7,880 | 135 |
| S3 | 13,438 | 13,104 | 334 |
| S4 | 14,774 | 12,566 | 2,208 |
| Total | 37,785 | 35,083 | 2,702 |

Refusals by reason:

| Signal | Already open | Shares below 2 | No usable quote | Cost above 10% of risk |
|---|---|---|---|---|
| S1 | 3 | 15 | 3 | 4 |
| S2 | 14 | 72 | 9 | 40 |
| S3 | 8 | 103 | 32 | 191 |
| S4 | 1,931 | 262 | 13 | 2 |
| Total | 1,956 | 452 | 57 | 237 |

Pools dropped for fewer than 20 members: 0. The firings and the issued tickets per block (0-9) are in the JSON.

## Cost in R of filled tickets (nearest rank)

| Signal | p50 | p75 | p90 |
|---|---|---|---|
| S1 | 0.0473079 | 0.0770306 | 0.127383 |
| S2 | 0.0658564 | 0.110692 | 0.170535 |
| S3 | 0.0763 | 0.131311 | 0.21298 |
| S4 | 0.0352139 | 0.0561434 | 0.0880335 |
