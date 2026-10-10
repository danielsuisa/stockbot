# Recommendation algorithm — phase A (data sources and coverage) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure, without computing any return, what spec section 7 A asks: item 2.02 coverage in EDGAR's
`submissions.zip` (and so the section 2 fallback for S2), the point-in-time universe size per year, and the firings
per signal per year. Write the result to a report.

**Architecture:** A measurement probe in three files: `bot/probe_ensemble_data.py` (readers: insider data sets,
`submissions.zip`, daily bars with the bad-price rule), `bot/probe_ensemble.py` (universe, the four detectors as
firing counters, the report). It runs once on GitHub Actions from a throwaway branch, because the Alpaca keys and
`SEC_UA` exist only as GitHub secrets; it writes `docs/backtest/ensemble-phaseA-<date>.md`. Nothing here is product
code: `bot/levels.py` and `bot/signals.py` (phases B and C) are not created.

**Tech Stack:** Python 3.11/3.12, stdlib only (`zipfile`, `json`, `csv`, `bisect`), `unittest` with the network
mocked; existing helpers `bot/alpaca.py`, `bot/common.py`, `bot/probe_insider.py`.

**Spec:** `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md` (revision 5; approved for its
methodology and phase A only, section 1.1).

## Global Constraints

- **No return of any ticket or stock is computed, printed, logged or saved.** Signal inputs are allowed (S3's
  D−147→D−21 return rank, S4's 5-session return, S2's close / previous close), always from bars through D only. No
  bar after D is read for any quantity, and no forward window exists in this code.
- Nothing from phases B–E: no levels, sizes, fills, costs, pools, tickets, journal or statistics of section 6.
- Free sources only. Keys from the environment only (`ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`, `SEC_UA`), never printed.
- Signal days: 2017-01-03 → 2026-03-31. Bars start 2016-01-04.
- Universe on day D: the symbol appears as the issuer's trading symbol in a Form 3/4/5 filed in the 365 days before
  D; an Alpaca bar on D; raw close ≥ $5; 20-session average dollar volume ≥ $10M; ≥ 252 earlier sessions of bars.
