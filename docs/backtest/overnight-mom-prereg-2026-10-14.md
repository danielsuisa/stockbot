# OVERNIGHT-MOM pre-registration — 2026-10-14

Locked before any download and before any return is computed. The executable copy is `bot/overnight_mom.py`. Thresholds are not changed after seeing results. This run does not place or transmit an order.

Source of the rules: `trading-research.md` section B (Lou, Polk, Skouras, JFE 2019), plus the go/no-go line in the task that ordered this test. Where the task is stricter than section B, both must pass.

## Data

- Alpaca SIP only (`feed=sip`). No IEX. The last session in the file is the last calendar session strictly before the run's New York date, so every bar is from a finished session (more than 15 minutes old).
- Keys: `ALPACA_KEY_ID` and `ALPACA_SECRET_KEY`. They were not in the agent environment. The download runs on GitHub Actions, which is how the breakout SIP replay on `cursor/breakout-pings-f857` read SIP.
- Window: entries on or after 2016-01-01. Bars are requested from 2015-10-01 so the 21-day signal and the 20-day dollar-volume average exist on the first entry day. Out-of-sample entries are on or after 2023-01-01. In-sample is reported and does not decide.
- Prices for returns: daily bars with `adjustment=split`, so a split does not look like an overnight crash. The traded return is the split-adjusted price ratio. Cash dividends are not added. The written signal is `ln(open / previous close)`, a price return, on the stocks and on SPY. Economic returns are therefore short by the dividend, on ex-dividend mornings, for every book including the benchmarks.
- Filters use raw bars (not split-adjusted). A later split must not push an old $8 stock through the $10 screen, and must not push an old $50 stock under it.

## Universe, each entry day

Common stocks on NYSE, NASDAQ, or AMEX (Alpaca's exchange code), active and inactive. Symbol matches `^[A-Z]{1,5}(\.[A-Z])?$`. The name must not match the fund / warrant / preferred / unit / note pattern in `bot/overnight_mom.py`.

Point-in-time, using only that session and earlier sessions:

- raw close strictly above $10
- mean of raw close × raw volume over the 20 sessions ending that day strictly above $20M, and a bar on each of those 20 sessions
- 21 overnight log-returns ending that day, each one `ln(split-adjusted open / split-adjusted previous close)`, previous meaning the previous calendar session, not the previous bar the stock happened to print. A missing bar breaks the chain and the name is ineligible that day.

The signal is the sum of those 21 log-returns. It includes the overnight gap into the entry day (known at the close) and does not include the next open.

## Portfolio

Top 5 by the signal. Ties: higher 20-day dollar volume, then symbol A–Z. Equal weight. Fewer than 5 eligible names: hold all of them, fully invested. None: cash, return 0, no cost.

Entry is the SIP daily close. Alpaca's daily close is the last regular-session trade that is allowed to set the close. Extended-hours condition `T` does not set the daily open or close. There is no separate opening or closing auction feed; the daily open and close are the auction proxies.

Variant 1 (the gate): exit at the next session's split-adjusted open.

Variant 2 (reported, not the gate): exit at the `vw` of the SIP 15-minute bar that starts at 09:30 New York (09:30–09:45), `adjustment=split`. Variant 2 does not have the random-draw test: that test would need the opening VWAP for every eligible name. The same t, year, and SPY comparisons are still reported for variant 2.

If the next session has no print, use the next print within 5 sessions (a halt). If there is still no print, use a cash-merger `rate`, or the cash part of a stock-and-cash merger (`cash_rate` only; the stock part is ignored). A worthless removal, or a name that never prints again, is a −100% price return. A missing print inside the last 5 sessions of the sample is dropped, not marked −100%, because the file has ended. Stock-only mergers with no print are −100%. That is harsh on purpose.

## Earnings

Section B says to drop names with earnings that night when the data exists, and otherwise to show both.

A name is flagged when an Alpaca news item timestamped after 16:00 New York on the entry day and at or before 09:30 New York on the exit session names that symbol and the headline matches `(?i)\bearnings\b|\bEPS\b|financial results|quarterly results|reports results`. The headline is a proxy for a scheduled release, not a claim that the text was known at 15:40.

The primary book is the earnings-excluded book only if AAPL has an Alpaca news item on or before 2016-01-01. Otherwise the feed does not cover the sample, the primary book is the unfiltered book, and a filtered book is still reported for the days the feed answered. A day with more than 40 news pages is treated as unfiltered. The choice uses the probe date only, not the returns.

## Costs

Round trip, subtracted from the night's mean stock return: 0.05%, 0.10%, 0.20%. No cost on a cash night. The gate uses 0.10%. A flat percentage cost does not change the horse-race against the random book; it does change the horse-race against gross SPY.

Reported, not gated: a $460 account, variant 1, primary book, whole shares, equity divided into 5 equal slots, commission $0.0035 a share with a $0.35 minimum each side, plus the 0.10% round trip. Share count uses the raw close. Leftover cash earns 0.

## Benchmarks and the gate

Random: 1,000 draws, seed 20261014. Draw `i` on a date uses `random.Random("20261014|{i}|{date}")` and samples 5 names, or every name if fewer than 5 are eligible, from that night's primary-book universe. Same exit as variant 1, same cost.

The 95th percentile is nearest-rank: sort the 1,000 mean night-returns, take index `ceil(0.95 × 1000) − 1`. The strategy must be strictly above that value. That is how "beats 1,000 random 5-stock draws" is scored, because section B says the 95th percentile.

SPY: split-adjusted close to next open, price return, no cost, same nights. A night missing SPY is dropped from this comparison only. The strategy's net return must be strictly above SPY's mean.

t is the ordinary one-sample t on the nightly net portfolio returns (sample standard deviation, N − 1). The observation is the night, not the stock. A Newey-West t with lag 5 is reported and is not part of the gate. If the standard deviation is 0, t is missing and the t check fails.

GO, variant 1, primary book, 0.10% round trip, only if every line is true:

1. Out-of-sample mean net return per night > 0 and t > 2.
2. That mean is strictly above the random-draw 95th percentile.
3. That mean is strictly above SPY's mean close-to-open on the same nights.
4. Every out-of-sample calendar year that has a night has mean net > 0. A year with no night fails.
5. At least 70% of calendar years in the full 2016–now sample have mean net > 0.
6. The file is complete: the AAPL 2020-08-31 split check passes (split-adjusted close-to-close across that split inside ±25%), SPY is present on at least 99% of entry nights, and no download failed.

Anything else, including a hole in the download, is NO-GO. Variant 2 and the other two costs do not flip the verdict. In-sample results do not flip it either.

## Survivorship

The stock list is Alpaca's asset file, `status=active` and `status=inactive`, so names Alpaca still knows after delisting stay in the universe. Eligibility is a bar on that day, not today's listing. A name that stops printing is not dropped from the average: a cash take-out is the cash rate, and a disappearance with no cash rate inside five sessions is −100%. Renames follow Alpaca `name_change` records: if the new symbol already has bars before the change, the old symbol is dropped (Alpaca stitches history onto the new symbol); otherwise the old symbol's bars end at the change.

What this does not fix, and the report must count: tickers Alpaca never stored, tickers that were reused, delistings with no inactive record, and stock-only mergers booked at −100%. The nine names from the earlier survivorship probe (LNKD, YHOO, WFM, MON, TWX, SHLD, SPLS, PNRA, CAB) are looked up and counted, not used as a tuning knob.
