# Trial log

Locked on 2026-10-15, before any Faber, GEM, or GEM-BIL return is computed. Nothing in this log places or transmits an order. A later GO would be a paper result. It would not be a merge and it would not be permission to trade.

Tax is not computed in any report in this log. T3, T4, T7, T8, and T9 are closed on cost and are not in the counter. T6 is the VIX term-structure book. The one-day book is the verdict, and the two-day book is reported beside it and does not add a count. T6's verdict is CLOSED-COST. T6 is the sixth prior completed test, so it is in the counter, and it is not a GO. The labels below are the gate function's return value. Faber, GEM, and GEM-BIL are three further trials. The counter increments when a test is run, including while its verdict is PENDING-REPLICATION. Only the verdict column waits for an independent-script replication. Completed tests stay 9. N stays 18.

## Not a trial

- Turn of the month (T9) is CLOSED-COST. It is not a trial.
- Post-earnings drift (T4) and the T3 penny book are CLOSED-COST. They are not counted.
- Pre-FOMC (T7) is CLOSED-COST. The CPI/PPI gap-down fade (T8) is CLOSED-COST. They are not trials.

T6, the VIX term-structure book, is a completed test. It is the sixth row in the table below.

## Effective trials

A completed test is one return test that was run. The counter of completed tests is 9: the six prior rows below, plus Faber, plus GEM, plus GEM-BIL. The deflated Sharpe for the next test uses a variant count of 18, which is 15 earlier variants plus those three. That variant count is not the completed-test counter.

| Completed test | What was run | Result |
|---|---|---|
| Breakout | v1; v2; the six post-mortem filters fit on 2024 | NO-GO. The filters were discarded. |
| Noise area | QQQ and SPY, long and short | NO-GO |
| Penny intraday | Opening-range breakout; VWAP pullback | NO-GO, both |
| Swing | No earnings filter; calendar earnings proxy | NO-GO, both |
| Sector momentum | The 2026-10-15 return replay, under that day's gate | NO-GO. See the turnover close below. No further return is run. |
| T6, VIX term structure | One-day book, with the two-day book reported beside it | CLOSED-COST. In the counter. Not a GO. |

T3, T4, T7, T8, and T9 are not in the six prior tests. T6 is the VIX term-structure row in that table, so it is in the counter. Faber, GEM, and GEM-BIL are three further completed tests. The live number is the marker below. The counter does not wait for replication. The verdict column does. N stays 18.

Older closed work (the ticket search, the ensemble, the index program, the squeeze list) is not reopened and is not added on top of the counter in the marker below. The opening-range and VWAP penny books stay in the table. They are not the T3 premarket book.

## Faber and GEM, locked before the run

### The pass rule

The decision window is the completed month-ends from January 2014 through the last completed month. On a run dated 2026-10-15 that is 2014-01-31 through 2026-09-30. History before 2014 is supplementary. It is reported and it cannot flip the verdict.

Pass only if both of these hold, after costs, on that window:

(a) Annual return (CAGR) is no more than 1 percentage point below buy-and-hold SPY over the same window.

(b) In a paired 3-month block bootstrap of 1000 draws, the strategy's maximum drawdown is at least one third smaller than SPY's in at least 80% of the draws.

Costs are IBKR Pro fixed commission, $0.005 a share, minimum $1.00 an order, maximum 1% of trade value, plus a 2 basis point round-trip spread (1 basis point a side). SPY buy-and-hold pays the entry and the final exit. Cash earns nothing. There is no tax.

The bootstrap is fixed as follows. The inputs are the paired monthly total returns of the strategy and of SPY on the decision window, in calendar order, length T. A block is three consecutive months. Draws are circular and with replacement, so the series wraps and every month can start a block. Each draw takes enough blocks to cover T months and then keeps the first T. The strategy and SPY use the same block starts. Wealth starts at 1 and compounds the drawn months. Maximum drawdown is the deepest peak-to-trough decline, as a positive fraction. "At least one third smaller" means the strategy's drawdown is less than or equal to two thirds of SPY's drawdown on that same draw. If SPY's drawdown on a draw is zero, the draw passes only when the strategy's drawdown is also zero. The generator is `random.Random(20261015)`. The pass fraction is reported.

