# Order-ticket backtest, second look: the IMPROVED spread gate — 2026-10-09

## סיכום

- מסחר רגיל: נבחר `G2` — NO-GO ❌, עסקאות `172`, ממוצע `-0.432R`, סטטיסטי t `-2.71`
- טרום מסחר: נבחר `G3` — NO-GO ❌, עסקאות `180`, ממוצע `-1.682R`, סטטיסטי t `-9.50`

זהו מבט שני: תוצאות 2025–2026 של השער המקורי כבר נראו, ולכן GO כאן הוא סיבה למסחר נייר קדימה, לא למסחר אמיתי.

## Method

This is a second look (spec section 10): G0's 2025-2026 results were already seen, so this is not a clean out-of-sample test.

Gates (spread always ≤ 1% of the price): G0 ≤ 0.1 × D, G1 ≤ 0.25 × D, G2 ≤ 0.5 × D, G3 no D bound.

## Gates

| Session | Gate | 2024 trades | 2024 mean | 2024 t | Verdict trades | Mean net R | t | Random p95 | 2025 | 2026 | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| REGULAR | G0 | 5 | -0.808 | -2.63 | 23 | +1.110 | 1.60 | +0.867 | +0.320 | +2.340 | NO-GO (AB) |
| REGULAR | G1 | 27 | -0.413 | -1.60 | 81 | +0.146 | 0.49 | +0.589 | -0.098 | +0.438 | NO-GO (AB) |
| REGULAR | G2 ← chosen | 75 | -0.184 | -0.54 | 172 | -0.432 | -2.71 | -0.020 | -0.535 | -0.312 | NO-GO (AB) |
| REGULAR | G3 | 141 | -0.548 | -2.21 | 348 | -0.609 | -4.69 | -0.501 | -0.605 | -0.612 | NO-GO (AB) |
| PREMARKET | G0 | 3 | -1.955 | -2.57 | 9 | -1.200 | -14.37 | -0.160 | -1.222 | -1.125 | NO-GO (AB) |
| PREMARKET | G1 | 12 | -2.190 | -2.14 | 52 | -1.984 | -5.30 | -0.162 | -2.423 | -0.522 | NO-GO (AB) |
| PREMARKET | G2 | 26 | -2.036 | -4.26 | 96 | -1.578 | -6.83 | -0.309 | -1.973 | -0.569 | NO-GO (AB) |
| PREMARKET | G3 ← chosen | 60 | -2.396 | -5.54 | 180 | -1.682 | -9.50 | -0.861 | -1.995 | -1.178 | NO-GO (AB) |

## Verdict checks of the chosen gates

| Session | Gate | Check | Pass |
|---|---|---|---|
| REGULAR | G2 | at least 100 trades (172) | pass |
| REGULAR | G2 | mean net R > 0 with t >= 2 | fail |
| REGULAR | G2 | mean net R above the 95th percentile of 20 random-direction means | fail |
| REGULAR | G2 | mean net R > 0 in 2025 and in 2026 | fail |
| PREMARKET | G3 | at least 100 trades (180) | pass |
| PREMARKET | G3 | mean net R > 0 with t >= 2 | fail |
| PREMARKET | G3 | mean net R above the 95th percentile of 20 random-direction means | fail |
| PREMARKET | G3 | mean net R > 0 in 2025 and in 2026 | fail |

## Assumptions

- Everything else as in the first run's report (`ticket-2026-10-09.md`):
  - Universe A: the 1,330 out-of-sample picks of docs/backtest/squeeze-2026-09-27-picks.csv, each tested on its day in both sessions from the session start (all tickers still listed: survivorship bias).
  - Universe B: each session, tickers in SEC's current map (survivorship bias) with FINRA short interest usable that day, SEC shares outstanding as the float (< 50M; short / shares > 20%), the mean volume of the last 63 daily bars > 500K, short / that mean > 5, previous close > $2; listed options not checked (no history).
  - Universe B enters the list at the first minute with regular volume > 2x the average day (REGULAR) or pre-market volume >= 10% of it with a price over $2 (PREMARKET); rules act only after that minute closes.
  - Ticker-days with a day-over-day close ratio < 0.5 or > 2 in the 63-day window are skipped (raw prices, splits).
  - A ticker-day in both A and B counts once when pooled (B's result).
  - 5-minute bars are built from the 1-minute bars that exist and used only after they close; swings need the 2 bars after them; VWAP runs from 09:30 (REGULAR) or 04:00 (PREMARKET).
  - ORIGINAL: the owner's rules with two fixes: the trail uses the most recent higher low; swing high / higher low use the 2-bar definition. Volume is compared with up to 10 previous bars of the session.
  - IMPROVED: opening-range breakout; the first 1-minute high above the level decides (a stop-limit triggers once).
  - Halts are approximated as 5+ minutes without a 1-minute bar during the regular session.
  - Fills: a buy limit fills only when a minute's low reaches it, at the limit; a stop at the stop or at the open if gapped; stop first when a minute touches stop and target; on the fill minute a stop counts only when its side of the minute comes after the fill (bar path open-low-high-close if the minute closes at or above its open, else open-high-low-close).
  - Stops act on pre-market minutes too (the intended protection; a plain IBKR STP would not trigger outside RTH).
  - Costs: $0.0035 a share, $0.35 minimum per order, each side; half the SIP spread at the signal in and out; a locked quote (bid = ask) counts as a $0.01 spread.
  - Risk $100 a trade; fewer than 10 shares is NO TICKET; results in R = P&L / (R x shares).
  - Random benchmark: the same entries with a coin-flip direction (20 seeds), shorts mirrored around the fill; the short takes the long's conservative limit price, so it is credited what the long is charged.
  - Universe B's average volume comes from Alpaca daily bars, which include extended hours (~14% more than the regular session), so its REGULAR entry bar of 2x is ~2.3x of regular-session volume.
  - Periods: tuning 2024; verdict 2025-01-01 to the last complete session. GO needs >= 100 trades, mean net R > 0 with t >= 2, above the 95th percentile of the random means, and > 0 in 2025 and in 2026.
  - Verdict basis: universe B; A and B pooled when B has fewer than 100 verdict trades.

## Data

- Range 2024-01-02 … 2026-10-07 · universes AB · runtime 30s · Alpaca requests 0
- A ticker-days: 1330
- B ticker-days: 5758
- skipped_split: 176
- ticker-days without minute bars: 1

`python -m bot.ticket_backtest --tune`
