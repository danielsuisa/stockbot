# Recommendation algorithm phase A.1 (survivorship and 8-K-only coverage) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure phase A.1 of spec section 7 with counts only:
1. item 2.02 coverage with form type 8-K only, and the 90% decision;
2. universe-like stock-months without bars;
3. what the rename rule recovers;
4. b = f × 1 R against 0.03 R.

**Architecture:**
- A new runner `bot/probe_survivorship.py` holds the pure counting functions, `run()` and `main()`.
- It reuses three existing modules:
  - `bot/probe_ensemble.py`: universe, coverage, the 90% rule;
  - `bot/probe_ensemble_data.py`: insider readers, the item 2.02 reader, bars;
  - `bot/shorts.py`: FINRA reports and symbol spelling.
- Two readers are added to `bot/probe_ensemble_data.py`: Form 4/5 transaction prices, and a forms filter on the item 2.02 reader.
- Live runs happen on GitHub Actions from a throwaway branch, as in phase A. Alpaca keys and SEC_UA exist only as secrets there.

**Tech Stack:** Python 3.11 stdlib; unittest with the network mocked.

**Spec:** `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md` (revision 6, commit `36f3223`): section 2 (data, renames, survivorship), section 6.4 (b), section 7 A.1.

## Global Constraints

- Counts and coverage only. No return of a ticket or a stock is computed, printed, logged or saved, including in temporary files. Nothing from phases B to E. No merge.
- Item 2.02 releases: form type `8-K` only. `8-K/A` is ignored (spec section 2).
- Coverage rule: S2 keeps the 8-K condition iff overall 8-K-only coverage ≥ 90% (`pe.keeps_8k`, unchanged). Coverage is reported per year too.
- Stock-months run from `2018-01` to `2026-03`, 99 months.
- Universe-like stock-month (spec section 7 A.1 item 2). All four conditions must hold:
  - the symbol is an issuer symbol on a Form 3/4/5 filed in the 365 days before the month's end;
  - it is in the latest FINRA short-interest report before the month's end, with average daily share volume V;
  - its latest Form 4/5 non-derivative transaction price P of those 365 days is ≥ $5;
  - V × P ≥ $10M.
- Rename rule (spec section 2): the symbol has no bars, and its CIK has exactly one other filed symbol with bars. The rule is used iff it recovers ≥ 1/3 of the universe-like stock-months without bars.
- f = (stock-months without bars not recovered) / all universe-like stock-months. b = f × 1 R. **The limit is b ≤ 0.03 R**; above it the result is NO-GO ("data").
- If FINRA's reports carry no volume field, stop and report. Do not substitute another definition.
- Symbols are normalised as in phase A (`pd.normalise_symbol`, e.g. `BRK.B`). FINRA writes class shares as `BRKB` or `BRK-B`.
- Artifacts are in English. The reply to the owner is in Hebrew (RTL).

## Decisions this plan makes where the spec is silent

Owner (2026-10-10): decisions 1, 2 and the price rule of 3 approved; 6 settled as (a) and written into spec section 7
A.1 item 3. The rest of 3, and 4 and 5, are listed in the pre-run report for confirmation before the full run.

1. **The 365-day window** is `month_end − 365 days ≤ filed ≤ month_end`, where month_end is the month's last calendar day. The same window applies to the Form 3/4/5 symbol and to the latest price.
2. **"Latest FINRA report before the month's end"** is the report with the latest settlement date ≤ month_end. FINRA's publication lag is not applied, because this is a count of listed stocks and not a trading decision.
3. **"Latest Form 4/5 transaction price"** is the latest by filing date, then trade date, then accession. Form types are `4` and `5`; amendments are ignored, as for S1. Only lines with a price > 0 count, because grants and gifts carry $0 and are not prices. Any transaction code counts.
4. **"Its CIK"** for the rename rule is the CIK of the symbol's latest Form 3/4/5 filing in the window.
   - The "other filed symbol" is any symbol that CIK filed under, at any time in the insider data.
   - A stock-month is recovered only if that other symbol has a bar in the same month.
   - A recovered stock-month whose other symbol is itself universe-like with bars in that month is also counted as "a duplicate of a stock-month with bars".
