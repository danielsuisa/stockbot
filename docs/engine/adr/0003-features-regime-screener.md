# ADR 0003: L1 features, regime, L2 screener and the pre-registered P2 measurement

- **Status:** Accepted for the decisions the P2 prompt specifies (user, 2026-10-18). Choices this ADR adds are marked
  **Proposed**; they are listed again in the P2 report under "Decisions needed".
- **Date:** 2026-10-18.
- **Branch:** `engine/p2`.
- **Commit order:** this ADR, including §9 (the measurement specification), is committed before any feature,
  screener output or outcome is computed. Nothing in §§2–9 may change after Stage 4 runs.
- **Context:**
  - [ARCHITECTURE.md](../ARCHITECTURE.md) (L1, L2);
  - [ADR 0001](0001-engine-foundations.md) (feature ids);
  - [ADR 0002](0002-holdout-v2-and-live-feed.md) (holdout, live feed, replay order);
  - [P1 data quality](../P1-data-quality.md).

## 0. Inputs and how they are read

**Market data** is the P1 store, read only through `data_access`. The tables are:
- `raw_bars_1m` (SIP and IEX);
- `raw_bars_1d` (SIP);
- `raw_quote_samples`, read through `quote_view`;
- `corporate_actions`;
- `universe_snapshots` (`top500_v3`);
- `calendar`.

**Proxies**, added in P2, ingested with the P1 mechanics (append-only, manifest, guard):

| Table | Source | Notes |
|---|---|---|
| `vix_daily` | Cboe daily VIX history, `https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv` | `event_at` = the session date at 16:15 ET |
| `macro_events` | FOMC decision days, CPI release days, NFP (Employment Situation) release days | See §0.1 |
| `edgar_filings` | `https://data.sec.gov/submissions/CIK##########.json` and its additional `files` pages | The form, items and acceptance time of every filing |
| `cik_map` | Ticker → CIK | See §6 |

**§0.1 Macro sources:**
- **FOMC:** the decision day is the last day of each meeting listed on
  `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm`. Unscheduled meetings are included.
- **CPI release dates:** from the archive file names (`cpi_MMDDYYYY.htm`) on
  `https://www.bls.gov/bls/news-release/cpi.htm`.
- **NFP release dates:** from the archive file names (`empsit_MMDDYYYY.htm`) on
  `https://www.bls.gov/bls/news-release/empsit.htm`.
- These are the actual release dates, delays included (for example the 2025 shutdown). A release date is treated as
  known at 00:00 ET that day.

**§0.2 Whole-document sources (Proposed).** The Cboe file, the BLS archives, the Fed calendar and an EDGAR submissions
document each cover their whole history in one response. None can be range-limited.
- Rule:
  - The ingest range is declared first and passes `check_range`.
  - The response is parsed in memory, and every row dated outside the range is dropped at parse time.
  - Dropped rows are never stored, logged or inspected.
- The range here is 2023-06-01..2026-10-16, which is disjoint from both sealed windows.
- `data_access` then guards the stored rows as usual.

**§0.3 EDGAR acceptance time.** The submissions JSON field `acceptanceDateTime` (for example
`2024-05-03T00:30:34.000Z`) is not UTC.
- **Measured** on 12 earnings 8-Ks from Apple and Microsoft, 2023–2025, against the filing header
  `ACCEPTANCE-DATETIME` (Eastern):
  - the JSON value equals the ET acceptance time + 8 h under EDT;
  - the JSON value equals the ET acceptance time + 10 h under EST.
- **Conversion:**
  - Take the JSON wall time minus 8 h.
  - If that instant is in EST (America/New_York), subtract 2 h more.
  - Interpret the result as ET and convert to UTC.
- A unit test pins the 12 measured vectors. The stored `event_at` is the true UTC acceptance time.

## 1. Live profiles (verified)

**Verification:**
- Alpaca's FAQ (`https://docs.alpaca.markets/us/docs/market-data-faq`): "For historical queries, the `end` parameter
  must be at least 15 minutes old to query SIP data without a subscription". And: "Our free market data offering
  includes live data only from the IEX exchange."
- Alpaca also documents a `v2/delayed_sip` stream, "a 15-minute delayed SIP feed"
  (`https://docs.alpaca.markets/docs/real-time-stock-pricing-data`). The page does not state which plans include it.
- So the free plan serves SIP data with a 15-minute delay through the REST endpoints.

**Profiles:**

