# P3 report: labeling, LightGBM baseline and L7 evaluator (2026-10-18)

- **Branch:** `engine/p3`, made from `engine/p2`. Worktree: `D:\investor\.claude\worktrees\engine-p3`.
- **Data, models, recommendations and ledger:** under `D:\scalp-data`, outside git.
- **Spec:** [ADR 0005](adr/0005-p3-labels-baseline-evaluator.md).

**P3 gate: FAIL.** The signal-level PF after costs ($500, Tiered, `live`/H60) is **0.371 / 0.428 / 0.367** in W1 / W2 / W3,
against the required > 1.2.
- **Failure class: (a) no gross edge.** The before-cost PF is already 0.84 / 0.81 / 0.73, and the before-cost expectancy
  is negative in every window.
- Per ADR 0005 §10 and the architecture, this points to the data or labeling, not to costs.
- Nothing was re-run or re-tuned.

## Findings (new)

| # | Finding | Evidence | Impact | Recommendation |
|---|---|---|---|---|
| F1 | No configuration found an after-cost edge, even out of fold in training. The engine's own out-of-fold PF for `live`/H60 has a median of 0.51 (range 0.41–0.62); the other models 0.39–0.60 | Trials ledger, 108 final fits | The models rank, but the top decile is still net-negative | The baseline cannot be rescued by thresholds |
| F2 | The gross edge is negative on fills. Before-cost expectancy for `live`/H60 is −0.07 / −0.10 / −0.16 R, although the label geometry averages about 0 R over all decision points | Label base rates (mean gross R at H60, 2023–26: −0.001 to 0.01) vs. the evaluator's before-cost fills | **Hypothesis, untested:** fill-side adverse selection. The buy limit fills only when price trades below last + ½ spread, so fills skew toward falling prices. Other P3 fill rules may contribute | A P4 prerequisite: measure the label-geometry R of the same recommendations against their simulated fills before any model work |
| F3 | Costs at $500 are large but not the cause. Tiered costs take the PF from about 0.8 to 0.4; on Fixed it falls to about 0.15–0.25 | Per-window table below | Even a gross edge of +0.1 R would be erased at $500 | Any future gate at $500 needs a large gross edge |
| F4 | The cash-account constraints dominate at $500. Only 230–320 trades per window, and 15k–75k signals blocked for unsettled cash | Account level | Signal-level and account-level outcomes differ strongly | Track both |
| F5 | There is no break-even capital: every account loses at every capital, though less with more capital | Capital sensitivity: $25k still returns −38% / −24% / −13% per window | Consistent with (a), not (b) | — |
| F6 | The real-time SIP features (`research`) do not help. `research`/H60 after-cost PF is 0.349 / 0.372 / 0.340, against 0.371 / 0.428 / 0.367 for `live` | Per-window table | The P3 measurement gives **no case for a paid real-time feed** | Keep IEX-live (ADR 0002 §3 decision: no purchase) |
| F7 | The golden set caught a hand-arithmetic error in G11's Fixed P&L (I wrote 0.676619; the correct value is 0.6766325). The evaluator was right | Recorded in the test comment | Shows the golden set does its job | — |

## Implementation

**Stage 0:**
- **ADR 0004:** P2 recorded as failed; the plan change; the ADR 0003 outcomes.
- **Dilution v2:** `dilution_filing@2.0.0`. It fails closed on a missing CIK and excludes 424B2. v1 and the P2 snapshots
  are unchanged.
- **BLS:** requests use only `BLS_USER_AGENT` and are skipped when it is unset. The SEC identity is sent to sec.gov hosts
  only.
- **Top-up (0.2):** skipped. The run started on 2026-10-18, before the 2026-10-30 extended session had closed.

**Decision points:**
- 3,822,984 tradable member rows at the 22 decision times per session, over 784 sessions. 639 MB, built in 11 min.
- Label base rates at H60: 9.7–11.0% positive; stop first 24–26%.

