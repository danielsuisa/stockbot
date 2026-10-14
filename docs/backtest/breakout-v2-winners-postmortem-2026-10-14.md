# Breakout v2, why the winners won — 2026-10-14

The A/B book is 2386 fills, win rate 35.8%, mean net R -0.206. A winner is a fill with net R above zero. No minute bars were downloaded again. Gap, the prior path, and the index regime come from a SIP daily slice for these names plus SPY and QQQ. Float is not in the data. Intraday failed probes are not in the trade file; the probe count is the daily proxy defined before the slice was read against 2025–2026.

The six filters were locked on 2024 alone. Their 2025–2026 numbers were computed after that lock.

## 1. Outcome anatomy

Net R percentiles: 10th -1.082, 25th -0.521, median -0.147, 75th +0.154, 90th +0.528.
Gross R mean -0.042, median -0.036. Mean cost +0.165 R.

The order's target is 1.5R, not 2R. A fill reaches 2R only when the exit price is that far through the printed R, which the target order does not wait for. A time stop is the 15:55 flat.

Winners: 853. Losers: 1533.

| Exit | Winners | Losers |
|---|---:|---:|
| target, the 1.5R order | 73 | 3 |
| time stop, gross at least 2R | 0 | 0 |
| time stop, gross at least 1R | 50 | 2 |
| time stop, gross under 1R | 730 | 215 |
| time stop, gross at or below 0 | 0 | 1019 |
| stop | 0 | 294 |

How the losers ended:

| Group | Trades | Win rate | Mean net R |
|---|---:|---:|---:|
| stop | 294 | 0.0% | -1.314 |
| target, still a net loss | 3 | 0.0% | -0.932 |
| time stop, closed down | 1019 | 0.0% | -0.413 |
| time stop, gross positive, costs took it | 217 | 0.0% | -0.143 |

Mean net R by exit, full book:

| Group | Trades | Win rate | Mean net R |
|---|---:|---:|---:|
| target | 76 | 96.1% | +1.114 |
| flat | 2016 | 38.7% | -0.095 |
| stop | 294 | 0.0% | -1.314 |

Winners' mean gross R +0.526, mean net +0.409. Losers' mean gross -0.358, mean net -0.549. A stop's mean net R is -1.314, past −1R because the fill is worse than the trigger and the denominator is the printed R.

## 2. Timing

### Hour

| Group | Trades | Win rate | Mean net R |
|---|---:|---:|---:|
| 09 | 473 | 35.5% | -0.356 |
| 10 | 799 | 38.9% | -0.197 |
| 11 | 356 | 35.1% | -0.179 |
| 12 | 207 | 39.1% | -0.143 |
| 13 | 180 | 34.4% | -0.162 |
| 14 | 153 | 34.0% | -0.151 |
| 15 | 218 | 24.8% | -0.096 |

### Day of week

| Group | Trades | Win rate | Mean net R |
|---|---:|---:|---:|
| Mon | 428 | 36.0% | -0.174 |
| Tue | 497 | 38.6% | -0.142 |
| Wed | 477 | 34.2% | -0.261 |
| Thu | 497 | 36.8% | -0.172 |
| Fri | 487 | 33.1% | -0.282 |

### Month

| Group | Trades | Win rate | Mean net R |
|---|---:|---:|---:|
| 01 | 166 | 40.4% | -0.173 |
| 02 | 220 | 37.3% | -0.195 |
| 03 | 197 | 38.1% | -0.211 |
| 04 | 197 | 46.2% | -0.030 |
| 05 | 234 | 29.5% | -0.202 |
| 06 | 232 | 32.8% | -0.320 |
| 07 | 233 | 32.2% | -0.245 |
| 08 | 213 | 39.0% | -0.139 |
| 09 | 218 | 27.5% | -0.309 |
| 10 | 207 | 34.8% | -0.179 |
| 11 | 150 | 42.7% | -0.152 |
| 12 | 119 | 32.8% | -0.318 |

### Index regime, prior close versus the average. Known before the session.

| Group | Trades | Win rate | Mean net R |
|---|---:|---:|---:|
| SPY 20-day above | 1759 | 35.0% | -0.204 |
| SPY 20-day below | 627 | 38.0% | -0.214 |

| Group | Trades | Win rate | Mean net R |
|---|---:|---:|---:|
| SPY 50-day above | 1960 | 35.1% | -0.212 |
| SPY 50-day below | 426 | 39.0% | -0.178 |

| Group | Trades | Win rate | Mean net R |
|---|---:|---:|---:|
| QQQ 20-day above | 1685 | 35.8% | -0.195 |
| QQQ 20-day below | 701 | 35.7% | -0.233 |

