# P1 report: L0 ingestion and deterministic replay (2026-10-18)

- **Branch:** `engine/p1`, made from `engine/p0`. Worktree: `D:\investor\.claude\worktrees\engine-p1`. Local
  commits only.
- **Data:** under `D:\scalp-data`, outside git.
- **Details:** [P1-data-quality.md](P1-data-quality.md) and [ADR 0002](adr/0002-holdout-v2-and-live-feed.md).
- **History:** the first run stopped at Stage 1 because the Alpaca keys were missing. It resumed on 2026-10-17 after
  you provided them. Stage 0 was not repeated.

## Findings (new)

| # | Finding | Evidence | Impact | Recommendation |
|---|---|---|---|---|
| F1 | Alpaca's asset list has no record of most acquired companies: PXD, SPLK, MRO, HES, CTLT, X, … | Asset snapshot (33,577 records); merger corporate actions name them | A pool built from the asset list alone is survivorship-biased | Fixed. `top500_v3` adds delisted tickers from merger and worthless-removal actions; 80 became members |
| F2 | Pre-rename tickers bypassed the common-stock check. A reused ticker (STRC) mixes two issuers | `top500_v1` admitted SPLG, SILJ, FNGU and STRC | ETPs and a preferred stock got into the universe | Fixed. Validity windows come from rename records, and a pre-rename ticker inherits the classification of the company it became |
| F3 | The minute-coverage tail reflects minutes without trades, not missing downloads | Median 99.74%, p05 84.1%; zero-bar member days 0.01% | P2 features must define how minutes without a bar are handled | P2 decision |
| F4 | Live IEX volume is a median 4.6% of SIP volume (p10 2.5%, p90 7.6%) | 391k member symbol-days | IEX-based RVOL and volume features are a different distribution from SIP | Measure in P3 as planned (ADR 0002 §3) |
| F5 | Quote sizes switch from round lots to shares on 2025-11-03; older quotes were not restated | Probe of median sizes on both sides of the date | Imbalance features need `size_unit` | Stored per row. Ratios such as bid/(bid+ask) are unaffected |
| F6 | Quote samples: 0.60% missing within 60 s; 415 crossed and 1 non-positive among `ok` samples | DQ report | Small | Flag or filter in the P2 spread features |
| F7 | NVDA's 2024-06-10 split is stored, but NVDA was not a member that month | Price above the $200 cap before the split | Expected behaviour of the $5–$200 rule | None |

## Implementation

**What changed:**
- **Stage 0**, as reported earlier:
  - ADR 0001 Accepted, and ADR 0002.
  - Holdout v2: two windows, per-window unseal.
  - Durable venvs with hash-pinned polars 1.44.2, tzdata 2026.5 and cryptography.
- **Contracts v2:**
  - `feed` is mandatory on every table.
  - New tables: `calendar`, `asset_snapshots`, `raw_bars_1d`, `raw_quote_samples`.
  - `universe_snapshots` v2 adds rank and method fields.
- **L0:**
  - `alpaca_client`: read-only allowlist, `check_range` before every request, a 170/min token bucket, and a manifest
    entry for every page.
  - `storage`: append-only Parquet writes, atomic rename, orphan-safe resume.
  - `data_access.read_frame`: partition pruning, predicate pushdown and a post-read assertion. A cache miss raises
    `MissingPartitionError`.
  - `universe`: `top500_v3`.
  - `ingest`: calendar, assets, actions, daily, universe, plan and minute steps.
  - `quality`: the data-quality aggregates.
  - The same Parquet guard is mirrored in `scalp_eval`.
- **Stage 2:**
  - `replay`: `StreamSource`, and `HistoricalSource` with `as_of` pinning. The total order is
    (available_at, priority, symbol, feed, seq). Bars become available at open + 62 s. The hash is SHA-256 over the
    canonical stream.
  - `live_source`: the IEX stream skeleton with an injected transport.

**Tests:**

| Suite | Count |
|---|---|
| `scalp_engine` | 74 |
| `scalp_eval` | 46 |

The new tests cover:
- store and contract enforcement;
- the Parquet guard, including mislabelled partitions and cache-miss errors;
- that a sealed request never reaches the network;
- the read-only endpoint allowlist;
- universe classification, renames and delistings;
- the static clock and network ban over the replay import closure, with a self-test of the checker;
- cross-process hashes under two `PYTHONHASHSEED` values with sockets disabled;
- one-row sensitivity;
- `as_of` pinning;
- live/historical equivalence;
- three real days.

**Measured results:**