5. **A stock-month "with an Alpaca bar in the month"**: the symbol's raw Alpaca bars (`alpaca.daily`, SIP) include at least one session in that calendar month.
6. **Settled (owner, 2026-10-10; spec section 7 A.1 item 3): (a).** What f is when the rename rule is not used (it recovers less than a third). Two readings:
   - (a) f = without bars / all, because a rule that is not used recovers nothing.
   - (b) f = (without bars − recovered) / all always, the literal text of item 3.

   Both are computed and reported; b uses **(a)**.

## Review Focus

1. A FINRA row with `averageDailyVolumeQuantity` null or 0 must not count as universe-like or crash. Such rows are counted as "FINRA row without volume".
2. A FINRA report where every row lacks volume must stop the run. A missing report for a month must leave that month with 0 universe-like stock-months and be listed in the gaps.
3. Class shares must match between FINRA and the insider spelling: `BRKB` / `BRK-B` ↔ `BRK.B`.
4. A symbol filed under several CIKs in the window takes the CIK of its latest filing. A CIK with two other symbols with bars does not recover anything.
5. The 8-K-only coverage must reproduce phase A's 95.03% (22,750 / 23,940) when `8-K/A` is included. This proves the only change is the form filter.

---

### Task 1: The item 2.02 reader with a forms filter

**Files:**
- Modify: `bot/probe_ensemble_data.py` (`earnings_releases`)
- Test: `tests/test_probe_ensemble.py` (class `Submissions`)

**Interfaces:**
- Produces: `earnings_releases(zip_path, ciks, start, end, forms=("8-K",)) -> {cik: sorted [(accession, filing date)]}`. Phase A's call site in `bot/probe_ensemble.py` passes nothing and so becomes 8-K only (spec revision 6).

- [ ] **Step 1: Write the failing test** `test_earnings_releases_8k_only`.
  - Fixture: a CIK JSON with an `8-K` (items `2.02,9.01`), an `8-K/A` (items `2.02`) and an `8-K` (items `7.01`).
  - Assert: the default returns only the first.
  - Assert: `forms=("8-K", "8-K/A")` returns the first two.
- [ ] **Step 2: Run it.** Expect FAIL: the 8-K/A is returned by default.
- [ ] **Step 3: Implement.** The filter becomes `form in forms`. Update the docstring: "8-K/A ignored by default (spec revision 6)".
- [ ] **Step 4: Run the full suite.** Run `SEC_UA= PYTHONUTF8=1 python <scratchpad>/run_tests.py`. Expect OK.
- [ ] **Step 5: Commit.** Message: "Phase A.1: item 2.02 releases are 8-K only by default (spec revision 6)".

### Task 2: Readers for transaction prices and FINRA reports

**Files:**
- Modify: `bot/probe_ensemble_data.py`
- Create: `bot/probe_survivorship.py`
- Test: `tests/test_probe_survivorship.py` (new; reuses the zip/fixture helpers by importing them from `tests.test_probe_ensemble`)

**Interfaces:**
- Produces in `bot/probe_ensemble_data.py`: `transaction_prices(z) -> [(filed ISO, symbol, trade_date ISO, accession, price)]`.
  - Rows come from NONDERIV_TRANS joined with SUBMISSION.
  - Document types are `4` and `5` only.
  - A row needs a price > 0 and a usable `normalise_symbol`.
- Produces in `bot/probe_survivorship.py`:
  - `finra_report(date) -> {FINRA symbol: shorts.slim row}`, cached gzip JSON under `.cache/finra/{date}.json.gz`, via `shorts.report`.
  - `finra_spellings(symbol) -> (str, str)`, e.g. `"BRK.B"` gives `("BRK-B", "BRKB")`.
  - `finra_row(report, symbol) -> row | None`.
  - `volume_check(reports: {date: report}) -> {"reports", "rows", "rows with volume", "reports without any volume": [dates]}`.

