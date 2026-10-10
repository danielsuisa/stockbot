# ZBA v2 — forensics of X5's holdout failure (2026-10-10)

A diagnostic report only. No parameter was changed, no new strategy was selected, and the holdout was not run again.
Every number is computed from the saved files only: `ticket-zba-v2-2026-10-09-realistic.csv` (development, 2,046 X5
trades) and `ticket-zba-v2-holdout-2026-10-10-realistic.csv` (holdout, 2,971 trades), with the script
`zba-v2-forensics.py`. The full tables are in `zba-v2-forensics-tables.md`.

## Summary

- The gap between development (+0.199R) and the holdout (−0.033R) is 0.231R a trade, and almost all of it is in the
  gross result: +0.413R against +0.180R. Costs are the same in both periods (0.214R against 0.213R).
- The method lives on rare tail trades. In both periods 83–85% of trades stop out (on average −1.22R) and 7.5–8.8% end
  at +5R or more (on average +11.5R). Without the top 1% of trades both periods are negative.
- Breakeven is at a tail share of about 7.3–7.8%. Development had 8.8%; the holdout had 7.5%, right on the line.
- The 1.3-point drop in the tail share explains about 70% of the gap: with development's tail share, the holdout would
  have been +0.130R. The rest comes from a slightly larger loss on the other trades (−0.972R against −0.916R).
- The tail-share difference (8.8% against 7.5%) is only about 1.7 standard errors. Development's result is consistent
  with luck; no bug or market change is needed to explain it.
- Within the model, the execution assumptions and data quality are the same in both periods and do not explain the gap
  (details below). The model itself does not measure real slippage beyond its own assumptions.
- No sub-group that looked good in development (relative volume, rank, time of day, spread, volatility, price, side)
  repeats in the holdout. There is no stable entry condition to rescue.

## 1. Proven findings (computed from the files)

### 1.1 The payoff shape is the same; the tail frequency is not

| | Development | Holdout |
|---|---:|---:|
| Trades | 2,046 | 2,971 |
| Mean net | +0.199R (t 2.1) | −0.033R (t −0.5) |
| Mean gross | +0.413R | +0.180R |
| Mean cost | 0.214R | 0.213R |
| Win rate | 16% | 14% |
| Stopped out | 83% (mean −1.219R) | 85% (mean −1.214R) |
| Held to the close | 17% (mean +7.14R) | 15% (mean +6.74R) |
| Trades ≥ 5R | 181 (8.8%), mean +11.69R | 224 (7.5%), mean +11.49R |
| The other trades | mean −0.916R | mean −0.972R |
| Total without the top 1% | −101.8R | −786.4R |

### 1.2 By year: the tail share decides

| Year | Trades | Mean net | Trades ≥ 5R |
|---|---:|---:|---:|
| 2016 | 481 | +0.207 | 9.1% |
| 2017 | 406 | −0.120 | 8.4% |
| 2018 | 484 | +0.148 | 7.4% |
| 2019 | 472 | +0.018 | 8.5% |
| 2020 | 591 | −0.071 | 7.3% |
| 2021 | 537 | −0.348 | 5.0% |
| 2022 | 551 | +0.119 | 8.2% |
| 2023 | 512 | +0.072 | 8.6% |
| 2024 | 353 | +0.128 | 7.1% |
| 2025 | 351 | +0.244 | 9.1% |
| 2026 | 279 | +0.622 | 12.5% |

- The worst year (2021, −0.348R, t −2.4) has the fewest tail trades (5.0%); the best (2026, +0.622R) has the most
  (12.5%).
- Without 2026, development was only about +0.13R, and 2026 had already been seen before this study.
- Losses are not concentrated in a few trades: they are 85% of all trades, about −1.2R each. The gains are what is
  concentrated.
- Months: 39 of 58 positive in development, 32 of 72 in the holdout. The holdout's five worst months (−187.9R
  together) are spread over 2016, 2017, 2020 and 2021, not one market period.

### 1.3 Execution assumptions and data quality are the same in both periods

- **Cost:** 0.21–0.23R a trade in each of the 11 years.
- **Entry slippage** (tick-level fill against the stop level): mean between −0.03R and +0.004R in every year, median 0
  in every year. About 20% of fills are beyond the level each year (29% in 2022).
- **Rule 201:** dropped 22% of the shorts in development and 18% in the holdout.
- **Data quality:** 9 skips for missing minutes in the holdout; 20 ticker-days without minute bars in the holdout
  against 6 in development.
