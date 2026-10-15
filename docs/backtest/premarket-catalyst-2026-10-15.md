# Premarket catalyst — 2026-10-15

Long only, one name a day, $460. Split-adjusted prices. The headline had to be a primary wire in the 24 hours ending 09:30. S-1, 424B, and S-3 / S-3ASR shelves in the prior 30 days were excluded. Shares outstanding stand in for float and are not float. This replay does not place or transmit an order.

Verdict: **NO-GO**.

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
The $0.35 minimum binds below 100 shares: 0 of 0.

## Cost model

Every trade in this book is a sub-$5 name. When a SIP quote exists at the entry minute and at the exit minute, the entry fills at the ask and the exit fills at the bid. Both quotes are scaled by that day's split-adjusted close divided by the raw close. If either quote is missing, the entry print is raised by 1% and the exit print is lowered by 1%. That is 1% on each side. It is not a 1% round trip. Commission stays $0.0035 a share with a $0.35 minimum, both sides. The share count is the whole shares of $460 at the ask, or at the slipped entry when the quote is missing. Net R divides the dollar result by the printed entry-to-stop risk.

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

The 86,582 relative-volume passers were not joined to Alpaca's reverse-split file. That would be a new download, and the recorded trade list has nothing to exclude. Both books are the same empty book, and both are NO-GO.

## ATM, S-3 full text, and going concern

Not tested. An at-the-market flag and a going-concern flag need the EDGAR full-text search. That search was not run. The filing screen never started, because no name had a primary-wire headline. This run also has no SEC_UA, and a request to sec.gov without it stops the process. The locked 30-day submissions screen for S-1, 424B, and S-3 / S-3ASR is unchanged. It was not reached either.

## After tax

Israeli capital-gains tax at 25% on realized gains. Losses offset gains inside the same calendar year. A losing year pays nothing and does not carry forward. There is no refund. The tax line does not change the pre-tax gate.

This book never bought, so realized gains are zero and the tax is zero. Buy-and-hold SPY, taxed once at the end, is not a comparison for a book that never bought.

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

- Premarket catalyst: NO-GO.
