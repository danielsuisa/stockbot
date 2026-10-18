# P3D report — pre-registered diagnostics of the P3 failure

Branch `engine/p3d` (from `engine/p3` b3a0452). Date 2026-10-18. Pre-registration: [ADR 0006](adr/0006-p3-diagnostics.md).

## Outcome: **A — no ranking signal**

The ADR 0006 §6 rule, applied by `scalp_eval/p3d_outcome.py` to the D1, D2 and P3 result files:

| Window | Top-decile R (≥ 0.10) | Spearman decile/R (≥ 0.6) | AUC (≥ 0.52) | Signal | Total friction R | Top decile > friction |
|---|---|---|---|---|---|---|
| W1 2025-05..10 | 0.0092 ✗ | 0.285 ✗ | 0.6739 ✓ | no | 0.5693 | no |
| W2 2025-11..2026-04 | 0.0796 ✗ | 0.479 ✗ | 0.6762 ✓ | no | 0.5243 | no |
| W3 2026-05..2026-10-16 | −0.0013 ✗ | −0.588 ✗ | 0.6535 ✓ | no | 0.5199 | no |

Signal present in 0 of 3 windows (A requires fewer than 2). Total friction = S1 mean R − P3 Tiered expectancy.

What this means: the live/H60 model ranks decision points by **how likely a barrier is touched**, not by **which barrier is touched**. AUC is about 0.67 in every window, and hit rate rises steadily from the bottom decile to the top (W1: 0.033 → 0.226). But mean frictionless R by decile is flat around zero (W1: −0.005 … +0.029, top +0.009), and in W3 the decile order is reversed (ρ −0.59). The AUC is real, but the label (target-first) is mostly a volatility label. A higher p picks names that move more, not names that move up. None of the other five models passes the top-decile threshold in two windows either (D1 below).

Even with a ranking signal, the best top decile in any window (0.08 R) is about one-seventh of the ~0.52–0.57 R total friction.

## D1 — ranking quality, all six models

Out-of-sample scores from the reproduced models, on every test decision point that passed the tradability filters (n W1 640,034, W2 556,284, W3 520,535). Frictionless R = `r_h<H>` label return in units of the stop distance.

| Model | Window | AUC | Top-decile R | Bottom-decile R | Spearman |
|---|---|---|---|---|---|
| live-h60 | W1 | 0.6739 | +0.0092 | −0.0049 | +0.285 |
| live-h60 | W2 | 0.6762 | +0.0796 | +0.0544 | +0.479 |
| live-h60 | W3 | 0.6535 | −0.0013 | +0.0336 | −0.588 |
| research-h60 | W1 | 0.6671 | +0.0156 | −0.0209 | +0.297 |
| research-h60 | W2 | 0.6755 | +0.0434 | +0.0586 | +0.394 |
| research-h60 | W3 | 0.6528 | −0.0094 | +0.0277 | −0.648 |
| live-h30 | W1 | 0.6733 | +0.0315 | −0.0016 | +0.588 |
| live-h30 | W2 | 0.6729 | +0.0260 | +0.0166 | +0.648 |
| live-h30 | W3 | 0.6658 | +0.0160 | +0.0256 | −0.224 |
| live-h90 | W1 | 0.6731 | +0.0231 | −0.0434 | +0.382 |
| live-h90 | W2 | 0.6763 | +0.1016 | −0.0147 | +0.697 |
| live-h90 | W3 | 0.6498 | −0.0038 | +0.0326 | −0.709 |
| research-h30 | W1 | 0.6724 | +0.0009 | +0.0106 | +0.236 |
| research-h30 | W2 | 0.6736 | +0.0254 | +0.0100 | +0.685 |
| research-h30 | W3 | 0.6663 | +0.0228 | +0.0277 | −0.176 |
| research-h90 | W1 | 0.6745 | +0.0284 | +0.0140 | +0.855 |
| research-h90 | W2 | 0.6621 | +0.0686 | +0.0419 | +0.515 |
| research-h90 | W3 | 0.6531 | −0.0028 | +0.0337 | −0.733 |

Only live-h90 W2 meets all three signal thresholds in one window. Every model has a negative Spearman in W3, the most recent window.

## D2 — loss decomposition of live/H60, S4 → S1 (mean R per trade, gross of commissions)

S4 is P3 itself (`fills.simulate`). S3 fills on the first eligible bar whatever its low, at limit − ½ delayed spread. S2 enters at that bar's open. S1 uses frictionless label-style exits.

| Window | n S4 | n S3–S1 | S4 | S3 | S2 | S1 | Fill selection (S3−S4) | Entry markup (S2−S3) | Exit frictions (S1−S2) | S1 same-bar conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| W1 | 44,341 | 51,947 | −0.0677 | +0.0702 | +0.0814 | +0.0874 | +0.1379 | +0.0112 | +0.0059 | 6 |
| W2 | 17,953 | 20,994 | −0.0989 | +0.0473 | +0.0639 | +0.0702 | +0.1462 | +0.0166 | +0.0063 | 2 |
| W3 | 19,312 | 22,491 | −0.1555 | −0.0195 | −0.0073 | −0.0015 | +0.1360 | +0.0122 | +0.0058 | 0 |