| Profile | Meaning | Live-eligible when |
|---|---|---|
| `iex_realtime` | IEX bars or stream | `available_at` = bar open + 60 s + 2 s (ADR 0002 §4) |
| `sip_delayed_15m` | SIP intraday data via REST | `available_at` ≥ event end + 15 min |
| `sip_eod` | SIP daily bars of earlier sessions | Always: available from the prior session's 20:00 ET + 15 min |
| `research_only` | Real-time SIP | Never live; used only for the P3 IEX-vs-SIP comparison |
| `public_realtime` *(Proposed)* | EDGAR acceptance times | `available_at` = acceptance + 60 s |
| `public_eod` *(Proposed)* | Cboe VIX close; scheduled macro dates | VIX at the session date 18:00 ET; macro dates at 00:00 ET of the day |

The last two profiles are added because the four market-data profiles do not describe non-market inputs.

## 2. Feature registry

**Feature id:** `name@MAJOR.MINOR.PATCH+<sha256(defining source)[:12]>`, per ADR 0001.
- The registry file is `scalp_contracts/features/registry_p2.json`, following `feature_definition.schema.json`.
- A test recomputes every code hash and fails on a mismatch.

**Price adjustment for daily features.** All daily features are split-adjusted as of decision day *d*:
- Bars dated before a forward or reverse split with ex date ≤ *d* are scaled: price × old/new, volume × new/old.
- Rates come from the stored corporate-action record.
- Nothing dated after *d* is used.

**Bar availability for intraday features.** A bar is available at its open + 62 s. Bars are taken from 04:00 ET of
day *d*.

**Missing minutes** (user decision):
- Price is carried forward, volume is 0, and `is_filled = true`.
- Never carried across sessions and never backfilled.
- In P2 this only affects `last_price`, which is the last trade.
- The spread estimators use real bars only. Filled bars have high = low = close and would bias them.

### Daily features

Decision day *d*; `available_at` = the 20:00 ET close of session *d − 1* + 15 min.

| Name | Feed / profile | Definition |
|---|---|---|
| `prev_close` | sip / `sip_eod` | Adjusted close of the last daily bar before *d* |
| `adv20` | sip / `sip_eod` | Mean adjusted volume of the last 20 sessions before *d*; needs ≥ 15 bars |
| `atr14_pct` | sip / `sip_eod` | Mean true range of the last 14 sessions (TR uses the previous adjusted close) ÷ `prev_close`; needs 15 bars |
| `sector_etf` | sip / `sip_eod` | Monthly. The SPDR sector ETF (XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY) with the highest correlation of daily log returns over the 60 sessions before the month's first session; needs ≥ 40 common returns. **Method: trailing correlation** (point-in-time by construction), not SIC |
| `spy_ret20` | sip / `sip_eod` | SPY close(*d − 1*) ÷ close(*d − 21*) − 1 |
| `spy_sma20_dev` | sip / `sip_eod` | SPY close(*d − 1*) ÷ mean(close *d − 20 .. d − 1*) − 1 |
| `vix_prev_close` | none / `public_eod` | Cboe VIX close of the last session before *d* |
| `macro_day` | none / `public_eod` | *d* is an FOMC decision day, a CPI release day or an NFP release day |

### Snapshot features

Symbol *s*, day *d*, snapshot time *t*; computed per variant (§4).

| Name | Inputs (variant feed) | Definition |
|---|---|---|
| `last_price` | Bars available ≤ *t* | Close of the latest available bar of *d*; null if none |
| `gap_pct` | `last_price`, `prev_close` | `last_price` ÷ `prev_close` − 1 |
| `rvol_pre` | Pre-market bars (04:00 ≤ open < 09:30) available ≤ *t* | Cumulative pre-market volume to *t* ÷ mean over the prior 20 sessions of cumulative pre-market volume to the same ET clock time |
| `rvol_open` | Regular-session bars (open ≥ 09:30) available ≤ *t* | Same, for cumulative regular-session volume |
| `vwap_dev_pct` | Bars available ≤ *t* | `last_price` ÷ VWAP − 1. VWAP = Σ vwap·volume ÷ Σ volume over the regular-session bars (pre-market bars before the first regular bar is available) |
| `pm_range_pct` | Pre-market bars available ≤ *t* | (max high − min low) ÷ `prev_close` |
| `minutes_since_open` | — | (*t* − 09:30 ET) in minutes; negative before the open |
| `spread_cs` | The last 30 real bars available ≤ *t* | Corwin–Schultz high-low estimator (§3); needs ≥ 10 bars |
| `spread_ar` | The last 30 real bars available ≤ *t* | Abdi–Ranaldo close-high-low estimator (§3); needs ≥ 10 bars |
| `spread_nbbo_delayed` | sip / `sip_delayed_15m` | (ask − bid) ÷ mid of the latest `ok` quote sample of *d* taken at or before *t* − 15 min |
| `spread_nbbo_rt` | sip / `research_only` | Same, latest `ok` sample at or before *t* |
| `earnings_8k` | none / `public_realtime` | An 8-K with item 2.02 by the symbol's CIK, accepted in (regular close of session *d − 1*, *t* − 60 s]. Null when there is no CIK |
| `dilution_filing` | none / `public_realtime` | A form matching `^(S-1|S-3)(/A|ASR|MEF)?$` or `^424B\d$`, accepted in (*t* − 30 days, *t* − 60 s]. Null when there is no CIK |
| `spy_rv_intraday` | SPY bars of the variant feed, open ≥ 09:30, available ≤ *t* | Standard deviation of 1-minute log close-to-close returns × √(390·252); needs ≥ 10 returns |

