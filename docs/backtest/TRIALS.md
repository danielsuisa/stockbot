# Trial log

Locked on 2026-10-15, before any Faber, GEM, or GEM-BIL return is computed. Nothing in this log places or transmits an order. A later GO would be a paper result. It would not be a merge and it would not be permission to trade.

No tax is computed in the Faber, GEM, or sector books. The T7 and T8 amendment computes a 25% tax. The queue amendment at the bottom applies the Fixed commission, the sample-size gate, and that tax to the pending books, and it reopens T9 and adds T10.

## Not a trial

- Turn of the month (T9). An earlier cost-arithmetic close is superseded by the queue amendment, which reopens T9 as a quick check. It was not in the effective-trial number when this section was written.
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

Variant A out of sample meets its required n (36.84, from a negative in-sample mean) and is NO-GO. After-tax CAGR is -7.57% against SPY +11.62%, on 96 round trips. The mean held-window move is +0.18%. Variant C's in-sample mean is nearly zero, so required n is 392,211. The 96 out-of-sample round trips are below that, and the queue verdict is UNKNOWN (insufficient sample). The after-tax CAGR is still -7.71% against the same SPY. Variant B is skipped. There is no 13:55 print. In-sample does not flip either queue verdict.

The CPI/PPI registered book is **UNKNOWN (insufficient sample)**. Out-of-sample trades at 9:35 ET are 0, n is undefined, and the count is also below the minimum of 40. The open-fill description is not a verdict. Its own n is 4.00 and its 23 out-of-sample trades meet that n. It is below the 1 point line: after-tax CAGR -1.54% against SPY +14.29%. The 2022 open-fill trades and the worst open-to-close losses are in the report. The VIX1D description, from 2023, has 5 trades and is not a verdict.

## Queue amendment, locked before T9 and T10

Locked on 2026-10-15, before any turn-of-month or new Faber return is computed. Nothing here places or transmits an order. The earlier Faber, GEM, and GEM-BIL reports stay as written. Their pass rule is not reopened. Desk-gate commission constants used by the closed penny, swing, and noise books stay at $0.0035 a share and a $0.35 minimum. Pending books use the rule below.

### Commission

IBKR Pro Fixed, on each order, from that order's shares and price. $0.005 a share, minimum $1.00, maximum 1% of that order's trade value. A $460 SPY order pays the $1 minimum, about 0.22% of the order. An order of $50 to $100 pays the 1% cap, because the $1 minimum is larger than 1% of the trade value. It is not a flat percent. The code is `bot/queue_rules.py`.

### Sample size

Every pending report computes, before its results,

n = (1.645 × sigma / mu)²

Sigma is the sample standard deviation of in-sample per-trade returns. Mu is their mean. One trade, or no trades, leaves n undefined. A mean of zero leaves n infinite. A negative mean is used as written, and the square keeps n positive. If the out-of-sample trade count is below n, or n is undefined, the queue verdict is UNKNOWN (insufficient sample), not NO-GO. The book's own checks stay in the report and are not that verdict.

T7 is flagged: a pre-Fed book has about 8 announcement windows a year. T8 is flagged: CPI and PPI together are about 24 release days a year, and the fade trades only the gap-down days.

T4, T5, and T6 are not re-run. Their reports get a preface. Where a saved trade list exists, n is computed from it. Where it does not, the preface says n was not computed. Saved fills keep the commission they were run with.

### T9, turn of the month

Reopened as a quick check. The window is the last trading session of the month plus the first three trading sessions of the next month. The entry is the close before that last session. The exit is the close of the third session of the next month. Cash earns nothing in between. Prices are Yahoo Finance chart v8 adjusted closes. In-sample exits fall in 2000 through 2014. Out-of-sample exits fall in 2015 through the last session. The per-trade return for n is the window's adjusted return minus the two Fixed commissions divided by $460.

The quick rate is the mean out-of-sample window return times 12, minus the mean two-order commission times 12. Each exit year's net window returns are summed. A positive year is taxed at 25%. A negative year is not taxed and does not carry forward. The after-tax rate scales the pre-tax rate by the tax due on those year nets. The position in the quick check is a fresh $460 each window. It does not compound.

