# P1 data quality: L0 store (2026-10-18)

- **Content:** aggregates only, with no price rows.
- **Produced by:** `python -m scalp_engine.quality --start 2023-08-01 --end 2026-10-16`, run on the store at
  `D:\scalp-data` (commit 5aa3324). Every read goes through `data_access`, one day at a time.
- **Range:** 2023-08-01 → 2026-10-16. That is 807 sessions, 7 of them early closes.
  - The first monthly universe is for 2023-09. August 2023 is warm-up.
  - The end date was passed explicitly. It was the last complete session before the run (Friday 2026-10-16), below
    the 2026-10-30 cap.

## Fetch

| Dataset | Requests | Downloaded (JSON) | Stored (Parquet, zstd) |
|---|---|---|---|
| Calendar | 1 | 0.1 MB | 4.2 MB (807 partitions) |
| Asset snapshot (status=all) | 1 | 15 MB | 1.4 MB |
| Corporate actions (all types) | 175 | 36 MB | 17.3 MB (843 partitions) |
| Daily bars, SIP (candidate pool) | 1,014 | 500 MB | 164 MB |
| 1-minute bars, SIP + IEX, 04:00–20:00 ET | 31,114 | 29.8 GB | 5.33 GB (807 + 807 partitions) |
| Quote samples, SIP | 59,694 | 38.6 GB | 121 MB (784 partitions) |
| **Total** | **91,999 (all HTTP 200; 8 retries after 429, 5xx or network errors)** | **69.0 GB** | **5.83 GB** under `D:\scalp-data` |

- **Wall-clock time:** about 10.1 h.
  - Reference data and daily bars: 11 min.
  - Minute bars and quote samples: 596 min. The run was restarted once, after 15 minutes, to go from 8 to 14
    workers; it resumed from the manifest.
- **Rate:** about 160 requests/min, under a self-imposed cap of 170 (the account limit is 200/min).
- **Budget check, done before the fetch:**
  - Estimate: 122,697 requests, 12.0 h, 6.7 GB.
  - Limits: 24 h and 100 GB, with about 20 GB actually free on D:.
  - Actual: fewer requests, because the 5 s quote lookback usually sufficed.
- **Resume check:** re-running every step after completion issued 0 requests.
- **Store hygiene:** 0 dropped symbols, 0 orphan parts, 0 temp files.

## Universe (method `top500_v3`)

**Candidate pool: 8,956 tickers.** Sources:
- Alpaca's asset list (status=all) on NYSE, NASDAQ and AMEX, minus ETPs, units, warrants, rights and preferreds.
- Pre-rename tickers, from name-change actions.
- Delisted tickers, from merger and worthless-removal actions.

**Members:**
- 38 monthly universes, each with exactly 500 members.
- 1,074 distinct members:
  - 972 active;
  - 80 delisted (from corporate actions);
  - 3 inactive in the asset list;
  - 19 pre-rename tickers.
- Plus the fixed market set of 14 ETFs.

**Minute bars cover 1,088 symbols.** That is members, the market set, and warm-up symbols.
- Warm-up is 30 sessions before a symbol's first membership.
- It is capped at the range start. The September 2023 members therefore have 23 sessions of warm-up (August 2023).

**Classification check.** The sample check found two defects, and the method was revised twice:
- **`top500_v1`:** pre-rename tickers skipped the common-stock check. That admitted three ETP tickers (SPLG, SILJ,
  FNGU) and STRC. STRC's ticker now belongs to a preferred stock.
- **`top500_v2`:** Alpaca's asset list has no record at all of most acquired companies (PXD, SPLK, MRO, HES, CTLT,
  X, …). The pool had no delisted names.
- **`top500_v3`** adds delisted tickers from merger records. Each is a candidate only for months before its
  delisting. Merger targets of ETP acquirers are excluded as fund reorganizations.
- The v1 and v2 snapshots stay in the append-only store and are filtered out by `method_version`.

**Final check on v3:**
- No member name matches ETF, ETN, fund, preferred, warrant, unit, `Trust, Series` or index.
- A random sample of 30 members was all operating companies or ADRs. The sample was drawn from v1. v3 differs
  only by the added delisted tickers and the corrected rename handling, both reviewed separately.
- The 99 members with no vendor name (delisted or pre-rename tickers) were reviewed by ticker.

**Remaining caveats:**
- Partnerships trading as common units are kept, e.g. ET, EPD, MPLX.
- Asset attributes (`tradable`, `shortable`, …) are as of 2026-10-17, not point-in-time. Rows carry
  `backfilled=true`.
- Delistings for reasons other than mergers or worthlessness have no source.
- A ticker reused within the range is the same series to `asof='-'`.

