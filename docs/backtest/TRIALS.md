# Trial log

Locked on 2026-10-15, before any Faber, GEM, or GEM-BIL return is computed. Nothing in this log places or transmits an order. A later GO would be a paper result. It would not be a merge and it would not be permission to trade.

No tax is computed in the Faber, GEM, or sector books. The T7 and T8 amendment at the bottom computes a 25% tax, and only there.

## Not a trial

- Turn of the month (T9). Closed NO-GO on the cost arithmetic. It is not counted, and it is not in the effective-trial number.
- Post-earnings drift (T4) and the T3 penny book with its blacklist. Closed NO-GO on the cost arithmetic. They are not counted.
- The VIX term-structure test is not re-run. The one-day book already reported on 2026-10-15 remains a prior trial. Its two-day book stays in that same cluster.
- Pre-FOMC and the CPI/PPI gap-down fade were off the queue when the lines above were written. The amendment at the bottom puts them back on, as T7 and T8.

## Effective trials

A raw trial is one return variant that was actually run. Correlated variants share a cluster. The deflated Sharpe ratio uses the number of clusters, not the number of raw rows. Faber, GEM, and GEM-BIL are one cluster: they are monthly trend filters. GEM-BIL is still its own counted trial inside that cluster.

| Cluster | Raw trials already run | Result |
|---|---|---|
| Breakout | v1; v2; the six post-mortem filters fit on 2024 | NO-GO. The filters were discarded. |
| Noise area | QQQ and SPY, long and short | NO-GO |
| Penny intraday | Opening-range breakout; VWAP pullback | NO-GO, both |
| Swing | No earnings filter; calendar earnings proxy | NO-GO, both |
| Sector momentum | The 2026-10-15 return replay, under that day's gate | NO-GO. See the turnover close below. No further return is run. |
| VIX term structure | One-day book (the verdict); two-day book (not the verdict) | GO on the one-day book's own gate. Not re-run. |

The premarket catalyst and the post-earnings drift were removed from this table when they were closed on cost math. They are not clusters. What remains at this point in the file is 6 clusters already run, plus the monthly-trend cluster below (Faber, GEM, and GEM-BIL). The effective number for the Faber and GEM reports is **7**. The T7 and T8 amendment adds two clusters and uses **9** for those reports only. The Faber and GEM reports stay at 7.

Older closed work (the ticket search, the ensemble, the index program, the squeeze list) is not reopened and is not added on top of these 7. The opening-range and VWAP penny books stay in the table. They are not the T3 premarket book.

## Faber and GEM, locked before the run

### The pass rule

The decision window is the completed month-ends from January 2014 through the last completed month. On a run dated 2026-10-15 that is 2014-01-31 through 2026-09-30. History before 2014 is supplementary. It is reported and it cannot flip the verdict.

Pass only if both of these hold, after costs, on that window:

(a) Annual return (CAGR) is no more than 1 percentage point below buy-and-hold SPY over the same window.

(b) In a paired 3-month block bootstrap of 1000 draws, the strategy's maximum drawdown is at least one third smaller than SPY's in at least 80% of the draws.

Costs are IBKR Pro fixed commission, $0.005 a share, minimum $1.00 an order, maximum 1% of trade value, plus a 2 basis point round-trip spread (1 basis point a side). SPY buy-and-hold pays the entry and the final exit. Cash earns nothing. There is no tax.

The bootstrap is fixed as follows. The inputs are the paired monthly total returns of the strategy and of SPY on the decision window, in calendar order, length T. A block is three consecutive months. Draws are circular and with replacement, so the series wraps and every month can start a block. Each draw takes enough blocks to cover T months and then keeps the first T. The strategy and SPY use the same block starts. Wealth starts at 1 and compounds the drawn months. Maximum drawdown is the deepest peak-to-trough decline, as a positive fraction. "At least one third smaller" means the strategy's drawdown is less than or equal to two thirds of SPY's drawdown on that same draw. If SPY's drawdown on a draw is zero, the draw passes only when the strategy's drawdown is also zero. The generator is `random.Random(20261015)`. The pass fraction is reported.

Calendar 2020 and calendar 2022 maximum drawdowns are reported separately, for the strategy and for SPY. They are not a gate. Each year uses the daily equity curve inside that year, with the peak reset at the first close of the year.

The deflated Sharpe ratio and the probabilistic Sharpe ratio are reported, at the effective trial count of 7. They are not a third pass condition. The pass rule is (a) and (b) only.

### GEM and GEM-BIL are both reported

GEM holds AGG when SPY's 12-month total return does not beat BIL. GEM-BIL is the same rule with BIL as the defensive asset instead of AGG.

Both books are reported. The one with the better result is not selected afterward. The decision is the original GEM book, the one that uses AGG.

GEM-BIL is a separate counted trial. It can be marked GO only if it passes (a) and (b) on 2014-today and it also passes (a) and (b) on 2014-2021 alone. That second window is the month-ends from 2014-01 through 2021-12. The year 2022, and every later month, is excluded from it. Failing either window is not a GO.

