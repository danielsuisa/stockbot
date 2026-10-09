# Order-ticket backtest — design

Date: 2026-10-08 · Status: design approved in chat (owner sent the data keys); this spec awaits the owner's review ·
Branch: `ticket-backtest` · Background: `reports/ניתוח מודול כרטיס פקודה.md` (research report, 2026-10-08)

## 1. Purpose and decisions

Decide, with intraday data and costs, whether the owner's order-ticket rules (a breakout entry on the bot's
short-squeeze candidates) have an edge before any ticket is sent to Telegram. Owner's decisions (2026-10-08):
backtest first · include `PREMARKET` · free data (Alpaca Basic) · both candidate universes (section 4) · the owner's
rules as written vs the research report's improved version, each also against a random-direction benchmark · any
rule change is judged in the tuning period only.

Out of scope until a GO: live tickets, Telegram output, IBKR order automation, paid data.

## 2. Measured facts (2026-10-08)

- Alpaca data keys work (paper account). Historical SIP 1-minute bars cover pre-market from 04:00 ET with volume
  and a per-bar `vw`: WOLF 2026-10-08 04:00–09:30 = 272 bars, 2,198,942 shares. Nasdaq's "pre-market share volume"
  for the same session was 1,602,332, so live thresholds calibrated on Nasdaq differ by ~1.4x (to be measured on more
  names before any live use).
- Rate limit 200 requests/minute (`X-Ratelimit-Limit`); up to 10,000 bars per request; historical SIP quotes
  (bid/ask) are available (AAOI 2024-01-02 09:35).
- Delisted stocks are covered: BBBY 2022-08-16, BIG 2024-06-03, NKLA 2025-01-15, TUP 2024-08-01 all return 1-minute
  bars with pre-market volume.
- The 1,330 out-of-sample picks of the earlier backtest (`docs/backtest/squeeze-2026-09-27-picks.csv`) are all
  tickers still in SEC's current map: that universe already carries survivorship bias.

## 3. Architecture (stdlib only, existing patterns)

| File | Purpose |
|---|---|
| `bot/alpaca.py` | Alpaca market-data client: minute / 5-minute / daily bars (SIP, paginated, multi-symbol), quotes at a moment; keys from env `ALPACA_KEY_ID` / `ALPACA_SECRET_KEY`; disk cache `.cache/alpaca/` (gitignored), one file per symbol and day |
| `bot/ticket.py` | Pure rule engine shared by the backtest and (after a GO) live tickets: 5-minute bars from 1-minute bars, session VWAP, confirmed swing highs / higher lows, opening range, the two rule sets → a ticket or `NO TICKET` with the failed check |
| `bot/ticket_backtest.py` | Candidate universes, trade simulation on 1-minute bars, costs, statistics, verdict, Markdown report + trades CSV in `docs/backtest/` |

Keys never enter the repository; GitHub secrets of the same names are for later live use.

## 4. Candidates

- **A — earlier picks:** the 1,330 rows `(date, ticker)` of the picks CSV (2024-01-02 onward; made before the open of
  `date` from data through the day before). Each is tested on `date`, in both sessions.
- **B — the current screen, approximated:** every session `D` from 2024-01-02 to the last complete day, for the
  tickers in SEC's map (survivorship bias reported):
  - fixed part, from data before `D`: FINRA short interest usable on `D` (the existing `shorts.usable_from` rule);
    SEC shares outstanding usable on `D` as the float proxy (no free float history): shares < 50M, short / shares
    > 20%; Alpaca daily bars of the 63 sessions before `D`: average volume > 500K, short / average volume > 5,
    previous close > $2. Listed options: not applied (no history; reported).
  - intraday entry into the list (no look-ahead): `REGULAR` from the first minute ≥ 09:30 whose cumulative regular
    volume / average daily volume > 2; `PREMARKET` from the first minute whose cumulative pre-market volume /
    average daily volume ≥ 0.10 with the last price > $2. Rules are evaluated only from that minute on.
- A ticker-day in both A and B counts once when the universes are pooled.