Calendar 2020 and calendar 2022 maximum drawdowns are reported separately, for the strategy and for SPY. They are not a gate. Each year uses the daily equity curve inside that year, with the peak reset at the first close of the year.

The deflated Sharpe ratio and the probabilistic Sharpe ratio are reported. The next test uses the variant count in the marker, 18. They are not a third pass condition. The pass rule is (a) and (b) only. A GO or a NO-GO is recorded only after the independent-script replication in `docs/backtest/README.md`. Until that check passes, the verdict is PENDING-REPLICATION. The completed-test counter does not wait.

### GEM and GEM-BIL are both reported

GEM holds AGG when SPY's 12-month total return does not beat BIL. GEM-BIL is the same rule with BIL as the defensive asset instead of AGG.

Both books are reported. The one with the better result is not selected afterward. The decision is the original GEM book, the one that uses AGG. GEM and GEM-BIL are separate trials. Each has its own preregistered gate.

GEM-BIL can be marked GO only if it passes (a) and (b) on 2014-today and it also passes (a) and (b) on 2014-2021 alone. That second window is the month-ends from 2014-01 through 2021-12. The year 2022, and every later month, is excluded from it. Failing either window is not a GO. The recorded verdict still waits for the independent-script check.

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

## Effective-trial counter

<!-- TRIAL-COUNT -->
Completed tests: **9** (6 prior + Faber + GEM + GEM-BIL). GO: **0**. The counter moved when those tests were run. A verdict stays PENDING-REPLICATION until an independent script matches. The gem-agg verdict is NO-GO. The next test uses a deflated Sharpe N of **18** (15 earlier variants + Faber + GEM + GEM-BIL). A bug-fix re-run does not add a trial.
<!-- /TRIAL-COUNT -->

## What this queue runs

The Faber queue's return tests are Faber, GEM with AGG, and GEM-BIL. The September 2026 signal and the sector-turnover count are already filed and are not return tests. Post-earnings drift and the T3 penny book are not run. T7 and T8 are specified in the amendment below, which was locked before those returns were computed.

Every return report also shows buy-and-hold VT beside SPY. SPY stays the primary bar. VT exists for the whole decision window. Daily strategy returns and daily SPY returns are saved under `docs/backtest/series/`.

## Results, computed after the lock

The decision is GEM with AGG: **NO-GO**. GEM-BIL is **NO-GO**. Faber is **PENDING-REPLICATION** because no independent script has matched it yet; the test is already in the counter. GEM-BIL was not preferred to GEM. Reports: `docs/backtest/faber-10m-2026-10-15.md`, `docs/backtest/gem-agg-2026-10-15.md`, `docs/backtest/gem-bil-2026-10-15.md`. Daily returns are in `docs/backtest/series/`.

The standalone script `gem.py` replicates GEM with no costs and fractional shares from 2014-01 through 2025-12 (144 holding months). It uses each fund's own month-end adjusted close, a twelve-month return that includes the signal month, and a holding for the next calendar month. Against the main signals on that same no-cost path, GEM (AGG) is 7.8725% a year on both sides and GEM-BIL is 8.0031% a year on both sides. Holding mismatches: 0. The September 2026 signal is VEU on both sides. The record is `docs/backtest/replication.json`.

| Book | Verdict | CAGR | Verdict benchmark | Bootstrap |
|---|---|---:|---:|---:|
| Faber T10, cash at 0%, whole shares of SPYM | PENDING-REPLICATION | +7.62% | +9.64% SPYM | 41.5% |
| GEM, AGG, the decision | NO-GO | +6.48% | +12.34% SPYM | 0.1% |
| GEM-BIL | NO-GO | +6.50% | +12.34% SPYM | 1.6% |

The verdict column is row (b): whole shares of a $460 account on both the strategy and the benchmark, with SPY exposure in SPYM. Row (a), fractional units on both sides, is logic and is not the verdict. GEM row (a) is +7.08% against fractional SPY +12.96%. GEM-BIL row (a) is +7.24% against the same +12.96%. The earlier fractional adjusted-close SPY figure of +14.08% is not the verdict benchmark. GEM-BIL also fails 2014–2021 on row (b) (CAGR +5.21% against SPYM +13.44%, bootstrap 0%). The desk's independent cost path closes GEM at 5.25% and GEM-BIL at 5.42%, with 1.75 switches a year. This book's own execution switches 21 times in 12.66 years, 1.66 a year. Both closes are NO-GO. The first executable fill is 5 shares of SPYM at 84.00 on 2014-02-03, with $38.96 left in cash.

