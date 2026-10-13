# Index program, phases 2 to 4: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** the engine on synthetic prices (phase 2), the blind run on the real files (phase 3, counts only) and the
single approved verdict run (phase 4) of the index program.

**Architecture:** pure functions on sessions and arrays, in five small `bot/index_*.py` modules (data, rules,
engine, statistics, decision), driven by `bot/index_run.py`. Phase 2 runs on synthetic prices only. Phases 3 and 4
read the private Stooq file through a deploy-key checkout and check every sha256 against the committed manifest.
The block standard error and the consistency gate are reused from `bot/ensemble_stats.py`. The verdict follows
the pattern of `.github/workflows/ensemble-verdict.yml`.

**Tech stack:** Python 3.12 standard library, `unittest`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-12-index-program-design.md`, with Amendments 1 to 4. The probe report is
`docs/backtest/index-data-probe-2026-10-12.md`, fourth round: all four checks of C3 pass.

## Which text governs

| Topic | Governs | Replaced or changed text |
|---|---|---|
| Standing rules | Section 0 | — |
| SPY prices | C1: the Stooq file in the private repository (sha256 `3493247f…ca6f`), rows 2005-02-25 to 2026-09-30, used as given (dividend-adjusted, D2) | Section 3 equity-leg source; A1; B1; B2; B4 |
| Where data live | A2 for the Stooq file and the manifests; C1 for H.15 (public, the Board named) | Section 3 "Data files" bullet |
| Bill rate | Section 3's formula, with the H.15 series `RIFSGFSM03_N.B` (A1) in `data/index/h15-3m-tbill.csv` | FRED `DTB3` as the source |
| BIL | Dropped (C1) | Section 3 "BIL compared"; 8.3 "BIL against the cash model" |
| Windows | C2: P 2009-01-02 to 2026-09-30; H 2006-01-03 to 2026-09-30; S dropped; no account before 2006-01-03 | Section 3 windows table |
| Scale | 6.2 with A4 (search 0.01 to 1.00, crash cap after it) and C2 (P fitted on 2006-01-03 to 2008-12-31; operating scale on H) | 6.2 fitting table |
| Rules T, TOM, D | Section 4, unchanged | — |
| Execution and costs | Section 5, unchanged | — |
| Overlay test | 7.1, unchanged (71 quarters, Holm 1.9945 and 1.6670) | — |
| Eligibility | 7.2 as changed by C2: a is "maximum drawdown at most 20% over P"; b and d stand; c removed | 7.2 a and c |
| Outcome | 7.3 as changed by C2: TACT over TREND only if its P growth is at least 0.5 point higher | 7.3's S clause |
| Blind report | 8.2, unchanged (TOM wiring count 851) | — |
| Verdict report | 8.3 as changed by C2 (P, the fitting period, H at the operating scale), A5's first sentence and B5 | 8.3 windows S and H; 8.3 BIL; A5 rest |
| Acceptance | Done in phase 1 (C3 with D1, D2) | — |
| Missing day | D1: 2011-02-17 is not a trading day; 2011-02-16 to 2011-02-18 counts as one day | — |
| Limits | Section 11 plus C4, D3 and E1 | — |
| E1, the file's recent dividends | E1: the file is not adjusted for the six dividends from 2025-06-20 to 2026-09-18; it is used as it is, not repaired | The first bullet of D3 |
| E2, daily equity | E2: kept in memory only, saved nowhere; B5 governs the public report | 8.3's "daily equity CSV per policy"; A5's second sentence |
| E3, TACT | E3: in SPY when either passing overlay holds a position; it trades only when that combined state changes | 6.1's TACT row, made precise |
| E4, smaller rules | E4: a signal on a window's last day is not executed; P's fitting account starts in cash at the close of 2005-12-30 with $10,000 and base costs; 8.3's information items come after the decision, in their own section; a yearly rebalance is two order tickets | — |
| E5, phase 3 to phase 4 | E5: merge to `main` with the owner's approval; the blind run is a push of that merge commit to `index-run-blind`; the verdict runs on the same commit and must reproduce the blind report; no time limit | Plan question 8's 7-day limit |

## Global constraints

- **Blind rule (section 0.2):** before the verdict run, no return, profit, equity curve or drawdown of any rule
  or policy is computed, printed, logged, cached or saved on real prices. Phase 2 tests use synthetic prices
  only. Phase 3 reports counts only (8.2).
- **One verdict run (0.3)** at a recorded commit, after the owner's approval. No parameter changes after it.
- **No price** in the public repository, an artifact or a log (A2, B5). The public report holds aggregated
  numbers only (B5): test statistics, gates, scales, growth rates, drawdowns with their dates, counts, and
  calendar-year returns of each policy. No daily or monthly series.
- **Data:** the Stooq file is read only from `danielsuisa/stockbot-data` with `DATA_REPO_KEY`. Each run checks
  the sha256 of the Stooq file and of the H.15 file against `data/index/manifest.json` and stops on a mismatch.
- **Constants copied from the spec:**
  - costs: commission $0.35 per order; execution cost per side per leg 1 basis point at base, 3 at stress, 0 at
    optimistic;
  - accounts: model account $10,000, with $5,000 and $25,000 for information;
  - fund fee 0.14% a year;
  - scale: budget 16%, owner's limit 20%, crash cap 0.60, search step 0.01 up to 1.00, rebalance band 0.05;
  - gates: Holm 1.9945 and 1.6670, size 1.0% a year, 7.2 b 1.0 point, 7.3 0.5 point;
  - rules: T 10 months, D thresholds 10 / 200 / 5.
- Standard library only. Workflows with secrets run on `push` to their own branch or on `workflow_dispatch`, never
  on `pull_request`, on `ubuntu-24.04` with `PYTHONHASHSEED: "0"`.
- The live bot, `/ticket`, `/equity`, the live workflows and the ensemble files stay untouched. The full test
  suite stays green.

## Review focus (inputs the spec implies and no step below would otherwise exercise)

1. **The missing day 2011-02-17 (D1).** Rules count only the days in the file. The cash leg's d for 2011-02-18 is
   2 calendar days. A test pins both.
2. **A rule event on a window's first day.** At P's first open the account takes the policy's position and pays
   the normal costs (5); the TOM position carried over from December 2008 (8.2). A test pins both.
3. **A missing H.15 value on a stock-trading day** (bond holidays such as Columbus Day) takes the previous
   value. A test pins it.
4. **A position open at a window's end** is marked at the last close, with no exit cost (5, accounting). A test
   pins it.
5. **Determinism.** The verdict must give identical bytes in another process. A `PYTHONHASHSEED` test, as for
   the ensemble.

---

## Phase 2: the engine on synthetic prices

### Task 1: data access and the cash leg

**Files:**
- Create `bot/index_data.py`.
- Test `tests/test_index_data.py`.

**Interfaces:**
- Consumes: `bot.index_probe.parse_stooq(body) -> [(date, o, h, l, c)]`.
- Produces:
  - `load_prices(path, manifest_path) -> [(date, o, h, l, c)]`: rows 2005-02-25 to 2026-09-30. Raises
    `DataMismatch` when the sha256 differs from the manifest.
  - `load_h15(path, manifest_path) -> {date: value or None}`, with the same check.
  - `cash_returns(days, h15, fee=0.0014) -> {day: return}`.
  - `ACCOUNT_START = "2006-01-03"`.

- [ ] **Step 1: tests.**
  - `test_sha256_mismatch_stops` raises `DataMismatch`.
  - `test_rows_after_last_ignored`.
  - `test_cash_return_by_hand`: previous trading day's value 4.0 and d = 3 give
    `0.04 * 3 / 360 - 0.0014 * 3 / 365`.
  - `test_missing_h15_takes_previous`: an `ND` on a trading day uses the last value before it.
  - `test_d1_gap`: days 2011-02-16 and 2011-02-18 give d = 2 for 2011-02-18.
- [ ] **Step 2:** run the tests; expect them to fail (the module is missing).
- [ ] **Step 3: implement.** The previous trading day is the previous day of the SPY file. "DTB3 of the previous
  trading day" is the H.15 value on that date, or the last earlier value.
- [ ] **Step 4:** run the tests; expect them to pass. **Step 5:** commit.

### Task 2: the rules T, TOM and D (section 4)

**Files:**
- Create `bot/index_rules.py`.
- Test `tests/test_index_rules.py`.

**Interfaces:**
- `month_ends(days) -> [day]`.
- `trend_events(rows) -> [(signal_day, "ON" or "OFF")]`, executed at the next open.
- `tom_positions(days, planned_calendar) -> [(entry_day, exit_day)]`: entry and exit at the close.
- `rsi2(closes) -> [float or None]`.
- `sma(closes, n) -> [float or None]`.
- `dip_positions(rows) -> [(signal_day, entry_day, exit_signal_day, exit_day)]`: entry at the open of t+1, exit
  at the open of t'+1.
- `held_after_close(positions, days) -> set(day)`: 7.1's days held after the close, for pi.

- [ ] **Step 1: tests, hand-computed on synthetic closes.**
  - **`test_sma10_month_ends`:** 11 month-ends with closes 1 to 11 give SMA10 = 6.5 at the eleventh. T is ON when
    C = 11 > 6.5. A change executes at the next day's open.
  - **`test_tom_window`:** a month with last trading day L. Entry is at the close of the day before L, exit at the
    close of the third trading day of the next month. The position is held after the close on 4 days.
  - **`test_tom_planned_day_not_trading`:** on the 2012-10 calendar, the planned entry 2012-10-30 did not trade.
    Entry moves to the close of 2012-10-31, and the exit counts traded days.
  - **`test_rsi2_by_hand`:** closes 10, 11, 10.5, 10.5, 12.
    - The changes are +1, −0.5, 0, +1.5.
    - The first averages are gain 0.5 and loss 0.25; RSI = 100 − 100 / 3 = 66.67.
    - Next: gain (0.5 + 0) / 2 = 0.25 and loss (0.25 + 0) / 2 = 0.125; RSI 66.67.
    - Next: gain (0.25 + 1.5) / 2 = 0.875 and loss 0.0625; RSI = 100 − 100 / 15 = 93.33.
    - With an average loss of 0, RSI is 100.
  - **`test_dip_entry_exit`:** 205 synthetic days with C > SMA200 and an RSI2 below 10 on day t. Entry is at the
    open of t+1. The exit is at the open after the first close at or after the entry day that is above SMA5. There
    is no stop and no time limit, and no new entry while a D position is open.
  - **`test_rules_skip_missing_day`:** the D1 calendar counts the traded days only.
  - **`test_tom_wiring_851`:** on the real exchange calendar of 2008-12 to 2026-09 (`index_probe.nyse_days`, less
    2011-02-17), `held_after_close` of TOM over P gives exactly 851 days (8.2). This needs dates only, no price.
- [ ] **Steps 2 to 5:** run (fail), implement, run (pass), commit.

### Task 3: execution, accounting and costs (sections 5 and 6.2)

**Files:**
- Create `bot/index_engine.py`.
- Test `tests/test_index_engine.py`.

**Interfaces:**
- `Costs(commission=0.35, bps=1.0)`, with the levels `OPTIMISTIC`, `BASE` and `STRESS` (0, 1 and 3 basis points).
- `run_window(rows, cash, in_spy_events, scale, costs, start, end, equity0=10_000.0)` returns a `Result`:
  - `daily_equity`: in memory only;
  - `order_tickets`: a count;
  - `spy_weight_by_day`.
- `in_spy_events` is `[(day, "open" or "close", in_spy: bool)]`.

- [ ] **Step 1: tests, hand-computed ledgers on 3 to 6 synthetic days.**
  - **`test_entry_day_marked_from_execution_price`:** buy at O(t). That day's equity moves by
    C(t) / O(t) on the SPY part.
  - **`test_exit_day_marked_to_execution_price`:** sell at O(t'). That day moves by O(t') / C(t'−1).
  - **`test_both_legs_pay_costs`:** a switch into SPY pays two orders ($0.70) and 1 basis point on each leg's
    traded amount.
  - **`test_cash_leg_credit`:** day t's cash return goes to the cash held at the close of t−1.
  - **`test_fresh_window_starts_in_cash`:** the account takes the policy's position at the window's first open,
    with costs.
  - **`test_open_position_marked_at_window_end`:** no exit is charged.
  - **`test_yearly_rebalance`:** on the first trading day of a year, after any rule event of that open:
    - a weight 0.66 against s = 0.60 is restored to 0.60 at the open;
    - a weight 0.64 is left alone (within the 0.05 band).
  - **`test_scale_partial`:** s = 0.6 puts 60% in SPY and 40% in the cash leg.
- [ ] **Steps 2 to 5.**

### Task 4: growth, drawdown and the scale search (6.2, 6.3, A4, C2)

**Files:**
- Modify `bot/index_engine.py`.
- Test `tests/test_index_scale.py`.

**Interfaces:**
- `growth_rate(equity, first_day, last_day) -> float`.
- `max_drawdown(equity) -> (depth, peak_day, trough_day)`.
- `calendar_year_returns(equity) -> {year: r}`.
- `fit_scale(rows, cash, events, fit_start, fit_end) -> {"fitted": s or None, "scale": min(s, 0.60) or None,
  "excluded": bool}`.

- [ ] **Step 1: tests.**
  - **`test_growth_by_hand`:** 10,000 to 12,100 over 730.5 calendar days gives 10% a year.
  - **`test_drawdown_dates`:** the equity 100, 120, 90, 110, 80, 130 has a maximum drawdown of 1/3, from the
    peak of day 2 to the trough of day 5.
  - **`test_scale_search_stops_above_16pct`:** a synthetic path where s = 0.37 gives 15.9% and 0.38 gives 16.1%
    fits 0.37.
  - **`test_search_runs_to_1`:** a path that never exceeds 16% fits 1.00, and the cap makes the scale 0.60.
    Both values are reported.
  - **`test_excluded_at_0_01`.**
  - **`test_scale_fixed_at_base_and_10000`:** the same scale is reused at every cost level and account size.
- [ ] **Steps 2 to 5.**

### Task 5: the overlay test and the decisions (7.1, 7.2, 7.3 with C2)

**Files:**
- Create `bot/index_stats.py`.
- Test `tests/test_index_stats.py`.

**Interfaces:**
- `overlay_x(r, spy_cc, cash, pi) -> [x(t)]`.
- `quarter_blocks(days) -> [g]`: 71 blocks over P.
- `overlay_test(x, days, x_stress) -> {"t", "se", "gates": {...}}`.
- `holm(t_tom, t_d) -> {rule: pass}`.
- `eligible(policy_p, static_p, policy_p_stress, static_p_stress) -> {...}`.
- `outcome(trend, tact) -> "STATIC" or "TIMING", selected`.

**Reuse:**
- `bot.ensemble_stats.se(x, blocks, G=71)`: the 7.1.3 formula, exactly.
- `ensemble_stats.block(i, n)`: 7.1b's blocks floor(10·i/n), exactly.
- `ensemble_stats.gate3(x, blocks10)`: 7.1b, exactly (7 of 10 positive, and each half positive).
- Nothing else from the ensemble.

- [ ] **Step 1: tests.**
  - **`test_quarter_se_by_hand`:** a small case with G = 3 checked by hand, then the same formula through
    `ensemble_stats.se`.
  - **`test_holm_order`:**
    - larger t 1.9945 and smaller 1.6670 both pass;
    - larger 1.9944 means both fail;
    - larger 2.5 and smaller 1.6669: only the larger passes.
  - **`test_size_gate`:** 252 × x̄ = 0.0099 fails; 0.0100 passes.
  - **`test_stress_gate`.**
  - **`test_pi_and_benchmark_by_hand`:** b(t) = pi × SPY's change + (1 − pi) × cash.
  - **`test_eligibility_c2`:** drawdown 20.0% over P passes and 20.1% fails; growth over STATIC +1.0 point passes
    and +0.99 fails; stress growth must be above STATIC's.
  - **`test_outcome_c2`:** both eligible, with TACT 0.5 point above TREND over P, gives TACT; 0.49 gives TREND;
    none eligible gives STATIC.
- [ ] **Steps 2 to 5.**

### Task 6: the driver and the reports (8.2, 8.3, B5)

**Files:**
- Create `bot/index_run.py`.
- Tests `tests/test_index_run.py` and `tests/test_index_determinism.py`.

**Interfaces:**
- `blind_report(rows, cash) -> dict`, with the keys of 8.2 only:
  - the data fingerprint;
  - trading days per year;
  - for each rule, the entries per year and pi per window;
  - for TREND, the order tickets per year.
- `verdict_report(rows, cash) -> dict`: 8.3 with C2 and B5, aggregated numbers only.
- `summary_md(rep, today)`: Hebrew.
- `main(["--blind"])`, and `main(["--verdict", "--commit", sha, "--owner-go-ahead"])`.

- [ ] **Step 1: tests.**
  - **`test_blind_whitelist`:** the report has exactly 8.2's keys.
  - **`test_blind_writes_only_its_report`:** reuse the pattern of `tests.test_equity_privacy.Leaks`.
  - **`test_verdict_end_to_end_synthetic`:**
    - with a synthetic path where TREND avoids a synthetic crash, the outcome is TIMING;
    - with a flat path, the outcome is STATIC.
  - **`test_no_daily_series_in_report`:** no list longer than the number of years.
  - **`test_hebrew_summary_states_outcome`:** the summary states the outcome, STATIC included.
  - **`test_determinism`:** identical bytes under PYTHONHASHSEED 0, 1 and 4242.
- [ ] **Steps 2 to 5.**

**Phase 2 ends with an independent review and a stop for the owner.**

## Phase 3: the blind run (real files, counts only)

### Task 7: the blind workflow

**Files:**
- Create `.github/workflows/index-blind.yml`.

**The workflow:**
- It runs on a push to `index-run-blind` only, with `contents: read`, on `ubuntu-24.04`, with
  `PYTHONHASHSEED: "0"`.
- **Private checkout:** `DATA_REPO_KEY` is written to `$RUNNER_TEMP/data_key` with mode 600, and
  `stockbot-data` is cloned with `GIT_SSH_COMMAND="ssh -i … -o IdentitiesOnly=yes"`. The key file is deleted
  right after the clone.
- **Run:** `python -m bot.index_run --blind --stooq $RUNNER_TEMP/private/spy_us_d.csv`. `load_prices` and
  `load_h15` check both sha256 against `data/index/manifest.json` and stop on a mismatch (exit 2).
- **Upload:** only the blind report, which holds no price.
- **Test:** a workflow test, as `tests/test_ensemble.Workflow`, checks:
  - push only;
  - `fail` on a missing key;
  - no `${{ }}` inside `run:`;
  - one upload.
- **After the run:** the owner reviews 8.2. Any TOM count other than 851 is explained before phase 4.

## Phase 4: the verdict (one run)

### Task 8: the verdict workflow (pattern of `ensemble-verdict.yml`)

**Files:**
- Create `.github/workflows/index-verdict.yml`.

**The workflow:**
- **Trigger:** `workflow_dispatch` only. The inputs are `blind_run_id`, `blind_commit` and a typed `confirm`.
- **Environment:** `environment: index-verdict`, with the owner as required reviewer, to be created and read back
  as for `ensemble-verdict`.
- **Settings:** `concurrency` with one run at a time, `ubuntu-24.04`, `PYTHONHASHSEED: "0"`.
- **First step:** checks the ref, that `github.sha` equals `blind_commit`, and the confirmation, with inputs
  passed only through `env`.
- **Data:** the same private checkout and sha256 checks as task 7.
- **Blind reproduction:** the verdict recomputes the blind report and refuses unless it equals the blind run's
  artifact, as the ensemble did.
- **Upload:** the JSON report and the Hebrew summary `docs/backtest/index-<date>.md`. Aggregated numbers only (B5).

## Open questions: all eight settled by Amendment 5 (2026-10-13)

The owner accepted the eight recommendations below, with one change: question 8 has no 7-day limit (E5). The answers, by question: 1 → E2; 2 → E3; 3 → E1; 4 → E4 (first bullet); 5 → E4 (second); 6 → E4 (third); 7 → E4 (fourth); 8 → E5.

1. **Daily equity files.**
   - A5 sent them to the private repository. B1 replaced A5's second sentence with "not saved anywhere". C1 then
     replaced B1 as a whole, without saying whether A5's second sentence returns.
   - **Recommendation:** keep the daily equity in memory and save it nowhere. 8.3's "daily equity CSV per policy"
     is then dropped, and B5's aggregated numbers are enough.
2. **TACT when both overlays pass.**
   - 6.1 says TACT holds SPY when "a passing overlay holds a position".
   - **Recommendation:** in SPY when either passing overlay holds a position (the union). It trades only when
     that state changes.
3. **The Stooq file's recent dividends.**
   - The 6 ex-dividend days nearer Alpaca's raw change are the 6 most recent (2025-06-20 to 2026-09-18). Their
     five-day sums are −0.0026 to −0.0030 each, about the dividend size. The file looks unadjusted for every
     dividend since mid-2025.
   - D3 calls this "about one ex-dividend day in seven", but it is one block at the end, not a scattered error.
   - **Recommendation:** an amendment that names this. It understates SPY by about 0.28% a quarter over the last
     5 quarters of P and H, and so affects both STATIC and the timing policies.
4. **Which price executes "at the open" when the rule's signal day is the last day of a window.**
   - **Recommendation:** no execution after the window's last close. The position stays as it is and is marked
     at the last close (5).
5. **The fitting account for P's scale** (2006-01-03 to 2008-12-31).
   - **Recommendation:** it starts in the cash leg at the close of 2005-12-30, as 5 says for every window. It is
     a fresh $10,000 account at base costs.
6. **8.3's information items** (no crash cap; budgets of 15% and 17%; T with 8 and 12 months; D with 5 and 15;
   T's timing statistic).
   - **Recommendation:** compute them in the same verdict run, after the decision, in a separate report section
     marked "information, no decision".
7. **The order ticket count.**
   - "An order ticket is one order in one leg." A switch is therefore 2 tickets, and a yearly rebalance is 2.
   - **Recommendation:** confirm that a rebalance counts both legs.
8. **The phase 3 to phase 4 branch.**
   - **Recommendation:** use the ensemble pattern: merge to `main` first, then create `index-run-blind` at the
     merge commit. The verdict is dispatched on that ref within 7 days. No cache is needed here, because the
     inputs live in the private repository and are checked by sha256.
   - **Settled by E5:** as recommended, without the 7-day limit.
