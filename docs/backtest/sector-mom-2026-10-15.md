# Sector momentum — 2026-10-15

## Queue preface

This return file was not re-run. It is a prior trial. The commission in the path is $0.0035 a share with a $0.35 minimum. The queue commission for a future fill is IBKR Pro Fixed. No per-trade return list was saved, so required n is not computed. The saved verdict stays NO-GO on that file's own gate. The turnover close in `T5_TURNOVER.md` is the queue close for T5.

Nine original SPDR sector ETFs, equal weight in the top two, held for one month. XLRE and XLC are excluded. The formation return at month-end t is the adjusted close at t-1 divided by the adjusted close at t-6, minus one. If SPY's month-end adjusted close is below its 200-day average, the account holds cash. Cash earns nothing. Prices are Yahoo Finance adjusted closes (`Yahoo Finance chart v8 adjclose`). This replay does not place or transmit an order.

Account $460, compounding, fractional shares. IBKR fixed commission is $0.0035 a share with a $0.35 minimum on each order. The spread is 0.02% round trip, half on the buy and half on the sell. In-sample months end in 2010–2019. Out-of-sample months end in 2020 through the last completed month. The rule is the same on both sides.

Verdict: **NO-GO**.

- pass: positive mean monthly net return after costs
- fail: Sharpe above buy-and-hold SPY

Window: 2009-12-31 through 2026-09-30. First SPY bar: 2008-01-02.
Universe: XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY. Excluded: XLC, XLRE.
Commission paid $166.60. Spread paid $11.57. Ending equity $1119.62.

| Sample | Months | Mean monthly | Sharpe | SPY Sharpe | CAGR | SPY CAGR | Max drawdown | Cash months |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2020–now | 81 | +0.61% | 0.58 | 0.93 | +5.71% | +15.25% | -24.03% | 16 |
| 2010–2019 | 120 | +0.63% | 0.68 | 1.08 | +5.31% | +13.44% | -24.63% | 17 |

Out-of-sample years. The return is the compounded monthly result. SPY is buy-and-hold over the same months, with no commission and no spread.

| Year | Months | Strategy | SPY | Months in cash |
|---|---:|---:|---:|---:|
| 2020 | 12 | +17.67% | +18.33% | 3 |
| 2021 | 12 | +19.79% | +28.73% | 0 |
| 2022 | 12 | -9.19% | -18.18% | 8 |
| 2023 | 12 | -0.93% | +26.18% | 2 |
| 2024 | 12 | +19.85% | +24.89% | 0 |
| 2025 | 12 | +4.37% | +17.72% | 2 |
| 2026 | 9 | -1.98% | +12.71% | 1 |

## Verdict

- Sector momentum: NO-GO.
