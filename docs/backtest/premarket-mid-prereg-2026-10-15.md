# Premarket catalyst, $3–$20 — locked 2026-10-15

Locked before any Alpaca or SEC request for this book. This is a parallel variant of the sub-$5 premarket test. It does not replace that test, and it does not change that test's band. Nothing here places or transmits an order. A GO is a paper result. It is not a merge and it is not permission to trade. Thresholds below stay as written.

Cash account, $460, long only, whole shares, one name a day. The exit is the same session, so the next session may take a new $460 trade. The book does not compound. In-sample is 2016-01-01 through 2022-12-31 and is not gated. Out-of-sample is 2023-01-01 through the last Alpaca session strictly before the run's New York date. An incomplete download is NO-GO. A finished NO-GO exits 0.

The sub-$5 book stays `0.50 <= prior close < 5`. Its report is `docs/backtest/premarket-catalyst-2026-10-15`. This book is a different report.

## What stays the same

Universe, split-adjusted prices, the AAPL 2020-08-31 ±25% check, and skipping a session that is a forward-split or reverse-split ex-date. Premarket is 04:00 through 09:29, measured on the 5-minute bars from 04:00 through 09:25. Relative volume is at least 5 times the median of the prior 20 sessions, with at least 10 of those 20 printing. The headline is in the strict 24 hours ending 09:30 New York, and Alpaca's `source` must contain businesswire, prnewswire, cision, globenewswire, accesswire, accessnewswire, or newsfile. There is no keyword list. Blocking forms in the 30 calendar days ending 09:30 are S-1, S-1/A, anything starting with 424B, and S-3, S-3/A, S-3ASR, S-3ASR/A. Shares outstanding under 20 million stand in for float, with the same 100,000 floor and 400-day age. This is not float.

The entry is the first 1-minute close strictly above the premarket high, from 09:35 through 10:30. The fill is that close. The stop is one cent under the opening-range low. The path starts on the next bar. The target is 2R. A bar that reaches both exits at the stop. Whatever is open at 15:55, or at the minute before an early close when that is earlier, exits there. One name a day: the highest premarket relative volume, then the symbol. The same-day book does not compound.

The gate is the $460 whole-share out-of-sample book, judged by `desk_gate.judge`: at least 100 trades, mean net R above 0 with t above 2, mean above the 95th percentile of 20 random-direction means, and mean net R above 0 in 2023 and in 2024 and in 2025 and in 2026. A year with no trades fails. The desk-sized book is reported beside the gate and cannot flip the verdict.

WARM, START, and OOS stay 2015-06-01, 2016-01-01, and 2023-01-01. The survivorship probe is the same nine names. The known gaps are the same holes, plus the sentence that this book is $3 through $20.

## The price band

The prior session's split-adjusted close is at least $3 and at most $20. Both ends are inside. $2.99 and $20.01 are out. A later split can move an old day into or out of the band. That lookahead is part of the lock.

`price_ok` in `bot/premarket_catalyst.py` is still the sub-$5 test. This book passes `mid_price_ok` and does not edit that function.

## Costs

The cost follows the entry print, not the prior close.

A print strictly under $5 uses the sub-$5 model. When a SIP quote exists at the entry minute and at the exit minute, the entry fills at the ask and the exit fills at the bid. Both quotes are scaled by that day's split-adjusted close divided by the raw close. If either quote is missing, the entry print is raised by 1% and the exit print is lowered by 1%. That is 1% on each side, not a 1% round trip. The share count is the whole shares of $460 at the price paid. The gate uses that 1% fallback. The same prints at 0.5% and at 2% per side are reported beside the gate and do not flip the verdict.

A print at $5 or above uses the locked $5-and-above model. The entry minute's quoted bid-ask is charged once, as the full spread. The exit quote is not a second spread. A missing entry quote uses the 1% round-trip haircut: half of 1% on the way in and half on the way out. It does not use 1% on each side. The share count is the whole shares of $460 at the printed entry. Those names repeat the same net R in the 0.5% and 2% columns. `score` in the sub-$5 module is unchanged and still uses the cheap model for every price. This book calls `score_priced`.

Commission stays $0.0035 a share with a $0.35 minimum, both sides. Net R divides the dollar result by the printed entry-to-stop risk. The same trades with the commission removed are reported and are not gated.

## Desk size

Reported beside the gate. Size is the smaller of 1% of $460 at the stop and 2% of $460 at the worst case. Worst case per share is the larger of the stop distance and the LULD dollars. This book forces the LULD fraction to 10% for every name, including a print of exactly $3.00. The generic lookup is still 20% at $3 and 10% only above $3. That lookup is not edited. A position under $50 is skipped. The size may be fractional. The share of recorded signals that survive is the figure in the report.

When the 10% band binds, the position is `0.02 * 460 / 0.10 = $92`. When the stop is wider than 10% of the entry, the 1% leg binds and the position is `$4.60 / stop_pct`. That is under $50 once `stop_pct` is above 9.2%.

## Halts, the blacklist, and tax

A likely halt is a gap of at least 5 minutes between 1-minute bar stamps from 09:30 through the flat minute. The fill for that count is the open of the first bar after the gap. The report compares that loss with the planned stop. A stop or a target on an earlier bar means the halt did not affect the trade. A gap that never resumes by the flat minute is not counted. The halt does not change the gated exit.

A trade is excluded from the beside-book when a reverse-split ex-date falls in the 365 calendar days strictly before the trade date. The trade date itself does not count. Passers that did not trade are not joined. The blacklist does not change the gate.

An at-the-market flag beyond the locked S-3 / S-3ASR form screen, and a going-concern flag, need the EDGAR full-text search. They are marked not tested. The submissions screen is unchanged.

Israeli capital-gains tax is 25% of a calendar year's net realized gain. A realized loss offsets a realized gain in the same year. There is no carryforward and no refund. The tax is subtracted once from the terminal sum of trade profits. It does not compound. Pre-tax Sharpe is not this book's gate; the four checks above are. A book that never bought has no tax. Buy-and-hold SPY is taxed once at the end only when this download has that comparison. This download does not add a SPY book. The desk count is not a filled book and has no tax of its own.

## Run

`python -m bot.premarket_mid_backtest`. Daily bars, corporate actions, premarket bars, minute bars, and SEC files may reuse the earlier T3 cache. Headlines for this book are stored under `news-mid`, because the sub-$5 news file holds only the symbols that book asked for. The workflow starts only after this file is committed. The report commit removes the pull-request trigger so a later push does not download again.

Do not retune this band, the wires, the relative-volume floor, the forms, the shares-outstanding proxy, the entry window, the stop, the cost split at $5, the forced 10% band, or the gate after seeing the numbers.

## Queue amendment, 2026-10-15

Commission is IBKR Pro Fixed on each order: $0.005 a share, minimum $1.00, maximum 1% of that order's trade value. The $0.0035 / $0.35 sentence above is replaced for this book. The spread rules are unchanged. The same sample-size gate as the sub-$5 book applies: n from in-sample per-trade returns, and an out-of-sample count below n is UNKNOWN (insufficient sample), not NO-GO. The after-tax line stays.
