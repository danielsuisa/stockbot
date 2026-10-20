# ADR 0016: T1 pre-registered design (intraday tickets, H1)

- **Status:** Accepted. The design in sections 1–7 was decided by the user on 2026-10-20 (T1 prompt,
  "Pre-registered design"). Sections 8–10 (data plan, budget, golden set) and the implementation details marked
  *(T1 detail)* are written by T1 under that design, before any request; any one of them can be overruled in T2's
  prompt before the T2 ledger entry.
- **Context:** [ADR 0015](0015-intraday-ticket-validation-m1.md); stockbot audit
  `docs/research/intraday-audit-2026-10-17.md` §4; `bot/intraday.py` at `origin/main`
  09bfa55c7445762cf021e6a3a1d8128527f07eba (2026-10-17 16:03 +03:00).

## 1. Frozen rules

- **Stage 1 (stocks in play) and stage 2 (setup detection) are copied from `bot/intraday.py` at the SHA above**, with
  their helpers `ticket.to5`, `ticket.vwap`, `ticket.atr14` and `levels.px`. Copied, never imported. The bot's test
  vectors (`tests/test_intraday.py`) are ported as fixtures. The SHA-256 of the copied rules text is written into
  every signed set.
- **Parameters, exactly as the bot:**
  - price $5–$500;
  - 20-session ADV ≥ 1M shares and ≥ $20M;
  - ATR14 ≥ 2% of the close and ≥ $0.50;
  - gap ≥ 2% either way, or pre-market volume ≥ 10% of ADV;
  - watchlist = the top 20 by pre-market relative volume (`WATCH`);
  - from the 09:55 scan, the watchlist is re-ranked by the opening 5-minute relative volume (14 sessions), keeping
    ≥ 1.0;
  - candles are 5 minutes, scanned every 5 minutes 09:40–11:30 (and 15:00–15:45 for parity only);
  - setups ORB 5/15, VWAP pullback (hammer, engulfing), bull flag, red-to-green, with the bot's thresholds
    (`CANDLE_RELVOL` 1.5, `VWAP_NEAR` 0.003, `IMPULSE_ATR` 0.5, `HAMMER_WICK` 2.0, `ENGULF` 1.0, `FLAG_PULLBACK` 0.5,
    `FLAG_MAX_BARS` 4);
  - per scan, one ticket per symbol (its best-scored setup), at most `MAX_TICKETS` = 5 per scan by score, and one
    ticket per symbol and setup per day (the bot's `sent` map).
- **Bracket, exactly as the bot (`bracket()`):**
  - trigger (entry stop) = setup trigger + $0.01;
  - limit = trigger + 5% of ATR14 (`LIMIT_ATR`);
  - R = trigger − stop reference + $0.01, floored at max(10% ATR14, 0.3% of the trigger) and capped at
    max(1 × ATR14, floor);
  - ORB 5 uses exactly the floor;
  - stop = trigger − R; target = trigger + 2R; time exit 15:55 ET.
- **Selection is independent of the account** *(T1 detail)*. The bot's `ok` test (≥ 2 shares under its own pinned
  equity) is replaced by "R > 0" in study mode. Parity mode (section 6) uses the bot's own sizing test.
- **Universe** *(T1 detail)*.
  - The bot's `universe()` filters Alpaca assets on exchange (NYSE, NASDAQ, ARCA), alphabetic symbols of ≤ 5
    letters, and no fund words in the name.
  - The replay applies the same filter to the store's asset snapshot of 2026-10-17, active and inactive. It is the
    only asset list held.
  - Keeping inactive names avoids survivorship. The metadata (exchange, name) is post-dated, and it only excludes
    non-common-stock names; prices and volumes are point-in-time.

## 2. Primary hypothesis H1

- **The entry.** A long entry one tick above:
  - a VWAP-pullback reversal candle (`vwap_hammer`, `vwap_engulf`), or
  - a bull-flag high (`flag`).
- **The window.** The ticket is issued by a scan from 09:40 to 11:30 ET.
- **The rest.** The stop, target, time exit, stop-limit offset and validity are as in section 1.
- **The H1 set** is the set of tickets the bot's selection would issue (section 1), restricted to those setups and
  that window.
  - Because the bot ranks ORB above VWAP within a scan, an ORB ticket on the same symbol and candle replaces an H1
    ticket. This is the bot's behaviour, and it is kept.

## 3. Feeds and clock

- **Signals use the live bot's feed for each input** (verified at the SHA; T1 report, Stage 0 table):
  - daily bars: SIP, raw;
  - pre-market volume: SIP;
  - the opening print: IEX;
  - opening relative volume: SIP;
  - candles and VWAP: IEX.
