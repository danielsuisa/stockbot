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
