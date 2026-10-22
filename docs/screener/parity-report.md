# Parity report — engine vs the public finviz screener (Phase G)

Run: engine in **CLOSED** mode on the last completed session **2026-10-21**; finviz
pages loaded in the same window (after the close, before the next pre-market), at ≥ 20.5 s between loads,
unauthenticated, robots.txt-allowed preset URLs only, at most 3 pages (30 rows) per screen. No finviz ticker
list or raw value is stored anywhere: the comparison ran in memory, and only the aggregates below were kept
(G2, G4). Evidence for every number: OBSERVED (this run).

**How to read the set metrics.** finviz caps these signals at 200 and shows 10 rows a page. Only the top 30 were
loaded. So the sets compared are finviz's top 30 vs the engine's top 30 by the same ranking (Jaccard,
precision, recall). *recall within cap* = the share of finviz's top 30 that is anywhere in the engine's full
(≤ 200) list. Universe misses (finviz tickers the engine has no bars for) are counted apart and excluded from
the per-metric statistics.

## Signal screens 1–5

| # | screen | finviz rows | engine total | universe misses | non-stock rows (finviz) | top-30 overlap | Jaccard | precision | recall | recall within cap | result |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `s=ta_topgainers` | 30 | 200 | 0 | 2 | 20 | 0.5 | 0.667 | 0.667 | 1.0 | **FAIL** |
| 2 | `s=ta_unusualvolume` | 30 | 200 | 0 | 7 | 22 | 0.579 | 0.733 | 0.733 | 1.0 | **FAIL** |
| 3 | `s=ta_newhigh` | 30 | 112 | 0 | 0 | 19 | 0.463 | 0.633 | 0.633 | 0.9 | **FAIL** |
| 4 | `s=ta_oversold` | 36 | 100 | 0 | 0 | 27 | 0.6 | 0.75 | 0.75 | 1.0 | **FAIL** |
| 5 | `s=ta_mostactive` | 30 | 200 | 0 | 0 | 29 | 0.935 | 0.967 | 0.967 | 0.967 | **PASS** |

Screen-level pass rule (DECISION, from the prompt): Jaccard ≥ 0.80 for EXACT-only screens (all five signals
use EXACT metrics), and every compared metric's median deviation within tolerance.

## Per-metric deviation on the overlap

| screen | metric | n | median abs. deviation | P90 | tolerance | within? |
|---|---|---|---|---|---|---|
| `ta_topgainers` | averageVolume | 29 | 2.616 | 3.21 | 5.0 % rel (prompt) | yes |
| `ta_topgainers` | change | 30 | 0.0 | 0.0 | 0.5 pp (prompt) | yes |
| `ta_topgainers` | high52w | 29 | 0.0 | 0.0 | 1.0 % rel (DECISION) | yes |
| `ta_topgainers` | low52w | 29 | 0.0 | 0.0 | 1.0 % rel (DECISION) | yes |
| `ta_topgainers` | marketCap | 21 | 0.024 | 1131.925 | 5.0 % rel (prompt) | yes |
| `ta_topgainers` | prevClose | 30 | 0.0 | 0.0 | 0.5 % rel (DECISION (as price)) | yes |
| `ta_topgainers` | price | 30 | 0.0 | 0.0 | 0.5 % rel (prompt (price ≤ 0.5 pp)) | yes |
| `ta_topgainers` | volume | 30 | 0.0 | 0.009 | 5.0 % rel (DECISION (as avg volume)) | yes |
| `ta_unusualvolume` | averageVolume | 30 | 3.115 | 30.626 | 5.0 % rel (prompt) | yes |
| `ta_unusualvolume` | change | 30 | 0.0 | 0.0 | 0.5 pp (prompt) | yes |
| `ta_unusualvolume` | high52w | 22 | 0.0 | 0.001 | 1.0 % rel (DECISION) | yes |
| `ta_unusualvolume` | low52w | 22 | 0.0 | 0.0 | 1.0 % rel (DECISION) | yes |
| `ta_unusualvolume` | marketCap | 19 | 0.241 | 90.8 | 5.0 % rel (prompt) | yes |
| `ta_unusualvolume` | prevClose | 30 | 0.0 | 0.0 | 0.5 % rel (DECISION (as price)) | yes |
| `ta_unusualvolume` | price | 30 | 0.0 | 0.0 | 0.5 % rel (prompt (price ≤ 0.5 pp)) | yes |
| `ta_unusualvolume` | volume | 30 | 0.0 | 0.002 | 5.0 % rel (DECISION (as avg volume)) | yes |
| `ta_newhigh` | averageVolume | 29 | 0.82 | 2.383 | 5.0 % rel (prompt) | yes |
| `ta_newhigh` | change | 30 | 0.0 | 0.0 | 0.5 pp (prompt) | yes |
| `ta_newhigh` | high52w | 27 | 0.0 | 0.0 | 1.0 % rel (DECISION) | yes |
| `ta_newhigh` | low52w | 27 | 0.0 | 0.0 | 1.0 % rel (DECISION) | yes |
| `ta_newhigh` | marketCap | 22 | 0.0 | 0.375 | 5.0 % rel (prompt) | yes |
| `ta_newhigh` | prevClose | 30 | 0.0 | 0.0 | 0.5 % rel (DECISION (as price)) | yes |
| `ta_newhigh` | price | 30 | 0.0 | 0.0 | 0.5 % rel (prompt (price ≤ 0.5 pp)) | yes |
| `ta_newhigh` | volume | 30 | 0.0 | 0.016 | 5.0 % rel (DECISION (as avg volume)) | yes |
| `ta_mostactive` | averageVolume | 30 | 2.401 | 4.441 | 5.0 % rel (prompt) | yes |
| `ta_mostactive` | change | 30 | 0.0 | 0.0 | 0.5 pp (prompt) | yes |
| `ta_mostactive` | high52w | 28 | 0.0 | 0.001 | 1.0 % rel (DECISION) | yes |
| `ta_mostactive` | low52w | 28 | 0.0 | 0.0 | 1.0 % rel (DECISION) | yes |
| `ta_mostactive` | marketCap | 22 | 1.855 | 90.8 | 5.0 % rel (prompt) | yes |
| `ta_mostactive` | prevClose | 30 | 0.0 | 0.0 | 0.5 % rel (DECISION (as price)) | yes |
| `ta_mostactive` | price | 30 | 0.0 | 0.0 | 0.5 % rel (prompt (price ≤ 0.5 pp)) | yes |
| `ta_mostactive` | volume | 30 | 0.002 | 0.679 | 5.0 % rel (DECISION (as avg volume)) | yes |

