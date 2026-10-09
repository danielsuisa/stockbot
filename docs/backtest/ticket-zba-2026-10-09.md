# ZBA 2024 "Stocks in Play" 5-minute ORB on 2024-2026 — 2026-10-09

## סיכום

- הכרעה על 2024-01-02 → 2026-10-08: NO-GO ❌ — עסקאות `10930`, ממוצע נטו `-0.718R`, סטטיסטי t `-14.75`, תיק: תשואה שנתית `-93.1%`, שארפ `-8.50`
- שחזור על 2022-01-03 → 2023-12-29 (בתוך תקופת המחקר): עסקאות `8115`, ממוצע נטו `-0.426R`, תיק: תשואה שנתית `-79.0%`, שארפ `-5.65`
- גרסה מכוילת (נבחרה על 2022–2023 מתוך 8 גרסאות של רוחב סטופ ומסנן מרווח): `K10s` — GO ✅ על 2024-02-01 ואילך, עסקאות `2007`, ממוצע נטו `+0.284R`, סטטיסטי t `2.92`, תיק: שארפ `2.05`
- בדיקת מציאותיות (מילוי לפי עסקאות בודדות, כלל 201): NO-GO ❌ — עסקאות `1592`, ממוצע נטו `+0.277R`, סטטיסטי t `2.25`. זו ההכרעה הקובעת.

השורה הראשונה היא האסטרטגיה בפרמטרים של המאמר; כל כללי הבחירה וההכרעה נרשמו לפני ההרצות.

## Verdict checks

| Check | Pass |
|---|---|
| at least 100 trades (10930) | pass |
| mean net R > 0 with t >= 2 | fail |
| mean net R above the 95th percentile of 20 random-direction means | pass |
| mean net R > 0 in 2024 and in 2025 and in 2026 | fail |

## Results (net of costs)

| Period | Side | Trades | Mean net R | t | Win | Median | Random p95 | 2022 | 2023 | 2024 | 2025 | 2026 | <$5 | ≥$5 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| replication | all | 8115 | -0.426 | -9.66 | 14% | -1.434 | -0.587 | -0.288 | -0.564 | — | — | — | +2.296 | -0.428 |
| replication | long | 4137 | -0.398 | -6.35 | 14% | -1.452 |  | -0.312 | -0.482 | — | — | — | — | -0.398 |
| replication | short | 3978 | -0.454 | -7.35 | 14% | -1.417 |  | -0.265 | -0.651 | — | — | — | +2.296 | -0.460 |
| verdict | all | 10930 | -0.718 | -14.75 | 13% | -1.638 | -0.855 | — | — | -0.631 | -0.664 | -0.901 | -0.703 | -0.718 |
| verdict | long | 5663 | -0.689 | -9.83 | 13% | -1.657 |  | — | — | -0.584 | -0.643 | -0.881 | — | -0.689 |
| verdict | short | 5267 | -0.750 | -11.13 | 12% | -1.617 |  | — | — | -0.683 | -0.685 | -0.923 | -0.703 | -0.750 |

## Portfolio (1% risk a trade, at most 4x gross, after costs)

| Period | Sessions | Total | Annual | Sharpe | Worst drawdown |
|---|---:|---:|---:|---:|---:|
| replication | 501 | -95.5% | -79.0% | -5.65 | 95.5% |
| verdict | 695 | -99.9% | -93.1% | -8.50 | 99.9% |

Paper (2016-2023, no spread or slippage): total 1,637%, IRR 41.6%, Sharpe 2.81, hit ratio 48.4%, max drawdown 12%.

## Cost-aware grid (spec section 12)

Selected on 2022-2023 (≥ 300 trades, highest mean); verdict on 2024-02-01 onward (January 2024 was seen in a probe).

| Variant | Stop × ATR | Spread gate | 2022-23 trades | 2022-23 mean | 2024+ trades | 2024+ mean | 2024+ t | Cost R |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| K10 | 0.1 | — | 8115 | -0.426 | 10930 | -0.718 | -14.75 | 1.044 |
| K25 | 0.25 | — | 8101 | -0.212 | 10922 | -0.312 | -12.35 | 0.418 |
| K50 | 0.5 | — | 8074 | -0.107 | 10859 | -0.153 | -9.93 | 0.211 |
| K100 | 1 | — | 7891 | -0.055 | 10577 | -0.074 | -8.25 | 0.108 |
| K10s ← chosen | 0.1 | ≤ 0.25 × stop | 2367 | +0.013 | 2107 | +0.273 | 2.89 | 0.217 |
| K25s | 0.25 | ≤ 0.25 × stop | 5268 | -0.050 | 5172 | -0.026 | -0.81 | 0.150 |
| K50s | 0.5 | ≤ 0.25 × stop | 7220 | -0.064 | 7995 | -0.061 | -3.55 | 0.118 |
| K100s | 1 | ≤ 0.25 × stop | 7780 | -0.049 | 9761 | -0.057 | -6.24 | 0.085 |

### K10s verdict (2024-02-01 onward)

| Check | Pass |
|---|---|
| at least 100 trades (2007) | pass |
| mean net R > 0 with t >= 2 | pass |
| mean net R above the 95th percentile of 20 random-direction means | pass |
| mean net R > 0 in 2024 and in 2025 and in 2026 | pass |

