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
| 6 | `cap_small,sh_float_u50,sh_short_o20` | PENDING_MANUAL (engine 15 → 69 with the float follow-up, F3) |
| 7 | `sh_float_u50,sh_short_o20,sh_avgvol_o500,sh_price_o2,sh_relvol_o2,sh_opt_option` | PENDING_MANUAL (engine 0 → 6, F3) |
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

## Float follow-up (2026-10-22): F1–F3

Engine float after this PR (all of it, including the split scaling of D-057): shares outstanding − Forms 3/4/5 officers, directors and 10% owners (8 quarters of SEC's
Insider Transactions Data Sets + the daily index) − other ≥ 5% holders from XML Schedules 13D/13G (qualified
institutions under Rule 13d-1(b) kept), counted once per holder group (D-045..D-049, D-053, D-054). All numbers come
from local runs over the same stores the workflow builds (`.cache/screener` layout): 1,390 SEC and 78 Alpaca
requests to build them, then 2 SEC and 78 Alpaca for the public-float store and 45 Alpaca for the replay. The
13D/13G filings were fetched for the 23 squeeze names and a random sample of 150 universe companies (seed 22); the
other issuers had Forms 3/4/5 only (method `insiders`), as on a cache whose 13D/13G backlog is not yet cleared.
Scripts: `D:\float-work` (outside the repo; not part of the PR).

### F1 (a): against Yahoo, as the squeeze recorded it

The 23 `screen-2026-10-08` journal entries (2026-10-08..21), each on its own date (`seen`), point in time (holdings
by period of report, 13D/13G by filing date, shares outstanding usable that day). Error = |engine / recorded − 1|.

| float | n | median | P90 | target |
|---|---|---|---|---|
| **this PR** (`insiders+5pct` for all 22) | 22 | **6.4%** | **17.5%** | ≤ 15% / ≤ 35% — **PASS** |
| PR #28 (shares outstanding: no holdings had been read on those dates; 6 names had no share count at all) | 17 | 41.5% | 246.7% | |
| Forms 3/4/5 only | 22 | 12.8% | 60.4% | |
| every 13D/13G holder subtracted (13d-1(b) institutions too) | 22 | 15.4% | 43.5% | |
| issuer-reported, carried by ratio (i) | 14 | 21.3% | 32.7% | |
| issuer-reported, carried by affiliate count × split factor (ii) | 14 | 17.6% | 40.8% | |

MMYT (a 20-F filer) has no share count in any SEC frame and stays without a float. 8 of the 23 have no
`dei:EntityPublicFloat` (multi-class or recent IPO covers).

