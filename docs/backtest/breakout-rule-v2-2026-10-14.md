# Breakout rule v2, SIP minute replay — 2026-10-14

## סיכום

- הכרעה: NO-GO. החלון והמספרים בטבלאות שמתחת.
- עסקאות מדורגות א׳ ו־ב׳: 2386. ממוצע נטו מינוס 0.206 R. סטטיסטי t מינוס 15.47.
- אחוז זוכות פלוס 35.8 אחוז. רצף הפסדים ארוך 15.
- שפל מרבי בסיכון של אחוז אחד לעסקה: פלוס 98.6 אחוז.
- הכלל ננעל לפני ההרצה. לא נשלחה פקודה.

## Verdict

**NO-GO.** Window 2024-01-02 → 2026-10-13. Complete: True.

| Check | Pass |
|---|---|
| at least 100 trades (2386) | pass |
| mean net R > 0 with t >= 2 | fail |
| mean net R above the 95th percentile of 20 random-direction means | fail |
| mean net R > 0 in 2024 and in 2025 and in 2026 | fail |

## Numbers (grades A and B, after costs)

| Trades | Win rate | Avg net R | Expectancy (net R) | t | Median | Cost R | Random p95 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2386 | 35.8% | -0.206 | -0.206 | -15.47 | -0.147 | +0.165 | -0.157 |

| 1% risk, 4x gross cap | Max drawdown | Total | CAGR | Sharpe | Longest losing streak | Extra 1 cent each side |
|---|---:|---:|---:|---:|---:|---:|
| K10s book | 98.6% | -98.6% | -78.8% | -7.42 | 15 | -0.246 |

Live weights (A 1%, B 0.5%, same 4x cap), max drawdown 96.5%, total -96.5%. Not gated.

## By year

| Year | Trades | Mean net R |
|---|---:|---:|
| 2024 | 899 | -0.176 |
| 2025 | 860 | -0.214 |
| 2026 | 627 | -0.239 |

## Counts

| Status | N |
|---|---:|
| drop | 2915 |
| few shares | 231 |
| filled | 7902 |
| fire | 10168 |
| limit | 150 |
| no quote | 49 |
| no trigger | 1836 |
| none | 5902 |
| tracked | 18985 |
| v1 C | 7902 |
| v2 A | 2052 |
| v2 B | 334 |
| v2 C | 5516 |

## v1 grade C (context, not gated)

Same fills, graded by the v1 rule. The v1 artifact stored counts, not per-trade R, so these stats are
recomputed here. They do not enter the verdict.

| Trades | Win rate | Mean net R | t |
|---:|---:|---:|---:|
| 7902 | 32.1% | -0.478 | -43.31 |

## What was locked

Pre-registered replay v2 of the breakout rule on Alpaca SIP minute bars.

Locked on 2026-10-14, before any v2 result. v1 (bot.breakout_backtest) stays as it was run. This module does not
change the listener and does not place or transmit an order. A GO is a paper-trade result, not permission to trade,
and it is not a merge.

Same as v1, unchanged: window 2024-01-02 through the last Alpaca session before the run's New York date; the same
universe, context_row, and passes screen (close within 3% of the 20-day high, and the contraction filter: a 10-day
range of at most 0.75 x ATR14, or an NR7); the same SIP minute tape; the same drop for a gap over 10% or a
pre-market close already through the level; the same trigger (one cent above yesterday's high or the high seen
today, and only after a print underneath); the same bracket, tick fill, stop-limit, and flat; the same costs
($100 of planned risk, 10-share minimum, commission, half the SIP spread each side).

The only change is the grade, so a sized grade can occur on a break that starts underneath. v1 required a 2–8%
gap as well as pre-market volume, and that gap is usually already through yesterday's high, so every v1 fill was
grade C.

v2 grade, on a filled trade only:
- The break has already started from underneath (a fill exists only after that).
- The name has already passed the contraction filter (it would not have been tracked otherwise).
- Adequate volume: cumulative SIP share volume from 04:00 through the trigger minute, divided by the 20-day
  average share volume, is at least 5%. Five percent is a liquidity floor, half of the live rule's 10%
  pre-market bar, counted through the cross rather than only before the open. Volume after the trigger minute
  is not used. The 5% was chosen before this run.
- C when that ratio is missing or under 5%.
- B when the ratio is at least 5%.
- A when the trade is B and the raw SIP spread at the fill is at most a quarter of the printed R. A missing
  spread stays B.

The gated book is v2 grades A and B. v2 grade C is reported and is not in the gate.

The v1 grade is still computed on each fill (the grade bot.breakout would have stored). The v1 artifact saved
counts only, not per-trade R, so this run reports the v1 grade-C book's trade count, win rate, mean net R, and t
from these same fills. That block is context. It is not gated and it cannot flip the v2 verdict.

Random control and the GO rule are v1's, applied to the v2 A+B book: at least 100 trades; mean net R > 0 with
t >= 2; mean net R above the 95th percentile of the 20 random-direction means; mean net R > 0 in 2024 and in 2025 and in 2026
(a year with no trade fails). An incomplete download is NO-GO.

Reported, not gated: win rate, median, mean cost, max drawdown of a 1% risk book capped at 4x gross, the same
book at A 1% and B 0.5%, the longest run of gated trades with net R <= 0, and the mean after an extra $0.01 a
share each side.