- Bad prices: a one-day close ratio ≥ 4 (on the split-adjusted series: owner's decision 2026-10-10), or a gap of more than 10 sessions without bars, ends a symbol's series (a
  different security afterwards). Downward one-day moves are kept.
- Fallback (section 2): if item 2.02 is available for fewer than 90% of universe stock-years, S2 drops the 8-K
  condition and keeps its price and volume conditions.
- S1: ≥ 3 distinct officers/directors, open-market P, not a 10b5-1 line, ≥ $100,000, trade dates in the 30 days
  before; the same issuer, trade date, shares and price count as one person; no second event within 21 sessions.
- S2: the reaction session of an 8-K item 2.02 (the acceptance day, or the next session when accepted at or after
  16:00 ET); close / previous close ≥ 1.05; volume ≥ 2 × the 20-session average; close in the upper half of D's range.
- S3: first session after ≥ 21 sessions outside the state; state = close ≥ 0.95 × the highest close of the last 252
  sessions and the D−147→D−21 return in the top 20% of D's universe.
- S4: first session in the state; state = 5-session return in the bottom 5% of D's universe and ≤ −10%, in the top
  half of the universe by liquidity, no S2 reaction session in the last 5 sessions.
- Artifacts in English; the reply to the owner in Hebrew (RTL).

## Review Focus

1. **8-K acceptance time zone.** `submissions` JSON writes `acceptanceDateTime` with a `Z`, while EDGAR's filing index
   shows the same instant in Eastern time. A wrong guess moves every after-close release to the wrong reaction
   session. Pinned by a live check in Task 6 and by `test_reaction_session_after_close_et` in Task 2.
2. **Symbols as filed.** Values like `"BRK.B"`, `"brk-b"`, `"AAPL, AAPLW"`, `"NONE"`, `"N/A"` and blanks must
   normalise to Alpaca symbols or be dropped, and they must be counted. Pinned by `test_normalise_symbols` in Task 1.
3. **Raw vs split-adjusted series.** Price and liquidity thresholds use raw closes; signal conditions use
   split-adjusted bars. Swapping them makes splits look like ±50% moves. Pinned by `test_split_vs_raw_roles` in
   Task 4.
4. **The 252-session history after a bad-price break.** The count restarts in the new segment. Pinned by
   `test_history_restarts_after_break` in Task 3.
5. **Percentile ranks on small or tied universes** (early 2017, half-days). The top 20% / bottom 5% use a fixed rule:
   rank by value, ties by symbol, cut = ceil(share × size). Pinned by `test_rank_cut_ties_and_small` in Task 5.

---

### Task 1: Insider data sets — the symbol index and S1's purchases

**Files:**
- Create: `bot/probe_ensemble_data.py`
- Test: `tests/test_probe_ensemble.py`

**Interfaces:**
- Consumes: `probe_insider.quarter_zip(y, q)`, `probe_insider.table(z, name)`, `probe_insider.day(s)`,
  `probe_insider.num(s)`, `form4.PLAN`, `form4.NOT_PLAN`.
- Produces:
  - `normalise_symbol(raw: str) -> str | None`: upper case, `-` → `.`, the first of a comma/semicolon/space list,
    `None` for blanks, `NONE`, `N/A`, or anything outside `[A-Z]{1,5}(\.[A-Z])?`.
  - `symbol_filings(z) -> list[tuple[str, str, str]]`: (filing date ISO, issuer CIK, symbol) for every Form 3, 4 or 5
    and their amendments in a quarter's zip.
  - `index(rows) -> tuple[dict[str, list[str]], dict[str, set[str]]]`: from `symbol_filings` rows, symbol → sorted
    filing dates (the universe's 365-day test) and symbol → issuer CIKs (to join 8-K releases to symbols).
  - `purchases(z) -> list[dict]`: per open-market P line not under a 10b5-1 plan, on an original Form 4 by an
    officer/director: `{"filed", "issuer", "symbol", "owner", "trade_date", "shares", "price", "value"}` (the 10b5-1
    rule exactly as `probe_insider.classify`).

- [ ] **Step 1: Write the failing tests** — `test_normalise_symbols` asserts `"brk-b"` → `"BRK.B"`, `"AAPL, AAPLW"` →
  `"AAPL"`, `"NONE"` / `"N/A"` / `""` / `"TOOLONG"` → `None`; `test_symbol_filings_all_forms` builds an in-memory zip
  (SUBMISSION.tsv with document types 3, 4, 5, 4/A, 10-K) and asserts four rows, the 10-K dropped;
  `test_purchases_plan_and_role` asserts a footnoted 10b5-1 line and a 10%-owner-only line are excluded and an
  `aff10b5One`-box filing without plan footnotes is excluded.
- [ ] **Step 2: Run them, expect failure** — `python -m unittest tests.test_probe_ensemble -v` → ImportError.
- [ ] **Step 3: Implement the three functions** in `bot/probe_ensemble_data.py`.
- [ ] **Step 4: Run, expect pass**; then the full suite (`python -m unittest discover -s tests`; on Windows without
  tzdata use the stub runner as in earlier probes) → OK.
- [ ] **Step 5: Commit** — `git commit -m "Phase A: insider data-set readers (symbol index, S1 purchases)"`.

### Task 2: `submissions.zip` — item 2.02 releases and the reaction session

**Files:**
- Modify: `bot/probe_ensemble_data.py`
- Test: `tests/test_probe_ensemble.py`

**Interfaces:**
- Produces:
  - `earnings_releases(zip_path: Path, ciks: set[str], tz: str) -> dict[str, list[str]]`: CIK → acceptance times as
    ISO New York datetimes (`"YYYY-MM-DDTHH:MM"`), for 8-K / 8-K/A filings whose `items` contain `2.02`, from
    `filings.recent` and every `CIK…-submissions-NNN.json` page in the zip. `tz` is `"UTC"` or `"America/New_York"`:
    how the `acceptanceDateTime` field is to be read, decided by the live check in Task 6.
  - `reaction_session(accepted: str, sessions: list[str]) -> str | None`: the acceptance day if it is a session and
    the time is before 16:00, else the next session; `None` past the last session.
  - `SUBMISSIONS = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"`, downloaded once to
    `.cache/sec/submissions.zip` with `common.fetch` (SEC_UA, paced).

- [ ] **Step 1: Write the failing tests** — `test_items_and_pages`: a zip with one CIK's main file (items
  `"2.02,9.01"`, `"5.02"`) and a `-submissions-001.json` page (`"2.02"`) → three filings found, two with 2.02, both
  pages read. `test_reaction_session_after_close_et`: `"2024-02-01T16:00"` → the next session; `"2024-02-01T15:59"`
  → `"2024-02-01"`; a Saturday acceptance → Monday. `test_acceptance_tz`: the same field read with `tz="UTC"` in
  winter shifts by 5 hours to New York time.
- [ ] **Step 2: Run, expect failure.**
- [ ] **Step 3: Implement** with `zipfile` member iteration (never extract the 1+ GB archive to disk).
- [ ] **Step 4: Run, expect pass; full suite OK.**
- [ ] **Step 5: Commit** — `"Phase A: submissions.zip item 2.02 reader and reaction session"`.

### Task 3: Daily bars, raw and split-adjusted, with the bad-price rule

**Files:**
- Modify: `bot/probe_ensemble_data.py`
- Test: `tests/test_probe_ensemble.py`

**Interfaces:**
- Consumes: `alpaca.daily(symbols, start, end)` (raw), `probe_insider.split_daily(symbols, start, end)`
  (split-adjusted, cached), `alpaca.calendar`.
- Produces:
  - `Series = dict` with keys `"dates"` (ascending ISO), `"raw_close"`, `"raw_high"`, `"raw_low"`, `"raw_volume"`,
    `"close"`, `"volume"` (split-adjusted), `"segment"` (int per bar: segment id after the bad-price rule).
  - `load_series(symbols: list[str], start: str, end: str, sessions: list[str]) -> dict[str, Series]`: joins the
    two feeds by date (a date missing from either feed is dropped) and applies the bad-price rule.
  - `segments(dates: list[str], close: list[float], sessions: list[str]) -> list[int]`: a new segment starts after a
    **split-adjusted** close ratio close / previous close ≥ 4, or after more than 10 sessions without a bar. (Owner's
    decision 2026-10-10: the ratio is on the split-adjusted series, so a real reverse split does not end a series;
    the rule catches data errors such as a reused symbol or a missed split.)
  - `earlier_in_segment(segment: list[int], i: int) -> int`: bars before index i in the same segment.
  - Range position (S2's "upper half") uses the raw high, low and close of the day (a split scales a day's prices
    together); close / previous close, volumes and returns used by signals use the split-adjusted series.

- [ ] **Step 1: Write the failing tests** — `test_bad_price_breaks`: ratios 3.99 (same segment), 4.0 (new),
  0.2 (same: downward kept), a gap of 10 sessions (same) and of 11 sessions (new). `test_history_restarts_after_break`:
  300 bars, a ×4 jump at bar 280 → bars after it have fewer than 252 bars in their segment.
  `test_join_raw_and_split`: a date only in the raw feed is dropped.
- [ ] **Step 2: Run, expect failure.**
- [ ] **Step 3: Implement.** The split-adjusted request ends the day before the run date (the free plan refuses
  recent SIP data, as found in STEP 0).
- [ ] **Step 4: Run, expect pass; full suite OK.**
- [ ] **Step 5: Commit** — `"Phase A: daily series with the bad-price rule"`.

### Task 4: The point-in-time universe and its liquidity

**Files:**
- Create: `bot/probe_ensemble.py`
- Test: `tests/test_probe_ensemble.py`

**Interfaces:**
- Consumes: Task 1's `symbol_filings`, Task 3's `Series`.
- Produces:
  - `universe(day: str, series: dict[str, Series], filed: dict[str, list[str]]) -> dict[str, float]`: symbol → its
    20-session average dollar volume (raw close × volume, the 20 sessions through D), for every symbol that meets
    every universe condition of the Global Constraints on D. `filed`: symbol → sorted filing dates.
  - `liquidity_top_half(u: dict[str, float]) -> set[str]`: symbols at or above the median dollar volume.

- [ ] **Step 1: Write the failing tests** — `test_universe_conditions`: one symbol per failing condition (no filing in
  365 days, filing exactly 365 days before → in, no bar on D, raw close 4.99, $9.99M, 251 earlier bars) and one that
  passes. `test_split_vs_raw_roles`: a 1:10 reverse split leaves the split-adjusted close unchanged and the raw close
  ×10; the $5 check follows the raw close.
- [ ] **Step 2: Run, expect failure.**
- [ ] **Step 3: Implement** with `bisect` on dates (one pass per day; no bar after D is read).
- [ ] **Step 4: Run, expect pass; full suite OK.**
- [ ] **Step 5: Commit** — `"Phase A: point-in-time universe"`.

### Task 5: The four detectors as firing counters

**Files:**
- Modify: `bot/probe_ensemble.py`
- Test: `tests/test_probe_ensemble.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `s1_firings(purchases: list[dict], sessions: list[str]) -> list[tuple[str, str]]`: (D, symbol), with the
    one-person rule for identical (issuer, trade date, shares, price) lines and the 21-session gap.
  - `s2_firings(day, series, releases_by_symbol, use_8k: bool) -> list[str]`: symbols firing on D; `use_8k=False` is
    the fallback.
  - `s3_state(day, series, u) -> set[str]` and `s4_state(day, series, u, recent_s2) -> set[str]`; firings come from
    `transitions(states_by_day, min_outside: int)` (21 for S3, 1 for S4).
  - `rank_cut(values: dict[str, float], share: float, top: bool) -> set[str]`: rank by value, ties by symbol,
    cut = ceil(share × len(values)).
  - Every detector receives series truncated at D, through `through(series, day) -> dict[str, Series]`.

- [ ] **Step 1: Write the failing tests** — one test per condition at its edge: S1 with 2 vs 3 people, $99,999 vs
  $100,000, a duplicate joint line counted once, a second cluster 20 vs 21 sessions later; S2 at 1.0499 / 1.05,
  volume 1.99× / 2×, close exactly at the range's midpoint (in), acceptance at 16:00 (next session), and the fallback;
  S3 after 20 vs 21 outside sessions; S4 at −9.99% / −10% and with an S2 reaction session 5 vs 6 sessions back.
  `test_rank_cut_ties_and_small`: 7 symbols, top 20% → 2, ties resolved by symbol. `test_no_look_ahead`: a bar
  after D set to NaN changes nothing in any detector's output for D.
- [ ] **Step 2: Run, expect failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect pass; full suite OK.**
- [ ] **Step 5: Commit** — `"Phase A: the four detectors as firing counters (no returns)"`.

### Task 6: The run on GitHub and the report

**Files:**
- Modify: `bot/probe_ensemble.py` (add `main()`)
- Create (by the run): `docs/backtest/ensemble-phaseA-<date>.md`
- Throwaway, never merged: branch `probe-ensemble` with `.github/workflows/probe-ensemble.yml` (push trigger, secrets
  `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`, `SEC_UA`, timeout 330 minutes), deleted after the run.

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: `item_202_coverage(universe_days: dict[str, set[str]], ciks: dict[str, set[str]], releases: dict[str,
  list[str]]) -> dict` — a universe stock-year (symbol, calendar year with at least one universe day) is covered when
  any of the symbol's CIKs has an item 2.02 release accepted in that year; per year and overall counts and shares.
- Produces: `main()` printing one JSON document (also appended to the job summary) with exactly these keys:
  `acceptance_tz_check`, `item_202_coverage` (per year and overall: universe stock-years, covered, share),
  `fallback` (`"8-K kept"` or `"8-K dropped"`, by the 90% rule), `universe_per_year` (mean daily size, distinct
  symbols), `firings_per_signal_per_year` (S1–S4; S2 under the decided rule, and both variants for information),
  `data_gaps` (symbols as filed without bars, segments broken by the bad-price rule, filings with unusable symbols),
  `requests_and_runtime`.

- [ ] **Step 1: Write the failing test** — `test_report_keys_only`: `main()` on a tiny mocked data set prints
  exactly the keys above, and no key or value contains the words return, mean, median or win (whitelist check).
- [ ] **Step 2: Run, expect failure; implement `main()`; run, expect pass; full suite OK; commit.**
- [ ] **Step 3: The time-zone check, first in `main()`:** fetch the EDGAR index pages of 5 known item 2.02 filings
  (two before 16:00, two after, one in summer time), compare their "Accepted" time with `acceptanceDateTime`, and set
  `tz` for Task 2 from the result. If the five disagree, stop the run with the evidence (no report).
- [ ] **Step 4: Run on GitHub** — push `recommendation-algorithm` to `probe-ensemble` with the throwaway workflow;
  expected: success, the JSON in the log.
- [ ] **Step 5: Write `docs/backtest/ensemble-phaseA-<date>.md`** (English) from the JSON: the coverage table, the
  fallback decision and why, the universe per year, the firings per signal per year, the gaps, the requests and the
  runtime; then delete the throwaway branch.
- [ ] **Step 6: Commit and push `recommendation-algorithm`.**

### Task 7: Review and hand-off

- [ ] **Step 1:** A fresh reviewer (most capable model) checks the branch against the Global Constraints, above all
  that no return is computed and no bar after D is read. Fix what it confirms; full suite OK.
- [ ] **Step 2:** Open a PR (base `insider-ticket`), never merge it. Report to the owner in Hebrew: Changed / Tests /
  Measured result / PR link, and stop; phase B needs the owner's go-ahead (spec section 1.1).
