# Recommendation algorithm: phase A.1 (survivorship and 8-K-only coverage)

Spec: `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md` (revision 6), section 7 A.1 items 1–4.
Plan: `docs/superpowers/plans/2026-10-10-recommendation-algorithm-phase-a1-claude.md` (owner's choice; the other plan
file is marked not executed).
Code: `bot/probe_survivorship.py`, readers in `bot/probe_ensemble_data.py`, `bot/shorts.py`; tests in
`tests/test_probe_survivorship.py`.
Raw output: `docs/backtest/survivorship-phaseA1-2026-10-10.json` (full run) and `...-checks.json` (pre-run checks).

**Counts and coverage only.** No return of a ticket or a stock is computed, logged or written. The run's only
output is the JSON report. The only files it writes are raw-data caches: FINRA reports, Alpaca bars and SEC zips.

## Results

| Item | Result |
|---|---|
| 1. Item 2.02 coverage, form type 8-K only | **95.03%** (22,749 / 23,940 universe stock-years). **S2 keeps the 8-K condition** (≥ 90%, threshold fixed in advance) |
| 2. Universe-like stock-months 2018-01 .. 2026-03 | 177,889, of which 1,362 (0.77%) have no Alpaca bar in the month |
| 3. Rename rule | recovers 739 of the 1,362 (54%) → **used** (≥ one third). 19 of the 739 duplicate a company already counted and are left out of f. Of the other 720, 708 have recovering bars that fit the stock-month's own price (review check), so the rule stays used with those alone |
| f | (1,362 − 19 − (739 − 19)) / (177,889 − 19) = 623 / 177,870 = **0.35%**, the same under readings (a) and (b) because the rule is used |
| 4. b = f × 1 R | **0.0035 R ≤ 0.03 R**: the bars can support the test (spec 6.4) |

## The run

| | |
|---|---|
| Pre-run checks | GitHub Actions run 38076826515 (code `1708ce2`), passed |
| Full run | GitHub Actions run 38081309153 (code `b6c6199`, after the independent review), passed, 2,606 s. The earlier full run 38078179088 (code `fd7d557`) gave the same numbers; the review added the rename price check |
| Requests | Alpaca 3,171; SEC quarterly zips 48; FINRA reports 99; submissions.zip once |
| Tests | 499 offline tests, all passing (`tests/test_probe_survivorship.py`: 22) |

## Item 1: item 2.02 coverage, 8-K only

The universe and the stock-years are phase A's, counted by the release's filing year. The 8-K/A column re-runs phase
A's definition and reproduces its 22,750 exactly. The only difference is one 2026 stock-year that was covered by an
8-K/A alone.

| Year | Universe stock-years | With an 8-K item 2.02 | Share | With 8-K or 8-K/A |
|---|---:|---:|---:|---:|
| 2017 | 2,149 | 2,067 | 96.2% | 2,067 |
| 2018 | 2,224 | 2,152 | 96.8% | 2,152 |
| 2019 | 2,176 | 2,094 | 96.2% | 2,094 |
| 2020 | 2,392 | 2,252 | 94.2% | 2,252 |
| 2021 | 2,766 | 2,633 | 95.2% | 2,633 |
| 2022 | 2,526 | 2,426 | 96.0% | 2,426 |
| 2023 | 2,339 | 2,258 | 96.5% | 2,258 |
| 2024 | 2,382 | 2,304 | 96.7% | 2,304 |
| 2025 | 2,531 | 2,408 | 95.1% | 2,408 |
| 2026 (Q1) | 2,455 | 2,155 | 87.8% | 2,156 |
| **Overall** | **23,940** | **22,749** | **95.03%** | **22,750** |

## Items 2–3: universe-like stock-months, the rename rule, f per year

A stock-month is universe-like when all four conditions hold:
- the symbol is on a Form 3/4/5 filed in the 365 days through the month's end;
- it is in the FINRA short-interest report with the latest settlement on or before the month's end, with average daily share volume V;
- its latest Form 4/5 market-price transaction (codes P, S, F, price > 0) in those 365 days gives P ≥ $5;
- V × P ≥ $10M.

"Duplicates" are recovered stock-months whose other symbol is itself universe-like with bars that month (the same
company counted twice). They are left out of the numerator and the denominator of f. The rule is decided once on the
whole period, so per-year f uses that decision.