Buy-and-hold SPY runs from the first out-of-sample entry close to the last session, pays one entry and one exit, and is taxed once at the end. Close means the quick after-tax rate is within 1 percentage point of that SPY after-tax CAGR, or ahead of it. The sample gate is applied first. Below n, the verdict is UNKNOWN (insufficient sample) and the compounded path is not run. At or above n, and not close, the verdict is NO-GO and the compounded path is not run. At or above n, and close, the compounded path is run on the out-of-sample dates, cash at 0%, Fixed commission, no spread, tax as in `bot/tax.py`. That path passes only if its after-tax CAGR is within 1 percentage point of SPY's, or ahead of it.

T9 does not change the 7 used by the earlier Faber and GEM reports, or the 9 used by T7 and T8.

### T10, Faber on SPY

A different book from the SPYM whole-share Faber report. At the close of each month's last trading day, hold SPY when that adjusted close is strictly above the average of the last ten month-end adjusted closes, including that month. Otherwise hold cash at 0%. A tie is cash. There is no spread. Shares may be fractional. The account starts at $460.

The 10-month average was published in 2007 and is not fitted. There is no in-sample period, so n is not computed and the sample-size gate does not apply. The reported window is one out-of-sample period: the December 2006 signal, which needs the ten month-ends ending that month, through the last session. The order is at that close. The next month's moves belong to the new holding. The last session is liquidated so the exit cost is in the result. That liquidation is not a switch.

Metrics: CAGR, maximum drawdown, daily Sharpe with a zero risk-free rate, and switches per year. Commission is Fixed. Tax is 25% of each calendar year's net realized gain, subtracted once from terminal equity. Sharpe and maximum drawdown stay pre-tax. Buy-and-hold SPY pays one entry, one exit, and one tax at the end.

The goal is drawdown reduction, not return. The book is GO only when its pre-tax maximum drawdown is strictly smaller than SPY's over the same dates. Return is reported and is not the gate. The old one-third bootstrap bar is not applied. T10 is a new raw trial inside the monthly-trend cluster already counted. It does not change the 7 on the earlier Faber and GEM reports.

## Queue results, computed after the lock

T3, the empty premarket book, has no in-sample trades, so n is undefined. The queue verdict is UNKNOWN (insufficient sample). The four desk checks still fail. There were no fills to re-price. The after-tax line is zero. Report: `docs/backtest/premarket-catalyst-2026-10-15.md`.

T4 was not re-run. From the saved 20-session returns, n is 608.22 and the out-of-sample count is 69, so the queue verdict is UNKNOWN (insufficient sample). The saved gate still reads NO-GO. Saved out-of-sample pnl is -$643.32, tax $0.00. The fills stay at $0.0035.

T5 was not re-run. The turnover close stays NO-GO at 9.82 replacements a year. There is no trade list, so n is not computed and the after-tax line is not stored.

T6 was not re-run. n is not computed. The saved one-day verdict stays GO on its own gate. Its after-tax terminal is $1,950.35 against SPY $3,498.37, on the old commission.

T7 and T8, after the sample gate: variant A is NO-GO, variant C is UNKNOWN (insufficient sample), and the registered CPI/PPI book is UNKNOWN (insufficient sample). The after-tax figures are unchanged from the section above.

T9, the turn-of-month quick screen, has in-sample mu -0.001559 and sigma 0.022543 over 180 windows, so n is 565.79. Out-of-sample windows are 142. The queue verdict is UNKNOWN (insufficient sample). The quick after-tax rate is -2.29% against SPY's after-tax CAGR of +11.52%, so it is not close. The sample gate applies first. The compounded path was not run. Report: `docs/backtest/tom-2026-10-15.md`.

T10, the Faber filter on SPY from 2006-12-29 through 2026-10-14, is **GO** on the drawdown goal. Pre-tax maximum drawdown is 25.82% against SPY 55.19%. All 253 sessions in 2008 were cash. After-tax CAGR is +6.77% against SPY +9.55%. Pre-tax CAGR is +8.01% against +10.92%. Daily Sharpe is 0.67 against 0.63. Switches are 1.67 a year. Fees are $34.00 against SPY's $2.00. Tax is $430.77 against SPY's $778.60. Return is lower and is not the gate. This does not reopen the SPYM whole-share Faber report, which stays NO-GO. Report: `docs/backtest/faber-spy-2026-10-15.md`.

## Sector rotation is closed

`docs/backtest/T5_TURNOVER.md` counts the preregistered book from 2007-01-31 through 2026-09-30. The average is 9.82 single-fund replacements per year. The gate was 2. T5 is closed NO-GO. No return test is run. The earlier sector return report stays a prior trial under the old gate. It does not add a cluster beyond the one already counted.