| ticker | date | Yahoo (squeeze) | engine | error | method | shares outstanding | insiders only | all 13G | EPF (i) | EPF (ii) |
|---|---|---|---|---|---|---|---|---|---|---|
| BCAX | 2026-10-08 | 36.9M | 48.9M | +32% | insiders+5pct | 66.2M | 62.8M | 39.9M | 49.0M | 51.9M |
| AVTX | 2026-10-19 | 40.1M | 31.9M | −20% | insiders+5pct | 53.6M | 53.2M | 13.8M | 31.8M | 46.3M |
| BETR | 2026-10-14 | 8.5M | 9.9M | +18% | insiders+5pct | 19.0M | 15.0M | 7.5M | — | — |
| BKV | 2026-10-15 | 31.6M | 36.5M | +15% | insiders+5pct | 109.4M | 41.8M | 36.5M | 21.4M | 37.1M |
| ZURA | 2026-10-14 | 45.1M | 39.8M | −12% | insiders+5pct | 95.8M | 72.3M | 39.8M | 82.8M | 87.0M |
| CBRL | 2026-10-16 | 20.4M | 18.1M | −12% | insiders+5pct | 22.4M | 19.7M | 13.6M | — | — |
| IMRX | 2026-10-08 | 49.2M | 54.8M | +11% | insiders+5pct | 64.7M | 59.6M | 41.6M | — | — |
| HROW | 2026-10-09 | 28.9M | 26.6M | −8% | insiders+5pct | 37.5M | 29.5M | 26.6M | 33.3M | 33.3M |
| CELC | 2026-10-16 | 29.7M | 32.0M | +8% | insiders+5pct | 48.9M | 39.9M | 20.0M | 36.2M | 36.9M |
| TNXP | 2026-10-08 | 14.1M | 15.1M | +7% | insiders+5pct | 17.2M | 17.1M | 13.4M | 11.0M | 12.9M |
| MMED | 2026-10-08 | 28.5M | 26.5M | −7% | insiders+5pct | 281.3M | 26.5M | 26.5M | — | — |
| ALMU | 2026-10-13 | 14.7M | 13.8M | −6% | insiders+5pct | 19.3M | 14.8M | 12.9M | — | — |
| STOK | 2026-10-20 | 45.6M | 48.3M | +6% | insiders+5pct | 64.5M | 60.1M | 23.4M | 58.9M | 59.6M |
| PCRX | 2026-10-08 | 36.2M | 38.3M | +6% | insiders+5pct | 39.9M | 38.3M | 22.4M | 29.4M | 28.5M |
| FDMT | 2026-10-21 | 42.6M | 40.3M | −5% | insiders+5pct | 55.2M | 49.8M | 25.0M | — | — |
| ANAB | 2026-10-20 | 20.4M | 21.3M | +4% | insiders+5pct | 29.9M | 21.3M | 17.9M | 21.0M | 21.7M |
| LEU | 2026-10-14 | 19.2M | 19.7M | +3% | insiders+5pct | 19.9M | 19.7M | 11.7M | — | — |
| HPK | 2026-10-19 | 21.8M | 21.3M | −3% | insiders+5pct | 126.5M | 22.9M | 21.3M | 23.5M | 24.2M |
| IRON | 2026-10-20 | 32.9M | 32.2M | −2% | insiders+5pct | 38.4M | 34.6M | 27.3M | 26.9M | 27.1M |
| DFH | 2026-10-14 | 26.7M | 26.1M | −2% | insiders+5pct | 91.3M | 26.1M | 15.1M | — | — |
| TSSI | 2026-10-15 | 21.6M | 22.0M | +2% | insiders+5pct | 28.2M | 23.4M | 20.1M | 19.9M | 19.7M |
| AVBP | 2026-10-08 | 32.1M | 32.2M | +0% | insiders+5pct | 49.3M | 44.8M | 26.0M | 41.8M | 43.1M |
| MMYT | 2026-10-08 | 29.1M | — | — | none (20-F: no share count) | — | — | — | — | — |

### F1 (b): against the issuer's own figure (dei:EntityPublicFloat)

Implied non-affiliate shares = `val` / the RAW close of the last session on or before `end` (Alpaca
`adjustment=raw`, grouped by date: 36 distinct `end` dates, 3,196 symbols), carried to 2026-10-21 by (i) ratio and
(ii) affiliate count × split factor (raw / split-adjusted close on that day). Issuer-reported, annual and rounded,
and it runs HIGH against finviz's float by definition ("affiliates" are often only officers, directors and 10%
owners), so it is a reference, not ground truth. Median / P90 of |engine / reference − 1|:

| set | n | this PR vs (i) | this PR vs (ii) | PR #28 vs (i) | PR #28 vs (ii) | 13D/13G holders not subtracted, vs (ii) |
|---|---|---|---|---|---|---|
| every universe company with it | 2,792 / 2,780 | 7.9% / 93.7% | 6.5% / 63.1% | 14.9% / 139.4% | 10.7% / 99.9% | 6.3% / 63.5% |
| the 13D/13G-covered sample | 122 | 10.3% / 102.2% | 10.2% / 79.2% | 15.9% / 164.1% | 7.8% / 126.5% | 5.8% / 93.2% |
| the 23 squeeze names | 14 | 18.8% / 51.9% | 17.9% / 34.1% | 38.9% / 410.7% | 32.6% / 195.1% | 13.9% / 32.5% |

Method split over the 2,792: `insiders` 2,505, `insiders+5pct` 202, `outstanding_proxy` 85. As expected from
the definition gap, subtracting the ≥ 5% holders moves the engine *away* from (b) (median 5.8% → 10.2% on the
covered sample) while moving it *toward* Yahoo in (a) (12.8% → 6.4%): (b) treats most 5% holders as non-affiliates.
The P90s are large because (b) is up to 15 months old in a segment that dilutes heavily.