**Models:**
- LightGBM 4.6.0, with numpy 2.3.5 and scipy 1.16.3, hash-pinned.
- 18 monthly retrains × 6 models (`live`/`research` × H30/H60/H90).
- **Trials:** 1,278 fits logged. That covers **38 distinct configuration × feature set × label combinations**: the 8 grid
  configurations for `live`/H60, plus each month's chosen configuration reused by the other five models.

**Recommendations:**
- 736,948, each signed with Ed25519 using a key at `D:\scalp-env\keys\p3_signing.pem`, outside the repo.
- All 736,948 signatures were verified by `scalp_eval` with the public key.
- 0 recommendations lacked a real spread.

**L7:** in `scalp_eval`, with no engine imports:
- IBKR Pro Tiered and Fixed costs, from https://www.interactivebrokers.com/en/pricing/commissions-stocks.php;
- the fill simulator;
- the signal-level and cash-account simulators;
- the metrics.

**Tests:**

| Suite | Count | Includes |
|---|---|---|
| `scalp_engine` | **113 OK** | P2 and P3 leakage, purge, and baseline end-to-end |
| `scalp_eval` | **69 OK** | The 12-trade golden set and the fill and account rules |
| stockbot | **902 OK** | — |

**Measured results:**

| Run | When (UTC) | Commit |
|---|---|---|
| Training | 15:42–18:02 | 63a7f3c |
| Evaluation (once) | 18:02:24–18:38:48 | 63a7f3c |

**Signal level, $500 per recommendation:**

| Model / window | Trades | Expired / rejected / skipped | PF before costs | E(R) before | PF Tiered | E(R) Tiered | 95% CI PF Tiered | Win rate (Tiered) | PF Fixed | E(R) Fixed |
|---|---|---|---|---|---|---|---|---|---|---|
| **live-h60 W1** | 44,341 | 7,694 / 252 / 0 | 0.841 | −0.068 | **0.371** | −0.482 | 0.362–0.380 | 31.5% | 0.142 | −0.994 |
| **live-h60 W2** | 17,953 | 3,045 / 9 / 0 | 0.811 | −0.099 | **0.428** | −0.454 | 0.413–0.444 | 33.4% | 0.197 | −0.894 |
| **live-h60 W3** | 19,312 | 3,197 / 57 / 1 | 0.730 | −0.156 | **0.367** | −0.521 | 0.354–0.380 | 29.7% | 0.164 | −0.971 |
| research-h60 W1 | 50,299 | 8,557 / 234 / 0 | 0.766 | −0.114 | 0.349 | −0.516 | 0.342–0.358 | 30.5% | 0.139 | −1.010 |
| research-h60 W2 | 43,502 | 7,150 / 141 / 0 | 0.740 | −0.128 | 0.372 | −0.472 | 0.363–0.381 | 30.4% | 0.166 | −0.898 |
| research-h60 W3 | 17,627 | 2,780 / 32 / 4 | 0.665 | −0.187 | 0.340 | −0.551 | 0.327–0.352 | 28.5% | 0.153 | −1.001 |

**Secondary labels.** The pattern is the same: before-cost PF below 1 everywhere.

| Model | Before costs W1 / W2 / W3 | Tiered W1 / W2 / W3 |
|---|---|---|
| live-h30 | 0.64 / 0.65 / 0.67 | 0.24 / 0.29 / 0.30 |
| live-h90 | 0.87 / 0.89 / 0.77 | 0.42 / 0.51 / 0.41 |
| research-h30 | 0.66 / 0.68 / 0.63 | 0.27 / 0.30 / 0.28 |
| research-h90 | 0.80 / 0.86 / 0.71 | 0.40 / 0.47 / 0.37 |

**Signal-level risk.** Max drawdown and the intraday Sharpe are sums over independent $500 trades. For `live`/H60 the
max drawdown is $54k / $24k / $28k and the Sharpe −11.5 / −13.2 / −12.3. Both are shown for completeness only.

**Account level, `live`/H60, Tiered, cash account (T+1, at most 3 open, 2% daily loss limit):**

