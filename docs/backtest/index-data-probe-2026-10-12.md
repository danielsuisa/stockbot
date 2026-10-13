# Index program, phase 1: data probe (spec 8.1)

Spec: `docs/superpowers/specs/2026-10-12-index-program-design.md`, sections 3 and 8.1. Probe code:
`bot/index_probe.py`, with synthetic tests in `tests/test_index_probe.py`. GitHub run: `38271895893` (temporary
workflow on the branch `index-data-probe`).

**Blind rule.** Nothing here computes a return, profit, equity curve or drawdown of any rule or policy. The only
comparisons are price agreement between sources, which the spec allows.

**Result: no SPY source passes the acceptance rule of 8.1.** The probe stops here, as 8.1 says.

## Sources

| Source | Where tried | Status | First day | Last day | Rows | Prices | Storing (terms) |
|---|---|---|---|---|---|---|---|
| Stooq SPY / BIL CSV | this machine | 200, an HTML page: a JavaScript proof-of-work bot check instead of the CSV | — | — | — | not reached | Terms page not reachable (behind the same wall) |
| Stooq SPY / BIL CSV | GitHub runner | `URLError` (no answer) | — | — | — | not reached | — |
| FRED `DTB3` CSV | this machine | no answer (timeout) | — | — | — | — | — |
| FRED `DTB3` CSV | GitHub runner | 200, CSV | 1993-12-01 | 2026-09-30 | 8,566 | rate, % | **Not allowed:** FRED's terms forbid users to "store, cache, or archive" FRED content, whatever the series' copyright status |
| Yahoo chart SPY (unofficial) | both | 200, JSON | 1993-01-29 | 2026-09-30 | 8,475 | raw OHLC plus `adjclose`; 136 dividends | **Not allowed:** Yahoo's terms forbid automated collection and redistribution. Compared in memory only, never written |
| Yahoo chart BIL | GitHub runner | 200, JSON | 2007-05-30 | 2026-09-30 | 4,866 | raw plus `adjclose`; 127 dividends | Not allowed; not written |
| Alpaca SPY daily, `adjustment=all` | GitHub runner (repo secrets) | 200 | 2016-01-04 | 2026-09-30 | 2,701 | split and dividend adjusted | Not committed (exchange-licensed data); compared in memory only |
| Tiingo | not tried | needs a key (sign-up) | — | — | — | — | named as an option for the owner |

Notes:
- **Dividends.** Yahoo gives raw prices plus `adjclose`. The probe adjusts open, high and low by `adjclose / close`
  of the same day. Alpaca's `adjustment=all` adjusts for splits and dividends. Stooq's handling could not be
  checked.
- **`DTB3`** has 352 missing days (".") between 1993-12-01 and 2026-09-30, all of them bond-market holidays; the
  first are 1993-12-24, 1994-01-17 and 1994-02-21. Under 8.1, a missing day takes the previous value.
- **The FRED file** (sha256 `5ea29eadd254056ad0529cc92cb50a160001c7be1ef8c01e8577d4d45a893adc`) exists only as an
  artifact of run 38271895893. It was not committed. Nothing was deleted; the artifact expires under GitHub's
  retention.

## Acceptance (8.1)

| Check | Candidate | Numbers | Result |
|---|---|---|---|
| A full-history source | Yahoo (only one reached) | 1993-01-29 to 2026-09-30, 8,475 days | Reached, but may not be stored |
| 1. Integrity | Yahoo | Raw prices: 0 non-positive, 0 days with open or close outside [low, high]. Adjusted prices: 27 days outside by floating-point rounding only (all within 1e-9 relative, e.g. 1993-03-04) | Pass on raw prices; see question 2 |
| 1. Same trading days | Yahoo vs Alpaca, 2016-2026 | 2,701 against 2,701; none only in one | Pass |
| 2. Against Alpaca, 2016-01-04 to 2026-09-30 | Yahoo | Close-to-close change within 0.0005 on **93.59%** of days (need 99.5%); open-to-close within 0.0015 on **98.85%** (need 99%) | **Fail** |
| 3. Against a second full-history source, 1993-2015 | — | No second full-history source reached (Stooq blocked) | **Stop** (8.1: the owner decides) |

Largest differences in check 2:
- **Close-to-close change:** 0.0269 on 2020-03-13, 0.0264 on 2020-03-12, 0.0221 on 2020-03-18, 0.0185 on
  2020-03-19 and 0.0080 on 2020-03-10.