## 5. Rule sets (all on 5-minute bars built from 1-minute bars; a bar is used only after it closes)

Shared definitions: swing high = a 5-minute bar whose high is above the highs of the 2 bars before and the 2 after
(known only after those 2 bars close); swing low likewise; higher low = a swing low above the previous swing low;
session VWAP = Σ(vw·v)/Σv from 09:30 (`REGULAR`) or 04:00 (`PREMARKET`); ATR14 = 14-day average true range from daily
bars before `D`; risk per trade = $100 (results are reported in R, so the amount only sets share rounding).

**ORIGINAL — the owner's spec as written**, with two fixes, both reported: the trail uses the *most recent* higher
low (as written, "the lowest higher low after entry" never moves); "swing high" / "higher low" use the definition
above.
- `REGULAR`: level = max(high of 09:30–09:45, most recent swing high); checks from the 09:50 bar close to the 15:00
  bar close (later entries cannot run the 60-minute time stop).
- `PREMARKET`: level = the pre-market high before the trigger bar; checks from the 04:55 bar close (10 bars of
  history) to the 09:25 bar close.
- Entry checks: spread (SIP quote at the bar close) ≤ $0.05 · last 5-minute close > level · its volume ≥ 2 × mean of
  the previous 10 bars · close > VWAP · no halt now or in the last 15 minutes (historical halts approximated as a gap
  of ≥ 5 minutes without 1-minute bars during 09:30–16:00; reported).
- Orders: buy limit at level + $0.05, DAY (fills on a later 1-minute bar whose low ≤ limit, at the limit); stop =
  most recent higher low − $0.10, at least $0.20 below entry (no higher low → `NO TICKET`); shares =
  floor(100 / R), fewer than 10 → `NO TICKET`; half (floor) sells at entry + 2R; until then the stop covers all
  shares; after target 1 the rest's stop moves to entry; if target 1 is not reached within 60 minutes of the fill,
  everything exits at the next 1-minute open; after target 1 the rest trails $0.10 below the most recent higher
  low and exits when a 5-minute bar closes below VWAP; anything open exits at the 15:59 close.

**IMPROVED — the research report's baseline**
- `REGULAR`: level = high of 09:30–09:35; opening relative volume = 09:30–09:35 volume / mean of the same window over
  the 14 sessions before `D` ≥ 1.0; entry = buy stop-limit triggered by a 1-minute high above the level after 09:35,
  limit = level + 0.1 × ATR14, valid 60 minutes; at the trigger: price > VWAP, spread ≤ 1% of the mid and ≤ 0.1 × D,
  no halt rule as above.
- `PREMARKET`: level = the pre-market high before the trigger; same entry type and filters (no opening-volume
  filter); valid until 09:25.
- Stop distance D = 0.10 × ATR14; shares = floor(100 / (D + spread)), fewer than 10 → `NO TICKET`. Two equal legs
  (the OCA fix): leg A stop entry − D, target entry + 2D; leg B stop entry − D, exits at 15:55.

**Random-direction benchmark:** for each rule set, the same entry moments and prices with a coin-flip direction
(20 seeds; shorts mirror stops and targets; same costs). It tells whether the breakout carries direction or only
catches volatility.

## 6. Fills and costs (conservative)

Commission $0.0035 per share, minimum $0.35 per order, each side · half the spread at entry and half at exit, from
the SIP quote at the signal · a stop fills at the stop, or at the bar's open when the bar opened beyond it · a bar that
touches both stop and target counts as the stop first · buy limits fill only when the low reaches the limit.

## 7. Periods and verdict (fixed before any run)

- Tuning: 2024-01-01 → 2024-12-31 (any rule change is judged here only). Verdict: 2025-01-01 → last complete session.
- Evaluated per rule set × session, on universe B (closest to the live list); if B has fewer than 100 verdict trades,
  on A ∪ B. Universe A is always reported.