| Year | Universe-like | With bars | Without bars | Recovered | Duplicates | f (a) | f (b) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2018 | 20,813 | 20,438 | 375 | 190 | 12 | 0.89% | 0.89% |
| 2019 | 19,738 | 19,446 | 292 | 148 | 2 | 0.73% | 0.73% |
| 2020 | 19,988 | 19,795 | 193 | 93 | 0 | 0.50% | 0.50% |
| 2021 | 23,368 | 23,213 | 155 | 107 | 0 | 0.21% | 0.21% |
| 2022 | 22,488 | 22,357 | 131 | 83 | 0 | 0.21% | 0.21% |
| 2023 | 20,695 | 20,599 | 96 | 55 | 0 | 0.20% | 0.20% |
| 2024 | 21,633 | 21,566 | 67 | 36 | 0 | 0.14% | 0.14% |
| 2025 | 23,042 | 22,996 | 46 | 23 | 4 | 0.10% | 0.10% |
| 2026 (Q1) | 6,124 | 6,117 | 7 | 4 | 1 | 0.05% | 0.05% |
| **Overall** | **177,889** | **176,527** | **1,362** | **739** | **19** | **0.35%** | **0.35%** |

(a) = when the rename rule is not used, nothing counts as recovered (spec 7 A.1 item 3, owner's decision; b uses it).
(b) = recovered stock-months always subtracted. The two agree here because the rule is used.

### Are the recoveries the same company? (review check)

The rename rule only asks whether the CIK's other symbol has a bar in the month. An independent review pointed out
that those bars can belong to another security that held the ticker earlier. For example, `META` had bars in 2021,
when the ticker belonged to an ETF. So the run counted, for each recovery that is not a duplicate, whether the
stock-month's own price P is within a factor of 2 of the recovering symbol's raw closes that month. This is a
sensitivity count. The rule itself is unchanged.

| Recoveries (not duplicates) | Price-consistent | Price-inconsistent | Rule used with consistent ones only | f with consistent ones only | b within 0.03 R |
|---:|---:|---:|---|---:|---|
| 720 | 708 (98%) | 12 | yes (708 ≥ 1,343 / 3) | 0.357% | yes |

The rule's decision and b do not depend on the 12 doubtful recoveries. Even with no recoveries at all, f would be
1,343 / 177,870 = 0.76% and b = 0.0076 R.

The missing stock-months fall from 0.89% in 2018 to 0.05% in 2026. This is the expected direction: the further back,
the more of the listed names have since been delisted, renamed or merged.

## Bars under a renamed company's old symbol

Spec section 2 says bars are keyed by today's ticker. The run tested this on every clean symbol change: a CIK with
exactly two filed symbols, where the old symbol was filed only before the new one's first filing. Each change is
dated by that first filing. The run counted which symbol has raw Alpaca bars in a month before the change.

| Symbol changes | Old symbol has bars before | New symbol has bars before | Both | Neither |
|---:|---:|---:|---:|---:|
| 1,169 | 778 (67%) | 643 (55%) | 591 | 339 |

**Finding:** in 778 of 1,169 changes (67%), the old symbol still has bars before the change (FB in 2021 is one case).
So bars are often kept under the symbol as traded, not only under today's ticker. The known-bias text of spec section 2
("bars are keyed by today's ticker") is stricter than what the data shows. Changing it is left to the owner.

The other columns support less than they seem to:
- **"New symbol has bars before the change" (643)** often means the ticker belonged to another security before. META,
  an ETF ticker in 2021, is one example. These rows are not evidence that Alpaca re-keys a company's history.
- **"Neither" (339) is partly built in.** Bars start in 2016-01, so a change in 2015 cannot show bars before it.
  Names that traded off-exchange before the change have no bars. The change is dated by the new symbol's first
  filing, which can come after the actual change.
- **Reused tickers can distort the clean-change test.** A symbol's filings are taken from every CIK that used it, so
  a reused ticker can change which symbol counts as old and whether the change counts as clean.

## Data gaps and checks

| | |
|---|---|
| FINRA reports chosen (one per month) | 99; none missing or older than 31 days |
| FINRA rows with average daily volume | 775,219 of 780,368 (99.3%); no report without volume. The spec's stop condition is not met |
| Filed-symbol months not in the FINRA report | 114,956 (OTC or unlisted names, or a spelling FINRA writes differently) |
| Filed-symbol months whose FINRA row has no volume | 212 (not universe-like) |
| Insider data sets | 2015Q1 .. 2026Q1 (45 quarters) |
| Symbols as filed / with raw Alpaca bars | 14,035 / 10,508 |
| Filings with an unusable symbol | 41,596 |
| Pre-run spelling check | `BRK.B` found in FINRA's 2024-06 report (FINRA writes class shares as `BRKB`) |

## Decisions applied (owner, 2026-10-10)

1. The 365-day window includes the month's end.
2. The FINRA report is the one with the latest settlement date on or before the month's end. No publication lag is applied, because this is a count of listed stocks and not a trading decision.
3. The price P comes from transaction codes P, S and F only. Exercises and conversions (M, X, C) report the strike, and every other code is ignored. Forms are 4 and 5, amendments are ignored, and the price must be > 0. P is the latest by filing date, then trade date, then accession.
4. "Its CIK" is the CIK of the symbol's latest filing in the window. Recovery needs the CIK's one other symbol with bars to have a bar in the month. Duplicates leave f's numerator and denominator and the one-third test.
5. "A bar in the month" means a session in the raw Alpaca bars in that calendar month.
6. If the rename rule is not used, nothing counts as recovered (spec 7 A.1 item 3).

Implementation choices confirmed by the owner:
- Only the latest market-price transaction per symbol and filing month is kept. This gives the same answer for a month-end query.
- The pre-run checks include the 8-K versus 8-K/A coverage comparison.

## Limitations

1. **The measurement covers listed names only.** The universe-like definition needs the symbol in FINRA's
   exchange-listed classes (NNM, SC, NYSE, AMEX) on the month's end, so a delisted stock leaves the count when it
   leaves the exchange. What f measures is the share of stocks that were listed and liquid in their month that
   Alpaca's bars miss. Bars that end before a stock's real last trading day are not measured.
2. **FINRA spelling.** A filed symbol counts only when FINRA writes it as `X-Y`, `XY` or the same symbol. Other
   spellings fall into the 114,956 "not in the FINRA report" stock-months, which are left out of both f's numerator
   and its denominator.
3. **V is FINRA's average daily share volume of the report's own period** (about two weeks), and P is an insider's
   transaction price, not a close. Both are what the spec defines; neither is Alpaca data.
4. **The rename rule's recovery means "the CIK's one other symbol has a bar in the month".** That bar can belong to
   a share class of the same issuer, or to another security that held the ticker earlier. The 19 duplicates are
   the cases where both symbols are universe-like that month. The price check finds 12 of the other 720 recoveries
   inconsistent with the stock-month's own price.
7. **Exchange classes.** FINRA's NNM, SC, NYSE and AMEX classes leave out stocks listed only on NYSE Arca or Cboe.
   Those stocks are outside both sides of f.
5. **2026 is one quarter** (coverage 87.8%, 6,124 stock-months).
6. **b assumes each missing ticket loses its full risk against its pool** (spec 6.4). It is an allowance, not a
   measurement of returns.

## Independent review

A reviewer who did not write the code reviewed the code, tests, coverage and this report (2026-10-10):
- no critical finding;
- no return computed or written;
- every number matches the JSON, and f recomputes to 623 / 177,870.

Two major findings:
- **M1:** a rename recovery can rest on another security's bars. This is measured above, without changing the rule.
- **M2:** this report over-read the renamed-symbol table. The section above is rewritten.

Neither changes the S2 decision, the rename-rule decision or b ≤ 0.03 R.

Minor findings not acted on, for the owner:
1. When filing date, trade date and accession are all equal, the higher price wins.
2. Prices are matched by symbol and not by CIK, so a ticker reused within the 365-day window mixes two companies.
3. A spelling collision is possible: `AB.C` becomes `ABC` in FINRA's form.
4. Two symbols without bars that recover to the same other symbol both count.
5. The rename rule is applied per stock-month, which is broader than the spec's "a symbol as filed has no bars".
6. `keeps_8k` compares a share already rounded to four places. This has no effect at 95.03%.

## Next

Phase B is not started. It needs the owner's explicit approval.
