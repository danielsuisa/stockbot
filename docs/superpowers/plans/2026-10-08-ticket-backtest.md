# Order-Ticket Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reproducible intraday backtest that gives a GO / NO-GO for the owner's order-ticket rules (ORIGINAL) and the research report's version (IMPROVED), per session (`REGULAR`, `PREMARKET`), against a random-direction benchmark, on the bot's short-squeeze candidates.

**Architecture:** `bot/alpaca.py` fetches and caches Alpaca SIP bars, quotes and the trading calendar; `bot/ticket.py` is a pure rule engine over 1-minute bars (shared later by live tickets); `bot/ticket_backtest.py` builds the two candidate universes, simulates fills and exits on 1-minute bars with costs, computes the pre-registered verdict and writes the report.

**Tech Stack:** Python 3.11/3.12 stdlib only (`urllib` via `common.fetch`, `zoneinfo`, `json`/`gzip` cache), `unittest`, existing `bot.backtest` loaders (FINRA reports, SEC frames/filings).

**Spec:** `docs/superpowers/specs/2026-10-08-ticket-backtest-design.md`

## Global Constraints

- Stdlib only; tests offline with the network mocked; `python -m unittest discover -s tests` and actionlint stay green.
- Keys only from env `ALPACA_KEY_ID` / `ALPACA_SECRET_KEY`; never written to the repo, logs or the report.
- Cache under `.cache/alpaca/` (already gitignored via `.cache/`); a failed download is never cached.
- All session times are New York (`zoneinfo("America/New_York")`); Alpaca timestamps are UTC.
- A 5-minute bar is used only after it closes; nothing at or after minute `m` decides anything at `m`.
- Tuning period 2024-01-01 → 2024-12-31; verdict period 2025-01-01 → last complete session; verdict thresholds:
  ≥ 100 trades, mean net R > 0 with t ≥ 2, mean net R > 95th percentile of 20 random-direction means, mean net R > 0
  in 2025 and in 2026 separately.
- Costs: $0.0035/share, min $0.35 per order, each side; half the SIP spread at entry and at exit (spread at the
  signal); stop fills at the stop or the bar open if gapped; stop first when a bar touches stop and target.
- Risk per trade $100; fewer than 10 shares → `NO TICKET`.

## Review Focus

- Daylight-saving days: a UTC bar must map to the right New York minute in both EDT and EST (Task 1 test).
- Early-close days (13:00): the regular session, the 15:00 entry cutoff and the end-of-day exit follow Alpaca's
  calendar close, not 16:00 (Task 1 calendar, Task 3 test).
- Thin stocks with missing 1-minute bars: 5-minute bars aggregate what exists; a missing pre-market minute is not a
  halt (Task 2 test).
- Splits inside the 63-day daily window: ticker-days with a day-over-day close ratio < 0.5 or > 2 in the window are
  skipped and counted (Task 5 test).
- HTTP 429 / 5xx from Alpaca: retried by `common.fetch`; an interrupted run resumes from the cache (Task 1 test).

---

### Task 1: Alpaca client with cache

**Files:**
- Create: `bot/alpaca.py`
- Test: `tests/test_alpaca.py`

**Interfaces:**
- Produces:
  - `minute_bars(symbols: list[str], day: str) -> dict[str, list[tuple]]` — 1-minute SIP bars 04:00–20:00 New York
    of ISO `day`, each `(minute "HH:MM" New York, o, h, l, c, v, vw)` ascending; one cached file per symbol and day
    (`.cache/alpaca/1m/{day}/{sym}.json.gz`, an empty list cached as `[]` so "no trades" is not refetched);
    multi-symbol requests (`symbols=` comma list, `limit=10000`, follows `next_page_token`).
  - `bars(symbol: str, start: str, end: str, timeframe: str) -> list[tuple]` — same tuple shape (minute key is
    `"YYYY-MM-DD HH:MM"` New York) for any range/timeframe (`"5Min"`, `"1Day"`), cached by all four arguments.
  - `daily(symbols: list[str], start: str, end: str) -> dict[str, list[tuple]]` — `adjustment=raw` daily bars
    `(date ISO, o, h, l, c, v)`, cached per symbol and range.
  - `quote_at(symbol: str, at: str) -> tuple[float, float] | None` — the last SIP quote `(bid, ask)` at or before
    UTC ISO `at` (window 60 s, `sort=desc`, `limit=1`); None when none.
  - `calendar(start: str, end: str) -> dict[str, tuple[str, str]]` — `{date: (open "HH:MM", close "HH:MM")}` from
    `https://paper-api.alpaca.markets/v2/calendar`, cached.
  - `utc(day: str, hhmm: str) -> str` — New York `day hh:mm` → UTC ISO (`"2026-12-01", "09:30"` →
    `"2026-12-01T14:30:00Z"`; `"2026-10-08", "09:30"` → `"2026-10-08T13:30:00Z"`).