**RVOL denominators:**
- They use the prior 20 sessions on which the symbol's minute bars were fetched (the P1 day plan). A day it was
  fetched with no bar counts as 0.
- They need ≥ 10 such sessions and a denominator > 0; otherwise the value is null.

## 3. Spread estimator choice (rule fixed now)

**Estimators**, on 1-minute bars in time order, consecutive pairs (i, i+1):
- **Corwin–Schultz:**
  - β = ln(H_i/L_i)² + ln(H_{i+1}/L_{i+1})²
  - γ = ln(max(H_i, H_{i+1}) / min(L_i, L_{i+1}))²
  - α = (√(2β) − √β)/(3 − 2√2) − √(γ/(3 − 2√2))
  - S = 2(e^α − 1)/(1 + e^α), with negative pair estimates set to 0
  - The window estimate is the mean over pairs.
- **Abdi–Ranaldo:**
  - c = ln close, η = (ln H + ln L)/2
  - s² = 4 · mean((c_i − η_i)(c_i − η_{i+1}))
  - S = √max(s², 0)

**Target:** the relative NBBO spread (ask − bid) ÷ mid from `ok` quote samples at 09:30–11:30 ET.
- Samples with ask ≤ bid or a relative spread > 5% are excluded as quote errors.

**Error and choice:**
- The estimate is computed from the last 30 real bars available at the sample time.
- **Choice:** the estimator with the lower **median absolute error in basis points**, computed on IEX bars (the
  live feed) over samples dated **2023-09-01..2024-12-31**. A tie goes to Corwin–Schultz.
- **Out of sample, reported only:** the same metrics on **2025-01-02..2026-10-16**:
  - median absolute error;
  - median signed error;
  - Spearman rank correlation;
  - agreement on the `< 0.10%` filter;
  - each for both feeds.

The split is fixed here and is not revisited.

## 4. Variants and spread used by the screener

| Variant | Intraday bars | Spread used | Role |
|---|---|---|---|
| **IEX-live** (primary) | IEX (`iex_realtime`) | `spread_nbbo_delayed` if present, else the chosen estimator on IEX bars | The GO-relevant variant |
| **SIP research** | SIP (`research_only`) | `spread_nbbo_rt` if present, else the chosen estimator on SIP bars | P3 comparison only |

Daily features (`sip_eod`), filings and macro inputs are the same in both variants.

## 5. Regime (rules only, per day *d* and snapshot time *t*, per variant)

1. **`event`** if any of these hold:
   - `macro_day`;
   - `vix_prev_close` ≥ 30;
   - *t* ≥ 09:45 ET and `spy_rv_intraday` ≥ 0.40.
2. Else **`trend`** if |`spy_ret20`| ≥ 0.03 and sign(`spy_ret20`) = sign(`spy_sma20_dev`).
3. Else **`chop`**.

Thresholds are fixed here. The regime is recorded on every snapshot row and is not a screener filter in P2.

## 6. Screener (L2 table exactly)

**Snapshots:** every 5 minutes from 08:30 to 11:30 ET (37 per session), on every session 2023-09-01..2026-10-16.

**Universe:** that month's `top500_v3` members (point-in-time). The 14-ETF market set is not screened.