P3 order outcomes (S4): W1 filled 44,341 / expired 7,694 / rejected 252; W2 17,953 / 3,045 / 9; W3 19,312 / 3,197 / 57, plus 1 skipped. S4 equals the P3 before-cost expectancy.

**The largest gross loss element is limit-order fill selection: about −0.14 R per trade in every window.** Passive limits fill mainly when price trades down through them (adverse selection). The cost of the entry markup and exit rules is small (0.01–0.02 R). Commissions, fees and the $500 position size then cost a further ~0.39–0.41 R per trade (S4 → P3 net, D3).

## D3 — controls vs the model, live/H60, $500 Tiered

Random control: uniform draw (p = 0.5, seed `sha256(f"{window}|p3d-random-v1")`) from all filtered test decision points, with the same geometry and limit rules. Pools W1 640,020 / W2 556,283 / W3 520,534; drawn 52,287 / 21,007 / 22,567. Marketable control: the model's own recommendations, bought at the open of the first bar after t + 3 s plus ½ real spread, with no limit condition (95,861 envelopes). 287,583 signed envelopes were verified with the engine public key (key_id b4388290d66a62da).

| Set | Window | Trades | PF gross | PF net | Expectancy R gross | Expectancy R net |
|---|---|---|---|---|---|---|
| Model (P3) | W1 | 44,341 | 0.8414 | 0.3710 | −0.0677 | −0.4819 |
| Random | W1 | 44,721 | 0.7538 | 0.2764 | −0.0998 | −0.4844 |
| Marketable | W1 | 51,947 | 1.2092 | 0.5079 | +0.0814 | −0.3282 |
| Model (P3) | W2 | 17,953 | 0.8114 | 0.4280 | −0.0989 | −0.4541 |
| Random | W2 | 18,043 | 0.7414 | 0.3042 | −0.0941 | −0.4421 |
| Marketable | W2 | 20,994 | 1.1499 | 0.5751 | +0.0639 | −0.2907 |
| Model (P3) | W3 | 19,312 | 0.7301 | 0.3670 | −0.1555 | −0.5214 |
| Random | W3 | 19,519 | 0.7031 | 0.2872 | −0.1246 | −0.4732 |
| Marketable | W3 | 22,490 | 1.0131 | 0.4863 | −0.0073 | −0.3705 |

- **Model vs random:** the model's gross PF is a little higher than random in all three windows (+0.09, +0.07, +0.03). Expectancy is better than random in W1 only and worse in W2 and W3. Net, the model is no better than random in 2 of 3 windows.
- **Marketable:** removing the limit condition makes gross PF exceed 1 in all three windows (1.21 / 1.15 / 1.01). This is the same "fill selection" effect as in D2. But it is net-negative at $500 everywhere (PF 0.51 / 0.58 / 0.49), and W3 is gross-flat.

## D4 — barrier geometry (all filtered test decision points, label outcomes)

Shares of target-first / stop-first / time exit, and mean frictionless R:

| Window | H30 | H60 | H90 |
|---|---|---|---|
| W1 | 0.115 / 0.2693 / 0.6158 / +0.0059 | 0.0987 / 0.2451 / 0.6562 / +0.0107 | 0.0867 / 0.2244 / 0.6889 / +0.0157 |
| W2 | 0.1173 / 0.261 / 0.6217 / +0.0106 | 0.1033 / 0.2359 / 0.6608 / +0.0165 | 0.0923 / 0.2167 / 0.691 / +0.0179 |
| W3 | 0.1169 / 0.279 / 0.6041 / −0.0106 | 0.1 / 0.2561 / 0.6438 / −0.0105 | 0.0867 / 0.2354 / 0.6779 / −0.0076 |

Most decision points (60–69%) end on the time barrier. Stop-first is about 2.4× target-first, which is close to the 1.5:1 reward:risk ratio. The unconditional mean R is about ±0.01 to 0.02. The geometry by itself is neither a source of edge nor of loss.

## Process gate

| Item | Result |
|---|---|
| ADR 0006 committed before any diagnostic ran | **PASS.** e957e8a at 19:24:58Z. The first diagnostic (rescore) started at 19:27:02Z. |
| Each diagnostic ran once on committed code | **PASS.** See the table below. Each ran once, with no reruns. |
| No training except exact-reproduction refit, with hash match | **PASS.** 108/108 models (6 models × 18 months) refit from ledger final records. Every rebuilt recommendation set matched the stored P3 set by SHA-256 (`payload_set_hash`, which raises `ReproductionMismatch` on any difference). Training-row counts also matched. |
| R4 static tests | **PASS.** `test_static` on both sides: 5/5 engine and 5/5 eval, covering no cross-imports and `data_access` as the only reader. |
| Engine / eval / stockbot suites | **PASS** (after the fixture fix). Engine 116/116 OK on 254e8d8; the first run on 30b02df had 1 failure, see the deviation below. Eval 74/74 OK. Stockbot 902/902 OK. |
| No data files in git | **PASS.** `git ls-files` shows no data-format files. The only `.jsonl` is the empty `scalp_contracts/holdout_ledger.jsonl` (unchanged since P0). |
| Changes only in allowed paths | **PASS.** `git diff engine/p3...engine/p3d`: 11 files, +900/−4 before this report, all under `docs/engine/`, `scalp_engine/`, `scalp_eval/` and `scalp_contracts/`. |
| Disk floor (≥ 8 GB free on D:) | **PASS.** D: has 12 GB free (40 GB disk). `D:\scalp-data` is 9.3 GB. |
| No vendor requests; no sealed-window reads | **PASS.** All reads went through `data_access.check_range`. There was no network client use in P3D. |