| Group | Trades | Win rate | Mean net R |
|---|---:|---:|---:|
| QQQ 50-day above | 1831 | 35.2% | -0.208 |
| QQQ 50-day below | 555 | 37.5% | -0.199 |

## 3. Setup at the entry

| Group | Trades | Win rate | Mean net R |
|---|---:|---:|---:|
| grade A | 2052 | 38.8% | -0.115 |
| grade B | 334 | 17.1% | -0.765 |

Price bucket, from the fill. Float is not available.

| Group | Trades | Win rate | Mean net R |
|---|---:|---:|---:|
| under $20 | 307 | 35.8% | -0.134 |
| $20 to $50 | 572 | 34.6% | -0.197 |
| $50 to $100 | 614 | 37.6% | -0.188 |
| $100 and up | 893 | 35.2% | -0.250 |

Quartiles. Q1 is the low end.

| Gap at the open quartile | Range | Trades | Win rate | Mean net R |
|---|---|---:|---:|---:|
| Q1 | -0.04178 to -0.00482 | 596 | 37.1% | -0.139 |
| Q2 | -0.004816 to -0.001228 | 597 | 35.0% | -0.211 |
| Q3 | -0.001227 to 0.001229 | 596 | 36.4% | -0.201 |
| Q4 | 0.001232 to 0.07716 | 597 | 34.5% | -0.274 |

| Distance of the prior close under the 20-day high quartile | Range | Trades | Win rate | Mean net R |
|---|---|---:|---:|---:|
| Q1 | 0 to 0.003927 | 596 | 35.9% | -0.202 |
| Q2 | 0.003937 to 0.007154 | 597 | 37.0% | -0.182 |
| Q3 | 0.007163 to 0.01217 | 596 | 33.2% | -0.258 |
| Q4 | 0.01217 to 0.02986 | 597 | 36.9% | -0.184 |

| Fill versus the prior 20-day high quartile | Range | Trades | Win rate | Mean net R |
|---|---|---:|---:|---:|
| Q1 | -0.03105 to -0.005172 | 596 | 34.7% | -0.212 |
| Q2 | -0.00515 to -0.0004 | 597 | 35.0% | -0.199 |
| Q3 | -0.0003949 to 0.0003275 | 596 | 37.8% | -0.155 |
| Q4 | 0.0003278 to 0.04621 | 597 | 35.5% | -0.259 |

| ATR as a fraction of price quartile | Range | Trades | Win rate | Mean net R |
|---|---|---:|---:|---:|
| Q1 | 0.001401 to 0.01713 | 596 | 36.4% | -0.193 |
| Q2 | 0.01713 to 0.02181 | 597 | 36.2% | -0.199 |
| Q3 | 0.02182 to 0.02915 | 596 | 36.6% | -0.199 |
| Q4 | 0.02915 to 0.08572 | 597 | 33.8% | -0.235 |

| ATR percentile versus the prior 60 sessions quartile | Range | Trades | Win rate | Mean net R |
|---|---|---:|---:|---:|
| Q1 | 0 to 0.1 | 595 | 31.9% | -0.237 |
| Q2 | 0.1 to 0.35 | 596 | 35.6% | -0.205 |
| Q3 | 0.3667 to 0.6667 | 596 | 37.4% | -0.219 |
| Q4 | 0.6667 to 1 | 596 | 38.3% | -0.160 |

| Volume through the trigger, fraction of the 20-day average quartile | Range | Trades | Win rate | Mean net R |
|---|---|---:|---:|---:|
| Q1 | 0.05003 to 0.08056 | 596 | 36.9% | -0.297 |
| Q2 | 0.08062 to 0.1398 | 597 | 37.7% | -0.198 |
| Q3 | 0.1398 to 0.2623 | 596 | 35.2% | -0.212 |
| Q4 | 0.2623 to 2.965 | 597 | 33.2% | -0.119 |

| Charged spread divided by R quartile | Range | Trades | Win rate | Mean net R |
|---|---|---:|---:|---:|
| Q1 | 0.00184 to 0.03647 | 596 | 39.8% | -0.063 |
| Q2 | 0.03654 to 0.07438 | 597 | 41.7% | -0.088 |
| Q3 | 0.07444 to 0.1579 | 596 | 36.7% | -0.144 |
| Q4 | 0.1581 to 4 | 597 | 24.8% | -0.530 |

Winners versus losers, mean of the feature:

| Feature | Winners | Losers |
|---|---:|---:|
| Gap | -0.0021 | -0.0018 |
| Distance under the 20-day high | 0.0091 | 0.0089 |
| Fill versus the 20-day high | -0.0029 | -0.0029 |
| ATR / price | 0.0241 | 0.0242 |
| ATR percentile | 0.4217 | 0.3943 |
| Volume ratio | 0.1934 | 0.2119 |
| Spread / R | 0.1027 | 0.1748 |
| Fill price | 112.5955 | 118.2016 |

## 4. Path before the entry

Returns are close to close, ending at the prior close. Tightness is the prior 10-day range divided by ATR. A failed probe is a prior day whose high traded through the then-prior 20-day high and whose close finished back under that high. The count is over the five sessions before the trade.

| Prior 1-day return quartile | Range | Trades | Win rate | Mean net R |
|---|---|---:|---:|---:|
| Q1 | -0.02321 to -0.0006631 | 596 | 35.4% | -0.203 |
| Q2 | -0.0006537 to 0.003301 | 597 | 36.9% | -0.158 |
| Q3 | 0.003308 to 0.009093 | 596 | 35.1% | -0.225 |
| Q4 | 0.009098 to 0.4335 | 597 | 35.7% | -0.239 |

| Prior 5-day return quartile | Range | Trades | Win rate | Mean net R |
|---|---|---:|---:|---:|
| Q1 | -0.02562 to 0.01303 | 596 | 31.7% | -0.237 |
| Q2 | 0.01311 to 0.02908 | 597 | 37.4% | -0.188 |
| Q3 | 0.02913 to 0.05165 | 596 | 37.6% | -0.175 |
| Q4 | 0.05169 to 0.5875 | 597 | 36.3% | -0.226 |

| 10-day range / ATR quartile | Range | Trades | Win rate | Mean net R |
|---|---|---:|---:|---:|
| Q1 | 0.2419 to 2.676 | 596 | 34.7% | -0.210 |
| Q2 | 2.679 to 3.342 | 597 | 34.2% | -0.219 |
| Q3 | 3.343 to 4.095 | 596 | 39.3% | -0.194 |
| Q4 | 4.097 to 10.41 | 597 | 34.8% | -0.203 |

| Failed probes in the prior 5 days quartile | Range | Trades | Win rate | Mean net R |
|---|---|---:|---:|---:|
| Q1 | 0 to 0 | 596 | 36.7% | -0.184 |
| Q2 | 0 to 1 | 596 | 33.2% | -0.206 |
| Q3 | 1 to 1 | 596 | 37.1% | -0.191 |
| Q4 | 1 to 5 | 597 | 36.0% | -0.244 |

| Feature | Winners | Losers |
|---|---:|---:|
| Prior 1-day return | 0.0054 | 0.0056 |
| Prior 5-day return | 0.0387 | 0.0383 |
| 10-day range / ATR | 3.5127 | 3.4618 |
| Failed probes | 0.9297 | 0.8825 |

NR7 on the prior day is 2380 of 2386. It does not vary inside this book, so it cannot separate winners. The screen already required a contraction or an NR7.

## 5. Filters locked on 2024, then checked on 2025–2026

Each filter's 2024 row was the reason it was locked. The 2025–2026 row was not used to choose it. A filter is ranked later by its out-of-sample mean, and only if it has at least 50 out-of-sample trades.

| Filter | 2024 trades | 2024 win | 2024 mean | 2025–26 trades | 2025–26 win | 2025–26 mean | Lift vs 2025–26 book |
|---|---:|---:|---:|---:|---:|---:|---:|
| F1 spread ≤ 3% of R | 178 | 48.3% | +0.003 | 280 | 36.8% | -0.076 | +0.149 |
| F2 grade A, 10-day range ≤ 3.4 ATR, at most one failed probe | 300 | 45.3% | -0.059 | 550 | 32.2% | -0.157 | +0.068 |
| F3 grade A, spread ≤ 3% of R, entry before 15:00 | 148 | 51.4% | +0.011 | 238 | 38.2% | -0.082 | +0.143 |
| F4 SPY prior close below its 20-day average | 161 | 47.2% | -0.062 | 466 | 34.8% | -0.266 | -0.041 |
| F5 fill not above the prior 20-day high | 531 | 38.6% | -0.158 | 862 | 32.5% | -0.238 | -0.013 |
| F6 grade A and QQQ prior close below its 50-day average | 135 | 44.4% | -0.040 | 347 | 38.0% | -0.165 | +0.060 |

The unfiltered 2025–2026 book mean is -0.225 across 1487 trades.

### The filters that lift out-of-sample mean R the most

