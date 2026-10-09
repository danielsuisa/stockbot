# Forward shadow tracker of ZBA K10s — design

Date: 2026-10-09 · Status: approved by the owner ("תתחיל הכל מאושר") · Branch: `shadow-tracker` (on top of
`ticket-zba`, PR #7) · Background: `docs/superpowers/specs/2026-10-08-ticket-backtest-design.md` sections 11–13,
`docs/backtest/ticket-zba-2026-10-09.md`

## 1. Purpose

K10s (the ZBA "Stocks in Play" 5-minute ORB with stop 0.10 × ATR14 and a spread gate of 0.25 × the stop distance)
passed the optimistic verdict and failed the pre-registered realism check (2024 below zero). Re-mining 2024–2026
would only overfit. New evidence comes from sessions nobody has seen: every trading day the bot computes, with the
same code and the same SIP data, the trades K10s would have made, and posts them with a running tally. No order is
sent, no money is used, and every message says so. Real tickets (stage 2) come only if section 6 passes.

## 2. What runs, and when

- `bot/shadow.py`, started by the listener's clock (`bot/clock.py`) at **07:00 UTC, Tuesday–Saturday** through
  `.github/workflows/shadow.yml` (`mode=slot`), and by hand with `workflow_dispatch` (`mode=manual`).
- It processes completed sessions only (New York date before today): the free Alpaca plan refuses SIP data newer than
  15 minutes, and a morning run in Israel does not ring at night.
- Days to process: the sessions after the last processed one, at most 5 (catch-up after a stopped listener); on the
  very first run only the latest completed session (older sessions belong to the backtest, already seen).
- A processed day is recorded in `data/shadow_journal.json` before the next one starts; a day is never sent twice.

## 3. One session D — exactly the backtest's code

1. Calendar and the session before D from `alpaca.calendar`, loaded far enough back that D always has 15 sessions
   before it (a session without them is an error, never a short window).
2. Universe: `ticket_backtest.asset_universe` on all of Alpaca's assets, active and inactive, as in the backtest (a
   name whose last trading day was D can already be inactive the next morning).
3. `ticket_backtest.prefilter(D, daily, prev)` on raw daily bars of the 60 calendar days up to D (D is complete;
   `prefilter` and the Rule 201 check use only the bars before D).
   (Section 3 revised after review on 2026-10-09, before any shadow data.)
4. `alpaca.opening_bars` for the prefiltered tickers on D and on the 14 sessions before it, then
   `ticket_backtest.stocks_in_play` → the top 20.
5. Per ticker: `ticket_backtest._play("ZBA", "REGULAR", …)` with ctx `stop_atr` 0.10, `spread_d` 0.25 (the K10s row of
   `ZBA_GRID`), the spread from `ticket_backtest._spread`.
6. Filled trades go through `ticket_backtest._realism` (section 13: the fill minute on SIP trades, Rule 201 shorts
   dropped). Its result is the shadow trade. The optimistic bar-model result is kept in the journal for comparison.

## 4. Telegram message (Hebrew, RTL-clean, the bot's fixed lines added by `common.send`)

- Header: the session date, "shadow tracking, no order was sent".
- Per trade: side (buy / short), ticker, stop-order level and trigger time, fill, stop loss, shares at $100 risk,
  exit (stop or close) and net R.
- Counted but not traded: Rule 201 blocks, no trigger trade at the level, NO TICKET reasons.
- Running tally since the first day: trades, mean net R, total R, win rate, t; marked "small sample" under 100
  trades; next to it the backtest's realistic figure (+0.277R a trade on 2024-02 onward, 2024 negative).
- Last line: the method has not passed a pre-registered test, so this is not an order ticket.
- Nothing to send on a slot run with no new session → silence; a manual run says there is no new session.

## 5. Journal `data/shadow_journal.json`

`{"v": 1, "since": first session, "days": {D: {"top": [tickers], "reasons": {reason: n}, "dropped": {status: n},
"trades": [realistic trade rows], "optimistic": [bar-model rows]}}}`. Committed by the workflow (retry on push race,
like the squeeze workflow).

## 6. Forward verdict (pre-registered now, before any shadow data)

Judged once the journal holds **≥ 100 realistic trades and ≥ 60 sessions** (about 3 months): GO for stage 2 (text
tickets) iff mean net R > 0 with t ≥ 2 **and** total net R > 0 in each calendar month with at least 15 trades.
Otherwise NO-GO for the ZBA line. Nothing in sections 3–4 is changed while the tracker runs; a code fix that changes
results restarts the count from the fix.

## 7. Errors

Missing Alpaca keys or an Alpaca outage → the run fails, the workflow's alarm reaches Telegram, nothing is recorded,
and the next run retries the same session. A ticker without minute bars counts as a gap in the message.

## 8. Tests (offline, network mocked)

One mocked session end to end (a long trade through the realism model, a short dropped by Rule 201); day selection
(first run, catch-up, cap of 5, idempotence); message RTL-clean with trades, without trades, and the tally; the clock
starts `shadow.yml` at 07:00 UTC Tuesday–Saturday; the workflow accepts the clock's mode.

## 9. Live view (`/shadow`, added 2026-10-09 at the owner's request)

`/shadow` (a manual run) first journals any completed session as above, then shows today as it unfolds, data 15
minutes behind (the free plan's limit) and **never journaled**, so sections 5–6 are unaffected:

- before 5 minutes after the open: the stocks that pass section 3's prefilter, ranked by their volume so far today
  (from 04:00 New York) against a normal day. This is a watch list, not the method's choice;
- from then on: section 3 on today's data so far; a trade still running is shown as open with its result so far.
