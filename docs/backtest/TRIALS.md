# Trial log

Locked on 2026-10-15, before any Faber, GEM, or GEM-BIL return is computed. Nothing in this log places or transmits an order. A later GO would be a paper result. It would not be a merge and it would not be permission to trade.

No tax is computed anywhere in this program.

## Not a trial

- Turn of the month (T9). Closed NO-GO on the cost arithmetic. It is not counted, and it is not in the effective-trial number.
- The VIX term-structure test is not re-run. The one-day book already reported on 2026-10-15 remains a prior trial. Its two-day book stays in that same cluster.
- The 13:55 ET pre-FOMC variant and the CPI/PPI gap-down fade were taken off the queue before either was run. They are not trials.

## Effective trials

A raw trial is one return variant that was actually run. Correlated variants share a cluster. The deflated Sharpe ratio uses the number of clusters, not the number of raw rows. Faber, GEM, and GEM-BIL are one cluster: they are monthly trend filters. GEM-BIL is still its own counted trial inside that cluster.

| Cluster | Raw trials already run | Result |
|---|---|---|
| Breakout | v1; v2; the six post-mortem filters fit on 2024 | NO-GO. The filters were discarded. |
| Noise area | QQQ and SPY, long and short | NO-GO |
| Penny intraday | Opening-range breakout; VWAP pullback | NO-GO, both |
| Swing | No earnings filter; calendar earnings proxy | NO-GO, both |
| Premarket catalyst | Sub-$5 book | NO-GO. The $3–$20 book and the blacklist are the same cluster if they finish. |
| Post-earnings drift | Holds of 10, 20, and 40 sessions. The gate was the 20-session book. | NO-GO |
| Sector momentum | The 2026-10-15 return replay, under that day's gate | NO-GO. See the turnover close below. No further return is run. |
| VIX term structure | One-day book (the verdict); two-day book (not the verdict) | GO on the one-day book's own gate. Not re-run. |

That is 15 raw trials and 8 clusters. Two clusters are registered below and have not been run: monthly trend (Faber, GEM, GEM-BIL) and pre-FOMC daily closes. Counting those planned clusters now, so the number does not shrink after a result, the effective number of trials for a deflated Sharpe ratio in this program is **10**.

Older closed work (the ticket search, the ensemble, the index program, the squeeze list) is not reopened and is not added on top of these 10.

## Faber and GEM, locked before the run

### The pass rule

The decision window is the completed month-ends from January 2014 through the last completed month. On a run dated 2026-10-15 that is 2014-01-31 through 2026-09-30. History before 2014 is supplementary. It is reported and it cannot flip the verdict.

Pass only if both of these hold, after costs, on that window:

(a) Annual return (CAGR) is no more than 1 percentage point below buy-and-hold SPY over the same window.

(b) In a paired 3-month block bootstrap of 1000 draws, the strategy's maximum drawdown is at least one third smaller than SPY's in at least 80% of the draws.

Costs are IBKR Pro fixed commission, $0.005 a share, minimum $1.00 an order, maximum 1% of trade value, plus a 2 basis point round-trip spread (1 basis point a side). SPY buy-and-hold pays the entry and the final exit. Cash earns nothing. There is no tax.

The bootstrap is fixed as follows. The inputs are the paired monthly total returns of the strategy and of SPY on the decision window, in calendar order, length T. A block is three consecutive months. Draws are circular and with replacement, so the series wraps and every month can start a block. Each draw takes enough blocks to cover T months and then keeps the first T. The strategy and SPY use the same block starts. Wealth starts at 1 and compounds the drawn months. Maximum drawdown is the deepest peak-to-trough decline, as a positive fraction. "At least one third smaller" means the strategy's drawdown is less than or equal to two thirds of SPY's drawdown on that same draw. If SPY's drawdown on a draw is zero, the draw passes only when the strategy's drawdown is also zero. The generator is `random.Random(20261015)`. The pass fraction is reported.