- **GO** for a rule set × session iff, in the verdict period, all hold: ≥ 100 trades · mean net R per trade > 0 with
  t ≥ 2 · mean net R above the 95th percentile of the 20 random-direction means · mean net R > 0 in 2025 and in 2026
  separately. Otherwise NO-GO. Win rate, median R, exit types, price buckets (< $5, ≥ $5) and data gaps are reported,
  not gated.

## 8. Output

`docs/backtest/ticket-<date>.md` (Hebrew summary + tables, the verdict per rule set × session, every assumption above)
and `docs/backtest/ticket-<date>-trades.csv`. Runs in the dev workspace with the keys in the environment; data cached
in `.cache/alpaca/`.

## 9. Tests (offline, network mocked)

Alpaca client: pagination, multi-symbol split, cache hit, missing data. Rule engine: each entry check at its edge,
swing / higher-low confirmation never uses unclosed bars, VWAP, both level definitions, `NO TICKET` reasons, share
rounding and the 10-share minimum. Simulation: limit / stop-limit fills, stop gap fill, stop-first on a two-sided bar,
target then breakeven, 60-minute time stop, trailing and VWAP exits, 15:55 / 15:59 exits, costs. Candidates: no
look-ahead in universe B (an entry before the eligibility minute is impossible). Verdict at its thresholds.

## 10. Second look: the IMPROVED spread gate (pre-registered 2026-10-09, before any run)

Why: in the first run (`docs/backtest/ticket-2026-10-09.md`) the spread gate was IMPROVED's most common NO TICKET
reason (593 REGULAR, 797 PREMARKET). With a $0.01 tick, "spread ≤ 0.1 × D" needs D ≥ $0.10 (ATR14 ≥ $1), so most
small caps can never pass it. Owner's go-ahead: 2026-10-09.

- Only the IMPROVED spread gate changes; every other rule, cost and universe stays as in sections 4–7. ORIGINAL is
  not re-run.
- Grid (spread always ≤ 1% of the price): **G0** ≤ 0.10 × D (as before) · **G1** ≤ 0.25 × D · **G2** ≤ 0.50 × D ·
  **G3** no D bound.
- Selection, per session, on the tuning period (2024) only: the gate with the highest mean net R among gates with
  ≥ 30 trades in 2024 (basis A ∪ B); if none has 30, G0 stays and the tuning is reported as underpowered.
- Verdict for the selected gate: section 7's rule unchanged (2025-01-01 → last complete session; ≥ 100 trades, mean
  > 0 with t ≥ 2, above the random 95th percentile, > 0 in 2025 and in 2026).
- Caveat, stated in the report: the 2025–2026 results of G0 were already seen, so this verdict is a second look,
  not a clean out-of-sample test. A GO here is a reason to paper-trade forward, not to trade.

## 11. Clean test of the published strategy: ZBA "Stocks in Play" 5-minute ORB (pre-registered 2026-10-09, before any run)

Why: the owner asked for the best achievable result. Searching more variants on the already-seen 2024–2026 squeeze
data would only overfit. The one strategy with published evidence (ZBA 2024: Sharpe 2.81 on 2016–2023) has never
been tested on 2024 onward, and our data for its universe is unseen. It is tested exactly as published; nothing is
tuned.

- **Universe C, each session D:** Alpaca assets (active and inactive, so delisted names count) on NYSE or NASDAQ,
  symbols of 1–5 capital letters, names without fund/ETF markers. From raw daily bars before D: 14-day average volume
  ≥ 1,000,000 and ATR14 > $0.50 (ticker-days with a close jump > 2x in the window are skipped). From the 09:30–09:35
  5-minute bar of D: open > $5; relative volume = its volume / the mean of the same bar over the 14 previous
  sessions (a missing bar counts 0) ≥ 1.0. The top 20 by relative volume are traded.
- **Rules ZBA:** the 09:30–09:35 candle sets the side: close > open → buy stop at its high; close < open → sell stop
  at its low; close = open → no trade. Valid until the close. Fill when a minute trades at the stop (at the stop, or
  the open if it gapped through). Stop loss 10% of ATR14 from the fill (D = 0.1 × ATR14); everything exits at the
  last minute's close. One trade per ticker-day.