Median engine/finviz ratios (unit check, aggregate):

- `ta_topgainers`: averageVolume 1.02418, change 0.01, high52w 0.01, low52w 0.01, marketCap 999993.112052, prevClose 1.0, price 1.0, volume 1.0
- `ta_unusualvolume`: averageVolume 1.013034, change 0.01, high52w 0.01, low52w 0.01, marketCap 999911.304314, prevClose 1.0, price 1.0, volume 1.0
- `ta_newhigh`: averageVolume 1.004109, change 0.01, high52w 0.01, low52w 0.01, marketCap 1000000.392632, prevClose 1.0, price 1.0, volume 1.0
- `ta_oversold`: 
- `ta_mostactive`: averageVolume 1.010548, change 0.01, high52w 0.01, low52w 0.01, marketCap 999155.421001, prevClose 1.0, price 1.0, volume 1.000016

## Filter screens 6–12

robots.txt disallows `/screener?*` for these, so they were not loaded. Their status is **PENDING_MANUAL**: the
exact URLs and the engine's results are in [parity-manual-checklist.md](parity-manual-checklist.md).

| # | screen | status |
|---|---|---|
| 6 | `cap_small,sh_float_u50,sh_short_o20` | PENDING_MANUAL |
| 7 | `sh_float_u50,sh_short_o20,sh_avgvol_o500,sh_price_o2,sh_relvol_o2,sh_opt_option` | PENDING_MANUAL |
| 8 | `ta_sma50_pa,ta_sma200_pa,ta_highlow52w_b0to10h` | PENDING_MANUAL |
| 9 | `ta_rsi_os30,cap_midover` | PENDING_MANUAL |
| 10 | `fa_pe_u15,fa_roe_o15,cap_largeover` | PENDING_MANUAL |
| 11 | `ta_gap_u-5,sh_avgvol_o1000` | PENDING_MANUAL |
| 12 | `ta_volatility_wo5,sh_price_5to50` | PENDING_MANUAL |

Requests for this run: {"alpaca": 0, "sec": 0, "finra": 0, "nt": 0, "occ": 0}; 297 s.

## Calibration history (G3: one calibration round, then one measurement of the final configuration)

| round | engine change | top gainers | unusual volume | new high | oversold | most active |
|---|---|---|---|---|---|---|
| 0 (initial) | — | Jaccard 0.500 | 0.579 | 0.463 | (charts view not parsed) | 0.935 |
| 1 (calibration) | ETFs included (a misread of round 0's "non-stock" rows), average volume without the measured session | 0.395 | 0.250 | 0.429 | 0.532 | 0.500 |
| 2 (final measurement, no change after it) | ETFs out again (D-036b), 52-week distances from the sessions before the measured bar (D-044) | **0.500** | **0.579** | **0.463** | **0.600** | **0.935** |

What each round showed (aggregates only):

- **Exact on every compared value.** Price, change, previous close and volume deviate by a median of 0.0 in every
  round. The 52-week high/low distances deviate by 0.0 from round 2, after D-044. So finviz's volume is the
  whole-day consolidated volume (D-006), and its change is against the regular close.
- **Average volume** started at a median of 1–25% above finviz. With the measured session excluded (D-043) it is
  0.8–3.1%, within the 5% tolerance (P90 up to 31% on unusual-volume names).
- **Market cap** median 0.0–1.9%, within tolerance. P90 is large: multi-class issuers and ADRs, whose dei cover
  share count is not the per-class count (APPROX by design).
- **Set overlap.** Every name in finviz's top 30 is in the engine's list for top gainers, unusual volume and
  oversold (recall within cap 1.0; 0.9 for new high). The engine's top 30 also holds names finviz's top 30 does not.
  The engine-only names are common stocks, by the same aggregate features as finviz's: Nasdaq-listed, often under
  $1, most of them in good standing (checked against Nasdaq Trader's financial status), so it is not a
  warrant/unit/right leak. The likely cause is a universe-composition difference that the allowed pages cannot
  resolve. **Most active passes (0.935).**
- **New high:** the engine counts 112 new 52-week highs against finviz's capped 200. finviz probably uses a
  different window or high basis, for example including the pre-/post-market high (UNKNOWN).

Result: per-metric parity **PASS** on all compared metrics. Set parity **PASS** for most active; **FAIL** for top
gainers, unusual volume, new high and oversold (cause above: universe composition and the new-high basis, not metric
values). No ticker-level tuning was done (G3).