Calendar 2020 and calendar 2022 maximum drawdowns are reported separately, for the strategy and for SPY. They are not a gate. Each year uses the daily equity curve inside that year, with the peak reset at the first close of the year.

The deflated Sharpe ratio and the probabilistic Sharpe ratio are reported, at the effective trial count of 10. They are not a third pass condition. The pass rule is (a) and (b) only.

### GEM and GEM-BIL are both reported

GEM holds AGG when SPY's 12-month total return does not beat BIL. GEM-BIL is the same rule with BIL as the defensive asset instead of AGG.

Both books are reported. The one with the better result is not selected afterward. The decision is the original GEM book, the one that uses AGG.

GEM-BIL is a separate counted trial. It can be marked GO only if it passes (a) and (b) on 2014-today and it also passes (a) and (b) on 2014-2021 alone. That second window is the month-ends from 2014-01 through 2021-12. The year 2022, and every later month, is excluded from it. Failing either window is not a GO.

### The rule itself

There is no gap and no hysteresis. A month stands on its own comparison. A tie is not a win: if the SPY and BIL 12-month returns are equal, the book holds the defensive asset; if the SPY and VEU 12-month returns are equal, the book holds SPY.

The 12-month total return is the month-end adjusted close divided by the month-end adjusted close twelve months earlier, minus one. Adjusted closes include dividends. An ETF is not used before it exists.

The signal is the month-end close. The fill is the next session's open. A switch from one ETF to another is T+1: the sale is the next open, and the purchase is the open after that, because the sale has not settled. The night in between is cash at zero. Staying in the same ETF does not trade. The first purchase, from settled cash, is the next open.

Faber, on the same execution, holds SPY when the month-end adjusted close is strictly above the average of the last ten month-end adjusted closes, including that month. Otherwise it holds cash at zero. A close equal to the average is cash. There is no band around the average and no extra month of confirmation.

The September 2026 signal, computed before this return run, is recorded in `docs/backtest/GEM_SIGNAL_2026-09.md`. The holding is VEU. That file is not a return test.

### What is not the bar

Beating SPY by 1% a year, and an excess Sharpe of 0.8, are the bar for the return-seeking tests below. They are not the Faber or GEM bar.

## Return-seeking tests

These keep a different bar. GO requires all three, after the same fixed commission and spread, with no tax: CAGR at least 1 percentage point above SPY, annualized Sharpe at least 0.8 above SPY (excess Sharpe, risk-free rate zero), and a deflated Sharpe ratio of at least 0.95 at 10 effective trials. The required trade count uses half of the in-sample mean, the McLean-Pontiff haircut: `n = (1.645 * sigma / (mu / 2))^2` when the in-sample mean is positive. If the out-of-sample count is below that n, the verdict is UNKNOWN. An event test counts independent events, not days. Fewer than 100 events cannot be a GO; if the count is also below the required n, the verdict stays UNKNOWN.

Pre-FOMC is the event test still on the queue, and only its daily-close variants. The 13:55 variant is not run. A large-cap post-earnings split, if the data can be built without a second bulk download, is another raw trial inside the existing post-earnings cluster, not a new cluster.

Every return report also shows buy-and-hold VT beside SPY. SPY stays the primary bar. Where VT does not exist yet, the secondary line is an 80% VTI / 20% VXUS buy-and-hold, and only over the dates both exist. The comparison says so. Daily strategy returns and daily SPY returns are saved under `docs/backtest/series/` when a return test is run.

## Sector rotation is closed

`docs/backtest/T5_TURNOVER.md` counts the preregistered book from 2007-01-31 through 2026-09-30. The average is 9.82 single-fund replacements per year. The gate was 2. T5 is closed NO-GO. No return test is run. The earlier sector return report stays a prior trial under the old gate. It does not add a cluster beyond the one already counted.