| L2 parameter | Rule at snapshot *t* | Null → |
|---|---|---|
| Price $5–$200 | 5 ≤ px ≤ 200, where px = `last_price` (variant feed), else `prev_close` | fail |
| 20-day average volume > 2M | `adv20` > 2,000,000 | fail |
| RVOL at open > 2.0 | Before the first snapshot that has a regular-session bar available (that is, *t* ≤ 09:30) use `rvol_pre` > 2.0; from 09:35 use `rvol_open` > 2.0 | fail |
| ATR % > 2% | `atr14_pct` > 0.02 | fail |
| Spread < 0.10% | The variant's spread used (§4) < 0.0010 | fail |
| Catalyst: flag | `catalyst` = `earnings_8k` OR `macro_day`. Recorded as `true` / `false` / `unknown`, **not a pass condition**: the L2 table lists it as a flag | — |
| Dilution: exclude | Excluded if `dilution_filing` is true | **Proposed:** `unknown` (no CIK) passes and is flagged |

- **Candidate** = price ∧ adv20 ∧ rvol ∧ atr ∧ spread ∧ ¬dilution.
- **Output** goes to `screener_snapshots` (contract in `scalp_contracts/tables/`). Each row carries:
  - the pass/fail of every filter;
  - the feature values and the feature ids used;
  - the regime and the variant;
  - `inputs_available_at`, the latest `available_at` of any input. It must be ≤ the snapshot time.

**CIK mapping** (`cik_map`), in order:
1. SEC `company_tickers.json` (current tickers).
2. For a pre-rename ticker, the CIK of the company it became.
3. **Proposed:** for delisted tickers, EDGAR full-text search (`efts.sec.gov/LATEST/search-index`) restricted to
   10-K/10-Q filings dated 2023-01-01..2026-10-16. A CIK is accepted only when exactly one entity's display name
   carries `(<TICKER>)`.

Coverage is reported separately for delisted members.

## 7. Leakage tests (must pass)

1. **Future perturbation.** On randomized (day, symbol, *t*) samples covering every registered feature:
   - Alter or delete every input row whose `available_at` is after *t*: bars, quote samples, filings, daily bars of
     *d* or later, VIX of *d* or later, and corporate actions with ex date after *d*.
   - Every feature value at *t* must be unchanged.
2. Every stored row has `inputs_available_at` ≤ its decision time. For daily features, that is 04:00 ET of *d*.
3. **Replay parity.** On the three P1 replay days, features computed by streaming the P1 replay events must equal
   the batch features.

## 8. Disk

- Features are materialized only at snapshot times (inside `screener_snapshots`) and daily (`feature_daily`).
- The projection is checked before the run, and at least 8 GB must stay free on D:.

## 9. Pre-registered P2 measurement (run once, in `scalp_eval`)

**Inputs.** `scalp_eval` imports nothing from `scalp_engine`. It reads `screener_snapshots` and SIP `raw_bars_1m`
through its own `data_access`.

**Decision price.** The close of the latest SIP bar of *d* with open ≤ *t* − 1 min.
- If there is none, the (snapshot, symbol) pair is dropped and counted.

**Primary outcome.** Forward range = (max high − min low) ÷ decision price.
- The window is the SIP bars of *d* with open in [*t* + 1 min, *t* + 90 min), capped at open < `close_at` (the
  regular close, from the calendar).
- If the window has no bar, the pair is dropped and counted.

**Secondary outcome.** The same range with the window [*t* + 1 min, `close_at`).

**Random baseline:**
- For each snapshot (*d*, *t*) of a variant with *k* candidates, draw *k* symbols uniformly without replacement from
  that day's members.
- The members are the symbols of that variant's snapshot rows at (*d*, *t*), sorted.
- The draw uses `random.Random(seed).sample(members, k)`, where seed = `int(sha256(f"{d:%Y-%m-%d}|{t:%H:%M}|p2-random-v1"), 16)`
  and *t* is ET.

**Primary statistic.**
- **R** = (pooled median forward range of all IEX-live candidates) ÷ (pooled median forward range of all their random
  draws).
- **Gate:** R ≥ 1.5.

**Reported, not gating:**
- R per calendar quarter.
- **ATR-matched R.**
  - Each candidate is matched with one member drawn uniformly, with replacement, from the same `atr14_pct` decile.
  - Deciles are formed among that day's members with a non-null value: decile = ⌊10·(rank − 1)/n⌋, ascending, ties
    broken by symbol.
  - Seed = `sha256(f"{d}|{t}|p2-atr-matched-v1")`.
  - Candidates are taken in symbol order, and each draw is `rng.choice(sorted(decile members))`.
- R for the SIP research variant.
- The same with the secondary outcome.
- The candidate-count distribution per snapshot: quantiles, the number and share of zero-candidate snapshots, and
  counts per variant.

**Run discipline.** Run once on the committed code. The run's UTC timestamp and the commit hash are recorded in the
report. Any change proposed afterwards is reported and not re-run.
