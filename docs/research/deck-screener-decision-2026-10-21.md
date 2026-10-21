# Decision: the `/intraday` screener follows the user's presentation (2026-10-21)

- **Status:** decided by the user on 2026-10-21 (phase D1, milestone M3: advisor delivery).
- **Supersedes:** the change-control note of
  [intraday-audit-2026-10-17.md](intraday-audit-2026-10-17.md), which asked for a pre-registered section that
  passes the power gate before the screener's logic changes. That gate still governs any measurement. It no longer
  governs the content of the live screener.

## What the user decided

1. **Deliver now, before any M1 pass.** The live `/intraday` screener issues tickets from the user's own rules (the
   presentation "נרות יפניים ופקודות Bracket"), labelled as such. The engine stays paused (ADR 0021). Nothing in this
   change measures anything, and no statistic was computed for it.
2. **Setups:**
   - reversal candles on a level: Hammer, bullish Engulfing, and the VWAP pullback;
   - compression: the Inside bar (regular version) and NR7;
   - a breakout above a level, as two separate tickets: at the breakout, and on the Retest;
   - the Bull flag.

   Wyckoff is a tag on the card only. ORB 5, ORB 15 and red-to-green are removed, because the presentation does not
   have them.
3. **Universe:** the bot's stocks-in-play list:
   - from 08:00 ET, refreshed every 5 minutes from the pre-market gap and relative volume available at that moment;
   - re-ranked at 09:55 by the opening 5-minute relative volume.
4. **Hours:**
   - new tickets from 08:00 to 10:30 ET;
   - the time exit at 15:55, or 5 minutes before an early close;
   - VWAP from 09:30. Before then the VWAP setups are off, and every other setup runs.
5. **Checklist:** all 8 items of slide c7-checklist must pass for every setup. Item 6 means an estimated round-trip
   cost of at most 0.3R: IBKR Pro Tiered fees plus the full spread.
6. **Labels:**
   - VWAP pullback and Bull flag tickets carry "נבדק: תוחלת שלילית (T2)";
   - every other ticket carries "מחוץ למסלול שנבדק".
7. **Recommendations only.** The bot never sends an order. The user places each bracket in IBKR.

## Where it is written

- The rules, with each one's slide, are in [deck-screener-spec.md](../deck-screener-spec.md). Each rule is marked as
  coming from the presentation, the bot, the user or a default. The spec also lists the defaults the user should
  confirm, and every difference between the slides and the orchestrator's summary of them.
- The code is in `bot/deck.py` (the rules) and `bot/intraday.py` (the screener). The retired setups are frozen in
  `bot/intraday_v1.py` for `bot/intraday_count.py`.

## What this decision does not change

- **The record of what was measured.** T2 found the VWAP pullback and the Bull flag negative after costs (ADR 0018);
  the cards say so. Every other setup in the deck is untested here.
- **The insider, squeeze and ticket modules, and every other workflow.**
- **The journal's schema.** Only new setup names are added.
