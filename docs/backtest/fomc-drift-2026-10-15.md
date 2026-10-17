# Pre-FOMC drift — T7

Run date 2026-10-15. The last session is 2026-10-14. This is a paper test. It places no order.

Variant A buys SPY at the close two trading sessions before the announcement session and sells at the close of the session before the announcement. Variant C sells at the close of the announcement session. Variant B, the 13:55 ET sale, has no print in this run. There is no Alpaca credential and no public minute archive, so B is skipped and has no verdict.

Announcement dates are the Fed historical calendar: a Meeting heading on `fomchistorical2000.htm` through `fomchistorical2020.htm`, and `fomccalendars.htm` after that. A conference call, a notation vote, and a cancelled meeting are left out. A Fed date that is not a SPY session uses the next SPY session. The March 2020 unscheduled meeting is dated March 2 on the heading; the public statement is March 3, and this book uses March 2. A statement the Fed labels as a conference call is not in the book. That includes the January 22, 2008 and October 8, 2008 cuts. Daily prices are Yahoo Finance chart v8, including the years before 2016. SPY splits in the download: 0. The ETF marks are adjusted closes.

The calendar holds 227 announcement dates. Counts from 2000 through 2025 run from 8 to 9 a year.

The account starts at $460 and may hold fractional shares. Out of SPY it holds BIL before 2020-01-01, SGOV from its first bar in 2020, and before BIL a cash accrual at the prior session's Yahoo ^IRX close divided by 100 and by 252. Commission is IBKR Pro fixed, $0.005 a share, minimum $1, maximum 1% of trade value, plus 1 basis point a side. Selling the sleeve and buying SPY are both orders. No tax is computed.

The verdict is **CLOSED-COST**. In-sample does not change it. A and C are not selected against each other. VT is shown beside SPY.

## Required sample

Flag: T7 is a pre-Fed book, about 8 announcement windows a year, so the out-of-sample count grows slowly. The per-trade return is the round trip's dollar result divided by the dollars spent to buy SPY, after commission. The in-sample column later in this report is the economic reading. It is not the queue verdict.

Variant A: in-sample per-trade mu -0.004502, sigma 0.016611, 120 trades. Required n = (1.645 × sigma / mu)² = 36.84. Mu is negative. The formula is applied as written.
Out-of-sample trades: 96. That count meets n (36.84). The sample gate does not replace this book's own bar.

Variant C: in-sample per-trade mu -0.000048, sigma 0.018098, 120 trades. Required n = (1.645 × sigma / mu)² = 392211.14. Mu is negative. The formula is applied as written.
Out-of-sample trades: 96. That count is below n. The sample count is not the verdict.

| Book | Trades | CAGR | SPY CAGR | Fees | Max DD | Verdict |
|---|---:|---:|---:|---:|---:|---|
| A IS | 120 | -4.36% | +4.21% | $364 | 55.16% | CLOSED-COST |
| C IS | 120 | -0.18% | +4.21% | $364 | 29.16% | CLOSED-COST |
| A OOS | 96 | -7.57% | +13.69% | $388 | 60.36% | CLOSED-COST |
| C OOS | 96 | -7.71% | +13.69% | $388 | 61.05% | CLOSED-COST |

## Out of sample

Each round trip is four orders once the sleeve is an ETF: sell the sleeve, buy SPY, sell SPY, buy the sleeve. On a $460 account the $1 minimum binds, so those four orders cost $4. Before BIL exists the sleeve is a cash accrual and only the two SPY orders pay commission.

Variant A is **CLOSED-COST**. 96 round trips, from 2015-01-02 to 2026-10-14. CAGR -7.57% against SPY +13.69%. Mean held-window SPY move +0.18%. Winning windows 51.0%. VT buy-and-hold CAGR is +10.85% from 2015-01-02. Monthly Sharpe -0.56. Probabilistic Sharpe 0.000. Deflated Sharpe 0.000. Maximum drawdown 60.36%. Fees $388.00. BIL starts 2007-05-30. SGOV starts 2020-06-01. Forward-filled sessions: SPY 0, BIL 0, SGOV 0, ^IRX 8.

Variant C is **CLOSED-COST**. 96 round trips, from 2015-01-02 to 2026-10-14. CAGR -7.71% against SPY +13.69%. Mean held-window SPY move +0.17%. Winning windows 56.2%. VT buy-and-hold CAGR is +10.85% from 2015-01-02. Monthly Sharpe -0.51. Probabilistic Sharpe 0.000. Deflated Sharpe 0.000. Maximum drawdown 61.05%. Fees $388.00. BIL starts 2007-05-30. SGOV starts 2020-06-01. Forward-filled sessions: SPY 0, BIL 0, SGOV 0, ^IRX 8.

## In sample

In-sample is 2000 through 2014. It is reported here and does not change the verdicts above. The window contains 121 announcements. September 15 and September 16, 2003 are consecutive meetings and share one position, so the round-trip count is 120.

Variant A in sample is **CLOSED-COST**. CAGR -4.36% against SPY +4.21%, 120 round trips, 2000-01-03 to 2014-12-31. Monthly Sharpe -0.25. Probabilistic Sharpe 0.004. Deflated Sharpe 0.000.

Variant C in sample is **CLOSED-COST**. CAGR -0.18% against SPY +4.21%, 120 round trips, 2000-01-03 to 2014-12-31. Monthly Sharpe -0.00. Probabilistic Sharpe 0.480. Deflated Sharpe 0.058.

## Variant B

Variant B is skipped. The registered sale is 13:55 ET on the announcement session, and this run has no bar at that time. It is inside the pre-FOMC cluster. It is not a separate trial and it is not a verdict.

Daily returns, strategy and SPY, are in `docs/backtest/series/fomc-a-is.csv`, `fomc-a-oos.csv`, `fomc-c-is.csv`, and `fomc-c-oos.csv`. The announcement dates are in `fomc-announcements.csv`.
