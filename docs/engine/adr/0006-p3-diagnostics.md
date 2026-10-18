# ADR 0006: Pre-registered diagnostics of the P3 failure (P3D)

- **Status:** Accepted for every item the P3D prompt specifies (user, 2026-10-18). Choices this ADR adds are marked
  **Proposed**.
- **Commit order:** this ADR, including the decision rule in §6, is committed before any diagnostic is computed. Each
  diagnostic runs once, on committed code. No threshold changes after results are seen.
- **Context:**
  - [ADR 0005](0005-p3-labels-baseline-evaluator.md) (the P3 design, all items Accepted);
  - [P3 report](../P3-report.md) (FAILED, classification (a)).

**Question:** does the P3 model rank decision points at all, and if it does, where is the edge lost?

## 1. Scores for every test decision point (no training)

- P3 stored the predicted p only for recommended decision points, and it did **not** store its final model files.
- Per the P3D rules, each of the 108 final models (6 models × 18 test months) is **refit** from the P3 trials-ledger
  `final` record:
  - the same training rows (`baseline.load` + the walk-forward split);
  - the same configuration and round count;
  - the same fixed and deterministic LightGBM parameters.
- **Acceptance test:** a refit model is accepted only if its recommendations for that month reproduce the stored set
  exactly.
  - The stored set is the canonical JSON of the payloads sorted by `rec_id`, taken from the signed P3 envelopes; its
    SHA-256 is compared.
  - The refit's recommendations are built with the same threshold (from the ledger) and the same `recommendation()`
    function.
  - **Any mismatch stops the phase.** Nothing is reported from a model that does not reproduce.