- Consumes: `common.fetch(url, headers=..., tries=5, timeout=60)` (429/5xx backoff already there).

- [ ] **Step 1: Write the failing tests** `tests/test_alpaca.py` with `common.fetch` mocked:
  `test_minute_bars_split_pages_and_map_to_new_york` (two pages via `next_page_token`; a bar at `2026-10-08T08:00:00Z`
  maps to `"04:00"`, one at `2026-12-01T14:30:00Z` maps to `"09:30"`), `test_empty_day_is_cached_as_no_trades`
  (second call makes no request), `test_failed_download_is_not_cached` (`fetch` raises → next call requests again),
  `test_keys_in_headers_not_urls` (headers `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` from env; the key appears in no
  URL), `test_quote_at_takes_the_last_quote` (`sort=desc&limit=1`, returns `(bid, ask)`; empty → None),
  `test_calendar_early_close` (`{"date": "2026-11-27", "open": "09:30", "close": "13:00"}` → `("09:30", "13:00")`),
  `test_utc_in_summer_and_winter`.
- [ ] **Step 2:** Run `python -m unittest discover -s tests -p test_alpaca.py` — Expected: FAIL (no module).
- [ ] **Step 3:** Implement `bot/alpaca.py` with the signatures above (base URL `https://data.alpaca.markets`,
  `feed=sip`; reuse the gz-JSON pattern of `backtest._cached`, but cache `[]`).
- [ ] **Step 4:** Run the same command — Expected: PASS; then the full suite — Expected: OK.
- [ ] **Step 5: Commit** `git add bot/alpaca.py tests/test_alpaca.py && git commit -m "Alpaca: SIP bars, quotes and calendar with a disk cache"`

### Task 2: Rule-engine building blocks

**Files:**
- Create: `bot/ticket.py`
- Test: `tests/test_ticket.py`

**Interfaces:**
- Consumes: the 1-minute tuple shape of Task 1.
- Produces (all pure):
  - `to5(bars1: list[tuple]) -> list[dict]` — 5-minute bars `{"start": "HH:MM", "end": "HH:MM", "o","h","l","c","v","vw"}`
    on the :00/:05 grid from whatever minutes exist (`vw` volume-weighted; a bucket with no minutes is absent).
  - `vwap(bars1, start: str, upto: str) -> float | None` — Σ(vw·v)/Σv over minutes `start` ≤ m < `upto`.
  - `swings(bars5: list[dict]) -> list[dict]` — `{"i", "kind": "high"|"low", "price", "known_at": index of the
    bar whose close confirms it}`: high above the 2 bars before and the 2 after (strictly), likewise low; `known_at`
    = i + 2.
  - `last_swing_high(swings, upto: int) -> float | None` and `last_higher_low(swings, upto: int) -> float | None` —
    only swings with `known_at` ≤ `upto`; a higher low is a swing low above the previous swing low.
  - `atr14(daily: list[tuple], day: str) -> float | None` — mean true range of the 14 sessions before `day`.
  - `halted(bars1, at: str, open_: str, close: str, lookback: int = 15) -> bool` — a gap of ≥ 5 minutes without
    bars inside [open_, close) that overlaps the `lookback` minutes before `at`.

