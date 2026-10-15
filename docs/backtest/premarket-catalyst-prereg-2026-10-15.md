# Premarket catalyst — locked 2026-10-15

Locked before any Alpaca or SEC request for this test. Nothing here places or transmits an order. A GO is a paper result. It is not a merge and it is not permission to trade. Thresholds below stay as written.

Cash account, $460, long only, whole shares, one name a day. The exit is the same session, so the next session may take a new $460 trade. The book does not compound. In-sample is 2016-01-01 through 2022-12-31 and is not gated. Out-of-sample is 2023-01-01 through the last Alpaca session strictly before the run's New York date. Each book is this one variant. An incomplete download is NO-GO.

## Universe

Alpaca's asset file, `status=active` and `status=inactive`, the same inclusion rule as the overnight-momentum test. NYSE, NASDAQ, or AMEX. Symbol matches `^[A-Z]{1,5}(\.[A-Z])?$`. The name must not match the fund / warrant / preferred / unit / note pattern in `bot/premarket_catalyst.py`. Inactive wins when both rows exist. A name is not dropped for being delisted.

## Prices

Daily bars and intraday bars use `adjustment=split`. The prior close, the premarket high, the premarket volume, the opening-range low, the entry, the stop, and the exit are all on that basis. A reverse split therefore does not appear as a gap, and it does not appear as a one-day volume explosion. The screen uses the previous session's split-adjusted close only when that session printed. A halt does not borrow an older close.

The $0.50–$5 band is in split-adjusted dollars. A later reverse split raises old prices and can remove a day from the band. A later forward split lowers old prices and can add a day. That lookahead is part of the lock. It is not repaired after the result.

A session is skipped when Alpaca records a `forward_split` or a `reverse_split` whose `ex_date` (or `process_date` if there is no `ex_date`) is that session. The AAPL 4-for-1 split on 2020-08-31 must leave the split-adjusted close-to-close move inside ±25%. If that check fails, or the corporate-action download fails, the file is incomplete and the verdict is NO-GO.

## Screen, all required

- Prior close at least $0.50 and under $5.
- Premarket volume from 04:00 through 09:29 is at least 5 times the median premarket volume of the prior 20 sessions. At least 10 of those 20 must have printed. The measure is the 5-minute bars starting 04:00 through 09:25. Their volume and high are the 1-minute volume and high of that window.
- A headline in the 24 hours ending 09:30 New York. The window is strict: Monday 09:30 looks back to Sunday 09:30, so a Friday headline is outside it. Alpaca's `source`, with spaces and punctuation removed, must contain one of: businesswire, prnewswire, cision, globenewswire, accesswire, accessnewswire, newsfile. Any other source, including a missing source, does not qualify. This is the primary-source catalyst. There is no keyword list.
- No blocking SEC filing accepted in the 30 calendar days ending 09:30 New York. Blocking forms are S-1, S-1/A, anything starting with 424B, and S-3, S-3/A, S-3ASR, S-3ASR/A. The S-3 forms are the at-the-market shelf. EDGAR has no form named ATM. The source is the submissions API (`data.sec.gov/submissions`, recent filings and every older file). The full-text search index is not used. A CIK whose older file cannot be read is excluded.
- Shares outstanding under 20 million. Alpaca has no float. The proxy is `dei:EntityCommonStockSharesOutstanding` from companyfacts, the latest fact whose `filed` date is strictly before the trade date, period end within 400 days, and value at least 100,000 (a smaller figure is treated as a scale error). This is not float.

CIKs come from `company_tickers.json`, then from the EDGAR company page for a symbol that file does not contain. No CIK means the name is excluded. Current tickers do not cover every delisted symbol. That exclusion is counted.

## The one entry

The first 1-minute close strictly above the premarket high. The fill is that close. The stop is one cent under the low of the opening 5-minute bar (the 09:30 bar). That low is known only once the 09:34 minute has closed, so a close before 09:35 cannot enter. A close after 10:30 cannot enter. The allowed minutes are 09:35 through 10:30. The path starts on the next bar. A bar that reaches the stop and the 2R target exits at the stop. Whatever is open at 15:55, or at the minute before an early close when that is earlier, exits there. No print through that minute exits at the last print.

One name a day: the highest premarket relative volume, then the symbol.

## Costs and the gate

Amended 2026-10-15 after the empty download. The screen is unchanged. Every name in this book is under $5, so every trade uses the amended spread.

When a SIP quote exists at the entry minute and at the exit minute, the entry fills at the ask and the exit fills at the bid. Both quotes are scaled by that day's split-adjusted close divided by the raw close. If either quote is missing, the entry print is raised by 1% and the exit print is lowered by 1%. That is 1% on each side. It is not a 1% round trip. The share count is the whole shares of $460 at the price paid. Net R divides the dollar result by the printed entry-to-stop risk. Commission is $0.0035 a share with a $0.35 minimum, both sides. The same trades with the commission removed are reported and are not gated.