### The rule itself

There is no gap and no hysteresis. A month stands on its own comparison. A tie is not a win: if the SPY and BIL 12-month returns are equal, the book holds the defensive asset; if the SPY and VEU 12-month returns are equal, the book holds SPY.

The 12-month total return is the month-end adjusted close divided by the month-end adjusted close twelve months earlier, minus one. Adjusted closes include dividends. An ETF is not used before it exists.

The signal is the month-end close. The order is market-on-open at the next session's open. A switch from one ETF to another is T+1: the sale is the next open, and the purchase is the open after that, because the sale has not settled. The night in between is cash at zero. Staying in the same ETF does not trade. The first purchase, from settled cash, is the next open.

The account is $460. It buys whole shares of the fund the signal selected, at that fund's actual open price. Leftover cash earns nothing and is recomputed at every switch from the fund being bought. It is not a fixed remainder from SPYM. When the signal says SPY, the executable book buys SPYM (the fund formerly named SPLG). VEU, AGG, and BIL are bought as themselves. The signal is still computed from SPY, not from SPYM. A second book, the SPY reference, is the same signals with SPY bought in place of SPYM. The verdict uses the executable book. The reference is not chosen when it looks better.

Prices used for the fill are the exchange prints, with splits undone. Yahoo's historical opens are split-adjusted, so a print from before a split is scaled back to the price that traded. Share count changes on the split morning. A reverse split leaves a fractional share as cash at that morning's open. Dividends are cash, per share actually held, and are not spent on the same morning's open.

Faber, on the same execution, holds the equity sleeve when SPY's month-end adjusted close is strictly above the average of the last ten month-end adjusted closes, including that month. Otherwise it holds cash at zero. A close equal to the average is cash. There is no band around the average and no extra month of confirmation. The executable equity sleeve is SPYM. The reference sleeve is SPY.

The September 2026 signal, computed before this return run, is recorded in `docs/backtest/GEM_SIGNAL_2026-09.md`. The holding is VEU. That file is not a return test.

### What is not the bar

Beating SPY by 1% a year, and an excess Sharpe of 0.8, are the bar for the return-seeking tests below. They are not the Faber or GEM bar.

## What this queue runs

The Faber queue's return tests are Faber, GEM with AGG, and GEM-BIL. The September 2026 signal and the sector-turnover count are already filed and are not return tests. Post-earnings drift and the T3 penny book are not run. T7 and T8 are specified in the amendment below, which was locked before those returns were computed.

Every return report also shows buy-and-hold VT beside SPY. SPY stays the primary bar. VT exists for the whole decision window. Daily strategy returns and daily SPY returns are saved under `docs/backtest/series/`.

## Results, computed after the lock

The decision is GEM with AGG: **NO-GO**. Faber and GEM-BIL are also NO-GO. GEM-BIL was not preferred to GEM. Reports: `docs/backtest/faber-10m-2026-10-15.md`, `docs/backtest/gem-agg-2026-10-15.md`, `docs/backtest/gem-bil-2026-10-15.md`. Daily returns are in `docs/backtest/series/`.

| Book | Verdict | CAGR | SPY CAGR | Bootstrap |
|---|---|---:|---:|---:|
| Faber, SPYM whole shares | NO-GO | +6.32% | +11.24% | 2.3% |
| GEM, AGG, the decision | NO-GO | +6.48% | +11.24% | 0.1% |
| GEM-BIL | NO-GO | +6.50% | +11.24% | 1.0% |

GEM-BIL also fails 2014–2021 on its own (CAGR +5.21% against SPY +12.04%, bootstrap 0%). Each book fails both pass conditions. The first executable fill is 5 shares of SPYM at 84.00 on 2014-02-03, with $38.96 left in cash.

## T7 and T8, locked before those returns

Reopened on 2026-10-15, after the Faber and GEM results and before any FOMC or CPI return is computed. Nothing here places or transmits an order.

### Tax, costs, and the bar

These two tests compute tax. A calendar year's net realized gain is taxed at 25%. A loss offsets a gain in that year. A losing year pays nothing and does not carry forward. The tax is subtracted once from terminal equity. It is not taken out of the daily path, so Sharpe and drawdown stay pre-tax. Buy-and-hold SPY is taxed once, on the gain from the first dollar to the last dollar. The code is `bot/tax.py`.

The account starts at $460. Shares may be fractional, because a whole SPY share does not fit in $460 for most of the out-of-sample window. Commission is IBKR Pro fixed: $0.005 a share, minimum $1.00 an order, maximum 1% of trade value. The spread is 1 basis point a side. Selling the sleeve and buying SPY are separate orders, and so are the later sale of SPY and the purchase of the sleeve. Cash that accrues a T-bill yield has no commission.