Faber T10's recorded book holds cash at 0% when it is out of the market. Row (a) is the pre-registered fractional adjusted close, +8.01% against SPY +10.92%, bootstrap 45.3%. A BIL out-of-market sleeve, with a commission on every BIL entry and exit, is information only: whole-share CAGR +7.73% against SPYM +9.64%, bootstrap 40.7%, fees $66 against $34 on the cash book. That sleeve is NO-GO. It is not recorded and it does not add a trial. The GEM-window Faber book, +6.32% with the prior fractional benchmark, was not rebuilt in this pass. Its recorded verdict stays PENDING-REPLICATION.

## Benchmark audit

Month-end 2013-12-31 to 2025-12-31, SPY adjusted close compounds at 13.42% and the raw close at 11.50%. The old GEM and Faber-SPYM figure of 11.24% was the raw-close whole-share account, not that adjusted close. Gates below were recomputed on the adjusted close, with the same dates and the same open or close entry the strategy uses.

Alpaca keys are not set in this environment, so SPY, VEU, AGG, and BIL were not compared with Alpaca. That check has to request daily bars with `adjustment=all` (splits and dividends). `adjustment=raw` or `adjustment=split` is not comparable to Yahoo adjusted close.

| Test | Window | Entry | Strategy price field | SPY price field in the original gate | Recomputed SPY total return | Gate |
|---|---|---|---|---|---|---|
| Breakout | 2024-01-02 to 2026-10-13 | Intraday stop, minute bar | Alpaca SIP minutes, `adjustment=raw` | Not used | +21.04% adjusted close to close | Unchanged NO-GO. The gate does not use SPY. |
| Noise | 2023-01-03 to 2026-10-13, out of sample | Same-day open to close | Alpaca daily, `adjustment=raw` | Same raw open to close | Open-to-close is unchanged. A same-day adjustment factor scales the open and the close together. Window adjusted close is +22.35%. | Unchanged NO-GO. |
| Penny | 2023-01-03 to 2026-10-13, out of sample | Next minute's open | Alpaca SIP minutes, `adjustment=raw` | Not used | +22.35% adjusted close to close | Unchanged NO-GO. The gate does not use SPY. |
| Swing | 2023-01-03 to 2026-10-13, out of sample | Next session's open | Alpaca daily, `adjustment=raw` | Not used | +22.35% adjusted close to close | Unchanged NO-GO. The gate does not use SPY. |
| Sector momentum | Out of sample, month-ends 2019-12-31 to 2026-09-30 | Month-end close | Yahoo adjusted close | Yahoo adjusted close | +15.25%, the same figure already in the report | Unchanged NO-GO. Sharpe still trails SPY. |
| T6, VIX term structure | One-day execution 2009-09-21 to 2026-10-14; out of sample from 2020 | Buy at the open, mark at the close | Yahoo adjusted open and adjusted close | Same adjusted open and close | Already total return. Out-of-sample adjusted close, 2020-01-02 to 2026-10-14, is +15.17%. | T6 is this book. Verdict stays CLOSED-COST. It is the sixth prior completed test and it is in the counter. It is not a GO. |
| Faber, SPYM whole shares | 2014-01-31 to 2026-09-30 | Next session's open | Raw open and close, plus cash dividends, whole shares | Raw close, whole shares, +11.24% | Prior fractional adjusted path +14.08%. This book was not rebuilt onto the whole-share SPYM benchmark. | Legs fail. Recorded verdict stays PENDING-REPLICATION. |
| Faber T10 | 2006-12-29 to 2026-10-14 | That close | Row (a) Yahoo adjusted close. Row (b) SPYM actual close, cash dividends, whole shares. Cash at 0% when out. | Row (a) adjusted close, +10.92%. Row (b) whole-share SPYM, +9.64%. | Row (a) +8.01% against +10.92%, bootstrap 45.3%. Row (b) +7.62% against +9.64%, bootstrap 41.5%. | Legs fail. Recorded verdict stays PENDING-REPLICATION. The BIL sleeve is information only and is NO-GO. |
| GEM and GEM-BIL | 2014-01-31 to 2026-09-30 | Next session's open | Raw open and close, plus cash dividends. Row (b) whole shares. | Row (b) whole-share SPYM | +12.34%. Row (a) fractional SPY is +12.96%. Early window on row (b), 2014-01-31 to 2021-12-31, is +13.44%. | Closed NO-GO. GEM bootstrap 0.1%. GEM-BIL bootstrap 1.6%, and 0% on 2014–2021. |