The gate uses that 1% fallback: at least 100 out-of-sample trades, mean net R above 0 with t above 2, mean above the 95th percentile of 20 random-direction means, and mean net R above 0 in 2023 and in 2024 and in 2025 and in 2026. A year with no trades fails. The same prints at 0.5% and at 2% per side are reported beside the gate. They do not flip the verdict. When both quotes exist, the three rates are the same fill.

## Survivorship

The report counts inactive names in the asset file, inactive names with a daily bar, and trades in names Alpaca now marks inactive. It also counts the nine known 2016–2018 delistings (LNKD, YHOO, WFM, MON, TWX, SHLD, SPLS, PNRA, CAB): whether each is in the asset file and how many raw bars it has in the 40 days before the date used in the earlier probe.

Known gaps the report must repeat, with the counts this run can fill:

- Tickers Alpaca never stored are absent. The nine-name probe is only those nine names.
- A reused ticker is one symbol. The two companies are not split apart.
- A delisting with no inactive asset row is absent.
- A name with no CIK, no usable shares-outstanding fact, or an incomplete filing history is excluded. Shares outstanding are not float.
- An intraday halt or a delist after the entry exits at the last print. There is no CRSP delisting return.
- The $0.50–$5 band is split-adjusted, so a later split moves an old day into or out of the band.
- OTC names and symbols outside NYSE, NASDAQ, and AMEX are out.

## Run

`python -m bot.premarket_catalyst_backtest`. The workflow starts only after this file is committed. The report commit removes the pull-request trigger so a later push does not download again.

## Blacklist variant, added 2026-10-15

Reported beside the main book. It does not replace that book and it does not change the gate.

A trade is excluded when Alpaca records a `reverse_split` whose `ex_date` (or `process_date` if there is no `ex_date`) falls in the 365 calendar days strictly before the trade date. The trade date itself does not count. A split 366 calendar days earlier does not count. The report gives the share of recorded trades excluded, and the result with and without that exclusion.

The recorded book has no fills. The exclusion rate on that list is the rate that is reported. The relative-volume passers are not joined to a new corporate-action download.

An at-the-market flag beyond the locked S-3 / S-3ASR form screen, and a going-concern flag, need the EDGAR full-text search. They are marked not tested when that search is not run. The reason is stated in the report. The 30-day submissions screen is unchanged.

## Halts and the desk size, added 2026-10-15

Reported beside the main book. Neither one changes the gate, the entry, or the planned stop.

A likely halt is a gap of at least 5 minutes between 1-minute bars from 09:30 through the flat minute. The difference is measured on the bar stamps, so a bar 5 minutes after the previous one counts. The fill for this variant is the open of the first bar after the gap. The report compares that loss per share with the planned stop distance. A stop or a target on an earlier bar means the halt did not affect the trade. A gap that never resumes by the flat minute is not counted. The recorded book has no fills, so the minute bars are not downloaded again for this count.

The desk size is the smaller of 1% of the $460 account at the stop and 2% of the account at the worst case. Worst case per share is the larger of the stop distance and the LULD dollars. From $0.75 through $3 the band is 20%. Above $3 it is 10%. Under $0.75 the dollar band is the lesser of $0.15 and 75% of the price, and the fraction used in the size is that dollar band divided by the price. A price of $0.75 stays on the 20% band. A position under $50 is skipped. The report gives the share of recorded signals that survive. On this account the 20% band caps the position at $46, so a signal from $0.75 through $3 does not survive. Under $0.75 the dollar band keeps the position under $50 as well.

## Sub-$1 streak, added 2026-10-15

Reported beside the main book. It does not change the gate.

A trade is tagged when each of the 30 raw closes strictly before the entry day is under $1. The entry day is not in the count. A close of exactly $1.00 is not under $1. Fewer than 30 printed sessions is not the tag. The closes are the raw daily closes, not the split-adjusted series. The tag is a proxy for a Nasdaq closing-bid deficiency. The report splits mean net R and win rate by the tag, in-sample and out-of-sample. The recorded book has no fills, so the split on that list is empty.

## Tax line, added 2026-10-15

Reports written from this date on include an after-tax line. Israeli capital-gains tax is 25% of a calendar year's net realized gain. A realized loss offsets a realized gain in the same year. There is no carryforward and no refund. Buy-and-hold SPY is taxed once, at the end. The pre-tax gate is unchanged. A book that never bought has no tax, and the SPY end-tax comparison does not apply to it.

## Parallel book, added 2026-10-15

A separate book uses this rule on a prior close from $3 through $20, both ends inclusive. It is locked in `docs/backtest/premarket-mid-prereg-2026-10-15.md`. It does not change the $0.50–$5 band, and it is not this report.