| Metric | Value |
|---|---|
| Fetch | 91,999 requests (all HTTP 200, 8 retries); about 10.1 h of wall clock (11 min for reference data and daily bars, 596 min for minute bars and quotes); 69.0 GB downloaded |
| Disk | 5.83 GB: 5.33 GB 1-minute bars, 164 MB daily, 121 MB quote samples |
| Symbols | Pool 8,956; members 1,074 distinct (972 active, 80 delisted, 3 inactive, 19 pre-rename); minute bars for 1,088 symbols (members, 14 market ETFs, warm-up); 38 universes of 500 |
| Rows | SIP 187,563,979 and IEX 106,948,837 one-minute bars; 10,192,000 quote samples |
| Delisted coverage | 83 of 83 delisted or inactive members have usable SIP minute history (mean regular-hours coverage 87.6%) |

**Replay hashes:** identical across two processes with `PYTHONHASHSEED` 0 and 987654321, sockets disabled.

| Day | What it is | Events | SHA-256 |
|---|---|---|---|
| 2024-04-17 | Normal day | 364,526 | `2d22a3bb928d8518003210b0257ef8258ed0c7be934d2c2d63a7496fd1daee2e` |
| 2024-11-29 | Early close, 13:00 ET; 500 after-close samples | 224,074 | `012ff04392abe27d64f162b28d438ee7ccd81c30c5fe110337bbd11e14d9f94b` |
| 2024-02-26 | WMT 3:1 forward split, WMT a member; the split event is in the stream | 360,766 | `e2513002c8d266c4d2dc32c7cfbe296d1d78a5666ec385669d2f99ecaa5cd6e9` |

**Regressions:** none. stockbot passed 902 tests before and 902 after.

**Commits:**
- 052c92b `docs: accept ADR 0001, add ADR 0002, P0 report`
- e719a54 `feat(contracts): holdout v2 + tests`
- 01cece4 `build: pinned engine and eval requirements`
- 1416223 `feat(L0): calendar, universe, bars, quote samples`
- 5aa3324 `feat(L0): deterministic replay + tests`
- `docs: P1 data quality + report` (this commit)

## Gate

| Criterion | Result | Evidence |
|---|---|---|
| Stage 0 complete; P0 suites pass in the durable venvs | **PASS** | Engine 41 and eval 38 at the `engine/p0` state, without `PYTHONTZPATH`. Local `main` was not fast-forwarded (untracked files), which is non-blocking |
| Ingestion complete for the planned range | **PASS** | 807/807 sessions for SIP and IEX minute bars; 784/784 member sessions with quote samples; 38/38 universes; 0 days without a partition; reruns issue 0 requests |
| Median SIP regular-hours minute coverage ≥ 99% for member symbol-days | **PASS** | 99.74% (n = 392,000) |
| Zero duplicate keys | **PASS** | 0 in every table |
| Zero rows in sealed windows | **PASS** | 0 partitions in either window across all 7 tables (partition metadata); every request checked before the network |
| Replay: identical hashes across processes on 3 days; negative test; clock and network tests | **PASS** | Hashes above; the one-row change alters the hash and the unmodified copy reproduces it; static ban and sockets-disabled runs pass |
| stockbot suite unchanged | **PASS** | 902 / 902 |
| Diff only in allowed paths; no data files in git | **PASS** | See the commit check in the final message |

**P1 gate: PASS.**

## Decisions needed

1. **Rotate the Alpaca keys.** They were pasted into chat. *Recommendation: rotate now and set the new keys as User
   environment variables, so later runs need no paste.*
2. **Local `main`:** still behind because of the two untracked files (unchanged from the first P1 report).
3. **2016-01-01..03:** seal the non-trading days too by starting `legacy_2016_2021` on 2016-01-01.
   *Recommendation: yes, before P2 ingests news.*
4. **Minutes with no trade (F3):** forward-fill the price and record zero volume, or treat the minute as missing.
   *Recommendation: forward-fill price, zero volume, and an `is_filled` flag in L1. Decide in the P2 feature ADR.*
5. **polars 2.0:** *Recommendation: stay on 1.44.x through P2, and revisit at P3.*

## Next step: P2 prerequisites only (P2 not started)

1. A feature ADR covering:
   - the feature registry and code-hash procedure;
   - the missing-minute policy (decision 4);
   - the bar-based spread estimator (for example Corwin–Schultz or Abdi–Ranaldo), validated against the stored SIP
     quote samples;
   - rule-based regime definitions.
2. Approval of the free proxy sources:
   - Cboe daily VIX history;
   - EDGAR 8-K Item 2.02 timestamps for earnings;
   - Nasdaq Trader halts, going forward.
   - Breadth comes from our own universe bars.
   - Each needs a contract table and the same request guard.
3. The random-sample baseline for the P2 gate: same day, same price/ADV prefilter. Pin its seed scheme in the ADR.
4. Decisions 1 and 3 above.
