# The deck screener: spec (D1, 2026-10-21)

- **Source of truth.** The user's presentation "נרות יפניים ופקודות Bracket" (Slides artifact
  `Tc8VHK4HKcsPNhZwXp1pro`, version 1792553162-a473, 58 slides). Every rule below names its slide id.
- **Decision.** [deck-screener-decision-2026-10-21.md](research/deck-screener-decision-2026-10-21.md).
- **Code.** The rules are pure functions in `bot/deck.py`. The screener around them (watchlist, scans, cards, journal)
  is `bot/intraday.py`. The retired v1 setups are frozen in `bot/intraday_v1.py`, because `bot/intraday_count.py`
  replays them.
- **Rule set id: `ddcc3dde5bdf`.** It is a hash of every parameter in the table in section 9. Checklist item 8
  ("fixed rule") compares the running parameters with this id. Any change to a number therefore stops every ticket
  until the spec and the id are updated together.
- **Recommendations only.** The bot never sends an order. The user enters each bracket by hand in IBKR. No
  statistics, backtest or outcome was computed for this phase.

Source labels used below:
- **presentation**: the slide states the rule or the number.
- **bot**: the existing stockbot constant is reused, same name and value.
- **user**: a user decision of 2026-10-21.
- **default**: proposed by the orchestrator. The user confirmed all of them on 2026-10-22 (section 11).
- **IBKR**: IBKR's published schedule (section 7).

## 1. Scope and timing

| Rule | Slide | Source |
|---|---|---|
| 5-minute candles from the live IEX feed. Long only. Tick $0.01 | c1-time, c2-entry | presentation, user |
| The daily candle gives context: yesterday's high, low and close are levels | c1-time, c4-levels | presentation |
| New tickets 08:00–11:30 ET, scanned every 5 minutes, 25 seconds after each candle closes | c5-pullback ("until 11:30") | presentation, user (2026-10-22) |
| Time exit 15:55 ET, or the close − 5 minutes on early-close days (from the exchange calendar) | c7-a, c5-pullback | presentation, user |
| Validity, reversal setups (Hammer, Engulfing, VWAP pullback, Retest): until the close of the 5-minute candle after the signal candle; untriggered by then, the order is cancelled. The card prints the exact ET end | c2-hammer ("the next candle trades above the high; without confirmation, no trade"), c2-entry | presentation, user (2026-10-22) |
| Validity, the others (Inside bar, NR7, Breakout, Bull flag): until the window's end, 11:30 ET, then cancel | c7-a ("until 11:30, then cancel") | presentation, user (2026-10-22) |
| Checklist item 7 needs time left on the order: now < its validity end. So a window-class setup gets no ticket from the 11:30 scan; a reversal setup can, valid until 11:35 | c7-checklist item 7 | presentation |
| VWAP is the session's, from 09:30. Before 09:30 the VWAP setups are off and VWAP is not a level; every other setup runs | c5-vwap | presentation, user |
| A pre-market candle is usable for patterns only with trades in at least 3 of its 5 minutes. A 5-minute slot with no trades is a gap, and no pattern spans it | – | user (`PRE_MIN_TRADES` default) |

**Universe (user).** The bot's existing stage 1, unchanged except for when it runs:
- the prefilter: $5–$500, average volume of at least 1M shares and $20M, ATR14 of at least 2% and $0.50;
- in play: a gap of at least 2% or pre-market volume of at least 10% of an average day;
- the top 20 by pre-market relative volume.

When it is built:
- from 08:00, every scan until 09:35 rebuilds the list from SIP pre-market bars that are at least 15 minutes old;
- the gap is measured from the latest such price;
- the 09:35 build measures the gap from the IEX open print;
- at 09:55 the list is re-ranked by the opening 5-minute relative volume, as before.

## 2. Levels (c4-levels, c2-hammer, c5-vwap)

A level counts only if it was known before the pattern's first candle.