- **Costs and fills:** as in section 6 (commission $0.0035/share, $0.35 minimum; half the SIP spread at the trigger,
  in and out; a locked quote counts $0.01; no quote → NO TICKET; stop at the stop or the gapped open; on the fill
  minute the bar-path rule). R = D; results in R.
- **Random benchmark:** the same fills with a coin-flip side (20 seeds).
- **Verdict (2024-01-02 → 2026-10-07):** GO iff ≥ 100 trades, mean net R > 0 with t ≥ 2, above the random 95th
  percentile, and mean net R > 0 in 2024, in 2025 and in 2026 separately.
- **Replication check (not gated):** the same run on 2022-01-03 → 2023-12-29, inside the paper's sample, to show
  whether this engine with real spreads reproduces the paper's edge.
- **Also reported:** a ZBA-style portfolio (each trade risks 1% of capital; the day's positions are scaled down
  together to at most 4x gross exposure): annual return, Sharpe, worst drawdown — after costs.

## 12. Cost-aware ZBA: stop width and a spread gate (pre-registered 2026-10-09, before the full run)

Why: a one-month probe of section 11 (January 2024, run to check the code and the speed) showed a positive edge
before costs (+0.26R a trade) and costs of 0.77R a trade: with the stop at 10% of ATR14 the stop distance is about
twice the opening spread. Costs in R shrink as the stop widens. That probe looked at January 2024 only, and also at
the four stop widths below on that month (all negative net); January 2024 is therefore left out of this section's
verdict.

- Section 11 stays exactly as registered (its verdict is the K10 row below on 2024-01-02 onward).
- Grid, everything else as in section 11: stop distance k × ATR14 with k ∈ {0.10, 0.25, 0.50, 1.00} (K10, K25,
  K50, K100), each without and with a spread gate (spread ≤ 0.25 × the stop distance; suffix "s"): 8 variants.
- Selection on 2022-01-03 → 2023-12-29 only (unseen by us for this universe): the highest mean net R among variants
  with ≥ 300 trades there.
- Verdict for the selected variant on 2024-02-01 → the last complete session: ≥ 100 trades, mean net R > 0 with
  t ≥ 2, above the 95th percentile of 20 random-side means, and mean net R > 0 in 2024, 2025 and 2026 separately.
- A GO is a reason to paper-trade it forward in the bot, not to trade it.

## 13. Realism check of the selected ZBA variant (pre-registered 2026-10-09, after section 12's run, before this check)

Section 12 selected K10s and its verdict passed (2,007 trades, +0.284R, t 2.92). An independent review found that
three optimistic modelling choices could carry that result: (1) the fill minute's stop is decided by the bar-path
assumption, and 125 K10s trades survive only because of it; (2) Rule 201 (the short-sale restriction after a 10%
drop) is not modelled, though it blocks short sales at or below the bid; (3) no slippage beyond the half spread.
These are corrections to the fill model, not new variants: nothing is re-selected.

- Tick-level fill minute, for every filled K10s trade in both periods: SIP trades of the trigger minute (round lots,
  conditions that update the last sale). The stop triggers on the first trade at or through the level; the order
  fills at the next trade (latency), or the trigger trade when none follows in that minute; then the stop loss
  (fill ∓ D) is checked on the remaining trades of that minute and, if hit, fills at the next trade. From the next
  minute on, the bar model of section 11. A trade whose trigger minute has no round-lot trade at the level is
  dropped (counted).
- Rule 201: a short is dropped when the previous session's low was ≤ 90% of the close before it, or the day's
  regular-session low before the fill is ≤ 90% of the previous close (counted).
- Costs as in section 11. Reported, not gated: +$0.005 and +$0.01 a share each side.
- **Realistic verdict** for K10s on 2024-02-01 onward, the four checks of section 12 on the corrected trades. If it
  fails, the conclusion is NO-GO for the whole ZBA line (every other variant was already negative on 2024+ under the
  optimistic model).

## 14. ZBA v2: the owner's improvements to K10s, developed on seen data, judged once on unseen 2016–2021 (pre-registered 2026-10-09, before any run)