- [ ] **Step 1: Write the failing tests.**
  - `test_transaction_prices`:
    - Form 4 lines at $12 (code S) and $0 (code A, a grant) give one row at 12.0.
    - A Form 5 line at $7 is kept.
    - A `4/A` line and a Form 3 line are dropped.
    - A line on a filing whose symbol is `NONE` is dropped.
  - `test_finra_spellings_and_row`:
    - `finra_spellings("BRK.B") == ("BRK-B", "BRKB")` and `finra_spellings("AAPL") == ("AAPL", "AAPL")`.
    - `finra_row({"BRKB": r}, "BRK.B") is r`.
    - `finra_row({}, "AAPL") is None`.
  - `test_volume_check`:
    - Report 1 has rows with adv 1000 and None.
    - Report 2 has only adv None.
    - Assert rows 3, with volume 1, and "reports without any volume" `["2018-01-12"]` (report 2's date).
  - `test_finra_report_cached`: the second call with the same date makes no request (`shorts._post` mocked, call count 1 across both calls).
- [ ] **Step 2: Run.** Expect FAIL (names undefined).
- [ ] **Step 3: Implement** the four functions with the signatures above. Reuse `shorts.norm` for the FINRA spelling and `alpaca._read` / `alpaca._write` for the cache.
- [ ] **Step 4: Run the full suite.** Expect OK.
- [ ] **Step 5: Commit.** Message: "Phase A.1: Form 4/5 transaction prices, cached FINRA reports, the volume-field check".

### Task 3: Universe-like stock-months, bars, the rename rule, f and b

**Files:**
- Modify: `bot/probe_survivorship.py`
- Test: `tests/test_probe_survivorship.py`

**Interfaces:**
- Consumes:
  - `pd.index(rows)`, which returns `({sym: sorted filing dates}, {sym: {ciks}})`;
  - the `symbol_filings` rows `(filed, cik, sym)`;
  - `transaction_prices` rows;
  - `finra_row`.
- Produces:
  - `MONTHS = ("2018-01", "2026-03")`, `MIN_PRICE, MIN_DOLLAR_VOLUME, WINDOW_DAYS = 5.0, 10_000_000.0, 365`, `RENAME_SHARE, B_MAX = 1 / 3, 0.03`.
  - `month_ends(first, last) -> [(month "YYYY-MM", last calendar day ISO)]`.
  - `latest_before(sorted_items, end) -> item | None`, where `sorted_items` is a list of tuples sorted by their first element (an ISO date) and the result is the last one with date ≤ end.
  - `universe_like(month_end, symbols_filed, prices, finra) -> {symbol: {"V": float, "P": float}}`. Here `symbols_filed` is `{sym: sorted dates}`, `prices` is `{sym: sorted [(filed, trade_date, accession, price)]}`, and `finra` is the chosen report or None. "FINRA row without volume" is counted through an optional `stats` dict.
  - `cik_at(filings_by_sym, sym, month_end) -> cik | None`, the CIK of the latest filing in the window. `filings_by_sym` is `{sym: sorted [(filed, cik)]}`.
  - `recovered(sym, month, cik, symbols_of_cik, bar_months) -> other symbol | None`. Here `symbols_of_cik` is `{cik: {syms}}` and `bar_months` is `{sym: {"YYYY-MM"}}`.
  - `survivorship_counts(per_month: {month: {"like": set, "with bars": set, "recovered": {sym: other}, "duplicates": int}}) -> {"per_year": {...}, "overall": {...}}`. The cells are `"universe-like"`, `"with bars"`, `"without bars"`, `"recovered by the rename rule"`, `"recovered, duplicating a stock-month with bars"`.
  - `decide(overall) -> {"rename rule used": bool, "f (rule as decided)": float, "f (literal, recovered always subtracted)": float, "b_R": float, "b within 0.03 R": bool}`. b uses "f (rule as decided)" (Decision 6a).

- [ ] **Step 1: Write the failing tests.**
  - `test_month_ends`: `month_ends("2018-01", "2018-02") == [("2018-01", "2018-01-31"), ("2018-02", "2018-02-28")]`, and 2024-02 ends on the 29th.
  - `test_universe_like_conditions`, for the month ending 2018-01-31:
    - In: AAA filed 2017-02-01; price row filed 2018-01-10 at $20; FINRA adv 600,000. That is $12M.
    - Out:
      - BBB, filed 2017-01-30, which is outside the 365 days;
      - CCC, latest price $4.99, with an older $50 price before it;
      - DDD, adv 499,000 at $20, which is $9.98M;
      - EEE, missing from FINRA;
      - FFF, adv None (counted in stats as "FINRA row without volume").
    - Edges:
      - AAA at exactly $5 with adv 2,000,000 is in;
      - a filing on 2018-01-31 counts;
      - a filing dated 2017-01-31 (365 days before) counts.
  - `test_latest_price_order`: two price rows filed on the same day. The later trade date wins. On equal trade dates, the larger accession wins.
  - `test_cik_and_rename`:
    - OLD (CIK 1, no bars) with NEW (CIK 1, bars in 2019-03) is recovered as NEW in 2019-03, and as None in 2019-04 (no NEW bar that month).
    - CIK 2 with two other symbols with bars recovers None.
    - A symbol filed under CIK 3 in 2017 and CIK 4 in 2018 takes CIK 4 for 2018-06.
  - `test_counts_and_decision`:
    - Overall: 100 universe-like, 80 with bars, 20 without, 5 recovered. 5 < 20 / 3, so the rule is not used: f = 0.20 (6a), literal f = 0.15, b = 0.20, `b within 0.03 R` False.
    - With 8 recovered: the rule is used (8 ≥ 6.67), f = 0.12.
    - Edge: 20 without bars and 20/3 recovered is exactly the third, and the rule is used. Tested with 3 without and 1 recovered.
    - Edge: f = 0.03 exactly gives b within limit True.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement** with the signatures above. Use `bisect` on the sorted lists. Compare ISO strings for dates.
- [ ] **Step 4: Run the full suite.** Expect OK.
- [ ] **Step 5: Commit.** Message: "Phase A.1: universe-like stock-months, the rename rule, f and b (counts only)".

### Task 4: The run, the checks, the whitelisted report

**Files:**
- Modify: `bot/probe_survivorship.py` (`run`, `checks_report`, `main`)
- Test: `tests/test_probe_survivorship.py`

**Interfaces:**
- Consumes: `pe.load_insider`, `pe._calendar`, `pe.universe`, `pe.item_202_coverage`, `pe.keeps_8k`, `pd.download(pd.SUBMISSIONS, ...)`, `pd.earnings_releases`, `pd.load_series`, `alpaca.daily`, `shorts.settlement_dates`.
- Produces:
  - `REPORT_KEYS = ("finra_volume_check", "item_202_coverage_8k_only", "item_202_coverage_with_8k_a", "s2_decision", "stock_months", "rename_rule", "allowance", "data_gaps", "requests_and_runtime")`.
  - `CHECK_KEYS = ("finra_volume_check", "spelling_check", "rename_check", "sample_stock_months", "requests_and_runtime")`.
  - `run(checks: bool) -> dict`.
  - `main(argv) -> int`: prints JSON and the job summary. It returns 1 when the report has `"stopped"`.

- [ ] **Step 1: Write the failing tests.**
  - `test_report_keys_only`:
    - `run(False)` runs on a mocked data set of 3 months and 4 symbols, with every network function mocked.
    - Its keys equal `REPORT_KEYS`.
    - `json.dumps(out)` matches no `(?i)\b(return|mean|median|win)` word.
  - `test_stops_without_volume`: every FINRA row has adv None. Then `run(False)` returns `{"finra_volume_check": ..., "stopped": "FINRA's reports carry no volume field"}`, and `main` returns 1.
  - `test_coverage_with_8k_a_reproduces_phase_a`: on the mock, the two coverage keys differ only by a stock-year covered by an 8-K/A alone.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement `run(checks)`**, in this order:
  1. Settlement dates 2017-12-01 … 2026-03-31. Choose the latest settlement ≤ each month end and fetch those reports (≤ 99).
  2. Run `volume_check`. If any chosen report has no volume at all, return the stop result immediately.
  3. Load the insider rows and the prices (`transaction_prices` over the 2017Q1 … 2026Q1 zips).
  4. Universe-like per month (`universe_like`).
  5. Raw bars for every universe-like symbol, plus every other symbol of their CIKs, through `alpaca.daily(symbols, "2018-01-01", "2026-03-31")`. Then build `bar_months`.
  6. Count with/without bars and recovered, then `decide`.
  7. Item 2.02 coverage. Universe stock-years as in phase A (`pe.universe` over the sessions from 2017-01-03, on `pd.load_series` from 2016-01-04). Releases are taken twice, `forms=("8-K",)` and `("8-K", "8-K/A")`, counted by filing year (no index pages). Then `pe.keeps_8k` on the 8-K-only coverage.
  8. Data gaps:
     - months without a FINRA report;
     - FINRA rows without volume;
     - universe-like symbols not spelled in FINRA;
     - insider quarters read.

  In checks mode, use a seeded sample of 300 filed symbols plus `BRK.B`, `META`, `FB`, `AAPL`. `spelling_check`: `BRK.B` is found in FINRA for 2024-06. `rename_check`: `FB` in 2021-06 has no bars and is recovered as `META` (CIK 1326801), or the observed outcome is stated.
- [ ] **Step 4: Run the full suite.** Expect OK. Then grep the run path to confirm it is counts only: `grep -n "print(\|log(\|write\|dump" bot/probe_survivorship.py`. The only output must be `main`'s JSON. The only files written must be caches of raw data (FINRA reports, Alpaca bars, SEC zips).
- [ ] **Step 5: Commit.** Message: "Phase A.1: run, pre-run checks and the whitelisted report (counts only)".

### Task 5: The checks run, the full run, the report, the review

**Files:**
- Create: `docs/backtest/survivorship-phaseA1-<date>.md`, `docs/backtest/survivorship-phaseA1-<date>.json`, `docs/backtest/survivorship-phaseA1-<date>-checks.json`
- Throwaway branch `probe-ensemble` (deleted afterwards): `.github/workflows/probe-ensemble.yml` as in phase A, running `python -m bot.probe_survivorship $(cat .probe-args)`.

- [ ] **Step 1: Checks run** (`.probe-args` = `--checks`). Expected: success, with JSON keys = `CHECK_KEYS`.
  - If the volume check stops, report to the owner and stop.
  - Otherwise show the owner a short check report (volume field, spelling, rename example, sample counts) and wait for the go-ahead.
- [ ] **Step 2: Full run** (`.probe-args` empty). Expected: success within the 330-minute limit, with JSON keys = `REPORT_KEYS`.
- [ ] **Step 3: Write the report.** Tables:
  - 8-K-only coverage per year and overall, with the S2 decision;
  - the 8-K/A comparison;
  - stock-months per year: universe-like, with bars, without bars, recovered, duplicates;
  - the rename rule decision;
  - f under both readings, b against 0.03 R;
  - gaps, limitations, requests and runtime.
  No returns.
- [ ] **Step 4: Independent review.** A reviewer who did not write the code (most capable model) reviews the code, the tests, the data coverage and the report. Fix confirmed findings with tests. Rerun if counts can change.
- [ ] **Step 5: Commit** to `recommendation-algorithm` and delete `probe-ensemble`. No merge, no PR merge, no phase B.
- [ ] **Step 6: Report to the owner** in Hebrew: commit hashes, tests, findings, limitations, the S2 decision, the rename-rule decision, b.
