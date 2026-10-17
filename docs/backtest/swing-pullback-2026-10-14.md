# Swing pullback — 2026-10-14

Long only, one position, $460, settled the session after the exit. No earnings calendar was available from Alpaca, so the proxy is the ten-day window from the second Monday of January, April, July, and October. The daily entry has no SIP quote, so every trade takes the 1% round-trip haircut plus the $0.35 minimum commission. This replay does not place or transmit an order.

Window asked for: in-sample 2016-01-01 through 2022-12-31, out-of-sample 2023-01-01 through 2026-10-13. First daily bar in the download: 2016-01-04. Universe 7718 names, 1370 of them inactive.

Candidates before the one-position rule: 185592.

### No earnings filter

Verdict: **NO-GO**.

- pass: at least 100 trades (255)
- fail: mean net R > 0 with t > 2
- fail: mean net R above the 95th percentile of 20 random-direction means
- fail: mean net R > 0 in 2023 and in 2024 and in 2025 and in 2026

| Sample | Trades | Mean net R | t | Random p95 | Win rate |
|---|---:|---:|---:|---:|---:|
| 2023–2026 | 255 | -1.122 | -3.84 | -0.958 | 30.6% |
| 2016–2022 | 421 | -0.911 | -8.88 | — | 28.7% |

| Year | Trades | Mean net R |
|---|---:|---:|
| 2016 | 44 | -0.975 |
| 2017 | 77 | -1.287 |
| 2018 | 59 | -0.888 |
| 2019 | 59 | -0.815 |
| 2020 | 49 | -0.692 |
| 2021 | 69 | -0.807 |
| 2022 | 64 | -0.803 |
| 2023 | 79 | -1.176 |
| 2024 | 68 | -1.031 |
| 2025 | 57 | -1.397 |
| 2026 | 51 | -0.854 |

Trades in names Alpaca now marks inactive: 17 of 676.

### Calendar earnings proxy

Verdict: **NO-GO**.

- pass: at least 100 trades (173)
- fail: mean net R > 0 with t > 2
- fail: mean net R above the 95th percentile of 20 random-direction means
- fail: mean net R > 0 in 2023 and in 2024 and in 2025 and in 2026

| Sample | Trades | Mean net R | t | Random p95 | Win rate |
|---|---:|---:|---:|---:|---:|
| 2023–2026 | 173 | -1.191 | -2.86 | -0.853 | 30.6% |
| 2016–2022 | 296 | -1.056 | -5.77 | — | 30.4% |

| Year | Trades | Mean net R |
|---|---:|---:|
| 2016 | 29 | -1.265 |
| 2017 | 47 | -1.040 |
| 2018 | 45 | -1.564 |
| 2019 | 49 | -1.011 |
| 2020 | 34 | -0.584 |
| 2021 | 43 | -0.970 |
| 2022 | 49 | -0.926 |
| 2023 | 53 | -0.890 |
| 2024 | 53 | -1.022 |
| 2025 | 36 | -2.012 |
| 2026 | 31 | -1.040 |

Trades in names Alpaca now marks inactive: 11 of 469.

## Verdict

- No earnings filter: NO-GO.
- Calendar earnings proxy: NO-GO.