| Start equity | W1: trades, PF, return | W2: trades, PF, return | W3: trades, PF, return |
|---|---|---|---|
| **$500** | 278, 0.12, −63.1% | 251, 0.24, −45.9% | 230, 0.31, −40.1% |
| $2,000 | 294, 0.25, −43.7% | 265, 0.47, −26.7% | 238, 0.57, −21.0% |
| $10,000 | 299, 0.32, −37.9% | 284, 0.58, −22.1% | 270, 0.68, −15.7% |
| $25,000 | 324, 0.35, −38.1% | 317, 0.55, −23.6% | 294, 0.72, −13.4% |

- Most signals are blocked for unsettled cash (about 45k in W1).
- The other models at $500 lose 43–65% per window.

**IEX vs. SIP features:** `live` − `research` after-cost PF is +0.022, +0.056 and +0.027 at H60. The real-time SIP features
do not improve the result.

**Run facts:**

| Item | Value |
|---|---|
| Run time | Decision points 11 min, training 139 min, evaluation 36 min, suites about 12 min |
| Disk | Data root 6.9 GB → 8.9 GB (recommendations 1.4 GB, decision points 0.64 GB); D: 13 GB free, so the 8 GB floor held |
| Regressions | None |

**Commits:**
- b884cab `docs: ADR 0004 (P2 failure, plan change) + dilution v2`
- 9fe92c5 `docs: ADR 0005 pre-registered P3 design`
- b9195e0 `feat(L3): labels, decision-point features + leakage tests`
- 749f44e `feat(L7): evaluator + tests`
- 63a7f3c `feat(L3): walk-forward LightGBM baseline + trials ledger`
- `docs: P3 report` (this commit)

**Backup push:** reported in the final message, after this commit.

## Gate

| Criterion | Result |
|---|---|
| ADR 0005 committed before the training code ran | **PASS**: 9fe92c5 at 15:27:01Z → training code 63a7f3c at 15:42:49Z → the run started right after |
| Leakage, purge, evaluator and golden-set tests; engine, eval and stockbot suites (stockbot 902) | **PASS**: 113 / 69 / 902 |
| W1, W2, W3: signal-level PF after costs > 1.2 at $500 Tiered, ≥ 100 trades | **FAIL**: 0.371 / 0.428 / 0.367, with 44,341 / 17,953 / 19,312 trades |
| No data files in git, allowed paths only, disk floor | **PASS**: only the empty ledger `.jsonl` is tracked; 22 files changed, all in the allowed paths; 13 GB free |

**P3 gate: FAIL. Classification (a): no gross edge.**

## Decisions needed

1. **What comes next.** Per the architecture's P3 NO-GO rule, the problem is the data or labeling, not the model. P4
   (networks) should not start on this baseline. Options:
   - (a) A diagnostic phase, with no new models, in this order:
     - decompose the gross loss into fill selection versus label-geometry R (F2);
     - test random-entry and market-order fill variants as controls;
     - check the barrier geometry against realized volatility.
   - (b) Redesign the labels and entry, for example a market or marketable entry, or a mean-reversion versus momentum
     target, with a new pre-registration and an untouched test period.
   - (c) Stop the intraday line.
   - *My recommendation: (a). It is cheap, uses no new data, and decides between (b) and (c) on evidence.*
2. **Proposed items in ADR 0005** that need your acceptance:
   - secondary models reuse the `live`/H60 configuration;
   - only the stop is checked in the fill bar;
   - the latest earlier sample is the spread fallback;
   - the $0.0030 exchange-fee assumption;
   - the pre-holiday TAF rate;
   - account equity counted at cost.
   - *My recommendation: accept all of these. None changes the verdict: the gross PF is below 1.*
3. **Where models and the ledger live.** Per D2, models, the journal and processed artifacts belong in the private
   `stockbot-data` repo from P3 on. Nothing has been pushed there; that needs your go.
4. **Credentials.** The Alpaca keys are still unset as environment variables; P3 needed none.
   *My recommendation: rotate the keys pasted on 2026-10-17 before the next vendor phase.*

## Next step: P4 prerequisites only (P4 not started)

Under the phase rules, P4 does not start after a failed P3 gate. Before any P4 work:
1. Your decision on item 1.
2. If (a): a pre-registered diagnostic spec (ADR 0006) using the existing P3 artifacts. No vendor requests are needed.
3. Decisions 2–4.