- **F1 spread ≤ 3% of R.** Out of sample: 280 trades, win rate 36.8%, mean net R -0.076, t -3.10, lift +0.149 versus the unfiltered 2025–2026 mean. Full sample, same rule: 458 trades, mean -0.045.
- **F3 grade A, spread ≤ 3% of R, entry before 15:00.** Out of sample: 238 trades, win rate 38.2%, mean net R -0.082, t -2.84, lift +0.143 versus the unfiltered 2025–2026 mean. Full sample, same rule: 386 trades, mean -0.046.
- **F2 grade A, 10-day range ≤ 3.4 ATR, at most one failed probe.** Out of sample: 550 trades, win rate 32.2%, mean net R -0.157, t -7.21, lift +0.068 versus the unfiltered 2025–2026 mean. Full sample, same rule: 850 trades, mean -0.122.

### Does any filtered subset clear the gate?

The gate on a filtered book, applied to 2025–2026: at least 100 trades, mean net R above 0 with t above 2, mean above the 95th percentile of 20 random-direction means, and a positive mean in 2025 and in 2026. 2024 is the year the filter was chosen, so it is shown and not counted as confirmation.

| Filter | Sample | Trades | Mean | t | Random p95 | 2024 | 2025 | 2026 | Gate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| F1 spread ≤ 3% of R | 2025–2026 | 280 | -0.076 | -3.10 | +0.008 | +0.003 | -0.066 | -0.087 | NO-GO |
| F2 grade A, 10-day range ≤ 3.4 ATR, at most one failed probe | 2025–2026 | 550 | -0.157 | -7.21 | -0.078 | -0.059 | -0.117 | -0.204 | NO-GO |
| F3 grade A, spread ≤ 3% of R, entry before 15:00 | 2025–2026 | 238 | -0.082 | -2.84 | +0.012 | +0.011 | -0.069 | -0.097 | NO-GO |
| F4 SPY prior close below its 20-day average | 2025–2026 | 466 | -0.266 | -8.44 | -0.168 | -0.062 | -0.248 | -0.281 | NO-GO |
| F5 fill not above the prior 20-day high | 2025–2026 | 862 | -0.238 | -11.14 | -0.168 | -0.158 | -0.218 | -0.264 | NO-GO |
| F6 grade A and QQQ prior close below its 50-day average | 2025–2026 | 347 | -0.165 | -6.08 | -0.087 | -0.040 | -0.149 | -0.176 | NO-GO |
| unfiltered A/B | 2025–2026 | 1487 | -0.225 | -13.29 | -0.163 | -0.176 | -0.214 | -0.239 | NO-GO |

No filtered subset clears that gate.

## 6. Costs and the winner label

Mean cost is +0.117 R on winners and +0.191 R on losers. The charged spread averages 0.103 of R on winners and 0.175 of R on losers. A stop turns that spread into a loss past one R.
3 target exits lost money, all of them grade B, because the spread was larger than the gain.
220 fills were gross winners and net losers. 0 net winners were gross losers, so the cost model does not create the win.
Of 853 net winners, 793 stay winners after another cent a share on each side. The book's mean gross R is -0.042, already below zero, so a cheaper cost model does not turn the book into a win. The same is true inside F1: gross mean -0.016, net mean -0.045.

## What to keep

The largest out-of-sample lift among the locked filters with at least 50 trades is F1 spread ≤ 3% of R, lift +0.149 R. It is a description of which fills lost less. It is not a passing book.

## למה ה־36% זכו

הזוכות, 853 מתוך 2386, כמעט לא הגיעו ליעד. 730 מהן נסגרו בשעה 15:55 ברווח גולמי קטן מ־R אחד, ורק 73 נגעו ביעד של אחד וחצי R. אף זוכה לא הגיעה ל־2R, כי הפקודה יוצאת ביעד ולא מחכה לו. מה שהפריד אותן ב־2024, לפני שנפתחו 2025 ו־2026, הוא מרווח צר: לכל היותר 3% מהמרחק לסטופ, 178 עסקאות, ממוצע פלוס 0.003 R. ציון ב׳ באותה שנה, 112 עסקאות, הפסיד בממוצע מינוס 0.673 R. בסיס רפוי, נגיעות כושלות, וכניסה מעל שיא 20 הימים נראו גרועים יותר ב־2024, אבל ב־2025–2026 הם לא הרימו את הממוצע כמו המרווח. הסינונים שננעלו ואז נמדדו מחוץ למדגם הם F1 ו-F3 ו-F2. אף סינון לא עבר את השער. העלות לא המציאה את הזוכות: אפס זוכות נטו עם רווח גולמי שלילי, והממוצע הגולמי של כל הספר כבר שלילי.