- **Fills, costs and exits use SIP** minute bars and the SIP NBBO grid.
- **SIP-signal variant (reported only):** stage 2 on SIP minute bars, stage 1 unchanged.
- **Clock:**
  - **Stage 1** is decided at 09:38 ET from the following inputs:
    - pre-market volume, the SIP minutes 04:00–09:29, as the bot's 15-minute bars 04:00–09:15 return them;
    - the open, from the first IEX minute in 09:30–09:34;
    - otherwise, the last SIP pre-market price, which is the bot's fallback.
  - **The re-rank** is decided at the 09:55 scan, from the SIP minutes 09:30–09:34 against the same 5-minute window
    of the 14 prior sessions.
  - **Stage 2.** A scan at candle close C uses the IEX minutes before C. The live bot scans at C + 25 s.
  - **Ticket.** A ticket is issued at C. Its order is live from C + 60 s, so the first live minute is C + 1 min.
  - **Validity.** A DAY order: it can fill in minutes before the time exit.
- **Time exit** *(T1 detail)*: 15:55 ET, or close − 5 minutes on early-close sessions. The exit price is the close
  of the last SIP minute before it.

## 4. Fills, exits, costs, sizing

- **Entry.**
  - **Normal trigger.** On the first live minute whose high ≥ trigger:
    - if its open ≥ trigger and ≤ limit, the reference price is the open;
    - if its open < trigger, the reference price is the trigger;
    - if its open > limit, the stop triggers and the order rests as a limit (below).
  - **The trigger traded before the order went live** (the high of the minute C ≥ trigger). The stop-limit triggers
    on the first live minute:
    - if its open ≤ limit, the reference price is the open;
    - otherwise the order rests as a limit.
  - **A resting limit** *(T1 detail)* is checked from the next minute until the time exit. It fills on the first
    minute whose low ≤ limit, at min(open, limit).
  - **The fill price** is the reference price + ½ spread.
- **Exits:**
  - **stop:** the first minute with low ≤ stop. The exit price is min(stop, open) − ½ spread − $0.01. A stop counts
    in the fill minute *(T1 detail)*.
  - **target:** only on a trade-through (high > target), at the target, with no spread cost. It never counts in the
    fill minute *(T1 detail)*.
  - **same-minute stop and target:** the stop wins.
  - **time exit:** the close at 15:55 − ½ spread.
- **Spreads** *(T1 detail)*:
  - the spread is the NBBO grid sample's ask − bid in dollars;
  - the entry and the stop use the sample at the signal (or control) candle close;
  - the time exit uses the 15:55 sample;
  - a missing or crossed sample is replaced by the nearest valid sample of the same symbol and session;
  - if none is valid, the ticket is skipped as "no quote" and counted.
- **Costs:** IBKR Pro Tiered, from `scalp_eval/costs.order_cost` on each side.
- **Liquidity:** an order larger than 1% of its fill minute's SIP volume is rejected and counted.
- **Sizing:**
  - shares = ⌊min($25 / (trigger − stop), $2,500 / trigger)⌋;
  - 0 shares means the ticket is skipped and counted;
  - net R = net $P&L / (shares × (trigger − stop));
  - the bot's 20%-of-equity cap (⌊min($25 / R, $500 / trigger)⌋) is reported only.

## 5. Controls

- **K = 3 per H1 ticket**, on the same stock and day.
- Each control is entered at a 5-minute candle close drawn uniformly, without replacement, from the scan closes
  09:40–11:30.
  - A close excludes itself if any setup was detected on that symbol at it (a signal candle).
  - Fewer than 3 eligible closes gives fewer controls, and the shortfall is counted.
- **Seed:** int(SHA-256(f"{ticket_id}|t1-control-v1"), 16). Here
  ticket_id = f"{date}|{symbol}|{setup}|{scan close}".
- **Identical geometry, fills and costs** *(T1 detail)*:
  - the control's trigger is its candle's high + $0.01;
  - its R, target multiple and limit offset are the parent's (stop = trigger − parent R; target = trigger + 2 ×
    parent R; limit = trigger + 5% ATR14);
  - the clock, fills, costs, liquidity and sizing are as in sections 3–4.

## 6. Windows, gate, reported-only items

- **Development:** 2022-02-01 → 2024-12-31 (733 sessions).
- **Confirmation:** 2025-01-02 → **2026-10-16** (449 sessions), the last complete session in the store when this ADR
  is committed. It runs once.
- **Top-up sessions** (2026-10-19 → last complete, ≤ 2026-10-30) are for parity only and belong to neither window.
- **Gate, in T2, on the confirmation window:**
  - **(i)** mean net R per filled H1 ticket > 0, with t ≥ 2.0 clustered by trading day (CR1);
  - **(ii)** mean over filled H1 tickets of (ticket net R − mean net R of its filled controls) > 0.
    - A ticket none of whose controls filled is left out of (ii) and counted *(T1 detail)*.
- **Reported only:**
  - development statistics;
  - per-setup results (VWAP hammer, VWAP engulfing, flag);
  - the 20%-cap sizing;
  - the SIP-signal variant;
  - the sign per year;
  - results without the top 1% of trades;
  - a day-block bootstrap CI (10,000 draws, seed tag `t2-dayboot-v1`).