## T7 and T8, locked before those returns

Reopened on 2026-10-15, after the Faber and GEM results and before any FOMC or CPI return is computed. Nothing here places or transmits an order.

### Tax, costs, and the bar

T7 and T8 are CLOSED-COST. Tax is not computed. `bot/tax.py` is not called by these reports.

The account starts at $460. Shares may be fractional, because a whole SPY share does not fit in $460 for most of the out-of-sample window. Commission is IBKR Pro fixed: $0.005 a share, minimum $1.00 an order, maximum 1% of trade value. The spread is 1 basis point a side. Selling the sleeve and buying SPY are separate orders, and so are the later sale of SPY and the purchase of the sleeve. Cash that accrues a T-bill yield has no commission.

These books are not trials, so they do not add to the counter. In-sample is reported and cannot change the closed-cost label.

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

The registered buy is 9:35 ET, and the exit is the close. This run has no 9:35 print. The registered out-of-sample trade count is 0. The pre-registered minimum was 40. The book is closed on cost.

A separate description buys at the official open and sells at the close when the gap is at most -0.5%. It uses the same sleeve and costs. On a release day that description does not accrue the sleeve from the prior close to the open. Every other session accrues the sleeve from close to close. In-sample release dates fall in 2010 through 2018. Out-of-sample release dates fall in 2019 through the last session. The description's numbers, its tail losses, and its 2022 trades are reported. They are not the verdict.

The VIX1D variant keeps the 0.5% rule and also requires the gap to be strictly below the prior session's VIX1D close divided by 100 and by the square root of 252. VIX1D is the CBOE daily history. The file's first print is in 2022. The variant still starts in 2023, on the first session that has a prior VIX1D close. It is an out-of-sample description. It is not a verdict.

## T7 and T8 results

Computed after the lock above. Reports: `docs/backtest/fomc-drift-2026-10-15.md` and `docs/backtest/cpi-ppi-fade-2026-10-15.md`.

T7 is **CLOSED-COST** and T8 is **CLOSED-COST**. Variant B is skipped. There is no 13:55 print. The open-fill description and the VIX1D description are not verdicts.

## Queue amendment, locked before T9 and T10

Locked on 2026-10-15, before any turn-of-month or new Faber return is computed. Nothing here places or transmits an order. The earlier Faber, GEM, and GEM-BIL reports stay as written. Their pass rule is not reopened. Desk-gate commission constants used by the closed penny, swing, and noise books stay at $0.0035 a share and a $0.35 minimum. Pending books use the rule below.

### Commission

IBKR Pro Fixed, on each order, from that order's shares and price. $0.005 a share, minimum $1.00, maximum 1% of that order's trade value. A $460 SPY order pays the $1 minimum, about 0.22% of the order. An order of $50 to $100 pays the 1% cap, because the $1 minimum is larger than 1% of the trade value. It is not a flat percent. The code is `bot/queue_rules.py`.

### Sample size

Every pending report computes, before its results,

n = (1.645 × sigma / mu)²

Sigma is the sample standard deviation of in-sample per-trade returns. Mu is their mean. One trade, or no trades, leaves n undefined. A mean of zero leaves n infinite. A negative mean is used as written, and the square keeps n positive. If the out-of-sample trade count is below n, or n is undefined, the sample count is reported. A book the gate file marks closed takes that status instead. The sample count is not the verdict.

T7 is flagged: a pre-Fed book has about 8 announcement windows a year. T8 is flagged: CPI and PPI together are about 24 release days a year, and the fade trades only the gap-down days.

T4 and T5 are not re-run. T6, the VIX term-structure book, is not re-run. It is already in the counter. These reports get a preface. Where a saved trade list exists, n is computed from it. Where it does not, the preface says n was not computed. Saved fills keep the commission they were run with. This does not change N.

### T9, turn of the month