### F1 (c): hand checks against the companies' own filings

The three largest engine-vs-Yahoo disagreements, read in each company's latest proxy statement (DEF 14A
beneficial-ownership table, which also gives the share count of its record date):

1. **BCAX** (engine 48.9M, Yahoo 36.9M, (ii) 51.9M). DEF 14A of 2026-04-27: 65.60M shares; officers and directors
   9.49M (13.8%), of which director K. Mazumdar-Shaw 6.35M through Biocon; ≥ 5%: RA Capital 7.30M (13D), T. Rowe
   Price 6.61M, Biocon 5.52M, Invus 5.22M, Vestal Point 3.58M, FMR 3.35M. Closely held without the 13d-1(b)
   institutions (T. Rowe, FMR): 65.60 − 9.49 − 7.30 − 5.22 − 3.58 ≈ **40.0M**. **Yahoo is closest** (−8%); the engine
   is +22%: the Biocon / Mazumdar-Shaw block has no Form 3/4/5 inside the 8 bulk quarters (the IPO Form 3s were filed
   in 2024q3) and no XML 13D/13G. (ii) is high for the definition reason.
2. **BKV** (engine 36.5M, Yahoo 31.6M, (ii) 37.1M). DEF 14A of 2026-04-28: 109.39M shares; Banpu North America
   63.88M (58.4%) and Banpu Power US 5.32M (4.86%, the same group), officers and directors 2.75M. Closely held:
   109.39 − 63.88 − 5.32 − 2.75 = **37.44M**. **The engine (−2.5%) and (ii) (−1%) are right**; Yahoo is 16% low.
   This check changed the code: the Banpu group's Form 4 lines show 63.9M but its 13D 69.2M, and the engine now
   counts a holder filing both once, at the larger amount (`bb6a24d`; before it, 41.8M).
3. **AVTX** (engine 31.9M, Yahoo 40.1M, (ii) 46.3M). DEF 14A of 2026-04-10 (record date 2026-03-26): 24.50M shares;
   RA Capital, OrbiMed and BVF 9.99% each, FMR 8.68%, Logos 5.24%, Nantahala 5.04%; officers and directors 1.13M.
   The share count has since doubled (53.6M after an offering), so the proxy cannot settle today's float. The
   engine's inputs are as filed: six 13G/As of 2026-05..08 under Rule 13d-1(c) (specialist funds, 5.2–9.9%) totalling
   21.3M. **Undetermined by the filings**: whether 13d-1(c) funds are closely held is a definition choice; Yahoo's
   figure implies it removes only part of them. A 10-Q alone gives the share count, not the float.

**Decision D-053 (from (a) and (c)):** the issuer's figure carried by affiliate count × split factor (ii) becomes the
float fallback `issuer_public_float` where an issuer has no Form 3/4/5 and no 13D/13G holding at all: on (a) it errs
by a median 17.6% against 41.5% for shares outstanding, and on (c) it matched BKV's proxy (37.1M vs 37.44M); (i) has
the better P90 on (a) (32.7%) but understates after dilution by construction (AVTX). It applies to 27 issuers of
the local universe; the 422 left on shares outstanding have no `EntityPublicFloat` (20-F and similar filers).

### F2: squeeze parity by historical replay

