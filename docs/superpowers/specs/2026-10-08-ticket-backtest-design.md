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
