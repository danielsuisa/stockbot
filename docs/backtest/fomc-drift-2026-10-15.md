# Pre-FOMC drift — T7

Run date 2026-10-15. The last session is 2026-10-14. This is a paper test. It places no order.

Variant A buys SPY at the close two trading sessions before the announcement session and sells at the close of the session before the announcement. Variant C sells at the close of the announcement session. Variant B, the 13:55 ET sale, has no print in this run. There is no Alpaca credential and no public minute archive, so B is skipped and has no verdict.

Announcement dates are the Fed historical calendar: a Meeting heading on `fomchistorical2000.htm` through `fomchistorical2020.htm`, and `fomccalendars.htm` after that. A conference call, a notation vote, and a cancelled meeting are left out. A Fed date that is not a SPY session uses the next SPY session. The March 2020 unscheduled meeting is dated March 2 on the heading; the public statement is March 3, and this book uses March 2. A statement the Fed labels as a conference call is not in the book. That includes the January 22, 2008 and October 8, 2008 cuts. Daily prices are Yahoo Finance chart v8, including the years before 2016. SPY splits in the download: 0. The ETF marks are adjusted closes.

The calendar holds 227 announcement dates. Counts from 2000 through 2025 run from 8 to 9 a year.

The account starts at $460 and may hold fractional shares. Out of SPY it holds BIL before 2020-01-01, SGOV from its first bar in 2020, and before BIL a cash accrual at the prior session's Yahoo ^IRX close divided by 100 and by 252. Commission is IBKR Pro fixed, $0.005 a share, minimum $1, maximum 1% of trade value, plus 1 basis point a side. Selling the sleeve and buying SPY are both orders. The 25% tax nets each calendar year and is subtracted once from terminal equity. The daily path stays pre-tax.

The bar is the out-of-sample after-tax CAGR, at least 1 percentage point above buy-and-hold SPY over the same dates. In-sample cannot flip the verdict. A and C are judged separately. The higher one is not selected. VT is shown pre-tax, beside SPY. SPY is the bar.

| Book | Trades | After-tax CAGR | SPY after-tax | Pre-tax CAGR | SPY pre-tax | Fees | Tax | Max DD | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| A IS | 120 | -4.92% | +3.36% | -4.36% | +4.21% | $364 | $20 | 55.16% | NO-GO |
| C IS | 120 | -1.01% | +3.36% | -0.18% | +4.21% | $364 | $53 | 29.16% | NO-GO |
| A OOS | 96 | -7.57% | +11.62% | -7.57% | +13.69% | $388 | $0 | 60.36% | NO-GO |
| C OOS | 96 | -7.71% | +11.62% | -7.71% | +13.69% | $388 | $0 | 61.05% | NO-GO |

## Out of sample

Each round trip is four orders once the sleeve is an ETF: sell the sleeve, buy SPY, sell SPY, buy the sleeve. On a $460 account the $1 minimum binds, so those four orders cost $4. Before BIL exists the sleeve is a cash accrual and only the two SPY orders pay commission.

Variant A is **NO-GO**. 96 round trips, from 2015-01-02 to 2026-10-14. After-tax CAGR -7.57% against SPY +11.62%. Pre-tax CAGR -7.57% against SPY +13.69%. Mean held-window SPY move +0.18%. Winning windows 51.0%. VT buy-and-hold pre-tax CAGR is +10.85% from 2015-01-02. Monthly Sharpe -0.56. Probabilistic Sharpe 0.000. Deflated Sharpe 0.000 at 9 effective trials. Maximum drawdown 60.36%, pre-tax. Fees $388.00. Tax $0.00. BIL starts 2007-05-30. SGOV starts 2020-06-01. Forward-filled sessions: SPY 0, BIL 0, SGOV 0, ^IRX 8.

Variant C is **NO-GO**. 96 round trips, from 2015-01-02 to 2026-10-14. After-tax CAGR -7.71% against SPY +11.62%. Pre-tax CAGR -7.71% against SPY +13.69%. Mean held-window SPY move +0.17%. Winning windows 56.2%. VT buy-and-hold pre-tax CAGR is +10.85% from 2015-01-02. Monthly Sharpe -0.51. Probabilistic Sharpe 0.000. Deflated Sharpe 0.000 at 9 effective trials. Maximum drawdown 61.05%, pre-tax. Fees $388.00. Tax $0.00. BIL starts 2007-05-30. SGOV starts 2020-06-01. Forward-filled sessions: SPY 0, BIL 0, SGOV 0, ^IRX 8.

## In sample

In-sample is 2000 through 2014. It is reported here and does not change the verdicts above. The window contains 121 announcements. September 15 and September 16, 2003 are consecutive meetings and share one position, so the round-trip count is 120.

Variant A in sample would read NO-GO on the same bar: after-tax CAGR -4.92% against SPY +3.36%, 120 round trips, 2000-01-03 to 2014-12-31. Monthly Sharpe -0.25. Probabilistic Sharpe 0.004. Deflated Sharpe 0.000 at 9 effective trials.

Variant C in sample would read NO-GO on the same bar: after-tax CAGR -1.01% against SPY +3.36%, 120 round trips, 2000-01-03 to 2014-12-31. Monthly Sharpe -0.00. Probabilistic Sharpe 0.480. Deflated Sharpe 0.058 at 9 effective trials.

## Variant B

Variant B is skipped. The registered sale is 13:55 ET on the announcement session, and this run has no bar at that time. It is inside the pre-FOMC cluster. It is not a separate trial and it is not a verdict.

Daily pre-tax returns, strategy and SPY, are in `docs/backtest/series/fomc-a-is.csv`, `fomc-a-oos.csv`, `fomc-c-is.csv`, and `fomc-c-oos.csv`. The announcement dates are in `fomc-announcements.csv`.
