# Breakout rule, SIP minute replay — 2026-10-14

## סיכום

- הכרעה: NO-GO. החלון והמספרים בטבלאות שמתחת.
- עסקאות מדורגות א׳ ו־ב׳: 0. ממוצע נטו חסר R. סטטיסטי t חסר.
- אחוז זוכות חסר אחוז. רצף הפסדים ארוך 0.
- שפל מרבי בסיכון של אחוז אחד לעסקה: אפס אחוז.
- הכלל ננעל לפני ההרצה. לא נשלחה פקודה.

## Verdict

**NO-GO.** Window 2024-01-02 → 2026-10-13. Complete: True.

| Check | Pass |
|---|---|
| at least 100 trades (0) | fail |
| mean net R > 0 with t >= 2 | fail |
| mean net R above the 95th percentile of 20 random-direction means | fail |
| mean net R > 0 in 2024 and in 2025 and in 2026 | fail |

## Numbers (grades A and B, after costs)

| Trades | Win rate | Avg net R | Expectancy (net R) | t | Median | Cost R | Random p95 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | — | — | — | — | — | — | — |

| 1% risk, 4x gross cap | Max drawdown | Total | CAGR | Sharpe | Longest losing streak | Extra 1 cent each side |
|---|---:|---:|---:|---:|---:|---:|
| K10s book | 0.0% | 0.0% | 0.0% | — | 0 | — |

Live weights (A 1%, B 0.5%, same 4x cap), max drawdown —, total —. Not gated.

## By year

| Year | Trades | Mean net R |
|---|---:|---:|
| 2024 | 0 | — |
| 2025 | 0 | — |
| 2026 | 0 | — |

## Counts

| Status | N |
|---|---:|
| drop | 2915 |
| few shares | 231 |
| filled | 7902 |
| fire | 10168 |
| grade C | 7902 |
| limit | 150 |
| no quote | 49 |
| no trigger | 1836 |
| none | 5902 |
| tracked | 18985 |

## What was locked

Pre-registered replay of the breakout rule on Alpaca SIP minute bars.

Locked on 2026-10-14, before the run. The decision rule is bot.breakout (the code the listener runs). Nothing here
places or transmits an order. A GO is a paper-trade result, not permission to trade.

Window: 2024-01-02 through the last Alpaca session before the run's New York date (the K10s verdict window, more than
a year). A session is an Alpaca calendar day.

Who is tracked: the listener's universe (NYSE/NASDAQ, 1–5 letters, no fund/ETF name), then context_row + passes on
daily bars strictly before the session, ranked by distance to the 20-day high, at most 30 names. Same functions as
the live loop.

Tape: SIP 1-minute bars from 04:00 through the flat minute. A minute is used only after it closes. On a pre-market
minute the close and the cumulative pre-market volume are passed to apply_quote before that minute's bar path; a
"drop" skips the name for the day. The bar path is open-low-high-close when the minute closes at or above its open,
otherwise open-high-low-close. observe() is the trigger. The grade is whatever apply_quote has already stored (a
cross on the first regular minute with no pre-market quote stays C).

Order, the prices bracket() prints at the trigger: buy stop at the trigger, limit at trigger + 0.25 x ATR, stop and
target absolute. One target for the whole position (the "half at the target" line in the ping is text, not a second
order). Flat at 15:55 New York, or at the last minute before an earlier close.

Fills (K10s section 13): SIP round-lot trades of the trigger minute. The stop triggers on the first trade at or
through the level and fills at the next trade (at the trigger trade when none follows). A fill above the limit is
cancelled. The printed stop is then checked on the remaining trades of that minute and fills at the trade after the
one that hits it. From the next minute, the bar model: a stop or a target fills at the stop or the target, or at the
open when the open is already through it; a minute that touches both is the stop; anything still open exits at the
flat minute's close.

Costs (K10s section 6): $100 of planned risk, and fewer than 10 shares is not a trade. Commission $0.0035 a share,
$0.35 minimum, once on the entry and once on the exit. Half the SIP spread on the way in and half on the way out
(the full spread once), from the quote at the fill; a locked quote counts as $0.01; no usable quote (missing, a side
at or below zero, crossed, or older than 60 seconds) drops the trade. R in the denominator is the printed R, so a
fill worse than the trigger makes a full stop lose more than 1R. Results are net of these costs.

Grade at the fill quote: C stays C, B becomes A when the raw spread is at most a quarter of R (the live rule). The
gated book is grades A and B (the pings the live rule would size). Grade C is reported and is not in the gate.

Random control: 20 seeds, each ticker-day a coin flip, the short played on prices negated around zero with the same
distances from the fill (ticket_backtest.random_means).

GO, all four, on the gated book, else NO-GO: at least 100 trades; mean net R > 0 with t >= 2; mean net R above the
95th percentile of the 20 random means; mean net R > 0 in 2024 and in 2025 and in 2026 (a year with no trade fails).
Reported, not gated: win rate, median, mean cost, max drawdown of a 1% risk book capped at 4x gross (the K10s
portfolio), the same book at the live weights (A 1%, B 0.5%), the longest run of trades with net R <= 0, and the
mean after an extra $0.01 a share each side. An incomplete download is NO-GO.

