# Squeeze list of 2026-09-27: a reanalysis of its 1,330 picks (2026-10-13)

**Source:** `docs/backtest/squeeze-2026-09-27-picks.csv`, the 1,330 out-of-sample picks of the 2026-09-27 squeeze
list. Each pick has its 10-session return `r10`, its largest rise `maxup` and its largest fall `dd` within those
sessions.

**Script:** `docs/backtest/squeeze_2026_10_13_reanalysis.py` (standard library). It recomputes every number below:

```bash
python docs/backtest/squeeze_2026_10_13_reanalysis.py
```

**Before costs, and biased upward by survivorship.** No commission, spread or slippage is subtracted. The picks
come from symbols that are still listed today, so stocks that later failed and were delisted are missing.

## Buying every pick for 10 sessions

| Measure | Recomputed | The analyst's | Difference |
|---|---|---|---|
| Picks | 1,330 | 1,330 | — |
| Mean of `r10` | +0.0028 (+0.002803) | +0.0028 | none |
| Median of `r10` | −0.0161 (−0.016150) | −0.0161 | none |
| Share above zero | 43.9% (0.439098) | 43.9% | none |
| Mean without the 13 largest | −0.0150 (−0.014999) | −0.0150 | none |

The 13 largest `r10` (1% of the picks) carry the whole positive mean. Without them the mean is −1.5%.

## With a stop and a target

The rule for each pick:
1. if its `dd` reaches the stop (`dd` ≤ −stop), it takes −stop;
2. else, if its `maxup` reaches the target (`maxup` ≥ target), it takes +target;
3. else it takes its `r10`.

The stop is checked first, because daily data cannot tell which level came first.

| Stop | Target | Mean, recomputed | The analyst's | Difference |
|---|---|---|---|---|
| none | 0.50 | +0.0042 (+0.004220) | +0.0042 | none |
| 0.10 | none | −0.0011 (−0.001079) | −0.0011 | none |
| 0.10 | 0.50 | −0.0024 (−0.002395) | −0.0024 | none |
| 0.20 | 0.50 | +0.0000 (+0.000021) | 0.0000 | none |
| 0.15 | 0.30 | −0.0058 (−0.005800) | −0.0058 | none |
| 0.08 | 0.24 | −0.0072 (−0.007236) | −0.0072 | none |

Every number agrees with the analyst's to four decimals.

**Reading.** No combination of stop and target turns the list into an edge before costs. A cost of about 1% a
trade, as in `docs/superpowers/specs/2026-10-13-squeeze-forward-test.md`, would make every row negative. These
are history with survivorship bias. The forward test registered in that spec is the clean check.
