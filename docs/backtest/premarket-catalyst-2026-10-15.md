# Premarket catalyst — 2026-10-15

Long only, one name a day, $460. Split-adjusted prices. The headline had to be a primary wire in the 24 hours ending 09:30. S-1, 424B, and S-3 / S-3ASR shelves in the prior 30 days were excluded. Shares outstanding stand in for float and are not float. This replay does not place or transmit an order.

## Required sample

T3 premarket catalyst: the in-sample period has no trades, so mu and sigma are not defined. Required n is not defined.
Out-of-sample trades: 0. That count is below n. The sample count is not the verdict.

The checks under the verdict are the book's own gate. When the sample is short they stay on the page and they are not the queue verdict.

Verdict: **CLOSED-COST**.

- fail: at least 100 trades (0)
- fail: mean net R > 0 with t > 2
- fail: mean net R above the 95th percentile of 20 random-direction means
- fail: mean net R > 0 in 2023 and in 2024 and in 2025 and in 2026

Window asked for: in-sample 2016-01-01 through 2022-12-31, out-of-sample 2023-01-01 through 2026-10-14. First split-adjusted daily bar: 2016-01-04.

Universe 6379 names, 745 inactive in the asset file, 726 of those inactive names have a daily bar.

AAPL split check: {'from': '2020-08-28', 'to': '2020-08-31', 'jump': 0.033891515102956404}. Split sessions skipped: 492.

Price-band days 1522111. Relative-volume pass 86582. Primary-source headlines 0. News days cut off early 0. No CIK 0. Filing blocked 0. Filing history incomplete 0. Shares missing 0, scale error 0, stale 0, at or above 20 million 0. Ready for the entry 0.

| Sample | Trades | Mean net R | t | Random p95 | Win rate | Mean R without commission |
|---|---:|---:|---:|---:|---:|---:|
| 2023–2026 | 0 | — | — | — | — | — |
| 2016–2022 | 0 | — | — | — | — | — |

| Year | Trades | Mean net R |
|---|---:|---:|
| 2016 | 0 | — |
| 2017 | 0 | — |
| 2018 | 0 | — |
| 2019 | 0 | — |
| 2020 | 0 | — |
| 2021 | 0 | — |
| 2022 | 0 | — |
| 2023 | 0 | — |
| 2024 | 0 | — |
| 2025 | 0 | — |
| 2026 | 0 | — |

Trades in names Alpaca now marks inactive: 0 of 0.
Commission is IBKR Pro Fixed on each order: $0.005 a share, minimum $1.00, maximum 1% of that order's trade value. Orders on which the $1 minimum is the smaller fee: 0 of 0. Orders on which the 1% cap is the smaller fee: 0 of 0.

## Cost model

Every trade in this book is a sub-$5 name. When a SIP quote exists at the entry minute and at the exit minute, the entry fills at the ask and the exit fills at the bid. Both quotes are scaled by that day's split-adjusted close divided by the raw close. If either quote is missing, the entry print is raised by 1% and the exit print is lowered by 1%. That is 1% on each side. It is not a 1% round trip. Commission is IBKR Pro Fixed, computed on each order from the shares and that order's fill price: $0.005 a share, minimum $1.00, maximum 1% of trade value. A $460 SPY order pays the $1 minimum, about 0.22% of the order. A penny order of $50 to $100 pays the 1% cap, because the $1 minimum is larger than 1% of that value. It is not a flat percent. The share count is the whole shares of $460 at the ask, or at the slipped entry when the quote is missing. Net R divides the dollar result by the printed entry-to-stop risk.

The gate uses the 1% fallback. The same prints at 0.5% and at 2% per side are a sensitivity check. They do not flip the verdict. When both quotes exist, the three rates are the same fill.

| Fallback per side | Out-of-sample trades | Mean net R | t |
|---|---:|---:|---:|
| 1% (the gate) | 0 | — | — |
| 0.5% | 0 | — | — |
| 2% | 0 | — | — |

This download had no fills, so the 1%, 0.5%, and 2% books are the same empty book. The screen was not run again for the cost amendment.

Quotes on both sides: 0. Percent slippage: 0.

## Reverse-split blacklist

This variant sits beside the main book. It does not replace it, and it does not change the gate.