## Coverage and integrity

| Check | Result |
|---|---|
| SIP regular-hours minute coverage, member symbol-days (n = 392,000) | **median 99.74%**; p25 97.44%; p10 90.51%; p05 84.10%; mean 97.00% |
| Same, excluding 672 symbol-days after a member's delisting or rename | median 99.74%; mean 97.17% |
| Member symbol-days with coverage ≥ 99% | 64.3% |
| Member symbol-days with zero SIP bars (excluding post-delisting days) | 0.01% |
| Early-close days (expected 210 minutes) | median 100%; p10 87.6% |
| Duplicate keys | **0**: `raw_bars_1m` SIP and IEX, `raw_quote_samples`, `raw_bars_1d`, `calendar`, `universe_snapshots` |
| Out-of-order rows within a partition, per feed | 0 |
| Non-positive prices in 1-minute bars; high < low | 0; 0 |
| Days without a minute-bar or quote-sample partition | 0; 0 |
| 1-minute rows stored | SIP 187,563,979; IEX 106,948,837 |

Coverage counts the minutes that have a SIP bar. A minute with no trades has no bar. The lower quantiles are
therefore the less-traded members and minutes, not missing downloads: zero-bar member days are 0.01%.

## Quote samples

**Updated in P2 (Stage 0.3, 2026-10-18).**
- The sampling rule is now `close_minus_5m_v2`: the samples are taken at 09:30–11:30 ET every 5 minutes, and at the
  session close minus 5 minutes from the calendar. That is 15:55 on a normal day and 12:55 on an early close.
- P1's fixed 15:55 sample fell after the close on the 7 early-close days.
- Those 7 sessions were re-sampled under the new rule as an appended part, `sip-samples-v2`: 424 requests, 7.5 min,
  91,000 rows. The P1 parts are unchanged.
- Readers use `data_access.quote_view`: for a date with v2 rows, only the v2 rows count.
- The figures below are through that view.

The samples are the prevailing SIP NBBO at 26 times per session for that month's 500 members.

| Status | Count |
|---|---|
| ok | 10,130,955 |
| missing (no quote within 60 s) | 61,045 |
| after_close | 0 (P1 had 3,500; superseded by the v2 re-sample) |

- **Missing rate:** **0.60%**.
- **By year:** 2023 0.31%, 2024 0.28%, 2025 0.72%, 2026 0.97%.
- **Duplicate keys** in the view: 0.
- **Store:** 791 files in 784 partitions, 122.0 MB.
- **Lookback:** 5 s first; a 60 s retry for the symbols still without a quote.
- **Anomalies among `ok` samples:** 415 crossed quotes (bid > ask) and 1 non-positive. They are kept as received
  and flagged for P2.
- **`size_unit`:** `round_lot` before 2025-11-03 and `shares` from that date. This was measured: Alpaca did not
  restate older quotes.

## IEX vs SIP

IEX volume as a share of SIP volume, regular hours, per member symbol-day (n = 391,282):

| p01 | p05 | p10 | p25 | p50 | p75 | p90 |
|---|---|---|---|---|---|---|
| 1.2% | 2.1% | 2.5% | 3.5% | 4.6% | 6.0% | 7.6% |

- Live IEX features see about 1/20th of consolidated volume.
- This is the gap that P3's IEX-vs-SIP baseline measurement prices (ADR 0002 §3).

## Delisted and renamed coverage

- **Delisted or inactive members:** 83. All 83 have usable SIP minute history on their member days, with mean
  regular-hours coverage of 87.6%.
- **Pre-rename tickers:** 19. All 19 have SIP minute history.

## Sealed windows

This check uses partition metadata only, meaning directory names; no file was opened.

| Table | Partitions | First | Last | In `legacy_2016_2021` or `forward_2026q4` |
|---|---|---|---|---|
| calendar | 807 | 2023-08-01 | 2026-10-16 | **0** |
| asset_snapshots | 1 | 2026-10-17 | 2026-10-17 | **0** |
| corporate_actions | 843 | 2023-08-01 | 2026-10-16 | **0** |
| raw_bars_1d | 807 | 2023-08-01 | 2026-10-16 | **0** |
| universe_snapshots | 38 | 2023-09-01 | 2026-10-01 | **0** |
| raw_bars_1m | 807 | 2023-08-01 | 2026-10-16 | **0** |
| raw_quote_samples | 784 | 2023-09-01 | 2026-10-16 | **0** |

**Request-level check:** every vendor request passed `check_range` before the network. A test asserts that a
sealed request never reaches `urlopen`. Corporate-action rows dated outside the requested range were dropped
before storage.