- [ ] **Step 1: Write the failing tests** `tests/test_ticket.py`: `test_to5_aggregates_existing_minutes_only`
  (minutes 09:30, 09:31, 09:34 → one bar 09:30–09:35 with o of 09:30, c of 09:34, v sum; no 09:35 bar when no
  minutes), `test_vwap_window`, `test_swing_confirmed_two_bars_later` (a peak at i=3 is absent at `upto`=4 and present
  at `upto`=5), `test_higher_low_needs_a_rising_low`, `test_atr14_uses_days_before`, `test_halt_gap_in_session_only`
  (a 6-minute gap 10:02–10:08 → halted at 10:15, not at 10:30; the same gap at 06:00 → not halted).
- [ ] **Step 2:** Run `python -m unittest discover -s tests -p test_ticket.py` — Expected: FAIL (no module).
- [ ] **Step 3:** Implement the functions above in `bot/ticket.py`.
- [ ] **Step 4:** Run the same command — Expected: PASS; full suite OK.
- [ ] **Step 5: Commit** `git commit -m "Ticket engine: 5-minute bars, VWAP, confirmed swings, ATR, halt gaps"`

### Task 3: The two rule sets

**Files:**
- Modify: `bot/ticket.py`
- Test: `tests/test_ticket.py`

**Interfaces:**
- Consumes: Task 2 functions; `ctx = {"day", "open": "HH:MM", "close": "HH:MM", "atr": float | None,
  "open_rel_vol": float | None, "from": "HH:MM"}` (`from` = eligibility minute, universe B; `"04:00"`/`"09:30"` for A)
  and `spread(at_utc: str) -> float | None` (a callable, so quotes are fetched only at signals).
- Produces: `signal(rules: str, session: str, bars1: list[tuple], ctx: dict, spread) -> dict` — the first ticket of
  the session `{"ok": True, "rules", "session", "at": "HH:MM", "level", "entry_type": "LMT"|"STP_LMT", "entry",
  "limit", "stop", "R", "shares", "legs": [{"qty", "target": float | None, "exit_at": "HH:MM" | None}], "valid_until":
  "HH:MM", "spread"}` or `{"ok": False, "reason": <first failed check>, "at": ...}`; `rules` ∈ `{"ORIGINAL",
  "IMPROVED"}`, `session` ∈ `{"REGULAR", "PREMARKET"}`. Reasons are the strings `"spread"`, `"trigger"`, `"volume"`,
  `"vwap"`, `"halt"`, `"no higher low"`, `"size below minimum"`, `"opening volume"`, `"no atr"`, `"no signal"`.
- Values (from the spec, section 5): ORIGINAL — REGULAR level = max(high 09:30–09:45, last swing high), checks from
  the 09:50 close to the 15:00 close (or `close` − 60 min on an early close); PREMARKET level = high of all bars
  before the trigger bar, checks 04:55 → 09:25; spread ≤ $0.05; volume ≥ 2 × mean of the previous 10 five-minute
  bars; close > VWAP; entry LMT = level + 0.05 valid to `close`; stop = last higher low − 0.10, at most entry − 0.20;
  shares = floor(100 / R); leg A floor(shares/2) target entry + 2R, leg B the rest. IMPROVED — REGULAR level = high
  09:30–09:35, `open_rel_vol` ≥ 1.0, trigger = first 1-minute high > level after 09:35, STP_LMT limit = level +
  0.1 × atr, valid 60 min; PREMARKET level = high before the trigger, valid to 09:25; D = 0.1 × atr; spread ≤ 1% of
  mid and ≤ 0.1 × D; shares = floor(100 / (D + spread)); leg A target entry + 2D, leg B exit at `close` − 5 min.

- [ ] **Step 1: Write the failing tests** (synthetic minute bars built by a helper `day(prices, vols)`):
  `test_original_regular_ticket_values` (level 10.00 → entry 10.05, higher low 9.85 → stop 9.75, R 0.30,
  shares 333, legs 166 @ 10.65 and 167), `test_original_min_stop_distance` (higher low 9.98 → stop 9.85),
  `test_original_each_check_fails_alone` (one case per reason: spread 0.06; close = level; volume 1.99×; close <
  VWAP; halt gap; no higher low; R so large that shares < 10), `test_original_premarket_level_excludes_trigger_bar`,
  `test_original_no_entry_after_cutoff_and_early_close` (cutoff 12:00 when `close` = "13:00"),
  `test_improved_regular_ticket_values` (atr 2.0 → D 0.20, limit level + 0.20, shares floor(100/(0.20+spread))),
  `test_improved_opening_volume_and_relative_spread`, `test_eligibility_minute_blocks_earlier_signals`
  (`ctx["from"]` = "11:00" → no ticket from a 10:00 breakout).