| Level | Side | Slide | Source |
|---|---|---|---|
| VWAP, from 09:30 | both | c5-vwap, c2-hammer | presentation |
| Yesterday's high, low and close | both | c4-levels, c1-time | presentation |
| Pre-market high and low so far | both | c4-levels | presentation |
| Round numbers: every $1 below $20, $5 from $20 to $100, $10 above $100 | both | c4-levels | default (`ROUND_STEPS`) |
| Repeated touches: at least 2 earlier candle highs (resistance) or lows (support) within `NEAR`, today and yesterday | high: resistance, low: support | c4-levels ("twice or more") | presentation (`TOUCHES`), default tolerance (`NEAR`, the bot's) |
| A previous low: today's earlier 5-minute swing lows (2 candles on each side, `ticket.swings`) | support | c2-hammer, c7-checklist item 2 ("שפל קודם") | presentation; the swing rule is the bot's |

**"On a level"** applies to support and resistance in the same way:
- **Support:** the candle's low is within `NEAR` (0.3%) of the level, or the candle's range contains the level.
- **Resistance:** the same test with the candle's high.

## 3. Context, checklist item 1 (c1-trend)

The item passes when either of these holds:
- **Uptrend:** the last two confirmed 5-minute swing highs are rising, and so are the last two swing lows. A swing
  has 2 candles on each side and is known only after those candles close. This is the bot's `ticket.swings`, run on
  today's usable candles.
- **End of decline,** measured just before the pattern's first candle. Either:
  - 3 candles in a row each made a lower low than the one before (4 candles are compared); or
  - over the 6 candles before the pattern, the highest high minus the pattern's low is at least 1 × ATR14(5m).

  ATR14(5m) is the mean true range of the last 14 usable 5-minute candles, yesterday's session included. These are
  defaults: `DECLINE_BARS`, `DECLINE_WINDOW`, `DECLINE_ATR`, `ATR5_N`.

## 4. Filters (c2-doji)

- **Shooting star on resistance.** If the latest candle is a Shooting star (the Hammer's mirror image, same
  ratios) and it sits on resistance, that symbol gets no ticket in that scan.
- **Doji.** A Doji is never an entry. If the latest candle is a Doji, checklist item 3 fails. A Doji is a candle whose
  body is at most `DOJI_BODY` (5%) of its range. This is a default: c2-doji says only "almost the same price".

## 5. Setups

Every ticket uses the same order:
- trigger = pattern high + $0.01;
- stop = pattern low − $0.01;
- target = 2 × R.

The stop distance R is then bounded by the bot's minimum and maximum (section 6).

| # | Setup (code) | Shape and placement | Trigger / stop | Slides | Sources |
|---|---|---|---|---|---|
| 1 | Hammer (`hammer`) | Lower wick ≥ 2 × body. Upper wick ≤ 0.5 × body. Body in the top third of the range. Must follow a decline and sit on support | above its high / below its low | c2-hammer, c2-entry, c2-a | presentation; upper wick: default (`UPPER_WICK`) |
| 2 | Bullish Engulfing (`engulf`) | Candle 1 is down. Candle 2 is up and its body covers candle 1's body (`ENGULF` 1.0). Must follow a decline; either candle on support | above candle 2's high / below the lower of the two lows | c2-engulf | presentation; `ENGULF`: bot |
| 3 | VWAP pullback (`vwap_hammer`, `vwap_engulf`), from 09:30 only | The 3 candles before the pattern closed above VWAP. Then a Hammer or Engulfing touches VWAP and closes above it | as 1 and 2 | c5-pullback, c7-q, c7-a | presentation; "3 candles": bot |
| 4 | Inside bar, regular version (`inside`) | The inside bar's high is below the mother bar's high, and its low is above the mother bar's low | above the mother's high / below the mother's low | c3-inside, c3-bracket, c3-q, c3-a | presentation |
| 5 | NR7 (`nr7`) | Its range is smaller than each of the 6 candles before it | above its high / below its low | c3-nr7 | presentation (`NR_N`). The stop is c2-entry's general rule |
| 6 | Breakout at a level (`breakout`) | A resistance level with compression under it: at least 3 of the last 5 highs within `NEAR` below the level, none above it, the last 3 lows rising, and the last close under the level | a resting buy stop at the level + $0.01 / below the lowest of the last 3 lows | c4-breakout, c4-entries, c4-fake | presentation; counts: default (`COMPRESS_*`) |
| 7 | Retest (`retest`) | A candle closed above a resistance level (the candle before it closed at or below it) on volume ≥ `CANDLE_RELVOL` × the mean candle volume earlier in the same part of the day. Within 6 candles, price came back to the level from above with no close under it, and a Hammer or Engulfing touched the level and closed above it | above that candle's high / below its low (the lower low of the two for an Engulfing) | c4-flip, c4-entries | presentation; `CANDLE_RELVOL`: bot; `RETEST_BARS`: default |
| 8 | Bull flag (`flag`) | **Pole:** 1–3 up candles rising ≥ 0.5 × ATR14, with mean volume ≥ `CANDLE_RELVOL` × the earlier mean. **Flag:** 2–4 candles, none above the pole's top, giving back less than half the pole, on lower mean volume, each body ≤ half the pole | above the flag's high / below the flag's low; target 2R (the pole-length target is not used) | c5-flag, c5-flag-order, c5-q, c5-a | presentation; pole and flag counts: bot; pole volume: default |

**Wyckoff (c6-\*)** is context only: it adds a tag to the card and never issues a ticket. It is computed from daily
bars, and every number is a default:
- **"Accumulation":** the last 20 sessions stay within a range of at most 15%, after a fall of at least 20% over the
  60 sessions before them (first close to last close).
- **"Spring":** one of the last 5 sessions traded below the low of the 20 sessions before it, then closed back above
  that low.

**One ticket per symbol per scan.** This is the bot's existing rule. When one candle fits several setups, the more
specific one wins, in this order (default):
- VWAP pullback;
- Retest;
- Breakout;
- Bull flag;
- Hammer;
- Engulfing;
- Inside bar;
- NR7.

The breakout ticket and the retest ticket are separate setups, each sent once per symbol per day. At most 5 tickets
go out per scan, ranked by relative volume (the bot's rule).

## 6. The bracket and sizing (c2-entry, c7-stoplimit, c7-size, c7-r)

- **Entry:** BUY STP LMT. Trigger = pattern trigger; limit = trigger + `LIMIT_ATR` × ATR14 (the bot's offset).
- **Stop distance R:** trigger − pattern stop, bounded by the bot's rule:
  - floored at max(10% ATR14, 0.3% of the price);
  - capped at 1 × ATR14.

  c5-pullback refers to this rule ("minimum and maximum distance by the stock's volatility"). The card says when R
  was widened or shortened.
- **Exits:** stop loss = trigger − R; target = trigger + 2R; time exit 15:55, or the close − 5 minutes.
- **Quantity:** ⌊$25 / R⌋, capped at ⌊$2,500 / limit⌋. A ticket needs at least 1 share. The cap uses the limit
  because that is the most the order can pay (c7-size: "the cash in the account").
- **Bracket:** entry, stop and target are sent together. The children are active only after the fill, and they are
  OCA.

## 7. Cost, checklist item 6 (c7-checklist, c7-winrate)

A ticket is sent only if its estimated round-trip cost is at most **0.3R**. The estimate assumes the buy, then the
sale at the target (the larger sale):

| Part | Value | Source |
|---|---|---|
| IBKR Pro Tiered commission, ≤ 300,000 shares a month | $0.0035 a share, min $0.35, max 1% of the trade value, on each leg | <https://www.interactivebrokers.com/en/pricing/commissions-stocks.php> |
| Exchange fee, removing liquidity, on both legs (default: both legs take liquidity) | $0.0030 a share (NYSE, Nasdaq) | <https://www.interactivebrokers.com/en/accounts/fees/NYSEstkfee.php?nhf=T>, <https://www.interactivebrokers.com/en/accounts/fees/INETstkfee.php?nhf=T> |
| Clearing (NSCC, DTC) | $0.00020 a share, each leg | commissions page above |
| CAT fee | $0.000003 a share, each leg (IBKR's figure) | commissions page above |
| Pass-through fees | NYSE 0.000175 × commission, plus FINRA 0.00056 × commission | commissions page above |
| SEC fee, on the sale | $20.60 per $1M sold (from 2026-04-04) | <https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2> |
| FINRA TAF, on the sale | $0.000195 a share, max $9.79. FINRA has set it to $0 from 2026-10-01 to 2026-12-31; the estimate keeps the normal rate (conservative) | <https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule>, <https://www.finra.org/rules-guidance/guidance/faqs/finra-billingcollections> |
| Spread | the full spread of the latest IEX quote × shares. A locked quote counts $0.01. No usable quote means no ticket | user |

The IEX quote is IEX's own best bid and offer, which is usually wider than the national best bid and offer, so the
estimate errs high. The free plan serves no live SIP quote.

## 8. Pre-market orders at IBKR (verified 2026-10-21)

| Fact | Source |
|---|---|
| "Allow order to be activated, triggered or filled outside of regular trading hours" (the "Fill outside RTH" / `outsideRTH` attribute) is set per order. Valid for US stocks on the major exchanges | <https://ibkrguides.com/traderworkstation/classic-order-ticket.htm>, <https://www.interactivebrokers.com/campus/glossary-terms/outside-rth/> |
| Without it, "stop orders will not trigger outside of regular trading hours" | <https://www.ibkrguides.com/traderworkstation/define-order-preset-value.htm> |
| STP LMT and LMT can work outside RTH. Plain STP, MKT and TRAIL on US stocks cannot. Both statements come from IBKR staff replies in 2025, not formal documentation | <https://www.interactivebrokers.com/campus/trading-lessons/trading-outside-regular-trading-hours-rth/>, <https://www.interactivebrokers.com/campus/trading-lessons/ibkr-desktop-stop-limit-orders/> |
| A stop submits a market order when triggered, and market orders are refused outside RTH. So a plain stop cannot protect a position before 09:30 | <https://www.interactivebrokers.com/en/trading/ordertypes.php> |
| In IBKR's own bracket example the children carry their own `outsideRTH` value: the stop child is `false`. Whether TWS and the mobile app copy it to the children is not confirmed either way | <https://www.interactivebrokers.com/campus/ibkr-quant-news/how-to-code-a-bracket-order-in-the-web-api/> |
| A stop-limit "may not fill at all" | <https://www.interactivebrokers.com/campus/trading-lessons/ibkr-desktop-stop-limit-orders/> |
| US pre-market for outside-RTH orders runs 04:00–09:30 ET | <https://ibkrguides.com/traderworkstation/classic-order-ticket.htm> |

**What the card says for a ticket issued before 09:30:**
- Tick Fill outside RTH on each of the three orders, and check each one on the confirmation screen.
- The stop loss must be a SELL STP LMT. Its limit is the stop − `STOP_LIMIT_ATR` × ATR14 (default). It can be
  switched to a plain SELL STP after the open.
- One line: before the open the protection is not guaranteed. If the price jumps below the stop's limit, the stop
  will not fill.

**Not verified on an IBKR page:** exactly when a DAY order with outside RTH ticked starts and stops in pre-market. The
2025 staff reply shows that an STP LMT with outside RTH ticked works before the open.

## 9. Parameters

Every number in `bot/deck.py`. Checklist item 8 compares the running set against the id `ddcc3dde5bdf`.

| Parameter | Value | Slide | Source | Note |
|---|---|---|---|---|
| `TICK` | 0.01 | c2-entry | presentation | |
| `TARGET_R` | 2.0 | c2-entry, c5-flag-order | presentation | |
| `STOP_ATR_FLOOR` | 0.10 | c5-pullback | bot | R ≥ 10% of ATR14 |
| `STOP_MIN_PCT` | 0.003 | c5-pullback | bot | R ≥ 0.3% of the price |
| `STOP_ATR_CAP` | 1.0 | c5-pullback | bot | R ≤ 1 ATR14 |
| `LIMIT_ATR` | 0.05 | c7-stoplimit | bot | the entry's limit offset, × ATR14 |
| `STOP_LIMIT_ATR` | 0.05 | – | default | the pre-market stop-limit's offset below the stop |
| `RISK_USD` | 25.0 | c7-size | presentation | 1% of $2,500 |
| `CASH` | 2500.0 | c7-size | presentation | shares ≤ ⌊cash / limit⌋ |
| `COST_MAX_R` | 0.3 | c7-checklist item 6, c7-winrate | user | |
| `HAMMER_WICK` | 2.0 | c2-hammer | presentation (bot constant, same value) | lower wick ≥ 2 × body |
| `UPPER_WICK` | 0.5 | c2-hammer ("small") | default | upper wick ≤ 0.5 × body |
| `ENGULF` | 1.0 | c2-engulf | bot | body 2 ≥ body 1 |
| `DOJI_BODY` | 0.05 | c2-doji ("almost the same price") | default | the bot's minimum hammer body |
| `NEAR` | 0.003 | c4-levels ("a zone, not a line") | bot (`VWAP_NEAR`) | "on a level" and touch tolerance |
| `TOUCHES` | 2 | c4-levels | presentation | |
| `DECLINE_BARS` | 3 | c1-trend, c2-hammer | default | 3 lower lows in a row |
| `DECLINE_WINDOW` | 6 | c1-trend, c2-hammer | default | |
| `DECLINE_ATR` | 1.0 | c1-trend, c2-hammer | default | a fall ≥ 1 ATR14(5m) over the window |
| `ATR5_N` | 14 | – | default | |
| `CANDLE_RELVOL` | 1.5 | c4-breakout item 3 | bot | the Retest's breakout candle; the flag's pole |
| `RETEST_BARS` | 6 | c4-flip | default | |
| `COMPRESS_N` | 5 | c4-breakout | default | |
| `COMPRESS_K` | 3 | c4-breakout | default | |
| `COMPRESS_LOWS` | 3 | c4-breakout ("rising lows") | default | |
| `IMPULSE_ATR` | 0.5 | c5-flag | bot | |
| `IMPULSE_MAX` | 3 | c5-flag ("several candles") | bot | |
| `FLAG_PULLBACK` | 0.5 | c5-flag ("less than half") | presentation (bot constant, same value) | |
| `FLAG_MIN` | 2 | c5-flag ("several") | bot | |
| `FLAG_MAX` | 4 | c5-flag | bot (`FLAG_MAX_BARS`) | |
| `NR_N` | 7 | c3-nr7 | presentation | |
| `PRE_MIN_TRADES` | 3 | – | default (the user's rule) | |
| `ROUND_STEPS` | $1 < $20, $5 < $100, $10 | c4-levels | default | |
| `WY_RANGE_N` | 20 | c6-accum | default | |
| `WY_RANGE_PCT` | 0.15 | c6-accum | default | |
| `WY_DECLINE_N` | 60 | c6-cycle | default | |
| `WY_DECLINE_PCT` | 0.20 | c6-cycle | default | |
| `WY_SPRING_N` | 5 | c6-spring | default | |
| `IB_PER_SHARE` | 0.0035 | c7-winrate | IBKR | |
| `IB_MIN` | 0.35 | c7-winrate | IBKR | |
| `IB_MAX_PCT` | 0.01 | – | IBKR | |
| `EXCHANGE_FEE` | 0.0030 | – | IBKR, both legs default | |
| `CLEARING_FEE` | 0.00020 | – | IBKR | |
| `CAT_FEE` | 0.000003 | – | IBKR | |
| `PASS_THROUGH` | 0.000735 | – | IBKR | 0.000175 + 0.00056, × commission |
| `SEC_FEE` | 0.0000206 | – | SEC | per $ sold |
| `TAF` | 0.000195 | – | FINRA | per share sold |
| `TAF_MAX` | 9.79 | – | FINRA | |

The times `FIRST` 08:00 and `LAST` 11:30 (c5-pullback, user 2026-10-22), `OPEN` 09:30 (c5-vwap) and the list of
next-candle setups `NEXT_CANDLE` (c2-hammer, user 2026-10-22) are in the set too.

## 10. Presentation vs the orchestrator's summary

The slides prevail. Each difference and what the code does:

1. **Validity.**
   - c7-a's worked ticket is valid "until 11:30, then cancel", which was the old window's end.
   - c2-hammer and c2-entry ask for confirmation on the next candle: "the next candle trades above the high; without
     confirmation, no trade".
   - The summary states no validity.
   - **Resolved by the user on 2026-10-22:** c2-hammer for the reversal setups (Hammer, Engulfing, VWAP pullback,
     Retest): valid until the next candle closes. c7-a for the others: valid until the window's end, 11:30.
2. **Trading window.** c5-pullback says "in the live bot: only 09:40 to 11:30". The user first set 08:00–10:30, then
   on 2026-10-22 moved the end to **11:30**, as on c5-pullback. VWAP setups still start at 09:30.
3. **Limit price.** c7-q and c7-a use a fixed $0.05 above the trigger; c7-stoplimit's example uses $0.10. The summary
   uses the bot's `LIMIT_ATR` (5% of ATR14), which is the same $0.05 at an ATR14 of $1.
4. **Cash cap.** c7-size compares shares × the entry (trigger) with $2,500. The summary and the code use shares ×
   the limit, the cash a stop-limit can use. That is stricter by at most one share.
5. **"A previous low" as a level.** c2-hammer and checklist item 2 list it; the summary's level list does not. Added
   as today's earlier confirmed 5-minute swing lows (support).
6. **RVOL.**
   - c4-breakout's side box defines RVOL against the same time of day on earlier days.
   - Its item 3 says "more shares than in the candles before it".
   - The code keeps the bot's `CANDLE_RELVOL`, measured against the earlier candles of the same part of the day,
     which matches item 3. A same-time-of-day RVOL would need 14 days of intraday history per symbol.
7. **Breakout stop.** c4-entries says only that "the stop is below the level, usually far from the entry". The lowest
   of the last 3 compression lows is the summary's default, and it is below the level by construction.
8. **The flag's breakout volume.** c5-flag item 3 has "volume rises again" on the breakout. A resting buy stop
   triggers on price alone, so this is not checked.
9. **The flag's trigger.** The deck (c5-flag-order, c5-a) triggers above the flag's high, as the summary does. The
   retired v1 code triggered above the pole's top.
10. **The NR7 stop.** c3-nr7 gives only the entry (a buy stop above the range). The stop below its low is c2-entry's
    general rule.
11. **Doji.** c2-doji gives no number. `DOJI_BODY` is a default.
12. **Item 2 for every setup.** c7-checklist lists "support, VWAP or a previous low". For the Inside bar, NR7, Flag
    and Breakout, which the slides place on no level, item 2 passes when any of the pattern's candles is on any
    level: support by its low, resistance by its high.
13. **The Engulfing stop in a VWAP pullback.** c5-pullback item 4 says "below its low" (the reversal candle).
    c2-engulf says "below the lower low of the two candles". The code follows c2-engulf, as the summary does ("as
    setups 1 and 2").
14. **The "truth" slide** says the VWAP pullback and Bull flag "are being tested now in T1, no results yet". That is
    out of date: T2 measured both negative, and their label says so.
15. **A "real" breakout** in c4-breakout closes above the level on volume, but c4-entries' first entry is a resting
    buy stop that fills before any close. c4-fake says so itself. The code follows the summary: a resting stop at
    the level.
16. **Wyckoff.** c6-limits calls it subjective and hard to make mechanical. Per the user it is a tag only, with
    default numbers.

## 11. Defaults (confirmed by the user, 2026-10-22)

Items 1–9 and 11–13 are confirmed by the user, 2026-10-22, as written. Item 10 was decided differently (below).

1. Hammer upper wick ≤ 0.5 × body (`UPPER_WICK`); Doji body ≤ 5% of the range (`DOJI_BODY`).
2. End of decline:
   - 3 lower lows; or
   - a fall of at least 1 × ATR14(5m) over 6 candles (highest high to the pattern's low);
   - ATR14(5m) over 14 candles.
3. Repeated touches within 0.3%, today and yesterday. Previous lows are today's swing lows. Levels count only when
   known before the pattern.
4. Round numbers: every $1 below $20, $5 to $100, $10 above.
5. Breakout:
   - at least 3 of the last 5 highs within 0.3% under the level;
   - the last 3 lows rising;
   - stop under the lowest of those lows.
6. Retest within 6 candles of the breakout candle. The breakout candle needs volume of at least 1.5 × the earlier
   mean, measured within the same part of the day.
7. Flag pole: volume of at least 1.5 × the earlier mean.
8. Pre-market candle: trades in at least 3 of its 5 minutes.
9. Wyckoff:
   - "Accumulation": a range ≤ 15% over 20 sessions after a fall ≥ 20% over 60;
   - "Spring": a break of the 20-session low within the last 5 sessions that closed back above it.
10. **Validity: decided by the user, 2026-10-22.** The next candle only for Hammer, Engulfing, VWAP pullback and
    Retest (c2-hammer); until 11:30 for Inside bar, NR7, Breakout and Bull flag (c7-a).
11. One ticket per symbol per scan, in the preference order of section 5.
12. Cost:
    - both legs take liquidity;
    - the TAF at its normal rate during FINRA's Q4 2026 holiday;
    - the spread from the latest IEX quote.
13. The pre-market stop-limit's limit: the stop − 0.05 × ATR14.

## 12. Data and schedule

- **Bars and quotes.**
  - Candles: Alpaca IEX 1-minute bars, live, from 04:00.
  - Spread: the latest IEX quote, fetched only for a setup that has passed every item before cost.
  - Watchlist inputs: SIP only when at least 15 minutes old.
  - Yesterday's candles: complete SIP.
  - Wyckoff: daily bars.
- **No paid data.** Keys come from the existing GitHub secrets only.
- **Schedule.** The listener's clock (`bot/clock.py`) dispatches `intraday.yml` on weekdays:
  - `morning` at 07:57, which loops 08:00–11:30;
  - `exit` at 12:50 and at 15:50. Each run reads the calendar, and only the one that matches today's close sends the
    time-exit reminder, 5 minutes before the close;
  - `followup` at 16:20, unchanged.
- **Job time limit.** 245 minutes for the job and 235 for the scan step (was 150 and 140 before D1). The 07:57–11:30
  loop is 213 minutes, plus stage 1 and set-up; GitHub's cap is 360.
- **Unchanged:**
  - the journal and its schema; the card's extra fields go to a separate `cards` key in the state file;
  - `/intraday stats`;
  - the insider, squeeze and ticket modules;
  - every other workflow.

## 13. Replay of 3 sessions (example cards only, 2026-10-16, 19 and 20)

The screener was replayed on the bot's own read paths: IEX bars and quotes as of each scan, and SIP only when at
least 15 minutes old. The replay produced cards only, with no fills, outcomes or statistics. What it showed about the
data:
- **The pre-market IEX feed is thin.** Of the watchlist's pre-market 5-minute candles, 10%, 16% and 11% had trades in
  3 of their 5 minutes. So the pre-market setups seldom have usable candles on the free feed.
- **The first ticket came after 10:00 in all three sessions.** Checklist item 1 needs confirmed swings or a measured
  decline, and those take several usable candles after the open.
- **Cards per session:** 7, 12 and 1 (the 10:30-scan cards came before item 7's time-left rule was added). The
  example cards are in the PR description.
- **The replay ran on the D1 rules:** window to 10:30, every ticket valid to 10:30. Its cards were re-rendered with
  the 2026-10-22 validity (no new data fetched); scans from 10:30 to 11:30 were not replayed.
