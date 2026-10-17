# VIX term structure — locked 2026-10-15

Locked before any CBOE or Yahoo request for this test. Nothing here places or transmits an order. A GO is a paper result. It is not a merge and it is not permission to trade. The rule below is used unchanged on both samples.

## Signal

The one-day book is the verdict. Hold SPY when the prior session's VIX close is strictly below the prior session's VIX3M close. Otherwise hold the cash sleeve. A close equal to VIX3M holds the sleeve.

The fill is the next session's open. The close that forms the signal is not traded on that same open.

The two-day book holds SPY only when both the latest prior close and the close before that one have VIX below VIX3M. Otherwise it holds the sleeve. Same next-open fill. This book is reported. It does not flip the verdict.

## Cash sleeve

Before 2020-01-01 the sleeve is BIL. On and after 2020-01-01 the sleeve is SGOV once SGOV has a bar, and BIL until that first bar. SGOV's listing is inside 2020, so the early months of 2020 are BIL when the signal is off. A change from BIL to SGOV is a switch.

## Prices

VIX and VIX3M are the free CBOE daily history: `VIX_History.csv` and `VIX3M_History.csv` under `https://cdn.cboe.com/api/global/us_indices/daily_prices/`. The columns are DATE, OPEN, HIGH, LOW, CLOSE. DATE is MM/DD/YYYY. The signal uses CLOSE.

SPY, BIL, and SGOV are Yahoo Finance chart v8. The adjusted open is the printed open times `adjclose / close`. The adjusted close is `adjclose`. Alpaca is not the source. Its daily history starts near 2016.

If VIX3M's first overlapping bar is after 2008-01-01, in-sample starts on that bar and the report states the gap. A session missing VIX, VIX3M, SPY, or BIL is removed. On and after SGOV's first bar, a session missing SGOV is removed too. The next kept session uses the previous kept close. If more than 1% of the SPY sessions in the overlap are removed, the file is incomplete and the verdict is NO-GO.

The last session is the last bar strictly before the New York run date. The run date itself is not used.

## Path and costs

The account starts at $460 and compounds. Shares may be fractional. The position is marked at the close. The overnight gap belongs to the asset already held. At the next open, a new symbol is bought and the old one is sold. The first buy is not a switch.

Each order pays IBKR fixed commission: $0.0035 a share, $0.35 minimum. The spread is 2 basis points round trip: half on the buy and half on the sell. These are the same costs as the sector-momentum test. A buy is shrunk so the cash balance does not go negative. Costs are paid on the first buy and on each switch.

Buy-and-hold SPY uses the same execution dates, buys once at the first open, and holds. It pays that one entry cost. The comparison is this path.

## Samples and the gate

In-sample execution dates fall in 2008 through 2019. Out-of-sample execution dates fall in 2020 through the last session. The in-sample result is reported and cannot flip the verdict.

Out of sample, on the one-day book, both of these have to hold, after costs:

- The annualized Sharpe ratio of daily simple returns is strictly above buy-and-hold SPY. The risk-free rate is zero. The deviation is the sample standard deviation. The scale is the square root of 252.
- The maximum drawdown is strictly smaller in magnitude than buy-and-hold SPY. Drawdown uses the out-of-sample close marks only. The first out-of-sample close starts the peak. An earlier in-sample peak does not carry in.

The report counts switches per year for both books. A switch is a change of symbol.

## Tax line

No tax is computed. A report does not add a tax line.
## Run

`python -m bot.vix_term_backtest`. An incomplete download is NO-GO and exits 1. A finished NO-GO exits 0.
