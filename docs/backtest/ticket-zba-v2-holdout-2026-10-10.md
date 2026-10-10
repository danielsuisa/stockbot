# ZBA v2 — holdout 2016-2021 (spec 14.5, single run) — 2026-10-10

## סיכום

- הגרסה `X5`: NO-GO ❌ — עסקאות `2971`, ממוצע נטו `-0.033R`, סטטיסטי t `-0.47`
- זו הבדיקה החד־פעמית. ביקום חסרות מניות שנמחקו מהמסחר אחר כך (סעיף `14.0`), ולכן גם `GO` כאן הוא ראיה חלקית, ומעקב הצל קובע.

## X5 — gates

| Gate | Pass |
|---|---|
| at least 300 trades (2971) | pass |
| mean net R > 0 with t >= 2 | fail |
| mean above the 95th percentile of 20 random-side means | pass |
| positive in at least 5 of 6 years (3) | fail |
| t >= 2 with +$0.005 a share each side | fail |
| mean > 0 without the top 1% of trades | fail |

| Variant | Trades | Mean net R | t | Win | Avg win | Avg loss | Random p95 | Book max DD | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| X5 | 2971 | -0.033 | -0.47 | 14% | +7.305 | -1.211 | -0.179 | 81.2% | +0.207 | -0.120 | +0.148 | +0.018 | -0.071 | -0.348 |

Trades: ok 2971, Rule 201 658

| Stress (not gated) | Mean net R | t |
|---|---:|---:|
| drop top 1% | -0.267 | -4.93 |
| +$0.005 a share each side | -0.120 | -1.70 |
| +$0.01 a share each side | -0.207 | -2.94 |

Skip reasons: news 15079, spread 7826, trigger 2828, tod 459, doji 69, dq: missing bars 9, size below minimum 1

## Data

- Range 2016-01-04 … 2021-12-31 · runtime 4492s · Alpaca requests 12058 · frozen 2026-10-09
- candidates: 345028
- top ticker-days (rel vol): 29920
- top ticker-days (quality rank): 29920
- ticker-days without minute bars: 20