Why: section 13 failed K10s's realistic verdict (2024 below zero). The owner listed improvements (2026-10-09) and
decided: apply them to K10s only; develop on the already-seen 2022-01-03 → 2026-10-08; judge with a single run on
2016-01-04 → 2021-12-31, which nobody has looked at, plus the forward shadow tracker. Both sides (long and short).
Nothing here sends a ticket; a GO only allows a second shadow line (section 14.8).

### 14.0 Measured before designing (STEP 0, `bot/probe_v2.py` on GitHub Actions, 2026-10-09)

Run 37917832860 (1,416 Alpaca requests), 12 sample sessions (the first on/after March 15 and September 15 of
2016–2021, 240 top-20 ticker-days computed with section 11's code):

- **a) News:** Alpaca's news API reaches back to 2015-01-01. 89 of the 240 top-20 ticker-days (37.1%) had an item
  between the previous close (16:00) and 09:35. Per day it ranged 0–13 of 20; two days had none (2017-09-15 and
  2019-03-15, both quarterly expiration and index-rebalancing Fridays). Enough to test #4.
- **b) Quote sizes:** historical SIP quotes carry bid and ask sizes in 2016 (AAPL 2016-03-01: bs 4, as 1). The unit
  (round lots or shares) is checked on a 2016 and a 2025 sample before #3's depth rule is used; the rule is written
  in round lots of 100 shares.
- **c) Survivorship — poor, stated plainly:** all 9 known 2016–2018 delistings checked (LinkedIn, Yahoo, Whole Foods,
  Monsanto, Time Warner, Sears, Staples, Panera, Cabela's) still have daily bars at Alpaca, but **none is in
  Alpaca's asset list as that company**: 7 are absent, and 2 symbols (SHLD, SPLS) now belong to ETFs, which the fund
  filter drops. Universe C is built from the asset list, so stocks that later delisted are missing from the holdout.
  The universe with a bar on the sample day grows from 3,455 (2016-03) to 4,709 (2021-09), +36%, while US listings
  grew far less. Our estimate: roughly 10–25% of the 2016 names are missing, fewer each later year. The exact
  share is unknown with free data.
- **d) Trades:** SIP trades exist for 2016 (5 trades in the 5 seconds probed), so the tick-level fill minute works in
  the holdout.
- **e) Sectors:** 205 of the 240 top-20 tickers (85%) are in today's SEC ticker map and 184 (77%) have a SIC code;
  the missing ones are mostly renamed or delisted. The sector rule is used only in the forward risk layer, not
  gated in the backtest.

### 14.1 Base: K10s as in sections 11–13

Universe C, top 20 by opening relative volume, the first candle's side, stop order at its high/low, D = 0.10 × ATR14,
spread at the trigger ≤ 0.25 × D, out at the close, costs of section 6, the tick-level fill minute and Rule 201 of
section 13, the 20-seed random-side benchmark. Every variant below changes only what it names. A variant that
changes D also moves the spread gate (0.25 × its D) and the share count ($100 / D, 10-share minimum).

### 14.2 The rules (numbers fixed here)

- **#2 Time-of-day relative volume (T15, T2, T3).** At the trigger minute m: the ticker's volume from 09:30 through
  m (inclusive) / the mean of the same window over the 14 previous sessions (a session without bars counts 0) ≥
  1.5 / 2 / 3. The opening-bar relative volume stays the universe ranking.
- **#3 Liquidity (L10, L25).** The 14-day average dollar volume (mean of close × volume of the 14 daily bars before
  D) ≥ $10M / $25M. **Q5 (depth):** the top-of-book size on the trigger side (the ask for a long, the bid for a short) at the trigger,
  from the same SIP quote as the spread, ≥ 5 round lots (500 shares).
- **#4 Catalyst (N).** Require an Alpaca news item for the ticker between the previous session's close (16:00) and 09:35 of D.
  Variants: require (N) / ignore (the base).