The book passes only if its out-of-sample after-tax CAGR is at least 1 percentage point above buy-and-hold SPY over the same dates, after SPY's entry cost, final exit, and end tax. In-sample is reported and cannot flip the verdict. VT buy-and-hold is shown beside SPY where VT has bars. SPY stays the bar. The deflated Sharpe ratio is reported at 9 effective trials. It is not an extra pass condition.

The new clusters are pre-FOMC (variants A, B, and C together) and the CPI/PPI gap fade (the base rule and the VIX1D variant together). With the 7 already locked, these reports use 9. Faber and GEM stay at 7.

### The sleeve and the daily prices

When the book is out of SPY it holds the sleeve. Before 2020-01-01 the sleeve is BIL. From 2020-01-01 it is SGOV once SGOV has a bar, and BIL until that bar. Before BIL's first bar the sleeve is cash accrued at the prior session's 13-week T-bill yield: the Yahoo `^IRX` close, divided by 100 and by 252. That accrual is realized income in the year it accrues.

SPY, BIL, SGOV, VT, and `^IRX` are Yahoo Finance chart v8. That is the public daily source before 2016, and it is the source after 2016 as well. Adjusted closes are the ETF total-return marks. The CPI gap uses the official open and the prior official close. Alpaca is not used.

The last session is the last bar strictly before the New York run date.

### T7, pre-FOMC drift

Announcement dates come from the Fed's historical calendar: `fomchistorical2000.htm` through `fomchistorical2020.htm`, and `fomccalendars.htm` after that. A heading counts when it is a Meeting. A conference call, a notation vote, and a cancelled meeting do not count. The announcement date is the last day named in the heading. When the heading names two months, that day is in the second month. When one month is named and the last day number is lower than the first, that day is in the following month, so June 30-1 is July 1. If that date is not a SPY session, the announcement session is the next SPY session.

Variant A buys SPY at the close two trading sessions before the announcement session and sells at the close of the session before the announcement. Variant C sells at the close of the announcement session. A dividend inside the window is in the adjusted close. An event belongs to a sample only when its entry close and its exit close are both inside the sample. In-sample is 2000 through 2014. Out-of-sample is 2015 through the last session.

Variant B would sell at 13:55 ET on the announcement session. This run has no Alpaca credential and no public minute archive, so that print is absent. Variant B is skipped. It has no verdict. It stays inside the pre-FOMC cluster and does not add one.

A and C are each judged on the bar above. The higher one is not selected afterward.

### T8, SPY gap-down fade on CPI and PPI days

Release dates come from the BLS year pages `https://www.bls.gov/schedule/YYYY/home.htm`. A row counts when its name is exactly Consumer Price Index or Producer Price Index. Two releases on one session are one day. The gap is the official open divided by the prior session's official close, minus one.

The registered buy is 9:35 ET, and the exit is the close. This run has no 9:35 print. The registered out-of-sample trade count is 0. The pre-registered minimum is 40, so the verdict is unknown.

A separate description buys at the official open and sells at the close when the gap is at most -0.5%. It uses the same sleeve, costs, and tax. On a release day that description does not accrue the sleeve from the prior close to the open. Every other session accrues the sleeve from close to close. In-sample release dates fall in 2010 through 2018. Out-of-sample release dates fall in 2019 through the last session. The description's numbers, its tail losses, and its 2022 trades are reported. They are not the verdict.

The VIX1D variant keeps the 0.5% rule and also requires the gap to be strictly below the prior session's VIX1D close divided by 100 and by the square root of 252. VIX1D is the CBOE daily history. The file's first print is in 2022. The variant still starts in 2023, on the first session that has a prior VIX1D close. It is an out-of-sample description. It is not a verdict.

## T7 and T8 results

Computed after the lock above. Reports: `docs/backtest/fomc-drift-2026-10-15.md` and `docs/backtest/cpi-ppi-fade-2026-10-15.md`.

Variant A out of sample is NO-GO. After-tax CAGR is -7.57% against SPY +11.62%, on 96 round trips. The mean held-window move is +0.18%. Variant C out of sample is NO-GO, at -7.71% against the same SPY. Variant B is skipped. There is no 13:55 print. In-sample does not flip either verdict.

The CPI/PPI registered book is **unknown**. Out-of-sample trades at 9:35 ET are 0, below the minimum of 40. The open-fill description is below the 1 point line and is not a verdict: 23 out-of-sample trades, after-tax CAGR -1.54% against SPY +14.29%. The 2022 open-fill trades and the worst open-to-close losses are in the report. The VIX1D description, from 2023, has 5 trades and is not a verdict.

## Sector rotation is closed

`docs/backtest/T5_TURNOVER.md` counts the preregistered book from 2007-01-31 through 2026-09-30. The average is 9.82 single-fund replacements per year. The gate was 2. T5 is closed NO-GO. No return test is run. The earlier sector return report stays a prior trial under the old gate. It does not add a cluster beyond the one already counted.
