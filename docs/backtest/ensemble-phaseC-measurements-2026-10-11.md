# Recommendation algorithm: phase C measurements (counts only)

Spec: `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md`, sections 6.4, 6.2 and 4.2 (the owner's
rulings C1-C25, 2026-10-11). Plan: `docs/superpowers/plans/2026-10-11-recommendation-algorithm-phase-c.md`.
Code: `bot/ensemble_data.py`, `bot/signals.py`, `bot/ensemble_counts.py`. Raw output:
`docs/backtest/ensemble-phaseC-measurements-2026-10-11.json`.

**Counts only.** No fill, exit or outcome of any ticket was computed. The simulation functions are not called by
this code, and its tests patch them to fail.

## The run

| | |
|---|---|
| GitHub Actions run | 38124880834 (code `18ff0b7`), passed, 4,642 s |
| Requests | Alpaca 7,117 (of which 3,946 quotes); SEC quarterly zips 48; EDGAR index pages 13,766 |
| Tests | 654 offline tests, all passing |

## Firings per signal, against phase A

| Signal | Phase A | Phase C (`bot/signals.py`) | Difference |
|---|---:|---:|---:|
| S1 | 1,511 | 1,558 | +3.11% |
| S2 | 7,979 | 8,015 | +0.45% |
| S3 | 13,355 | 13,438 | +0.62% |
| S4 | 14,679 | 14,774 | +0.65% |

**No difference is above 5%**, so no explanation is required by the rule. The differences come from the rules of 4.1
and 4.2:
- **The rename rule** adds firings on renamed symbols: S1 17, S2 43, S3 89, S4 76.
- **S1 by CIK:** 38 S1 firings use the CIK's other universe symbol.
- **S2's bar on the previous session:** removed 0 S2 firings.
- **8-K/A:** dropped as a release (phase A.1: one stock-year).

The warm-up excluded S3 369 and S4 4 entries.

## 1a. K (spec 6.4, ruling C4: r_s = the larger of the two ratios)

3,470 symbols' bars end for good before 2025-04-01 (rule C3). In the 12-month windows before their
ends they have 121,205 universe stock-sessions and 6,927 stock-months. All
symbols together have 4,142,387 stock-sessions and 211,660 stock-months.

| Signal | Firings in the windows | Firings, all | r per stock-month | r per stock-session | r_s | w_s (provisional) |
|---|---:|---:|---:|---:|---:|---:|
| S1 | 20 | 1,558 | 0.392 | 0.439 | **0.439** | 0.0403 |
| S2 | 178 | 8,015 | 0.679 | 0.759 | **0.759** | 0.2126 |
| S3 | 641 | 13,438 | 1.458 | 1.630 | **1.630** | 0.3559 |
| S4 | 290 | 14,774 | 0.600 | 0.671 | **0.671** | 0.3912 |

| | Value | Against 0.03 R |
|---|---:|---|
| K (provisional, phase A's shares) | 1.2243 | b = K x 0.41% = **0.0050 R** |
| K_max (the largest max(1, r_s): S3) | 1.6302 | b_max = **0.0067 R** |

**Result: b_max 0.0067 R <= 0.03 R**, so the run went straight on to 1b (ruling 5). The final K uses the
issued-ticket shares and is computed in the blind run by the same code.

Observation, not a judgement: S3 (momentum near the 52-week high) fires 1.5-1.6 times as often in a stock's last 12
months of bars as on an average stock. S1, S2 and S4 fire less often there. Max(1, r_s) counts only the excess, so
only S3 raises K.

## 1b. Quote coverage (spec 6.2, rulings C7-C10)

The sample: 2,000 firings, at most 50 per signal and year, drawn with `random.Random(20261011)`. Each
firing was run through section 3's plan at $100 risk with the signal's mode and H, on bars through D.

| Signal | Firings (population) | Sampled | Passing the quote-free refusals | No usable guard quote | Cost guard above 10% | Weighted quote-based share | No usable entry quote (reported only) |
|---|---:|---:|---:|---:|---:|---:|---:|
| S1 | 1,558 | 500 | 492 | 1 | 0 | 0.22% | 10 |
| S2 | 8,015 | 500 | 493 | 0 | 0 | 0.00% | 5 |
| S3 | 13,438 | 500 | 496 | 2 | 6 | 1.47% | 18 |
| S4 | 14,774 | 500 | 495 | 0 | 0 | 0.00% | 3 |

- **Quote-free refusals in the sample:** 24 ("shares below 2": R above $50 at $100 of risk).
- **Weighted overall share of quote-based refusals:** **0.53%**, with a standard error of
  **0.20%**, against the limit of 5%. It does not stop.
- **Per year:** 2017: 0.00% · 2018: 0.00% · 2019: 0.98% · 2020: 0.00% · 2021: 0.74% · 2022: 0.57% · 2023: 0.00% · 2024: 1.49% · 2025: 0.64% · 2026: 0.95%.
- **No usable quote 5 minutes after the next session's open:** 1.82% of the passing firings.
  In the backtest those tickets are charged the guard spread instead (section 5); this is not a refusal.

**Result: the quote coverage supports the comparability condition** (an early warning only; the condition itself is
judged in the blind run).

## Known property (ruling C14)

The count of firings not issued because a ticket of the same signal was already open depends on how long earlier
tickets stayed open. It is one of the refusal counts in the whitelist, and no rule or decision depends on it.

## Next

**Stop for the owner's review of both reports.** Part 2 (the build on synthetic bars) starts only after it.