- [ ] **Step 2:** Run `python -m unittest discover -s tests -p test_ticket.py` — Expected: FAIL (no `signal`).
- [ ] **Step 3:** Implement `signal` in `bot/ticket.py`.
- [ ] **Step 4:** Run — Expected: PASS; full suite OK.
- [ ] **Step 5: Commit** `git commit -m "Ticket engine: the owner's rules and the improved rules"`

### Task 4: Trade simulation with costs and the random-direction mirror

**Files:**
- Create: `bot/ticket_backtest.py`
- Test: `tests/test_ticket_backtest.py`

**Interfaces:**
- Consumes: a ticket from `ticket.signal`, the day's 1-minute bars, `ctx`.
- Produces: `simulate(t: dict, bars1: list[tuple], ctx: dict, direction: int = 1) -> dict` — `{"filled": bool,
  "fill_at", "fill", "exits": [{"qty", "price", "at", "kind": "stop"|"target"|"breakeven"|"time"|"trail"|"vwap"|"eod"}],
  "gross_r", "cost_r", "net_r"}` (R per trade = P&L / (R × shares)); `direction=-1` mirrors entry trigger, stops and
  targets around the entry (short) with the same costs. ORIGINAL management: stop on all shares until target 1; after
  target 1 the rest's stop = entry; time stop 60 min after the fill if target 1 not hit (exit next 1-minute open);
  after target 1 trail at last higher low − 0.10 and exit on a 5-minute close below VWAP; anything open exits at the
  last minute before `close`. IMPROVED: two legs, each stop entry − D; leg A target; leg B exits at `close` − 5 min.

- [ ] **Step 1: Write the failing tests**: `test_limit_fills_only_when_low_reaches_it`, `test_stop_limit_triggers_then_fills`,
  `test_stop_gap_fills_at_open`, `test_stop_first_when_both_touched`, `test_target_then_breakeven_original`,
  `test_time_stop_60_minutes`, `test_trail_and_vwap_exit_after_target`, `test_end_of_day_exit_on_early_close`,
  `test_costs` (333 shares at 10.05, spread 0.02: commission 2 × max(0.35, 333 × 0.0035), half-spread 0.01 each side
  → cost_r = (2 × 1.1655 + 333 × 0.02) / 100), `test_mirror_short_is_symmetric_on_symmetric_bars`.
- [ ] **Step 2:** Run `python -m unittest discover -s tests -p test_ticket_backtest.py` — Expected: FAIL.
- [ ] **Step 3:** Implement `simulate` in `bot/ticket_backtest.py`.
- [ ] **Step 4:** Run — Expected: PASS; full suite OK.
- [ ] **Step 5: Commit** `git commit -m "Ticket backtest: fills, exits, costs, random-direction mirror"`

### Task 5: Candidate universes A and B

**Files:**
- Modify: `bot/ticket_backtest.py`
- Test: `tests/test_ticket_backtest.py`

**Interfaces:**
- Consumes: `backtest.load_reports`, `backtest.load_frames`, `backtest.load_filings`, `shorts.remap`,
  `shorts.usable_from`, `shorts.shares_index`, `shorts.shares_at`, `common.cik_tickers`, `alpaca.daily`,
  `alpaca.minute_bars`, `alpaca.calendar`.