- **Open-to-close ratio:** 0.0288 on 2020-03-12, 0.0180 on 2020-03-18, 0.0136 on 2016-11-07, 0.0089 on
  2017-05-17 and 0.0076 on 2020-03-09.

The pattern is a hypothesis, not verified: the large gaps fall on the most volatile days. That fits Alpaca's daily
bar ending with extended-hours trades rather than the official closing auction.

## What the owner can supply by hand (8.1: no source passed)

1. **SPY, full history**, daily open, high, low and close, with dividends or adjusted prices, 1993-01-29 to
   2026-09-30, from a source whose terms allow keeping it. The simplest is Stooq's own CSV, downloaded in a
   browser: `https://stooq.com/q/d/l/?s=spy.us&i=d`. A second full-history file is needed for check 3, for example
   the same series from a broker export or from Tiingo (free tier, needs the owner's sign-up).
2. **The bill rate.** FRED's terms forbid storing its data. The same series comes from its originator, the
   Federal Reserve Board's H.15 release (3-month Treasury bill, secondary market, discount basis, business days),
   through the Board's Data Download Program. It is published by a federal agency; its terms should still be
   confirmed before it is committed.

## Second round (Amendments 1 and 2, 2026-10-13): stopped before the acceptance checks

**Tiingo was dropped under Amendment 3, and nothing from it is kept.** Runs 38381913765 and 38382019663 used
Tiingo; their artifacts were deleted on the owner's instruction.

The other set-up of this round stands:
- the deploy key `DATA_REPO_KEY` cloned the private repository `danielsuisa/stockbot-data`;
- the Stooq file there is `spy_us_d.csv`: 287,403 bytes, `;` separated, sha256
  `3493247f082c56292780c23c9f9272f39da5459b0eb10525e66b7b3ab6baca6f`, 5,440 rows from 2005-02-25 to 2026-10-12,
  0 non-positive prices and 0 days outside [low, high];
- the Board's H.15 zip answered 200.

### Tiingo's terms

The terms are at `https://app.tiingo.com/tos` ("Last Updated Date" October 6th, 2026; effective January 16th,
2022). The tool used here would not reproduce whole clauses, so they are cited by number with short quotes, for
the analyst to read in full:
- **1.6(a), Starter and Trial plans:** the user may "not write, save, archive, back up, or otherwise retain Tiingo
  Data" in persistent storage. Processing is transient only, in volatile memory or a temporary cache. As summarised
  here, the same limits apply to Derived Products.
- **1.6(c), Derived Products:**
  - What may be kept: results that are not a substitute for Tiingo Data and cannot reasonably be reversed to
    recover it.
  - Examples: aggregate statistics are permitted; rebased price series and simple reformatting are not.
  - On Starter and Trial plans, Derived Products may be held only transiently.
- **1.4(h):** no "publishing or otherwise making available to the public any analysis of" the Service or Tiingo
  Data, except Derived Products permitted under 1.6.
- **5.3:** among other things, no systematic retrieval of data to build a compilation or database.
- **7.3:** "All data via the API is for internal consumption only"; redistribution needs permission and fees.

**Reading against Amendment 2:**
- **B1, in-memory use:** keeping Tiingo rows in memory and dropping them fits 1.6(a).
- **B1, the committed fingerprint:** the per-year sha256 is derived from Tiingo Data. It cannot be reversed and
  substitutes for nothing, so it meets the tests of 1.6(c). But 1.6(c) as summarised lets Starter users hold
  Derived Products only transiently, and committing the hashes is persistent.
- **B5, the public report:** it publishes aggregate statistics computed from Tiingo Data: growth rates,
  drawdowns and yearly returns. Aggregate statistics are the permitted kind of Derived Product. But 1.4(h)
  allows publishing only Derived Products permitted under 1.6, and for Starter users 1.6 permits them only
  transiently.
- **On this reading,** both B1's committed fingerprint and B5's public report conflict with the free plan's
  terms. This is Claude Code's reading of a summary, not legal advice, and the analyst should check the full text.

## Third round (Amendment 3, 2026-10-13): stopped by C3, checks 2 and 3 failed

The run was `38409938767`, on the temporary branch `index-data-probe`, triggered by a push only:
- the Stooq file was read from `danielsuisa/stockbot-data` with the deploy key;
- Alpaca's raw and adjusted SPY bars were held in memory only;
- H.15 was downloaded from the Board;
- the artifact holds `probe.json` (no price), the H.15 file and the manifest.

The run ended with exit 5: a calendar difference that no documented closure explains.

### Sources

| Source | State |
|---|---|
| Stooq `spy_us_d.csv` (private repository) | sha256 equals C1's (`3493247f…ca6f`); 5,440 rows, 5,432 used (2005-02-25 to 2026-09-30) |
| Alpaca SPY, raw and adjusted (memory only) | 200; 2,701 rows each, 2016-01-04 to 2026-09-30 |
| H.15 zip (Board) | 200; zip sha256 `bb2e439536b7312483db8dafd4770509e1c34dcef692e14e4797bd16d051f24a` |

### C3, check by check

| Check | Numbers | Result |
|---|---|---|
| 1. Integrity (relative tolerance 1e-9) | 5,432 rows; 0 non-positive; 0 days with open or close outside [low, high]; dates in order | **Pass** |
| 2. Calendar, 2005-02-25 to 2026-09-30 | File 5,432 days, exchange calendar 5,433. In the calendar, not in the file: **2011-02-17** (a Thursday; no documented closure). In the file, not in the calendar: none | **Fail** (1 unexplained) |
| 3. Dividend adjustment, against Alpaca, 2016-01-04 to 2026-09-30 | The file's change over the span differs from Alpaca's adjusted change by **0.87%** relative (need at most 1%: holds). Ex-dividend days (raw and adjusted changes differ by more than 0.001): **41**, all quarterly, from 2016-06-17 to 2026-09-18. On **35 of 41 (85.4%)** the file's change is nearer the adjusted change (need 90%) | **Fail** |
| 4. Gross errors, against Alpaca's adjusted bars | The same 2,701 trading days. Close-to-close change within 0.005 on **99.74%**; open-to-close ratio within 0.005 on **99.81%** (need 99% each) | **Pass** |
| 5. H.15 (as A3.4) | 1993-12-01 to 2026-09-30: **8,566 rows, 352 missing** (the same counts as the FRED copy) | Reported |

The 10 largest differences of check 4:

| Close-to-close change | Open-to-close ratio |
|---|---|
| 2020-03-13: 0.026875 | 2020-03-12: 0.028829 |
| 2020-03-12: 0.026312 | 2020-03-18: 0.018037 |
| 2020-03-18: 0.022107 | 2020-03-09: 0.007655 |
| 2020-03-19: 0.018462 | 2020-03-17: 0.005237 |
| 2020-03-10: 0.007983 | 2020-03-16: 0.005205 |
| 2020-03-09: 0.007190 | 2020-03-24: 0.004600 |
| 2016-03-18: 0.005297 | 2020-03-13: 0.003225 |
| 2020-03-25: 0.004763 | 2020-03-20: 0.002876 |
| 2018-06-15: 0.004627 | 2020-03-27: 0.002273 |
| 2020-03-24: 0.003835 | 2018-02-20: 0.002178 |

### Information (C3.6)

**Close-to-close changes above 5% in size** (35 days):
- **2008:** 2008-09-29 −7.85%, 10-06 −5.10%, 10-09 −6.98%, 10-13 +14.52%, 10-15 −9.84%, 10-20 +6.01%,
  10-22 −5.45%, 10-24 −5.07%, 10-28 +11.69%, 11-06 −5.54%, 11-13 +6.23%, 11-19 −6.41%, 11-20 −7.42%, 11-21 +5.40%,
  11-24 +6.93%, 12-01 −8.86%.
- **2009:** 2009-01-20 −5.28%, 03-10 +5.96%, 03-23 +7.18%.
- **2011:** 2011-08-08 −6.53%.
- **2018:** 2018-12-26 +5.05%.
- **2020:** 2020-03-09 −7.82%, 03-10 +5.18%, 03-12 −9.56%, 03-13 +8.55%, 03-16 −10.95%, 03-17 +5.41%,
  03-18 −5.07%, 03-24 +9.06%, 03-26 +5.84%, 04-06 +6.72%, 06-11 −5.76%.
- **2022:** 2022-11-10 +5.49%.
- **2025:** 2025-04-04 −5.85%, 04-09 +10.50%.

**Share of days whose open equals the previous close, per year:**

| Year | Share |
|---|---|
| 2005 | 2.34% |
| 2006 | 4.78% |
| 2007 | 1.59% |
| 2008 | 0.40% |
| 2009 | 0 |
| 2010 | 0.40% |
| 2011 | 0.80% |
| 2012 | 1.60% |
| 2013 | 0.79% |
| 2014 | 1.19% |
| 2015 | 0 |
| 2016 | 0.40% |
| 2017 | 0.40% |
| 2018 | 0 |
| 2019 | 0.40% |
| 2020 | 0 |
| 2021 | 0 |
| 2022 | 0 |
| 2023 | 0.40% |
| 2024 | 1.19% |
| 2025 | 0 |
| 2026 | 0 |

### Files

| File | Rows | First | Last | sha256 | Where |
|---|---|---|---|---|---|
| `spy_us_d.csv` (Stooq) | 5,440 (5,432 used) | 2005-02-25 | 2026-10-12 | `3493247f082c56292780c23c9f9272f39da5459b0eb10525e66b7b3ab6baca6f` | private repository `danielsuisa/stockbot-data` |
| `h15-3m-tbill.csv` (Board, H.15) | 8,566 | 1993-12-01 | 2026-09-30 | `9306eed722fe9627e56ab097eb53cead1932e7f831a6186eaaf036a2b4b877cf` | `data/index/` in this repository, with `manifest.json` |

**Stop (C3: "If check 1, 2, 3 or 4 fails, stop and report").** The plan for phases 2 to 4 is not written. The two
failures are for the owner to decide:
1. **Check 2:** one trading day, 2011-02-17, is missing from the Stooq file.
2. **Check 3:** the file follows the adjusted series over the span (within 0.87%), but on 6 of the 41 ex-dividend
   days its change is nearer Alpaca's raw change. The probe does not list which 6 days. Listing them is a
   price-agreement diagnostic that could be added on request.

## Fourth round (Amendment 4, 2026-10-13): checks 1 to 4 pass

Run `38414306529` on the temporary branch `index-data-probe`, with the same sources and safeguards as the third
round. Exit 0.

| Check | Numbers | Result |
|---|---|---|
| 1. Integrity | 5,432 rows; 0 non-positive; 0 outside [low, high] (tolerance 1e-9); dates in order | **Pass** |
| 2. Calendar, with D1 | File 5,432 days against 5,433 on the exchange calendar. The only difference is 2011-02-17, absent from the file and accepted by D1. Nothing else | **Pass** |
| 3. Dividend adjustment (D2), 2016-01-04 to 2026-09-30 | The file's r against the r of Alpaca's adjusted bars: **−0.6791%** (need at most 1% in size). Against the r of Alpaca's raw bars: **+16.9757%** (need more than 5% in size). Signs are (file's r / other r) − 1 | **Pass** |
| 4. Gross errors | The same 2,701 trading days. Change within 0.005 on 99.7407%; open-to-close within 0.005 on 99.8149% | **Pass** |
| 5. H.15 | 8,566 rows, 352 missing. The file is unchanged: sha256 `9306eed7…77cf`, as committed | Reported |

