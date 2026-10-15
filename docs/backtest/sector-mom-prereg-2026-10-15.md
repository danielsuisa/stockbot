# Sector momentum — locked 2026-10-15

Locked before any price request for this test. Nothing here places or transmits an order. A GO is a paper result. It is not a merge and it is not permission to trade. The rule below is used unchanged on both samples.

## Universe

The nine original SPDR sector ETFs, for the whole period: XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY. XLRE and XLC are excluded. They did not exist for the whole window.

## Prices

Alpaca's daily history starts near 2016, so it is not the source. The source is Yahoo Finance chart v8 `adjclose` (split- and dividend-adjusted). Stooq's daily close is not dividend-adjusted, so it is not used. SPY uses the same field. A missing adjusted close, or a sector print more than five calendar days before the SPY session that needs it, makes the file incomplete.

## Signal

At each completed month-end t, rank the nine by the adjusted close at month-end t-1 divided by the adjusted close at month-end t-6, minus one. The month from t-1 to t is not in the ratio. A tie is broken by the symbol that sorts first. Hold the top two, equal weight, until the next month-end.

If SPY's month-end adjusted close is strictly below its 200-day simple average, ending on that same session, the next month is cash. Cash earns nothing. A close equal to the average stays invested.

The month that contains the run date is not a month-end. The last month in the test sells to cash, so the exit cost is included.

## Account and costs

The account starts at $460 and compounds. Shares may be fractional. A rebalance sells first, then buys. Each order pays IBKR fixed commission: $0.0035 a share, $0.35 minimum. The spread is 2 basis points round trip: the buy pays half of that and the sell pays half of that. A buy is shrunk if the commission and the spread would take the cash balance below zero. A name that stays in the top two is not sold and bought again; only the share difference is traded.

## Samples and the gate

In-sample months are those that end in 2010 through 2019. Out-of-sample months are those that end in 2020 through the last completed month. The in-sample result is reported and cannot flip the verdict.

Out of sample, both of these have to hold, after costs:

- The mean monthly net return is positive.
- The annualized Sharpe ratio of those monthly returns is strictly above buy-and-hold SPY over the same months. SPY pays no commission and no spread. The risk-free rate is zero.

The report also gives CAGR, maximum drawdown on the daily equity curve, and each out-of-sample year.

## Run

`python -m bot.sector_mom_backtest`. An incomplete download is NO-GO.
