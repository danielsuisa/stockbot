# Recommendation algorithm phase B (levels engine, costs, `/ticket`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build spec section 3 as pure functions in `bot/levels.py` (levels, size, refusals, the daily-bar fill model), with section 5's costs and cost guard. Add a live `/ticket SYMBOL` that sends section 8's Hebrew message with "אין אות תומך" (no supporting signal), `breakout`, H = 21 and the label "ללא יתרון מוכח" (no proven edge).

**Architecture:**
- **`bot/levels.py`** has no I/O. It turns daily bars through D, a guard spread and the risk into a ticket or a refusal. It also holds the section 3 simulation and the section 5 costs, so the backtest, shadow and live later share one code path (spec section 3).
- **`bot/ticket_command.py`** does the I/O. It picks D, reads Alpaca bars, the calendar and the 15:55 quote, calls `levels`, and writes the Hebrew message.
- **`bot/listen.py`** gets the `/ticket` command.
- It reuses `ticket.atr14`, `alpaca.daily`, `alpaca.calendar`, `alpaca.quote_full` and `alpaca.utc`, plus `common.send`, `common.code`, `common.price`, `common.il_date` and `listen.extract`.

**Tech Stack:** Python 3.11+ stdlib; unittest with the network mocked; `tests/msgrules.py` for message style.

**Spec:** `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md`, revision 7 (commit `9a2eba0`): sections 3, 5 and 8 (scope: section 7 B).

## Global Constraints

- **No signals.** Section 4 is not used. Every ticket is "אין אות תומך" with `breakout` and H = 21.
- **No outcomes.** No outcome or return of any past ticket is computed, printed, logged or saved. The simulation functions are tested only on synthetic bars. `/ticket` never calls them, and a test enforces this.
- **Scope.** Nothing from phases C–E: no journal (`data/ticket_journal.json`), no scan, no universe test, no K. No merge to main.
- **ATR and R.** `ATR = ticket.atr14` from bars through D. **R = 2 × ATR**.
- **Breakout entry.** Stop = high of D + $0.01. Limit = stop + 0.25 × ATR. Valid for the 3 sessions after D. Planned entry P = stop.
- **Open entry** (implemented and tested; unused by `/ticket` in B). Limit = close of D + 0.5 × ATR. Fills at the open when open ≤ limit. P = close of D.
- **Size.** shares = floor(risk / R). Leg A = floor(shares / 2), leg B = shares − leg A. Risk = env `TICKET_RISK_USD`, default 100.
- **Exits.** Stop loss for both legs at F − R. Leg A target at F + 1.5 × R. Leg B trails by R. Time exit at the close of the H-th session after the entry session.
- **Quote-free refusals:**
  - fewer than 15 daily bars;
  - shares < 2;
  - shares × P > 1% of the 20-session average dollar volume.
- **Quote-based refusals:**
  - no usable quote;
  - cost guard: (guard spread × shares + commission of 4 orders) / (shares × R) > 0.10.
- **Costs.** Commission is $0.0035 a share, minimum $0.35 an order, over 4 orders. The quote is the latest SIP quote no older than 60 s. A locked or crossed quote counts $0.01. Guard spread = the SIP quote at 15:55 ET of D. Backtest spread = 09:35 ET of the entry session, falling back to the guard spread. Stress case: spread × 1.5.
- **Messages.** Hebrew RTL, Hebrew first on every line, numbers and symbols in `<code>`, Israeli dates, no "R" unit (`tests/msgrules.py`). Artifacts are in English.
- **Secrets.** Alpaca keys and SEC_UA come only from the environment. Live checks run on GitHub Actions from a throwaway branch.

## Decisions this plan makes where the spec is silent (for the owner's review)

