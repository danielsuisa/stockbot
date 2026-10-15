# CPI and PPI gap-down fade — T8

Run date 2026-10-15. The last session is 2026-10-14. This is a paper test. It places no order.

The registered buy is 9:35 ET on a CPI or PPI release day, and the exit is the close, when SPY opens at least 0.5% below the prior close. This run has no 9:35 print. There is no Alpaca credential and no public minute archive. Registered out-of-sample trades at that fill: 0. The pre-registered minimum is 40. The queue verdict is computed in the sample section below, before the open-fill numbers.

The numbers below are a separate description. They buy the Yahoo official open and sell the official close on the same rule. They are not the 9:35 book and they are not the verdict. On a traded morning the sleeve does not accrue from the prior close to the open. Every other session accrues the sleeve from close to close.

Release dates are the BLS year pages from 2010 through the run year. A row counts when its name is exactly Consumer Price Index or Producer Price Index. Two releases on one session are one day. The gap uses the official open and the prior official close, dividend included. SPY splits in the download: 0. Daily prices are Yahoo Finance chart v8.

CPI rows by year run from 11 to 12. PPI rows run from 10 to 13. Distinct release sessions: 406.

Costs, the sleeve, the $460 account, and the 25% tax match the pre-FOMC book. The description is scored on the same 1 percentage point after-tax line so the distance is visible. That score is not a verdict.

## Required sample

Flag: T8 is a CPI/PPI book, about 24 release days a year. The registered rule trades only the days that open at least 0.5% below the prior close, so the trade count is smaller than 24 a year. The per-trade return on the open-fill description is the dollar result divided by the dollars spent at the open, after commission.

Registered 9:35 book: the in-sample period has no trades, so mu and sigma are not defined. Required n is not defined.
Out-of-sample trades: 0. That count is below n, so the queue verdict is UNKNOWN (insufficient sample), not NO-GO.
The pre-registered floor is 40 out-of-sample trades. The registered count is 0, which is also below that floor. The queue verdict is **UNKNOWN (insufficient sample)**. In-sample 9:35 trades are 0 as well. No tail and no 2022 result exists for the registered fill, because that fill was not observed.

Open-fill description: in-sample per-trade mu -0.006842, sigma 0.008318, 27 trades. Required n = (1.645 × sigma / mu)² = 4.00. Mu is negative. The formula is applied as written.
Out-of-sample trades: 23. That count meets n (4.00). The sample gate does not replace this book's own bar.
The open-fill description is not the verdict.

| Book | Trades | After-tax CAGR | SPY after-tax | Pre-tax CAGR | SPY pre-tax | Fees | Tax | Max DD | On the line |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Open IS | 27 | -3.46% | +9.31% | -3.46% | +11.38% | $110 | $0 | 26.99% | below |
| Open OOS | 23 | -1.54% | +14.29% | -1.25% | +17.19% | $96 | $9 | 21.53% | below |

## The registered book

Out-of-sample trades at 9:35 ET: 0. The queue verdict is **UNKNOWN (insufficient sample)**.

## Open-fill description

Out of sample the open-fill description has 23 trades. After-tax CAGR -1.54% against SPY +14.29%. Pre-tax CAGR -1.25% against SPY +17.19%. VT buy-and-hold pre-tax CAGR is +14.19%. Monthly Sharpe -0.08. Probabilistic Sharpe 0.179. Deflated Sharpe 0.007 at 9 effective trials. Maximum drawdown 21.53%, pre-tax. Fees $96.00. Tax $9.30. BIL starts 2007-05-30. SGOV starts 2020-06-01. Forward-filled sessions: SPY 0, BIL 0, SGOV 0, ^IRX 7.

## In sample, open fill

In sample, 2010 through 2018, the open fill has 27 trades. After-tax CAGR -3.46% against SPY +9.31%. It does not change the verdict.

## Tail losses, open fill, out of sample

| Date | Gap | Open-to-close | Dollar pnl |
|---|---:|---:|---:|
| 2020-06-11 | -2.36% | -3.48% | -$16.83 |
| 2020-03-12 | -6.69% | -3.08% | -$15.75 |
| 2020-03-11 | -2.68% | -2.26% | -$12.43 |
| 2022-09-13 | -2.22% | -2.17% | -$10.22 |
| 2025-04-10 | -3.00% | -1.43% | -$7.91 |

## 2022, open fill

| Date | Gap | Open-to-close | Dollar pnl |
|---|---:|---:|---:|
| 2022-02-10 | -1.36% | -0.45% | -$3.83 |
| 2022-03-10 | -1.14% | +0.70% | $0.62 |
| 2022-05-12 | -0.86% | +0.76% | $0.85 |
| 2022-06-10 | -1.63% | -1.29% | -$7.00 |
| 2022-07-13 | -1.50% | +0.99% | $1.64 |
| 2022-07-14 | -1.38% | +1.15% | $2.23 |
| 2022-09-13 | -2.22% | -2.17% | -$10.22 |
| 2022-10-13 | -2.06% | +4.80% | $15.37 |

2022 open-fill trades: 8. Mean open-to-close +0.56%.

## VIX1D variant, description only

VIX1D is the CBOE daily file. The file's first print is 2022-05-13. The registered window is 2023 onward, so this book starts at 2023-01-04. The variant keeps the 0.5% rule and also requires the gap to be strictly below that prior close divided by 100 and by the square root of 252. It is an out-of-sample description. Trades: 5. After-tax CAGR +2.84% against SPY +17.41%. Pre-tax CAGR +3.76%. Monthly Sharpe 1.31. Probabilistic Sharpe 0.997. Deflated Sharpe 0.899 at 9 effective trials. This description is not a verdict.

Daily pre-tax returns are in `docs/backtest/series/cpi-ppi-open-is.csv`, `cpi-ppi-open-oos.csv`, and `cpi-ppi-vix1d.csv`. The release dates are in `bls-cpi-ppi.csv`.