A trade is excluded when a reverse-split ex-date falls in the 365 calendar days strictly before the trade date. The trade date itself does not count. A split 366 calendar days earlier does not count.

Excluded 0 of 0 trades (not defined on an empty book). The book that remains has 0 trades.

The 86,582 relative-volume passers were not joined to Alpaca's reverse-split file. That would be a new download, and the recorded trade list has nothing to exclude. Both books are the same empty book.

## ATM, S-3 full text, and going concern

Not tested. An at-the-market flag and a going-concern flag need the EDGAR full-text search. That search was not run. The filing screen never started, because no name had a primary-wire headline. This run also has no SEC_UA, and a request to sec.gov without it stops the process. The locked 30-day submissions screen for S-1, 424B, and S-3 / S-3ASR is unchanged. It was not reached either.

## Trading halts

A likely halt is a gap of at least 5 minutes between 1-minute bars from 09:30 through the flat minute. The fill is the open of the first bar after that gap. A stop or a target on an earlier bar means the halt did not affect the trade. A gap that never resumes by the flat minute is not counted. This exit sits beside the main book and does not change the gate.

Trades affected by a halt: 0 of 0.

The recorded book has no fills, so there is no realized loss to set against the planned stop. The minute bars were not downloaded again.

## Desk size

Reported beside the main book. The gate still buys the whole shares of $460. This size is the smaller of 1% of the account at the stop and 2% of the account at the worst case. Worst case per share is the larger of the stop distance and the LULD dollars. From $0.75 through $3 the band is 20%. Above $3 it is 10%. Under $0.75 the dollar band is the lesser of $0.15 and 75% of the price. A position under $50 is skipped.

Signals that survive: 0 of 0 (not defined on an empty signal list).

On a $460 account the 20% band caps the position at $46, which is under $50, and a wider stop is smaller still. A signal priced from $0.75 through $3 does not survive. Above $3 the 10% band caps the position at $92. Under $0.75 the dollar band keeps the position under $50 as well.

The recorded book has no entry signals, so this percentage is not defined. The screen was not run again.

## Sub-$1 streak

A trade is tagged when each of the 30 raw closes before the entry day is strictly under $1. The entry day is not in the count. A close of exactly $1.00 is not under $1. Fewer than 30 printed sessions is not the tag. The tag is a proxy for a Nasdaq bid-price deficiency. It does not change the gate. The figures are the same net R as the main table.

| Tag | Sample | Trades | Mean net R | Win rate |
|---|---|---:|---:|---:|
| under $1 for 30 sessions | 2023–2026 | 0 | — | — |
| not tagged | 2023–2026 | 0 | — | — |
| under $1 for 30 sessions | 2016–2022 | 0 | — | — |
| not tagged | 2016–2022 | 0 | — | — |

The recorded book has no fills, so both sides of the split are empty. The screen was not run again.

## Survivorship

Known 2016–2018 delistings found in the asset file with bars: 2 of 9.
- LNKD (2016-12-07): NOT in the asset file, 28 raw bars.
- YHOO (2017-06-16): NOT in the asset file, 29 raw bars.
- WFM (2017-08-25): NOT in the asset file, 30 raw bars.
- MON (2018-06-06): NOT in the asset file, 28 raw bars.
- TWX (2018-06-14): NOT in the asset file, 28 raw bars.
- SHLD (2018-10-15): in the asset file, status inactive, 29 raw bars.
- SPLS (2017-09-12): in the asset file, status active, 28 raw bars.
- PNRA (2017-07-17): NOT in the asset file, 28 raw bars.
- CAB (2017-09-22): NOT in the asset file, 29 raw bars.

- Tickers Alpaca never stored are absent. The nine-name probe counts that hole for those names only.
- A reused ticker is one symbol. The two companies are not split apart.
- A delisting with no inactive asset row is absent.
- A name with no CIK, no usable shares-outstanding fact, or an incomplete filing history is excluded. Shares outstanding are not float.
- An intraday halt or a delist after the entry exits at the last print. There is no CRSP delisting return.
- The $0.50–$5 band is split-adjusted, so a later split moves an old day into or out of the band.
- OTC names and symbols outside NYSE, NASDAQ, and AMEX are out.

## Verdict

- Premarket catalyst: CLOSED-COST.