Reopened as a quick check. The window is the last trading session of the month plus the first three trading sessions of the next month. The entry is the close before that last session. The exit is the close of the third session of the next month. Cash earns nothing in between. Prices are Yahoo Finance chart v8 adjusted closes. In-sample exits fall in 2000 through 2014. Out-of-sample exits fall in 2015 through the last session. The per-trade return for n is the window's adjusted return minus the two Fixed commissions divided by $460.

T9 is CLOSED-COST. No tax is computed. The compounded path is not run. T9 does not change the counter.

### T10, Faber on SPY

A different book from the SPYM whole-share Faber report. At the close of each month's last trading day, hold SPY when that adjusted close is strictly above the average of the last ten month-end adjusted closes, including that month. Otherwise hold cash at 0%. A tie is cash. There is no spread. Shares may be fractional. The account starts at $460.

The 10-month average was published in 2007 and is not fitted. There is no in-sample period, so n is not computed and the sample-size gate does not apply. The reported window is one out-of-sample period: the December 2006 signal, which needs the ten month-ends ending that month, through the last session. The order is at that close. The next month's moves belong to the new holding. The last session is liquidated so the exit cost is in the result. That liquidation is not a switch.

Metrics: CAGR, maximum drawdown, daily Sharpe with a zero risk-free rate, and switches per year. Commission is Fixed. No tax is computed. Buy-and-hold SPY pays one entry and one exit.

The verdict is the same two-part gate as Faber and GEM, from `faber-spy` in `docs/backtest/gates.json`: CAGR no more than 1 percentage point below SPY, and the paired 3-month block bootstrap clearing 80%. Both are required. A smaller path drawdown alone is not a GO. T10 does not add a trial. Faber and GEM remain the one monthly-trend trial, counted only after GEM.

## Queue results, computed after the lock

T3, the empty premarket book, is **CLOSED-COST**. Report: `docs/backtest/premarket-catalyst-2026-10-15.md`.

T4 was not re-run. The queue verdict is **CLOSED-COST**. The saved fills stay at $0.0035.

T5 was not re-run. The turnover close stays NO-GO at 9.82 replacements a year.

T6, the VIX term-structure book, was not re-run. The verdict is **CLOSED-COST**. It is the sixth prior completed test and it is already in the counter. It is not a GO. This line does not add a trial. N stays 18.

T7 is **CLOSED-COST**. T8 is **CLOSED-COST**. Variant B stays skipped. There is no 13:55 print.

T9, the turn-of-month quick screen, is **CLOSED-COST**. The compounded path was not run. Report: `docs/backtest/tom-2026-10-15.md`.

T10, the Faber filter, keeps cash at 0% when it is out of the market. The recorded verdict is row (b), whole shares of SPYM: CAGR +7.62% against SPYM +9.64%, bootstrap 41.5%. Both legs fail. The recorded verdict stays **PENDING-REPLICATION**. Row (a) is unchanged at +8.01% against adjusted-close SPY +10.92%, bootstrap 45.3%. The BIL sleeve is information only. On row (b) it is +7.73% against SPYM +9.64%, bootstrap 40.7%, with $66 of commission against $34 on the cash book, and the gate label is NO-GO. It is not recorded. It does not add a trial. N stays 18. Report: `docs/backtest/faber-spy-2026-10-15.md`.


## Reopen thresholds

No book is rerun in this section. Completed tests stay 9. GO stays 0. N stays 18.

T3, T4, T6, T8, and T9 are NO-GO at any account size. A larger account does not reopen them. T3, T4, T8, and T9 stay out of the counter. T6 is the VIX term-structure book. It stays in the counter, and its recorded verdict stays CLOSED-COST. The account-size answer does not add a trial.

T9's in-sample mean is negative, so the sample-size formula has no threshold. The out-of-sample edge is not used.

T7 clears the cost screen on a cash-only account near $8,000. It does not beat SPY. It stays closed, and it is not in the counter.

Straddles are TBD after 100 events. A cash account is acceptable for long straddles.

## Sector rotation is closed

`docs/backtest/T5_TURNOVER.md` counts the preregistered book from 2007-01-31 through 2026-09-30. The average is 9.82 single-fund replacements per year. The gate was 2. T5 is closed NO-GO. No return test is run. The earlier sector return report stays a prior trial under the old gate. It does not add a cluster beyond the one already counted.
