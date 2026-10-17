# Intraday strategies of the most consistent US day traders — what the screener takes from them (2026-10-17)

Scope: strategies with the highest *consistency* (Sharpe, positive expectancy over thousands of trades), not the
biggest single wins. Only the academic ORB studies have independently verifiable numbers; the rest are
self-reported (Cameron) or book rules without a published trade log (Brooks, Raschke, Minervini, Burns).
High consistency did not mean a high win rate anywhere: the edge came from the R-multiple asymmetry and strict
selection, not from winning often.

## Measured (verifiable)

**Zarattini, Barbon & Aziz 2024, ORB on "stocks in play"** (~7,000 stocks incl. delisted, 2016–2023, costs
$0.0035/share): Sharpe 2.81, IRR 41.6%, hit ratio 48.4%, max drawdown 12%, beta ~0.
- Screen: open > $5 · 14-day average volume >= 1M shares · 14-day ATR > $0.50 · relative volume of the first
  5 minutes (vs the 14-day mean of that slot) >= 100% · keep the top 20 by relative volume.
- Entry: stop order at the 5-minute opening bar's high (bullish bar) / low (bearish); none on a doji.
- Stop: 10% of the 14-day ATR from the entry. Exit: the close (no target). Risk 1% of equity, 4x leverage cap.
- The relative-volume filter is the whole edge: without it Sharpe 0.48; below 100% RVOL the mean trade is
  −0.02R, above 100% +0.08R, above 30x +0.38R. 5-minute range beats 15/30/60 (Sharpe 2.81 vs 1.43/0.21/0.40).

**Zarattini & Aziz 2023, 5-minute ORB on QQQ/TQQQ**: win rate only 24%, +0.13R a trade, Sharpe 1.1; best stop
5% of ATR, hold to the close.

## Self-reported / rule books

**Ross Cameron (Warrior Trading)**: 2016 accuracy 66%, profit/loss ratio 1.27; best window 09:30–10:00; names
under 500K daily volume lost money. Scanner "5 pillars": price $1–$20, gap >= 10%, RVOL >= 5x, float < 10M, fresh
catalyst. Entries: break of the pre-market high or the opening 1/5-minute candle; bull flag (green candle breaking
the prior red candle's high); micro-pullback (3–5 green, 1–3 red retracing < 50%, buy the first new high, volume
>= 1.2x); red-to-green (first candle through the prior close); ABCD. Stop: the pullback / opening candle low.
Target: the high of day, minimum 2:1, skip without it; scale 25–50% at the first target, stop to breakeven.

**Al Brooks (5-minute price action)**: most setups 40–60%; pullback to the 20 EMA with a strong signal bar in a
strong trend ~60% for 1:1; ~80% of breakouts from a range fail. Trader's equation: take only P(win) x reward >
P(loss) x risk — on a 50/50 setup demand reward >= 2x risk. Entry: a stop order 1 tick above the signal bar.

**Raschke "Holy Grail"**: ADX(14) > 30 rising, buy a stop above the bar that touches the 20-period MA, stop under
the pullback low, target the prior high. **Minervini / Burns**: risk <= 1% a trade, 2–3:1 minimum, stop after 3
losers or ~3% of the account in a day, half off at 1–2R and stop to breakeven.

**Candlesticks (Bulkowski, daily bars)**: bullish engulfing reverses 63% of the time, hammer 60%, three-inside-up
65%; single candles are near random alone and no peer-reviewed study shows stand-alone predictive power on 1/5-minute
charts. So candles are *entry-timing triggers inside a filtered setup*, never the edge by themselves.

## What the screener implements (bot/intraday.py)

| Parameter | Value | From |
|---|---|---|
| Price | $5–$500 | Z&A (> $5); the $5 floor also matches this repo's cost findings |
| Average volume (20 sessions) | >= 1M shares and >= $20M | Z&A (1M), Cameron (names under 500K lost) |
| ATR14 | >= $0.50 and >= 2% of the close | Z&A ($0.50); 2% so a 2:1 target fits in a session |
| In play (09:38) | gap >= 2% either way, or pre-market volume >= 10% of a day | Cameron gappers / Z&A overnight catalyst |
| In play (09:50+) | opening 5-minute relative volume >= 1.0, top 20 | Z&A exact |
| Strong name | relative volume >= 5 (score bonus) | Cameron |
| Setups | ORB 5 / ORB 15, VWAP pullback with hammer / engulfing, bull flag (micro pullback), red-to-green | Z&A, Cameron, Brooks |
| Entry | buy stop 1 tick above the trigger candle, limit entry + 5% ATR | Brooks / Cameron |
| Stop | ORB 5: 10% of ATR14 under the entry (Z&A); others: under the trigger candle, floored at 10% ATR, capped at 1 ATR | Z&A, Cameron |
| Target | 2 x risk, plus a time exit 15:55 NY | Cameron / Brooks / Burns 2:1; Z&A hold to close |
| Risk a trade | equity x TICKET_RISK_PCT (as /ticket), position <= 20% of equity | Z&A 1%, Burns, this repo's sizing |
| Windows | 09:40–11:30 and 15:00–15:45 NY, every 5 minutes | Cameron's decay through the day, ORB papers |

Expected, if the studies hold: hit rate 45–50%, positive expectancy through 2:1 — not 70%+. Not backtested in
this repo yet (the ZBA holdout, docs/backtest/ticket-zba-v2-holdout-2026-10-10.md, was NO-GO on a universe
dominated by cheap names); the paper journal (/intraday stats) is the measurement.

## Sources
- https://papers.ssrn.com/abstract=4416622 (Z&A 2023)
- https://www.alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content (Z,B&A 2024)
- https://concretumgroup.com/a-profitable-day-trading-strategy-for-the-u-s-equity-market/
- https://www.cxoadvisory.com/individual-investing/intraday-trading-of-overactive-stocks-via-opening-range-breakout/
- https://medium.com/@WarriorTrading/2016-year-in-review-trading-statistics-222-244-91-dc6f98e17086
- https://www.warriortrading.com/analyzing-my-day-trading-results-for-2023/
- https://www.warriortrading.com/day-trading/
- https://www.topstep.com/blog/the-traders-equation-should-i-take-this-trade
- https://tradingsetupsreview.com/the-holy-grail-trading-setup
- https://traderlion.com/investing-champions/mark-minervinis-risk-management/
- https://trendspider.com/blog/daytrading-with-the-vwap-indicator/
- https://fidelity.com/bin-public/060_www_fidelity_com/documents/EightBestCandles.pdf
- https://thepatternsite.com/Hammer.html · https://ThePatternSite.com/BullEngulfing.html
