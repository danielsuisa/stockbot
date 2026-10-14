# Overnight momentum, SIP close-to-open — 2026-10-14

**NO-GO.** Primary book: ex_earnings. Variant 1, exit at the next open, 0.10% round trip. Window 2016-01-01 → 2026-10-13. Complete: True.

No order was sent. The rules were locked in `docs/backtest/overnight-mom-prereg-2026-10-14.md` before this run.

## Verdict

| Check | Result |
|---|---|
| OOS mean net > 0 and t > 2 | fail |
| OOS mean above the random 95th percentile | fail |
| OOS mean above SPY close-to-open on the same nights | fail |
| every OOS year mean net > 0 | fail |
| at least 70% of years positive | fail |
| file complete (AAPL split, SPY coverage, download) | pass |

## Out of sample, variant 1, 0.10%

| Nights | Mean (bps) | t | Newey-West t | Beat random p95 | SPY (bps) | Strategy on SPY nights (bps) |
|---:|---:|---:|---:|---|---:|---:|
| 947 | -29.73 | -2.65 | -2.69 | -2.47 | 4.39 | -29.73 |

| Hit rate | Simple annualized | Sharpe | Total | CAGR | Max drawdown |
|---:|---:|---:|---:|---:|---:|
| 45.93% | -74.91% | -1.37 | -96.60% | -59.32% | -96.71% |

## By year (variant 1, 0.10%, primary book)

| Year | Sample | Nights | Mean (bps) |
|---|---|---:|---:|
| 2016 | in-sample | 252 | 8.06 |
| 2017 | in-sample | 251 | 16.22 |
| 2018 | in-sample | 251 | 16.61 |
| 2019 | in-sample | 252 | 2.83 |
| 2020 | in-sample | 253 | 40.13 |
| 2021 | in-sample | 252 | 8.50 |
| 2022 | in-sample | 251 | -24.36 |
| 2023 | out-of-sample | 250 | -33.74 |
| 2024 | out-of-sample | 252 | 4.81 |
| 2025 | out-of-sample | 250 | -25.32 |
| 2026 | out-of-sample | 195 | -74.86 |

## Cost sensitivity, out of sample, variant 1

| Round trip | Mean (bps) | t | Above random p95 | Above SPY | OOS years all positive |
|---:|---:|---:|---|---|---|
| 0.05% | -24.73 | -2.21 | no | no | no |
| 0.10% | -29.73 | -2.65 | no | no | no |
| 0.20% | -39.73 | -3.54 | no | no | no |

## Variant 2, exit at the 09:30–09:45 VWAP (not the gate)

The random-draw test is not run here: it would need an opening VWAP for every eligible name, and only the names the book actually held were downloaded.

| Sample | Nights | Dropped nights | Mean (bps) | t | Above SPY |
|---|---:|---:|---:|---:|---|
| in-sample | 1762 | 0 | 2.97 | 0.49 | yes |
| out-of-sample | 947 | 0 | -32.04 | -2.68 | no |

## $460 account (reported, not gated)

Whole shares, five slots, $0.0035 a share with a $0.35 minimum each side, plus 0.10% round trip. Ending equity $49.47 from $460 (-89.25%). Max drawdown -89.25%. Average names held 0.21.

## Other book

Primary book is `ex_earnings` because the earliest AAPL news item is 2015-01-01. The other book, out of sample, variant 1, 0.10%: mean -27.43 bps, t -2.24, nights 947.

## Survivorship

- Universe: Alpaca assets with status active and inactive. A name is eligible on a day only if it has a bar that day, a raw close above $10, and 20 sessions of dollar volume above $20M.
- Known 2016–2018 delistings found with bars: 2 of 9. LNKD NOT in assets, 28 bars, YHOO NOT in assets, 29 bars, WFM NOT in assets, 30 bars, MON NOT in assets, 28 bars, TWX NOT in assets, 28 bars, SHLD in assets, 29 bars, SPLS in assets, 28 bars, PNRA NOT in assets, 28 bars, CAB NOT in assets, 29 bars
- A name that stops printing inside the file is kept in the average: cash merger at the cash rate, stock-and-cash at the cash part only, otherwise −100% after five sessions with no print. That −100% is harsh for a stock-only take-out Alpaca did not mark, and it is the locked choice.
- Renames: if the new symbol has bars before the name_change process date, the old symbol is dropped.
- Not fixed: tickers Alpaca never listed, a ticker reused by a later company, and dividends (the signal is the price ratio, so an ex-dividend morning is a loss even though the holder is paid).
- Exit counts: {'open': 3686807, 'delist': 98, 'merger': 98}. Inactive symbols eligible at least once: 320.

## Data

- Requests 31844 · runtime 11356s · git 62262a4 · prereg sha256 beb19f56f343adaa
- Asset-file exchanges: {'OTC': 17436, 'NASDAQ': 6843, 'NYSE': 3701, 'ARCA': 3170, 'AMEX': 382, 'BATS': 1763}. Universe symbols 6374 (744 inactive in the asset file). Eligible at least once: 3422 (320 of them inactive).
- Exit kinds: {'open': 3686807, 'delist': 98, 'merger': 98}
- AAPL split check: {'from': '2020-08-28', 'to': '2020-08-31', 'jump': 0.033891515102956404}
- Earnings flags: 2704 nights, 266349 name-nights. Truncated news days: 0.

In-sample is not part of the verdict. Variant 2 and the 0.05% / 0.20% columns are not part of it either.