- **#5 Breakout confirmation (C15, C2).** Instead of the touch entry: the first minute from 09:35 whose close is
  beyond the first candle's high (long) / low (short), with that minute's volume ≥ k × the mean minute volume since
  09:30 (k = 1.5 / 2). Entry at the next minute's open (bar model: that open plus half the spread; tick level: the
  first round-lot trade of that minute). The stop distance is measured from that fill.
- **#6 Candle filter (F).** The first 5-minute candle's range / ATR14 in [0.10, 0.60] and its body / range ≥ 0.50.
- **#7 Volatility stop (V10, V25).** D = max(k × ATR14, the distance from the fill to the far side of the first
  candle: its low for a long, its high for a short), k = 0.10 / 0.25.
- **#11 Reward-to-risk (RR).** Skip unless the room from the entry level to (the 09:30 open + 1.0 × ATR14) for a long
  — (open − 1.0 × ATR14) for a short — is ≥ 2 × D.
- **#12 Slippage guard (SL).** A stop-limit entry, limit = level ± max(0.25 × D, $0.01). Tick level: the fill trade
  beyond the limit → no trade (counted). Bar model: a fill minute that opens beyond the limit → no trade.
- **#16 Quality ranking (QR).** Among the session's candidates (universe C after the open > $5 and relative volume ≥ 1
  checks), the score = the equal-weight sum of z-scores (across that session's candidates) of opening relative
  volume, 14-day dollar volume, first-candle body / range and the catalyst (1 with a news item as in #4, else 0). The top 20 by this score are traded instead of
  the top 20 by relative volume. Weights are fixed at 1; never tuned.
- **#17 Data quality (DQ).** Skip a ticker-day, counted per reason, when: a minute bar is missing in the 5 minutes
  before the trigger minute or in the trigger minute itself; the quote used for the spread is older than 60 s at the
  trigger; any regular-session minute's VWAP is outside that minute's low–high; a regular-session minute has zero
  volume while its high ≠ its low.

### 14.3 The grid (22 variants, all reported)

K10s itself; the singles T15, T2, T3, L10, L25, Q5, N, C15, C2, F, V10, V25, RR, SL, QR, DQ; and the
combinations declared now: **X1** = T2 + L10 + DQ · **X2** = C2 + V25 + DQ · **X3** = F + RR + SL + DQ · **X4** = QR + T2
+ V25 + SL + DQ · **X5** = N + T2 + DQ.

### 14.4 Development and selection (2022-01-03 → 2026-10-08, seen data)

1. Screen every variant with the bar model (section 11's fills).
2. Re-run the 3 variants with the highest bar-model mean net R (among those with ≥ 500 trades) with the tick-level
   fill minute and Rule 201 (section 13).
3. Select on the tick-level numbers: the highest mean net R among those 3 with ≥ 500 trades, t ≥ 2, and mean net R > 0
   in each of 2022, 2023, 2024, 2025 and 2026 separately. The runner-up, if it qualifies too, is the second finalist
   (at most 2). If none qualifies: **NO-GO** for ZBA v2, stop, report — the holdout is not run.
4. Freeze: the finalists' parameters are committed (`docs/backtest/zba-v2-frozen.json`) before the holdout runs.

### 14.5 Holdout verdict (2016-01-04 → 2021-12-31, one run of the frozen finalists, both reported)

A finalist is **GO** only if all hold:

1. ≥ 300 trades;
2. mean net R > 0 with t ≥ 2 — with 2 finalists, t ≥ 2.24 for each (the same 5% split in two);
3. mean net R above the 95th percentile of the 20 random-side means;
4. mean net R > 0 in at least 5 of the 6 years;
5. t ≥ 2 still holds with an extra $0.005 a share of slippage on each side;
6. mean net R > 0 after removing the top 1% of trades by net R.

Also reported, not gated (#19): win rate, average win, average loss, expectancy, the maximum drawdown of a book that
risks 1% a trade (section 11's portfolio), trade count, and every skip reason. The holdout is single-shot: once run,
no rule in this section changes and it is not run again.

### 14.6 Data honesty

- The holdout misses stocks that later delisted (14.0 c). The verdict is reported with that caveat. A GO on a universe
  without them is weaker evidence, and the forward shadow line remains the deciding test.
- 2022–2023 was section 12's selection period and 2024–2026 section 13's verdict period: the development period is
  seen data, which is why the decision rests on 2016–2021.
- News items, quote sizes and SIC codes are free sources with uneven coverage; a missing value counts as "no news",
  "fails the depth rule" or "no sector", never as an estimate.

### 14.7 Implementation constraints

The filters are ctx options of `ticket._zba` / `ticket_backtest`; with none set, K10s's results and every existing test
stay byte-identical. The 2016–2021 download runs detached with the existing disk cache and request pacing. Stdlib
only; free data only; keys from the environment only.

### 14.8 Forward layer (only if a finalist passes 14.4; built after the holdout, no orders)

A second shadow line next to K10s; a journal of every candidate with the reason it was picked or rejected (#15);
shorts only when Alpaca flags the asset shortable and easy to borrow (#8, counted); a risk layer with these
configurable defaults (#9, #10, #20): $100 risk a trade, at most 5 open positions, at most $500 total open risk, at
most 2 in the same SIC 2-digit sector or in a cluster with 60-day return correlation > 0.7, no new entries after a
−$300 day, and a kill switch (no entries, a Telegram alert) when SPY's bars are missing or stale; #19 metrics in the
weekly tally.

### 14.9 Not in this section (follow-ups for the owner)

Live `/ticket` and IBKR orders (only after a holdout GO and the owner's approval). The squeeze list's relative volume
compares part-day volume with a full day's average; matching it by time of day is a separate change to the live list.

### 14.10 Clarifications recorded before any run (2026-10-09, from STEP 0 and the implementation)

- **Planned entry.** #7's distance, #11's room, the share count and the stop are measured from the planned entry:
  the level for the touch entry, the confirming minute's close for #5. A live order needs its size before it
  fills; the simulation then places the stop at the fill ∓ D, as in sections 11–13.
- **Quote sizes.** Alpaca's historical SIP sizes are in round lots (100 shares) up to 2025-10-31 and in shares from
  2025-11-03 (probed: AAPL 1–4 on 2025-10-31, 100–400 on 2025-11-04). #3's depth rule converts to shares by that
  date and needs ≥ 500 shares on the trigger side.
- **News volume.** A session's pre-open window holds 256–530 items (6–11 pages of 50); the news set is fetched once
  per session for all symbols.
- **#5's mean minute volume** is over the minutes from 09:30 to the minute before the confirming one.
- **#17 without look-ahead.** The data-quality checks look only at minutes from 09:30 through the entry minute; a
  bad minute later in the day is not used to skip a trade that would already be open.
- **Where it runs.** The keys exist only as GitHub secrets, so both runs happen on GitHub Actions: one workflow run
  per mode, six chained jobs of at most 5½ hours, the request cache carried from job to job. This replaces the
  detached local downloader; the result is the same (resumable, paced, cached).

### 14.11 Correction after the first development run, before the holdout (2026-10-10, owner's decision)

The first development run (run 37921278500, report kept as `docs/backtest/ticket-zba-v2-2026-10-09-superseded.md`)
selected X5 (N + T2 + DQ: 2,048 tick-level trades, +0.197R, t 2.13). Before the holdout, a look-ahead was found: #2
counted the trigger minute's own volume, but the order fills inside that minute, before most of its volume exists;
#17 likewise checked the trigger minute's bar. A live order can only see the minutes before. Owner's decision: fix
and re-select; the holdout has not been run.

- **#2:** the cumulative volume from 09:30 through the minute **before** the trigger minute / the same window's
  mean over the 14 previous sessions.
- **#17:** the bars of the 5 minutes before the entry minute, and the VWAP / zero-volume checks on the minutes from
  09:30 to the minute before the entry minute.
- Nothing else changes: the same 22 variants, thresholds, selection rule and holdout gates. The development period
  is re-run from the cache and the selection is made again; its result, whatever it is, is the one that counts.