1. **D for a live `/ticket`.** D is the latest session whose close was at least 15 minutes ago (Alpaca's free-plan lag, `shadow.LIVE_LAG`). A request during a session therefore gives a ticket on the previous session, valid for the next 3 sessions, and the message says so when the first of them is already trading.
2. **`atr14` through D.** `ticket.atr14(daily, day)` reads bars strictly before `day`, so the call is `atr14(bars, next_day)`, where `next_day` is the calendar day after D.
3. **Prices for orders.** Prices are rounded to a cent ($1 and up) or to $0.0001 below $1:
   - the stop, limit, stop loss and target are rounded to nearest;
   - R and the share count use the unrounded R;
   - the simulation uses the rounded prices, which are the prices the orders carry.
4. **Levels shown before the fill.** The stop loss and the target depend on the fill F. The message shows them from the planned entry P and says they move with the actual fill (F − R, F + 1.5 × R). The time-exit date shown assumes a fill on the first valid session; the message gives the rule for a later fill.
5. **Cost in R in the message** is shown as a percentage of the risk: cost / (shares × R) × 100, at the guard spread. This is because the message rules forbid an "R" unit.
6. **Simulation edge rules** (spec section 3, followed as written; they matter from phase C on):
   - On the entry session the stop fills at the stop, never at an open that came before the fill.
   - A resting breakout order (open above the limit) fills at the limit when a later low reaches it, even if a later session opens below the limit, as the spec says.
   - A target inside the entry session's bar counts unless the stop is also inside it (rule 2).
7. **Half-day sessions. OPEN, needs the owner:** the guard quote is "15:55 ET of D", but on a 13:00 close no quote exists at 15:55, so every half-day D gives `NO TICKET` (no usable quote). The plan follows the spec as written (15:55). The alternative is 5 minutes before the session's close (15:55 on a normal day), and likewise 5 minutes after the open instead of 09:35.
8. **No universe mark in B.** Section 4's "מחוץ ליקום שנבדק" (outside the tested universe) needs the point-in-time universe (insider filings, phase C). Phase B's message shows no universe line.

## Review Focus

1. A symbol with fewer than 15 bars, a split inside the last 15 bars, or a stale or missing quote must give a clear `NO TICKET` reason, never a crash or a wrong size.
2. A request at night, at the weekend or on a holiday must pick the right D and the right 3 valid sessions and time-exit date (holidays skipped).
3. A stock under $1 or above $1,000 must give sane prices (rounding) and sizes, including shares < 2 at a high R.
4. An unknown or lowercase symbol, or `BRK.B` / `BRK-B`, must be validated as in the other commands.
5. The message must pass `msgrules.check` for every refusal reason and for a full ticket.

---

### Task 1: Levels, size and quote-free refusals

**Files:**
- Create: `bot/levels.py`
- Test: `tests/test_levels.py`

**Interfaces:**
- Produces:
  - Constants: `MODES = {"open", "breakout"}`; `DEFAULT_MODE, DEFAULT_H = "breakout", 21`; `R_ATR, OPEN_LIMIT_ATR, BREAKOUT_LIMIT_ATR, TARGET_R = 2.0, 0.5, 0.25, 1.5`; `BREAKOUT_TICK, BREAKOUT_DAYS = 0.01, 3`; `MIN_BARS, MIN_SHARES, MAX_ADV_SHARE, ADV_SESSIONS = 15, 2, 0.01, 20`.
  - `px(x: float) -> float`: Decision 3 rounding.
  - `atr_through(bars: list, day: str) -> float | None`, which returns `ticket.atr14(bars_through_day, next calendar day)`.
  - `adv(bars: list) -> float | None`: the mean of close × volume over the last 20 bars; None with fewer.
  - `plan(symbol, bars, day, mode="breakout", horizon=21, risk=100.0) -> dict`, which returns either:
    - a ticket `{"ok": True, "symbol", "day", "mode", "horizon", "atr", "R", "entry_stop" (breakout only), "limit", "planned_entry", "shares", "legs": (a, b), "stop_loss", "target", "trail"}`; here `stop_loss`, `target` and `trail` are from P;
    - or `{"ok": False, "reason"}` with reason one of `"fewer than 15 bars"`, `"shares below 2"`, `"size above 1% of dollar volume"`.
  - `bars` are `alpaca.daily` rows `(date, o, h, l, c, v)` ascending; rows after `day` are ignored.

- [ ] **Step 1: Write the failing tests** in `tests/test_levels.py`. Use a helper `daily(n, close=50.0, rng=1.0, vol=1_000_000, start="2026-01-02")` that builds n weekday bars with high = close + rng/2 and low = close − rng/2.
  - `test_breakout_levels`:
    - 20 bars, every true range 1.0, so ATR = 1.0 and R = 2.0.
    - D's high is 50.5, so `entry_stop == 50.51` and `limit == px(50.51 + 0.25) == 50.76`.
    - `planned_entry == 50.51`, `stop_loss == 48.51`, `target == 53.51`, `trail == 2.0`.
    - `shares == 50` (100 / 2.0), `legs == (25, 25)`.
  - `test_open_levels`: same bars with mode `"open"`. Then `limit == 50.5` (close 50 + 0.5) and `planned_entry == 50.0`.
  - `test_bars_after_day_are_ignored`: appending a bar dated after D with high 999 changes nothing (no look-ahead).
  - `test_sizing_edges`:
    - risk 100 with R = 40 gives 2 shares, legs (1, 1).
    - R = 50.01 gives `"shares below 2"`.
    - 3 shares give legs (1, 2).
  - `test_fewer_than_15_bars`: 14 bars refuse with `"fewer than 15 bars"`; 15 bars give a ticket.
  - `test_dollar_volume_share`:
    - close 50 with volume 4,000 gives ADV $200,000; its 1% is $2,000.
    - 50 shares × P 50.51 = $2,525.50 is above it, so `"size above 1% of dollar volume"`.
    - Volume 5,051 gives 1% of ADV = $2,525.50, equal to the position, so a ticket. Only "above" refuses (compare with a 1e-9 tolerance).
  - `test_px`: `px(50.7651) == 50.77`, `px(0.123456) == 0.1235`, `px(1234.5649) == 1234.56`. Avoid exact half-cents in tests (binary floats).
- [ ] **Step 2: Run.** `SEC_UA= PYTHONUTF8=1 python <scratchpad>/run_tests.py tests.test_levels`. Expect FAIL (no module).
- [ ] **Step 3: Implement** the signatures above. `plan` checks the bars first, then size, then dollar volume.
- [ ] **Step 4: Run the full suite** (`run_tests.py` with no arguments). Expect OK.
- [ ] **Step 5: Commit.** Message: "Phase B: levels engine — breakout/open levels, size and quote-free refusals (spec section 3)".

### Task 2: Costs, the usable quote and the cost guard

**Files:**
- Modify: `bot/levels.py`
- Test: `tests/test_levels.py`

**Interfaces:**
- Consumes: Task 1's ticket dict.
- Produces:
  - `COMMISSION, MIN_ORDER, TICK, QUOTE_AGE, COST_GUARD, STRESS = 0.0035, 0.35, 0.01, 60, 0.10, 1.5`.
  - `GUARD_AT, ENTRY_AT = "15:55", "09:35"` (Decision 7: as written).
  - `spread(quote) -> float | None`. The quote is `alpaca.quote_full`'s `(bid, ask, bid_size, ask_size, age_s)` or None. A missing quote, a side ≤ 0, or age > 60 gives None. A locked or crossed quote gives 0.01. Otherwise it gives ask − bid.
  - `commission(legs: tuple[int, int]) -> float`: Σ max(0.35, q × 0.0035) over the 4 orders (a, b, a, b).
  - `cost(ticket, spread: float, stress=False) -> float`: the dollars of `commission(legs) + spread × shares` (spread × 1.5 under stress).
  - `cost_r(ticket, spread, stress=False) -> float`, equal to cost / (shares × R).
  - `guard(ticket, quote) -> dict`, which returns the ticket plus `"guard_spread"` and `"cost_r"`, or a refusal `"no usable quote"` or `"cost above 10% of risk"`.

- [ ] **Step 1: Write the failing tests.**
  - `test_spread`:
    - `(10.00, 10.02, 1, 1, 5)` gives 0.02.
    - A locked quote `(10, 10, ...)` gives 0.01. A crossed quote `(10.02, 10.00, ...)` gives 0.01.
    - `None` gives None. A bid of 0 gives None. Age 61 gives None.
  - `test_commission`:
    - legs (25, 25) give 4 × 0.35 = 1.40, since 25 × 0.0035 = 0.0875 is below the minimum.
    - legs (500, 500) give 4 × 1.75 = 7.00.
  - `test_cost_and_stress`: 50 shares, R = 2 and spread 0.02 give `cost_r == (1.40 + 1.00) / 100`. The stress case gives `(1.40 + 1.50) / 100`.
  - `test_cost_guard_edge`: pick the spread so that cost_r is 0.10 (50 shares, R 2, spread 0.172: (1.40 + 8.60) / 100) and assert a ticket. Only "above 0.10" refuses, compared with a 1e-9 tolerance. Add $0.0001 to the spread and assert the refusal `"cost above 10% of risk"`. A `None` quote gives `"no usable quote"`.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.** Expect OK.
- [ ] **Step 5: Commit.** Message: "Phase B: costs, usable quote and the cost guard (spec section 5)".

### Task 3: The daily-bar fill model (synthetic bars only)

**Files:**
- Modify: `bot/levels.py`
- Test: `tests/test_levels.py`

**Interfaces:**
- Consumes: Task 1's ticket, Task 2's `commission`.
- Produces:
  - `fill(ticket, after: list) -> tuple[int, float] | None`. `after` holds the bars of the sessions after D. The result is (index of the entry session, F), or None for no fill.
  - `exits(ticket, F: float, k: int, after: list) -> list[tuple[str, float, int]] | None`. The result is per leg (exit type `"stop" | "target" | "trail" | "time"`, price, session index). It is None when the bars end before the time exit.
  - `gross_r(ticket, F, legs_exits) -> float`, equal to Σ leg shares × (exit − F) / (shares × R).
  - `simulate(ticket, after, spread) -> {"filled": bool, "gross_r": float, "cost_r": float} | None`. An unfilled ticket gives 0 and 0.
  - Section 3 rules 1–5 apply. The levels after the fill are recomputed from F (stop F − R, target F + 1.5 × R, rounded by `px`). Decision 6 governs the entry session and resting orders.

- [ ] **Step 1: Write the failing tests**, one per rule and edge, with hand-checkable numbers. The breakout ticket is stop 50.51, limit 50.76, R 2.
  - `test_breakout_fills`:
    - high ≥ stop with open 50.0 fills at 50.51.
    - open 50.60 (above the stop, at or below the limit) fills at 50.60.
    - open 51.0 (above the limit) with low 50.70 fills at the limit 50.76 on the same session.
    - open 51.0 with low 50.9, and a next-session low of 50.7, fills at 50.76 on index 1.
    - 3 sessions with high < stop give None. A trigger on the 4th session gives None (expired).
  - `test_open_fills`: open ≤ limit fills at the open. Open > limit gives None.
  - `test_stop_gap_rule1`: on a later session, an open below the stop exits at the open.
  - `test_stop_first_rule2`: when one bar holds both F − R and F + 1.5 × R, leg A exits at the stop.
  - `test_entry_session_stop_rule3`: for breakout, the entry session's low ≤ F − R exits at F − R, even with an open below it.
  - `test_trailing_rule4`: highs 52, 54, then a session with low 51.9 give running max 54 and level 52, so the exit is at 52. A gap open below the previous session's level exits at the open.
  - `test_target_at_open_rule5`: an open above the target exits leg A at the open.
  - `test_time_exit`: with H = 21 and no stop or target, both legs exit at the close of session k + 21. Bars ending before it give None.
  - `test_unfilled_is_zero`: `simulate` on no fill gives `{"filled": False, "gross_r": 0.0, "cost_r": 0.0}`.
  - `test_gross_r_by_hand`: legs (25, 25) at F 50.51, A exiting at the target 53.51 and B at 52.51, give (25 × 3 + 25 × 2) / (50 × 2) = 1.25.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.** `fill` scans at most `BREAKOUT_DAYS` sessions. `exits` walks the sessions from k to k + H and applies, in order: the stop check (rule 3 on the entry session, rule 1 after it), the target check (rules 2 and 5), and the trail (rule 4).
- [ ] **Step 4: Run the full suite.** Expect OK.
- [ ] **Step 5: Commit.** Message: "Phase B: the daily-bar fill model of spec section 3 (synthetic tests only; not called on market data)".

### Task 4: `/ticket` data path and message

**Files:**
- Create: `bot/ticket_command.py`
- Test: `tests/test_ticket_command.py`

**Interfaces:**
- Consumes: `levels.plan`, `levels.guard`, `levels.cost_r`, `alpaca.daily`, `alpaca.calendar`, `alpaca.quote_full`, `alpaca.utc`, `common.env`, `common.code`, `common.price`, `common.il_date`.
- Produces:
  - `LIVE_LAG_MIN = 15`.
  - `SOURCE`: a Hebrew source line naming the levels engine, Alpaca SIP daily bars and the 15:55 quote, and the label.
  - `signal_day(now_ny: datetime, cal: dict) -> str`: Decision 1.
  - `sessions_after(day, cal, n) -> list[str]`.
  - `build(symbol, now=None) -> dict`. It reads the calendar (D − 60 days … D + 60 days), the daily bars (D − 60 days … D) and `quote_full(symbol, alpaca.utc(D, "15:55"))`. It returns the guarded ticket plus `"valid_sessions"` (3 dates) and `"time_exit_if_first"` (the date of session 21 after the first valid session), or the refusal.
  - `message(t) -> str`: the Hebrew text below.
  - `REASONS`: English refusal reason to Hebrew.

- [ ] **Step 1: Write the failing tests.** Mock `alpaca.daily`, `alpaca.calendar` and `alpaca.quote_full`. Fix `now` with `frozen_dt` as in `tests/test_commands.py`.
  - `test_signal_day`:
    - Monday 16:20 NY gives that Monday.
    - Monday 16:10 gives the previous Friday.
    - Saturday gives Friday.
    - The day after a holiday, at 10:00, gives the session before the holiday.
  - `test_build_ticket`: 30 synthetic bars and quote `(50.50, 50.52, 1, 1, 3)`. Assert:
    - `ok`, `shares == 50`, `valid_sessions` is the next 3 sessions;
    - the `alpaca.quote_full` call is made at `alpaca.utc(D, "15:55")`;
    - `cost_r == (1.40 + 50 × 0.02) / 100`.
  - `test_message_full`. The message contains each of these, and `msgrules.check` passes:
    - `code("AAPL")`;
    - "אין אות תומך" (no supporting signal);
    - "קניית פריצה" (breakout buy), the stop and the limit;
    - "סטופ לוס" (stop loss) with `price(48.51)`;
    - "יעד" (target) with `price(53.51)`;
    - "טריילינג" (trailing) with `price(2.0)`;
    - the shares per leg;
    - "יציאה בזמן" (time exit) with an Israeli date;
    - "עלות" (cost) with a percentage;
    - "ב-IBKR" (in IBKR) with the two-bracket mapping;
    - "ללא יתרון מוכח" (no proven edge).
  - `test_message_refusals`: one message for each reason (`fewer than 15 bars`, `shares below 2`, `size above 1% of dollar volume`, `no usable quote`, `cost above 10% of risk`). Each starts with "אין כרטיס" (no ticket), names the reason in Hebrew and passes `msgrules.check`.
  - `test_risk_from_env`: with `TICKET_RISK_USD=200`, shares double.
  - `test_no_simulation_on_market_data`: the source of `bot/ticket_command.py` contains none of `simulate(`, `fill(`, `exits(`, `gross_r(`.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement `build` and `message`.** The message has one item per line, each line starting in Hebrew:

```
🎫 כרטיס ל-<code>AAPL</code> · יום האות <date>
מקור: בקשה ידנית · אותות: אין אות תומך
כניסה: קניית פריצה — סטופ <price> · לימיט <price> · בתוקף ב-<3 dates>
סטופ לוס (שתי המנות): <price> · יעד מנה א': <price> · טריילינג מנה ב': <price>
המחירים מחושבים ממחיר הכניסה המתוכנן ויזוזו עם הכניסה בפועל (כניסה פחות <price>, כניסה ועוד <price>)
יציאה בזמן: בסגירה של יום המסחר ה-<code>21</code> אחרי הכניסה (כניסה ב-<date> → <date>)
מניות: מנה א' <code>n</code> · מנה ב' <code>n</code> · סיכון <price>
עלות משוערת: <code>x%</code> מהסיכון (מרווח ב-15:55 של יום האות ועמלות)
ב-IBKR: שתי פקודות bracket בחצי כמות — א': כניסה + יעד לימיט + סטופ · ב': כניסה + TRAIL — עד היציאה בזמן
תווית: ללא יתרון מוכח
```

  The English template above reads:
  - **Line 1:** 🎫 Ticket for AAPL · signal day `<date>`
  - **Line 2:** Source: manual request · Signals: no supporting signal
  - **Line 3:** Entry: breakout buy — stop `<price>` · limit `<price>` · valid on `<3 dates>`
  - **Line 4:** Stop loss (both legs): `<price>` · Leg A target: `<price>` · Leg B trailing: `<price>`
  - **Line 5:** The prices are computed from the planned entry and will move with the actual entry (entry minus `<price>`, entry plus `<price>`)
  - **Line 6:** Time exit: at the close of the 21st trading day after the entry (entry on `<date>` → `<date>`)
  - **Line 7:** Shares: leg A `n` · leg B `n` · risk `<price>`
  - **Line 8:** Estimated cost: `x%` of the risk (spread at 15:55 on the signal day, plus commissions)
  - **Line 9:** In IBKR: two bracket orders at half size each — A: entry + limit target + stop · B: entry + TRAIL — until the time exit
  - **Line 10:** Label: no proven edge

  Add a line when the first valid session has already started (Decision 1): "שים לב: יום הכניסה הראשון כבר במסחר" (note: the first entry day is already trading). Refusal format: `אין כרטיס ל-<code>X</code>: <Hebrew reason>` (no ticket for X: `<reason>`).
- [ ] **Step 4: Run the full suite.** Expect OK.
- [ ] **Step 5: Commit.** Message: "Phase B: /ticket data path and Hebrew message (no signals, breakout, H = 21, label unproven)".

### Task 5: The listener command

**Files:**
- Modify: `bot/listen.py` (`COMMANDS`, `HELP`, docstring, a `ticket_cmd(rest)` branch in `handle`)
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `ticket_command.build`, `ticket_command.message`, `ticket_command.SOURCE`, `listen.extract`.
- Produces: `/ticket SYMBOL`, which replies with `common.send(message, source=ticket_command.SOURCE)`.
  - No symbol: a usage line.
  - An unknown symbol: the existing "לא מצאתי ברשימת החברות של SEC" reply (I didn't find it in SEC's company list).
  - Several symbols: one message each, at most 3.

- [ ] **Step 1: Write the failing tests.**
  - `test_ticket_command`: patch `ticket_command.build` and `message`. Then `/ticket aapl` sends one message with `source=ticket_command.SOURCE`.
  - `test_ticket_unknown_and_empty`: `/ticket` alone gets the usage line, and `/ticket ZZZZ` gets the SEC-list reply.
  - Update `test_help_and_menu` to include `ticket`.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.** Add `("ticket", "כרטיס מסחר למניה")` (trading ticket for a stock) to `COMMANDS` and a `code('/ticket AAPL')` line to `HELP`.
- [ ] **Step 4: Run the full suite.** Expect OK.
- [ ] **Step 5: Commit.** Message: "Phase B: /ticket in the listener".

### Task 6: Live dry run, review, report

**Files:**
- Create: `docs/backtest/levels-phaseB-<date>.md`, a short English report: what was built, the tests, the live dry-run messages, the decisions and the limitations. It contains no outcome.
- Throwaway branch `probe-ensemble` (deleted afterwards). The workflow runs `python -m bot.ticket_command AAPL BRK.B <a sub-$5 symbol> <a thin symbol>` without `TG_TOKEN`, so `common.send` prints the messages.

- [ ] **Step 1:** `bot/ticket_command.py` gets a `main(argv)` that prints `message(build(s))` for each symbol (a dry run). Test `test_main_prints` with the network mocked.
- [ ] **Step 2: Live dry run on GitHub.** Expected: success, with one ticket or one Hebrew refusal per symbol in the log. Check the D, the dates and the prices against the bars by hand.
- [ ] **Step 3: Independent review.** A reviewer who did not write the code (most capable model) reviews the code, the tests (section 9's levels list) and the dry-run messages. Fix confirmed findings with tests.
- [ ] **Step 4: Commit** to `recommendation-algorithm` and delete the throwaway branch. No merge to main. `/ticket` reaches Telegram only after the owner approves a merge (the listener runs from main).
- [ ] **Step 5: Report to the owner** in Hebrew: commits, tests, the dry-run messages, findings, open decision 7.