Long 948 trades +0.306R · short 1059 trades +0.264R · random p95 +0.053 · book: total +2103.3%, annual +217.8%, Sharpe 2.05, worst drawdown 46.2%

Robustness (not gated):

| Test | Mean net R | t |
|---|---:|---:|
| drop top 1% | +0.017 | 0.22 |
| +$0.01 a share each side | +0.106 | 1.09 |
| +$0.02 a share each side | -0.072 | -0.74 |

By quarter: 2024Q1 +0.404 (163) · 2024Q2 -0.464 (175) · 2024Q3 +0.057 (200) · 2024Q4 +0.522 (172) · 2025Q1 +0.312 (178) · 2025Q2 +0.347 (184) · 2025Q3 +0.105 (201) · 2025Q4 +0.251 (181) · 2026Q1 +0.556 (163) · 2026Q2 +0.969 (191) · 2026Q3 +0.160 (180) · 2026Q4 -0.294 (19)

### Realism check of K10s (spec section 13) — the deciding verdict

Fill minute replayed on SIP trades (round lots); Rule 201 shorts dropped. Trades: ok 3727, Rule 201 747.

| Check | Pass |
|---|---|
| at least 100 trades (1592) | pass |
| mean net R > 0 with t >= 2 | pass |
| mean net R above the 95th percentile of 20 random-direction means | pass |
| mean net R > 0 in 2024 and in 2025 and in 2026 | fail |

2024-02-01 onward: 1592 trades, mean +0.277R, t 2.25, median -1.214, win 18% · long 948 +0.289R · short 644 +0.259R · random p95 +0.092 · 2022-2023: 2042 trades, mean -0.021R · book: annual +99.5%, Sharpe 1.38, worst drawdown 43.2%

| Stress (not gated) | Mean net R | t |
|---|---:|---:|
| drop top 1% | -0.017 | -0.21 |
| +$0.005 a share each side | +0.184 | 1.49 |
| +$0.01 a share each side | +0.091 | 0.74 |

By quarter: 2024Q1 +0.038 (134) · 2024Q2 -0.455 (155) · 2024Q3 +0.141 (168) · 2024Q4 +0.122 (132) · 2025Q1 +0.032 (141) · 2025Q2 +0.238 (137) · 2025Q3 +0.768 (150) · 2025Q4 +0.286 (140) · 2026Q1 +0.694 (124) · 2026Q2 +1.154 (151) · 2026Q3 +0.098 (145) · 2026Q4 -0.068 (15)

## Exits and NO TICKET reasons

- Exits (verdict period): eod 1486, stop 9444
- NO TICKET: trigger 4618, doji 149, spread 98, size below minimum 4

## Assumptions

- Universe: Alpaca assets on NYSE / NASDAQ, active and inactive (delisted names count), symbols of 1-5 capital letters, names without fund / ETF markers.
- Each session: 14-day average volume >= 1,000,000 and ATR14 > $0.50 from raw daily bars before the day, a bar on the session before, no close jump over 2x in the 15-bar window; open of the 09:30-09:35 bar > $5.
- Relative volume = the 09:30-09:35 volume / its mean over the 14 previous sessions (a missing bar counts 0); >= 1.0; the top 20 are traded.
- Side from the first 5-minute candle (close over open: buy stop at its high; under: sell stop at its low; equal: none); the stop order stays until the close and fills when a minute trades at it (at the stop, or the open if it gapped through).
- Stop loss 10% of ATR14 from the fill; no target; out at the last minute's close. One trade per ticker-day.
- Costs: $0.0035 a share, $0.35 minimum per order, each side; half the SIP spread at the trigger in and out (a locked quote counts $0.01; no quote: no trade). Stops fill at the stop or the gapped open; on the fill minute a stop counts only when its side of the minute comes after the fill (bar path).
- Random benchmark: the same fills with a coin-flip side (20 seeds).
- Verdict (pre-registered, 2024-01-02 onward, never seen for this universe): >= 100 trades, mean net R > 0 with t >= 2, above the random 95th percentile, mean net R > 0 in 2024, in 2025 and in 2026.
- Replication 2022-2023 (inside the paper's 2016-2023 sample): reported, not gated.
- Portfolio: each trade risks 1% of capital; a day's positions are scaled down together to at most 4x gross exposure, as if all were open at once.
- Section 13 realism check of the selected variant: the fill minute replayed on SIP trades (round lots that set the last sale; fill at the trade after the trigger; stop loss on the rest of the minute, out at the trade after the hit), Rule 201 shorts dropped; its verdict decides.
- Section 12 grid: stop k x ATR14 (k 0.10 / 0.25 / 0.50 / 1.00), each with and without a spread gate (spread <= 0.25 x the stop distance); chosen on 2022-2023 (>= 300 trades, highest mean); verdict on 2024-02-01 onward.

## Data

- Range 2022-01-03 … 2026-10-08 · runtime 191s · Alpaca requests 0
- symbols: 7728
- mean prefiltered a session: 992
- top ticker-days: 23920
- ticker-days without minute bars: 6

`python -m bot.ticket_backtest --zba` · data: Alpaca SIP (Basic plan)