- Produces: `universe_a(path: str) -> list[tuple[str, str]]` (`(day, ticker)` from the picks CSV);
  `fixed_b(day: str, reports, shares, primary, daily) -> dict[str, dict]` — `{ticker: {"short", "shares", "avg_vol",
  "prev_close", "atr"}}` for tickers passing the spec's fixed part (shares < 50M, short/shares > 0.20, avg volume of
  the 63 sessions before > 500K, short/avg > 5, previous close > 2), skipping ticker-days with a close ratio < 0.5 or
  > 2 inside the 63 sessions (counted in `skipped_split`); `eligible_from(session: str, bars1, avg_vol: float,
  open_: str) -> str | None` — REGULAR: first minute ≥ `open_` with cumulative regular volume / avg_vol > 2;
  PREMARKET: first minute < `open_` with cumulative pre-market volume / avg_vol ≥ 0.10 and close > 2.
  `open_rel_vol(symbol: str, day: str, sessions: list[str]) -> float | None` — the 09:30–09:35 volume of `day`
  divided by the mean 09:30–09:35 volume of the 14 sessions before it (`alpaca.bars(symbol, ..., "5Min")`, one
  request); None with fewer than 14 prior sessions of data.

- [ ] **Step 1: Write the failing tests**: `test_universe_a_reads_the_picks`, `test_fixed_b_each_gate_at_its_edge`
  (shares 50,000,000 out, 49,999,999 in; short/shares 0.20 out; avg 500,000 out; short/avg 5 out; prev close 2.00
  out), `test_fixed_b_uses_only_reports_usable_that_day`, `test_split_in_window_skips_and_counts`,
  `test_eligible_from_is_the_first_qualifying_minute` (no earlier minute qualifies; PREMARKET needs close > 2).
  `test_open_rel_vol_same_window_of_14_sessions` (14 prior 09:30 bars of 1,000 and today 2,000 → 2.0; 13 prior → None).
- [ ] **Step 2:** Run — Expected: FAIL.
- [ ] **Step 3:** Implement in `bot/ticket_backtest.py`.
- [ ] **Step 4:** Run — Expected: PASS; full suite OK.
- [ ] **Step 5: Commit** `git commit -m "Ticket backtest: candidate universes (earlier picks, current screen approximated)"`

### Task 6: Statistics, verdict, report and the run

**Files:**
- Modify: `bot/ticket_backtest.py`
- Test: `tests/test_ticket_backtest.py`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: `stats(trades: list[dict]) -> dict` (`n`, `mean`, `t`, `win`, `median`, by year, by price bucket < 5 /
  ≥ 5, exit kinds); `verdict(real: list[dict], random_means: list[float]) -> tuple[list[tuple[str, bool]], bool]`
  with the four Global-Constraints checks; `render(meta, results) -> str` (Hebrew summary, RTL-clean lines, tables,
  every assumption of the spec, data gaps); `main(argv)` — `python -m bot.ticket_backtest [--start 2024-01-02]
  [--end <last complete session>] [--universe A|B|AB]`: loads data (day by day, cached), evaluates both rule sets ×
  both sessions × universes, 20 random seeds (`random.Random(seed)` per ticker-day), writes
  `docs/backtest/ticket-<date>.md` and `docs/backtest/ticket-<date>-trades.csv`, prints `GO`/`NO-GO` per rule set ×
  session; exit 0. The spread callable for `ticket.signal` is `alpaca.quote_at(symbol, alpaca.utc(day, hhmm))` →
  ask − bid.

- [ ] **Step 1: Write the failing tests**: `test_stats_t_and_years`, `test_verdict_edges` (99 trades → fail; t 1.99 →
  fail; mean below the 95th random percentile → fail; 2026 mean ≤ 0 → fail; all pass → GO),
  `test_report_rtl_clean_and_names_every_assumption`, `test_main_dry_on_tiny_mocked_data` (one ticker-day per
  universe, mocked Alpaca → report and CSV written to a temp dir).
- [ ] **Step 2:** Run — Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run — Expected: PASS; full suite OK; actionlint clean.
- [ ] **Step 5: Live run** with the keys in the environment: first `--start 2024-01-02 --end 2024-01-31 --universe A`
  (Expected: completes, a few dozen ticker-days, report readable), then the full range. Record runtime, request
  count and data gaps in the report's metadata.
- [ ] **Step 6: Commit** the code, `docs/backtest/ticket-<date>.md` and the trades CSV;
  `git commit -m "Ticket backtest: verdict and report"`; push the branch; open the PR (body ends with the Claude Code
  footer and the session link).