`git log -p origin/main -- data/squeeze_last.json` (read only): 174 committed lists, 80 of them regular-session
slots. The replay takes the 16:30 ET list of the 7 latest sessions with one (the first list after the close; the
sessions' 10:30–15:30 lists are mostly empty), and evaluates the engine's
`sh_float_u50,sh_short_o20,sh_avgvol_o500,sh_price_o2,sh_relvol_o2,sh_opt_option` **plus the squeeze's short ratio
> 5 as an engine-only rule** (no finviz code) point in time at that slot: daily bars before the session, its
30-minute bars to 16:14, the FINRA report usable that day (settlement 2026-09-30 for all 7: the 8th-session rule),
holdings filed by then.

| session (16:30 ET list) | squeeze | engine | Jaccard | recall | with PR #28 float (Jaccard / recall) |
|---|---|---|---|---|---|
| 2026-10-21 | FDMT, IRON, STOK | COCO, FDMT, IRON, STOK | 0.750 | 1.000 | 0.000 / 0.000 |
| 2026-10-20 | ANAB, IRON, STOK | ANAB, IRON, SPHR, STOK | 0.750 | 1.000 | 0.000 / 0.000 |
| 2026-10-19 | AVTX, CELC, HPK | AVTX, CELC, HPK, LEU | 0.750 | 1.000 | 0.250 / 0.333 |
| 2026-10-16 | ALMU, CBRL, CELC, DFH | ALMU, CBRL, CELC, DFH, HPK | 0.800 | 1.000 | 0.500 / 0.500 |
| 2026-10-15 | BETR, BKV, DFH, TNXP, TSSI | BETR, BKV, CELC, DFH, LEU, TSSI | 0.571 | 0.800 | 0.000 / 0.000 |
| 2026-10-14 | BCAX, BETR, DFH, LEU, PCRX, TNXP, ZURA | BCAX, BETR, CELC, DFH, GRRR, LEU, PCRX, SPHR, ZURA | 0.600 | 0.857 | 0.250 / 0.286 |
| 2026-10-13 | ALMU, AVBP, BCAX, TNXP | ALMU, AVBP, BCAX, PCRX, SGMT, ZURA | 0.429 | 0.750 | 0.000 / 0.000 |

| | mean Jaccard | mean recall | pooled recall | target |
|---|---|---|---|---|
| **this PR** | **0.664** | **0.915** | 0.897 (26 / 29) | ≥ 0.50 / ≥ 0.80 — **PASS** |
| PR #28's float (shares outstanding; same share-count source) | 0.143 | 0.160 | 0.172 | |

Misses (3, all TNXP on 10-13, 10-14 and 10-15): **float source**. The engine's short float is 19.4% against the
squeeze's > 20%: the engine's float is 15.1M, Yahoo's 14.1M (+7%, F1 (a)). Same FINRA report, same average volume
basis.

Engine-only names (12 in 7 sessions), by the criterion they pass most narrowly — none is ticker-tuned:
- **relative-volume basis** (8): COCO 2.13, LEU 2.03 / 2.09, HPK 2.11, CELC 2.07 / 2.32, PCRX 2.02, SGMT 2.04. The
  squeeze's `rel_vol` is regular-session volume so far / Yahoo's 3-month average (OBSERVED in `bot/screen.py`, read
  only); the engine's is whole-day volume since 04:00 against the time-of-day curve (D-050 static curve here: no
  profile store), so names near 2.0 fall on different sides.
- **float source** (3): SPHR (short float 20.1%, 10-20 and 10-14) and GRRR (20.4%) sit at the 20% edge; their
  13D/13G were not read (method `insiders`), so with them the engine's float would fall and the short float rise;
  the squeeze presumably saw a higher Yahoo float for them.
- **float source or average-volume window** (1): ZURA on 10-13 (short float 23.5%, relative volume 18): the squeeze
  listed it the next day; its Yahoo float (45.1M vs the engine's 39.8M) gives a short float near 20.7% - so the
  10-13 difference is the 3-month average volume (Yahoo) vs 63 sessions before the measured one (engine). Not
  separable from the history file.

### F3: parity screens 6 and 7 regenerated

Same point in time as the checklist (CLOSED after 2026-10-21, FINRA settlement 2026-09-30). finviz pages stay
unloaded (robots.txt): the lists are in [parity-manual-checklist.md](parity-manual-checklist.md).

| # | screen | engine matches, PR #28 | engine matches, this PR | float methods (this PR) |
|---|---|---|---|---|
| 6 | `cap_small,sh_float_u50,sh_short_o20` | 15 | **69** | insiders 54, insiders+5pct 14, outstanding_proxy 1 |
| 7 | `sh_float_u50,sh_short_o20,sh_avgvol_o500,sh_price_o2,sh_relvol_o2,sh_opt_option` | 0 | **6** | insiders+5pct 4, insiders 2 |