Commit order and diagnostic runs (UTC, 2026-10-18):

| Time | Event |
|---|---|
| 19:24:58 | commit e957e8a `docs: ADR 0006 (pre-registered P3 diagnostics) + ADR 0005 accepted` |
| 19:26:51 | commit 61d3b83 `feat(L3): ranking, geometry diagnostics + control recommendation sets` |
| 19:27:02–19:38:31 | rescore (exact-reproduction refit, 108 models, OOS scores written) on 61d3b83 |
| 19:28:40 | commit 30b02df `feat(eval): loss decomposition + controls` |
| 19:39:22–19:39:24 | D1 on 61d3b83 |
| 19:39:25–19:39:26 | D4 on 61d3b83 |
| 19:39:27–19:41:10 | control recommendation sets (random, marketable), signed |
| 19:41:18–19:45:21 | D2 + D3 on 30b02df (287,583 envelopes verified) |
| 19:45:28 | outcome rule (`p3d_outcome`) → A |
| 19:55:56 | commit 254e8d8 `test(L3): make the decile Spearman fixture tie-free` (test only; see the deviation below) |

**Deviation (reported, not hidden).** The post-run full engine suite found one failing unit test in commit 61d3b83: `test_diagnostics.test_deciles_and_table`. The fixture gave deciles 1–9 the same R (−0.1). Their means then differed only by float rounding, so the decile Spearman ranked rounding noise (−0.135 against an asserted > 0.5).

This was a fixture flaw, not a code flaw. `diagnostics.py` is unchanged. On real data the decile means are distinct floats, and the D1 Spearman values were computed by the same committed code. The fix (254e8d8) checks Spearman on a strictly increasing R. It is an extra commit beyond the four pre-listed ones. No diagnostic was re-run.

Commit 30b02df only added evaluator files. It was committed while the rescore was running, and the rescore does not import it (R4).

Results are stored outside git in `D:\scalp-data\derived\`: `p3d_rescore.json`, `p3d_d1.json`, `p3d_d4.json`, `p3d_controls.json`, `p3d_d2d3.json`, `p3d_outcome.json`. Reproduced models are in `D:\scalp-data\models\p3\`, OOS scores in the `oos_scores` table, and control sets in `D:\scalp-data\recommendations\p3d\`.

## Pushes

1. **stockbot-data** (`danielsuisa/stockbot-data`, private, verified via `gh api`; no workflows directory). Pushed `engine/p3/trials_p3.jsonl`, `engine/p3/models_p3_meta.json` and `engine/p3/models/*.txt`: 110 files, 25.37 MB total, largest 0.8 MB. There were 0 secret or e-mail hits and no vendor-data files. Commit **d2de648** on `main`.
2. **origin** `engine/p3d`: backup push of this branch, including this report commit, under the P2 0.5 checks. The checks run before the report commit gave: no workflow triggers, 0 secret/e-mail hits in 155 new blobs, no data files, ALL CHECKS PASS. They are re-run on the final head before pushing. The push outcome and run check are recorded in the final message of the P3D session. Never merged.

## Decisions needed

1. **Stop or continue the intraday scalping line.** *Recommended: stop.* Outcome A means the P3 feature set ranks volatility, not direction. A retuned P3 or a P4 built on the same features and labels has no pre-registered reason to succeed, and the ~0.5 R friction at $500 is about 6× the best top decile observed. Not recommended: a P3 retry, P4 or new model families on this data.
2. **If you want to continue anyway, the only lead the diagnostics support** is entry mechanics, not modelling. Marketable entries remove the −0.14 R fill-selection loss and are gross-positive in W1/W2, but they remain net-negative at $500. Any such work would need: a new ADR; a direction-aware label (e.g. sign of R rather than target-first); and an account size where commissions are not about 0.4 R per trade. *Recommended: do not pursue it unless the account size changes.*
3. **Credential rotation.** *Recommended: rotate the Alpaca paper key now.* The Alpaca key/secret still come only from this chat and have never been in the repo, but they appeared in a chat transcript.
4. **Branches.** *Recommended: keep `engine/p0`…`engine/p3d` as unmerged, pushed reference branches.* The stockbot `main` is untouched.

## Next step (prerequisites only)

There is no P4 prompt. Before any further engine work:

- a user decision on item 1;
- if continuing, a new ADR that fixes the direction-aware label, the entry mechanics and the account size before any code;
- rotated credentials.