### D2, information (decides nothing)

**Per calendar year, the file's change less the change of Alpaca's adjusted bars:**

| Year | Difference |
|---|---|
| 2016 | +0.005780 |
| 2017 | −0.000317 |
| 2018 | +0.003926 |
| 2019 | +0.000856 |
| 2020 | −0.001175 |
| 2021 | +0.001317 |
| 2022 | +0.000033 |
| 2023 | −0.000010 |
| 2024 | +0.000009 |
| 2025 | −0.010161 |
| 2026 (to 2026-09-30) | −0.008793 |

**The ex-dividend days on which the file was nearer Alpaca's raw change** (6 of 41), each with the sum over the
five trading days centred on it of (file's change − adjusted change):

| Day | Five-day sum |
|---|---|
| 2025-06-20 | −0.002945 |
| 2025-09-19 | −0.002777 |
| 2025-12-19 | −0.002982 |
| 2026-03-20 | −0.002735 |
| 2026-06-18 | −0.002586 |
| 2026-09-18 | −0.002485 |

These are the six most recent ex-dividend days, one after another, and each sum is about the size of a quarterly
dividend. Together with the 2025 and 2026 rows of the yearly table, this suggests the file is not adjusted for the
dividends paid since mid-2025. This is an observation, not a check. The 2016 and 2018 rows include ex-dividend
days on which Alpaca's own adjustment is in doubt (Amendment 4, the reason, on 2016-03-18).

The two lists of C3.6 are unchanged from the third round.

All four checks pass, so the plan for phases 2 to 4 was written: `docs/superpowers/plans/2026-10-12-index-program.md`.
