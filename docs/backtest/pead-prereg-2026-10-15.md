# Post-earnings drift — locked 2026-10-15

Locked before any Alpaca request for this test. Nothing here places or transmits an order. A GO is a paper result. It is not a merge and it is not permission to trade.

Cash, $460, long only, whole shares, one position. Sale proceeds are not reused until the session after the exit. The book does not compound. In-sample is 2016-01-01 through 2022-12-31 and is not gated. Out-of-sample is an entry on or after 2023-01-01 through the last completed session. An incomplete download is NO-GO.

The download waits until the premarket-catalyst replay has finished, so the two jobs do not share Alpaca's request limit.

## Earnings day

Alpaca has no earnings calendar. A day is an earnings day when some headline in the 24 hours ending 09:30 New York matches `\bearnings\b`, `\bresults\b`, `\bquarter`, `\bEPS\b`, or `\brevenue\b`. Only the headline is read. Monday 09:30 looks back to Sunday 09:30. A missing or truncated news page excludes that day's names.

## The event, all required

Split-adjusted daily bars (`adjustment=split`).

- Gap: event open / previous session's close − 1 is at least 5 percent. The previous session has to have printed.
- Relative volume: event volume / median volume of the prior 20 sessions is at least 3. At least 10 of those 20 printed.
- The close is in the top half of the high-low range. A bar with no range is skipped.
- The close is above $5.
- Dollar ADV, the mean of close times volume over those same prior sessions, is above $20 million, with at least 10 prints.

A forward or reverse split whose ex-date is the event day or the entry day is skipped. The AAPL 2020-08-31 split-adjusted close has to stay inside ±25 percent, or the file is incomplete.

## Entry, stop, hold

Enter the next session's open. That next bar has to be the next session. The disaster stop is the event day's low. If the next open is at or below that low, skip the trade. There is no profit target. A later bar that opens through the stop exits at the open. A bar that trades the stop exits at the stop. Otherwise the exit is the close of session 10, 20, or 40, counting the entry session. A halt uses up one session. If the last session of the window did not print, or the calendar runs out, the exit is the last close and the kind is `last`. The gated hold is 20. Holds of 10 and 40 are the same signals, each scheduled as its own $460 book, and they do not flip the verdict.

One name at a time. The same entry day keeps the higher prior ADV, then the symbol.

## Costs

Amended 2026-10-15 for an entry print strictly under $5. A print at $5 or above is unchanged.

At $5 or above, both books pay the spread: the full SIP quote during the 09:30 minute of the entry, scaled by that day's split-adjusted close over the raw close, added on top of the printed prices. A missing quote is the 1 percent round-trip haircut.

- IBKR Pro, the gate: $0.0035 a share, $0.35 minimum, both sides, plus the spread.
- IBKR Lite, reported: commission zero, plus the same spread.

Under $5, the entry fills at the ask and the exit at the bid when both quotes exist. The entry quote is the 09:30 minute. The exit quote is the session close when the hold ends at the close. A stop has no minute on a daily bar, so if either quote is missing the entry print is raised by 1% and the exit print is lowered by 1%. That is 1% on each side, not a 1% round trip. The same quotes at 0.5% and at 2% per side are reported and do not flip the verdict. Pro and Lite still differ only by the commission.

## Gate

On the 20-session Pro book, out of sample: at least 100 trades, mean net R above 0 with t above 2, mean above the 95th percentile of 20 random-direction means, and mean net R above 0 in 2023 and in 2024 and in 2025 and in 2026. Besides that, the mean net return has to be strictly above buy-and-hold SPY over the same windows. SPY is the split-adjusted open on the entry day to the split-adjusted close on the exit day, with no commission and no spread. A trade with no SPY window fails that check.

## Survivorship

The universe is Alpaca's active and inactive asset file, the same inclusion rule as the premarket test. The report counts inactive names and the nine known delistings. The coverage gaps are: tickers Alpaca never stored, a reused ticker, a delisting with no inactive row, no CRSP delisting return, and a split-adjusted close that a later split can move across $5. Shares outstanding are not a filter. There is no earnings calendar.