- **Meaning:** within the model there is no difference between the periods that could explain the gap. The assumptions
  themselves (half the spread, commission, fill at the next trade) are identical, so these files cannot tell whether
  real execution would have been worse.

### 1.4 Sub-groups: no development pattern repeats

| Cut | Development | Holdout |
|---|---|---|
| Opening relative volume (quintiles) | Q2 +0.47, Q4 +0.72, Q3 −0.24 | Q2 −0.27, Q4 0.00, Q1 +0.15 |
| Rank in the top 20 | ranks 6–10: +0.60 | ranks 6–10: −0.19 |
| Entry time | positive in every window | positive only 09:35–09:44 (+0.04, t 0.4), negative after 10:00 |
| Spread / stop distance | best below 0.10 (+0.39) | best at 0.20–0.25 (+0.14) |
| Volatility (ATR / price) | below 3%: +0.52 | below 3%: +0.01 |
| Side | long +0.25, short +0.13 | long −0.07, short +0.01 |

- Every cut flipped or vanished, which is what noise in the development patterns looks like.
- The opposite side (same fill, other direction) loses in both periods: −0.37R and −0.44R. The first candle's direction
  does carry information, but not enough to cover a 0.21R cost.
- No ticker concentration: no stock has more than 25 trades in a period.

## 2. Hypotheses (not proven by the files)

1. **Market regime.** 2021 (tail share 5.0%) and 2026 (12.5%) are extremes. The method may work only when the whole
   market has strong intraday trends. The files hold no SPY or VIX data, so this was not tested.
2. **News is not the same thing across periods.** News coverage is thinner in 2016–2018 (50% of ticker-days skipped
   for no news, against 44%), and symbols were tagged as they were then. The N condition may select a different mix in
   each period. Against it: the thinner-news years (2016, 2018) did better.
3. **Later entries are weaker.** In the holdout, entries after 10:00 are negative even before costs; in development
   they were not. The samples are small (225–298 trades a window, t between −0.9 and −1.5), so this is a hypothesis
   only, and no rule may be chosen from it on the strength of the holdout.
4. **Survivorship.** Both periods' universes lack stocks that later delisted, more so in the holdout (spec 14.0). The
   direction of the bias is unknown.

## 3. Missing data (not saved, so not analysed)

- **News type:** only whether there was an item (yes / no) was kept, not its content, category or publication time. No
  analysis by news type is possible.
- **Time-of-day volume value** (the T2 condition): whether it passed 2 was checked, but the value itself is not in the
  trade row.
- **Dollar liquidity:** price and share count were kept, not the average daily dollar volume.
- **Market regime:** no SPY, VIX or breadth data in the files.
- **Intraday path:** no maximum favourable or adverse excursion, only the exit type and the result.
- **Candidates not traded:** only counts of skip reasons were kept, not the rows.
- **The holdout's bar-model rows:** only the tick-level rows were saved.
- **Real slippage:** the model assumes a fill at the next trade and half the spread; nothing in the files tests that
  assumption against real execution.

## 4. Research directions (at most three)

Each direction is built on development data only, registered in advance, and tested only on new data (the forward
shadow tracker). Nothing here is chosen from the holdout.

1. **The tail share as the target.** Instead of mean R, study which data known before the open (market regime from SPY
   and VIX, the opening gap, market breadth) predicts the share of tail trades, where the result is decided. Build the
   hypothesis on 2022–2026, register it, and test it forward.
2. **Cost as the lever.** The cost (0.21R) is as large as the whole edge measured in development. Measure real spread
   and slippage in paper trading against the model's assumptions, and test structures that lower cost in R (such as a
   wider stop, already tested in section 12) only as a pre-registered hypothesis.
3. **Infrastructure for the next diagnosis.** In every future run, keep all features of every candidate: news type and
   publication time, the time-of-day volume value, liquidity, market regime, and the intraday path. Build a
   survivorship-free universe from a free list of delisted stocks (for example SEC filings) before testing a
   historical period again.

## Data

- Inputs: the two realistic CSVs above (X5 rows, filled trades only). Script: `docs/backtest/zba-v2-forensics.py`
  (stdlib, no network). Tables: `docs/backtest/zba-v2-forensics-tables.md`.
- Tail share at breakeven: `-rest / (tail - rest)` with each period's own means (development 7.3%, holdout 7.8%).
  Counterfactual: the holdout's magnitudes with development's tail share give +0.130R. Standard error of the tail-share
  difference: √(0.088·0.912/2046 + 0.075·0.925/2971) ≈ 0.78 points, so 1.3 points ≈ 1.7 SE.