- **Outputs of accepted models:**
  - their scores for **all** test decision points, written to the `oos_scores` table;
  - the model files, under `D:\scalp-data\models\p3\`.
- The refit is a reproduction, not training: no parameter, threshold or row changes.

## 2. D1: ranking signal (engine; OOS test windows W1, W2, W3 only)

**Population:** for each window and each of the six models, every test decision point whose label for that model's H
is not null.

**Measures:**
- **AUC** of p against the label (Mann–Whitney, ties at half).
- **p-deciles** (Proposed):
  - rank by p ascending, ties broken by (symbol, `event_at`);
  - decile = ⌊10·(rank − 1)/n⌋ + 1, from 1 (lowest p) to 10 (top).
- **Calibration:** per decile, mean p against the realized hit rate (mean label).
- **Frictionless R:** per decile, the mean frictionless R. This is the stored P3 label R `r_h<H>`:
  - +1.5 if the target is hit first;
  - −1.0 if the stop is hit first;
  - otherwise (close at H − entry) ÷ (entry·σ_H).
- **Spearman** correlation between the decile index (1..10) and the decile mean R.

**Primary:** `live`/H60. The other five models are reported only.

## 3. D2: loss decomposition (evaluator; `live`/H60 recommended trades; gross of commissions)

- **Population:** all live/H60 P3 recommendations, read from the signed P3 envelopes and verified with the public key.
- **R per trade (Proposed):** (exit − entry) ÷ (limit − stop), per share. This uses the recommendation's own risk unit
  at every step, so the steps stay comparable.
- **Sizing:** shares are irrelevant gross of commissions. Liquidity rejection uses the P3 $500 sizing.

Each step removes one rule from the one before it:

| Step | Rule |
|---|---|
| **S4** | The P3 before-cost simulation exactly as run (`fills.simulate`, before-cost prices): a fill only when a bar's low < limit within the TTL; the fill bar checks the stop only; gap exit at the open; stop at the stop; target only strictly through; time exit at the close; entry price limit − ½ delayed spread |
| **S3** | As S4, but every recommendation fills on the **first eligible bar** (the first bar opening after *t* + 3 s, within the TTL and before the close), whatever its low. This removes fill selection. **Proposed:** the entry price keeps S4's convention (limit − ½ delayed spread), so only the fill condition changes. Liquidity rejection is kept |
| **S2** | As S3, but the entry price is the **open** of that first bar. This removes the entry markup |
| **S1** | As S2, with frictionless exits: the stop exits at the stop price exactly, gaps included; the target counts when high ≥ target, **including the entry bar**; if stop and target are hit in the same bar the stop wins; the time exit is at the bar close |

- **Reported per window:** the number of trades and the mean R at S4, S3, S2 and S1, and the deltas S3 − S4, S2 − S3 and
  S1 − S2.
- **Same-bar conflict count:** bars, from the entry bar on, where low ≤ stop and high ≥ target, under the S1 rules.
- **Check:** S4's mean R must equal the P3 before-cost expectancy. Its denominator is the same, limit − stop.

## 4. D3: controls (the engine generates signed sets; the evaluator simulates with the P3 rules, gross and net at $500 Tiered)

**Random entry:**
- Per window, draw k decision points, where k is the number of live/H60 recommendations in that window (W1 52,287;
  W2 21,007; W3 22,567).
- Draw uniformly without replacement from all test decision points of the window for which a P3 recommendation can be
  built (`last_price`, `spread_nbbo_delayed` and `sigma_1m_5d` not null).
- Rows are sorted by (`event_at`, symbol) before drawing. The seed is int(sha256(`f"{window}|p3d-random-v1"`)).
- The geometry is the same as P3 (`recommendation()` at H = 60), with p = 0.5 (Proposed).
- `model_version` is `p3d-random-h60-<window>`.

**Marketable entry:**
- The live/H60 P3 recommendations, re-emitted with `model_version` `p3d-marketable-h60-<YYYY-MM>`. Their payloads are
  otherwise identical.
- **Proposed:** the recommendation contract has no market type, so the evaluator applies the marketable rule to this
  set by its `model_version`.
- **The rule:** buy at the open of the first SIP bar opening after *t* + 3 s (before the close), plus ½ the real spread
  at *t*. There is no limit condition.
- Shares, liquidity and exits are as in P3. Sizing uses the payload limit and stop.

**Reported per window:** model (P3), random and marketable, each with:
- trades;
- PF and expectancy R, before costs;
- PF and expectancy R, after Tiered costs at $500.

## 5. D4: geometry (engine)

- **Population:** all test decision points per window, for H 30, 60 and 90.
- **Reported:**
  - the share of target-first (label 1);
  - the share of stop-first (R = −1.0);
  - the share of time exits (the rest);
  - the mean frictionless R.

## 6. Decision rule (fixed now; applied to live/H60)

**Signal present in a window** when all three hold:
- top-decile (decile 10) mean frictionless R ≥ **+0.10 R**;
- decile Spearman ≥ **0.6**;
- AUC ≥ **0.52**.

**Total friction in a window:**
- = mean R at S1 (D2) − the P3 mean R after Tiered costs at $500, for live/H60.
- The P3 values are read from `D:\scalp-data\derived\p3_evaluation.json`: −0.4819, −0.4541 and −0.5214 for W1, W2 and W3.

**Outcomes:**

| Outcome | Condition | Consequence |
|---|---|---|
| **A: no ranking signal** | Signal present in fewer than 2 of the 3 windows | Recommend stopping the intraday line: no P3 retry, no P4 |
| **B: signal but not economic** | Signal present in ≥ 2 windows, but top-decile frictionless R < total friction in ≥ 2 windows | Report the break-even friction. The next decision, capital or horizon, is the user's |
| **C: signal, possibly economic** | Signal present, and top-decile frictionless R > total friction in ≥ 2 windows | D2 and D3 name the execution element to fix. The next step is a redesign pre-registered on a new untouched period (2022-03..2023-07, which needs ingestion); not P4 |

The outcome is computed by a small committed script (`scalp_eval/p3d_outcome.py`, reading only result JSON files) from the two outputs: the engine's D1 JSON
and the evaluator's D2 JSON. No code is shared across the R4 boundary.

## 7. Storage

- The trials ledger, the accepted model files and their metadata go to the private `danielsuisa/stockbot-data`.
- Pushed only if all of these hold:
  - the repo is verified private;
  - there is no vendor data;
  - each file is < 100 MB and the push totals ≤ 500 MB, or else the ledger alone is pushed;
  - the secret scan passes.
