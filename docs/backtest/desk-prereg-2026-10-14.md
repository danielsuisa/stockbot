# Desk pre-registration — 2026-10-14

Locked before either replay. The rules below are the constants and docstrings in `bot/desk_gate.py`, `bot/penny_play.py`, `bot/penny_play_backtest.py`, `bot/swing_pullback.py`, and `bot/swing_pullback_backtest.py`. Nothing here places or transmits an order. Neither test changes the listener.

Cash account, $460, long only, whole shares. One full-size trade at a time. A sale is not reused until the next session.

In-sample is 2016-01-01 through 2022-12-31. Out-of-sample is 2023-01-01 through the last session before the run's New York date. If the tape starts later than 2016, the report says when the first bar is, and the out-of-sample date does not move. Only out-of-sample trades are gated. In-sample numbers are not a reason to change a threshold.

GO, all required, else NO-GO: at least 100 trades; mean net R above 0 with t above 2; mean above the 95th percentile of 20 random-direction means; mean net R above 0 in 2023 and in 2024 and in 2025 and in 2026. A year with no trades fails. An incomplete download is NO-GO. Each variant is gated on its own.

## T1 — penny stocks in play

Prior close at least $0.50 and under $5. Gap at least 10%, measured as the 09:30 five-minute open against that prior close. Relative volume at least 5, measured as that five-minute bar's volume against the median of the prior 20 sessions' opening bars, with at least 10 of those bars present. Full-day volume is not the screen.

Universe: Alpaca active and inactive, NYSE, NASDAQ, and AMEX, one to five letters, fund-like names excluded. OTC is out. Inactive names with bars stay in. Names Alpaca never listed are missing; this is not a CRSP tape. Prices are raw.

Variant A, opening-range breakout: the first one-minute bar after 09:34 that trades above the 09:30–09:34 high. Fill at that bar's open if it opens through the high, otherwise at the high. Stop one cent under the opening-range low.

Variant B, VWAP pullback: from 09:35, the first close under session VWAP (from 09:30), then the first later close back above it. Fill at the next bar's open. Stop one cent under the pullback's lowest low.

Both: target 2R, or the 15:55 close, or the minute before an early close when that is earlier. A bar that reaches the stop and the target exits at the stop. One name a day, the largest gap. The two variants are separate $460 books.

Costs: $0.0035 a share, $0.35 minimum, both sides. Half the SIP spread at the fill minute on each side when the quote is usable, otherwise a 1% round-trip haircut. The gate is the $460 book. The same trades with the commission removed are reported and are not gated.

Catalyst: a headline in the 24 hours ending 09:35 New York, only if the earliest AAPL headline Alpaca has is on or before 2016-01-01. If it is not, both books run without a catalyst and the report says so.

## T2 — swing pullback

Close above $5, prior 20-session dollar ADV above $20 million, close above a 50-day average that is higher than it was 10 sessions earlier. The low touches the 20-day or the 50-day average (at or below it, or within 0.5% above it) and the close finishes back above that average. The close is in the top third of the range and above the prior close.

Enter the next session's open. Stop one cent under the signal day's low. Target the highest high of the prior 20 sessions. No trade when that high is not above the entry or the open is not above $5. Hold at most 15 sessions from the entry. A halt still uses one session. A missing remainder exits at the last close. One position. The higher ADV wins when two names want the same day. The next entry is the session after the exit.

No Alpaca earnings calendar. Two books, each gated: no earnings filter, and a proxy that skips a planned hold overlapping the ten calendar days from the second Monday of January, April, July, or October. No daily entry quote, so the cost is the 1% round-trip haircut plus the commission. Universe: the same listed NYSE and NASDAQ names as the other research books, active and inactive.

## Run order

The overnight-momentum replay (Actions run 38603419083, branch `cursor/overnight-mom-ac8f`) has to finish before this workflow downloads anything. The workflow file stays on `workflow_dispatch` until then.