- **ORB 5 pipeline check** *(T1 detail)*:
  - the bot's `orb5` tickets from 09:40–11:30, through the same fills and costs;
  - the reference is the ZBA program's development figures on the same universe class: base K10s +0.135R, and the
    breakout-on-volume mechanism C15 −0.108R (`docs/backtest/ticket-zba-v2-2026-10-09.md`);
  - a development mean outside [−0.36R, +0.39R] (those values ± 0.25R) is treated as a pipeline defect to
    investigate before confirmation.
- **Book (reported only):**
  - one $2,500 cash account, one full-size position at a time, T+1 settlement (a sale's proceeds are usable the next
    session), compared with SPY over the same dates;
  - margin and PDT are noted only. Whether IBKR has phased in the 2026 FINRA intraday-margin rule is unverified.
- **Parity mode** *(T1 detail)*. The selection uses the bot's sizing test: shares ≥ 2 under the bot's equity E and
  risk share p, inferred from the journaled shares. It is used only to match the live journal.

## 7. Power and trials

- **Power:** SD = 1.47R (audit §4, power gate). The expected confirmation MDE = 2.8416 · 1.47 / √n.
  - n = the confirmation H1 ticket count (an upper bound on fills), reported by T1 from counts only.
  - At the audit's planning rate (~750 a year, 1.8 years, ~1,340 tickets), the MDE is ≈ 0.114R.
- **Trials ledger:** `scalp_contracts/t_trials.jsonl` (append-only).
  - Development reruns only for documented defects, each with a failing test first and a ledger line.
  - One confirmation run.
  - The evaluator CLI refuses real sets until a line with `"phase": "T2", "kind": "authorize"` exists. T1 writes
    none.

## 8. Data plan *(T1 detail)*

- **Rules for every request:** append-only, with a manifest; holdout-checked before the network; resumable.
- Nothing is requested before 2022-01-03 or on or after 2026-11-02.
- Fetch lists come only from data dated ≤ the decision time.
- No key is fetched twice: (symbol, minute, feed) and (symbol, sample time) already held are skipped.

| Step | Table / part | What |
|---|---|---|
| 1 Top-up | `raw_bars_1d`, `corporate_actions`, `calendar` | 2026-10-19 → the last complete session (≤ 2026-10-30), then the I1 duplicate check |
| 2 Stage 1 inputs | `raw_bars_1m` part `t1s1`, SIP and IEX | 04:00–09:55 ET, every session, for every symbol passing the daily prefilter (price, ADV, ATR from bars ≤ t − 1) and the universe filter, except symbol-days held by P1 |
| 3 Denominators | `raw_bars_1m` part `t1den`, SIP | 09:30–09:34 of the 14 sessions before t, for each session's top-20 watchlist, where not already held |
| 4 Top 20 | `raw_bars_1m` part `t1top`, SIP and IEX | 09:56–20:00 for each session's stage-1 top 20 (the pre-re-rank watchlist, which the 09:40–09:50 scans use), except symbol-days held by P1 |
| 5 NBBO grid | `raw_quote_samples` rule `t1_grid` | SIP NBBO (look-backs 5 s, then 60 s, as `sample_quotes`) for the top 20 at every 5-minute close 09:40–11:30 and at the time exit |

## 9. Budget *(T1 detail, from counts only)*

- **Sessions:** 1,182 in the windows, plus the top-up.
- **Prefilter symbol-days:** about 854,000, measured on the store's daily bars, ≈ 720 per session.
- **Requests:**
  - step 2, about 19,000 (≈ 8 chunks of 100 symbols × 2 feeds × 1,182);
  - step 3, ≤ 3,000;
  - step 4, about 2,400;
  - step 5, about 28,400 grid points × 1–3 pages.
- **Time:** 9–19 h at ≤ 170 requests/min, below the 24 h ceiling.
- **Disk:** ≈ 1.9 GB at the measured 19 bytes per minute-bar row (≈ 70 rows per symbol-day for SIP 04:00–09:55,
  ≈ 17 for IEX). 11 GB is free, which leaves ≈ 9.1 GB, above the 8 GB floor.
- **Stop rules.**
  - Every partition write checks the free space, and the run stops below 8.2 GB.
  - After 2% of each step, the projected total time is logged. If it exceeds 24 h, the run stops and the T1 report
    proposes a reduced plan.
  - The universe is never shrunk by T1.

## 10. Golden set (evaluator, synthetic, ≥ 16 cases)

1. A trigger after the order is live.
2. A trigger traded before live, filled at the open.
3. The same, left resting, then filled.
4. The same, expiring at the time exit.
5. A stop in the fill minute.
6. A same-minute stop and target.
7. A target touched but not traded through.
8. A gap through the stop.
9. The 15:55 exit.
10. A liquidity rejection.
11. A 0-share skip.
12. The full-equity cap binding.
13. The 20%-cap variant.
14. T+1 blocking a second trade.
15. The control draw excluding signal candles.
16. A commission minimum vs a per-share charge.
