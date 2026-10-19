# ADR 0007: Intraday scalping line closed

- **Status:** Accepted. Decided by the user on 2026-10-19 (I0 prompt, "User decisions").
- **Decision:**
  - The intraday scalping line is closed.
  - There will be no P3 retry and no P4.
  - Branches `engine/p0` … `engine/p3d` stay pushed to origin and unmerged, as reference.
  - Restarting intraday work needs a new user decision and a new ADR.
- **Context:**
  - [ADR 0004](0004-p2-failure-plan-change.md), [ADR 0005](0005-p3-labels-baseline-evaluator.md) and
    [ADR 0006](0006-p3-diagnostics.md);
  - the [P2](../P2-report.md), [P3](../P3-report.md) and [P3D](../P3D-report.md) reports.

## 1. Evidence chain

| Phase | Gate | Result | Source |
|---|---|---|---|
| P2: L2 screener | Next-90-min range ratio ≥ 1.5 vs random members | **FAIL**: 1.4304 (IEX-live). The ATR-matched ratio is 1.28, so most of the lift is volatility persistence | [P2 report](../P2-report.md) F1, F2 |
| P3: LightGBM baseline + L7 | Signal-level PF after costs > 1.2 at $500 Tiered in W1–W3 | **FAIL (a), no gross edge.** PF after costs 0.371 / 0.428 / 0.367; PF before costs 0.84 / 0.81 / 0.73 | [P3 report](../P3-report.md) gate table, F1–F3 |
| P3D: pre-registered diagnostics | ADR 0006 §6 rule | **Outcome A, no ranking signal.** Signal present in 0 of 3 windows; top-decile R 0.009 / 0.080 / −0.001 against a total friction of 0.57 / 0.52 / 0.52 R | [P3D report](../P3D-report.md) outcome, D1 |

Every gate was pre-registered. Each was evaluated once on committed code, and none was loosened after its result was
seen.

## 2. Lessons, each with its evidence

1. **A target-first triple-barrier label measures volatility, not direction.**
   - The P3 models reach an AUC of about 0.67 in every window, and the hit rate rises monotonically by decile (W1:
     0.033 → 0.226).
   - But mean frictionless R by decile is flat (W1: −0.005 … +0.029), and its decile order is reversed in W3 (ρ −0.59).
   - Source: [P3D report](../P3D-report.md) D1.
   - Consequence: any future label must be judged on the return it implies, not on AUC.
2. **Passive limit entries lose about 0.14 R per trade through adverse selection.**
   - The D2 waterfall's fill-selection step (S3 − S4) is +0.138 / +0.146 / +0.136 R.
   - Marketable entries on the same recommendations turn the gross PF to 1.21 / 1.15 / 1.01.
   - Source: [P3D report](../P3D-report.md) D2, D3.
   - Consequence: default to marketable or market-on-open entries. A limit entry needs its own fill-selection measurement.
3. **IEX carries a median 4.6% of consolidated volume** (p10 2.5%, p90 7.6%, over 391k member symbol-days).
   - Source: [P1 report](../P1-report.md) F4; [P1 data quality](../P1-data-quality.md).
   - Consequence: features built on IEX-live volume come from a different distribution than SIP features.
   - Real-time SIP did not help P3 either: `research` PF after costs ≤ `live` ([P3 report](../P3-report.md) F6).
4. **SIP data delayed 15 minutes is free** on the Alpaca free plan, through REST when `end` is at least 15 minutes old.
   Daily SIP bars of earlier sessions are always available.
   - Source: [ADR 0003](0003-features-regime-screener.md) §2 (`sip_delayed_15m`, `sip_eod`);
     [P0 audit](../P0-audit.md) data-source table.
5. **Intraday costs at $500 are about 0.4 R per trade.**
   - IBKR Tiered at $500 takes the before-cost expectancy from −0.07 / −0.10 / −0.16 R to −0.48 / −0.45 / −0.52 R.
   - Fixed pricing is worse (PF 0.15–0.25).
   - Source: [P3 report](../P3-report.md) F3; [P3D report](../P3D-report.md) D3.
   - Consequence: at this account size, a strategy needs either a much larger gross edge per trade or far fewer, larger
     moves per unit of commission. That is a reason to favour daily-horizon events.

## 3. Reusable components

These are tested, pushed on the `engine/*` branches, and independent of the intraday hypothesis.

| Component | Paths | Notes |
|---|---|---|
| L0 ingestion (Alpaca SIP bars, quote samples, calendar, corporate actions, universe) | `scalp_engine/ingest.py`, `scalp_engine/alpaca_client.py`, `scalp_engine/universe.py`, `scalp_engine/quality.py` | Resumable from manifests; survivorship-aware universe (`top500_v3`: pre-rename tickers from name-change corporate actions, delisted tickers from merger/delisting actions) |
| Append-only store and manifests | `scalp_engine/storage.py` | Parquet under `SCALP_DATA_ROOT`, never in git |
| Holdout guard (data access) | `scalp_engine/data_access.py`, `scalp_eval/data_access.py`, `scalp_contracts/holdout.yaml` | Every read and request passes `check_range`; enforced by `test_static` on both sides |
| Contracts | `scalp_contracts/` (`tables/`, `schemas/`, `features/`, `examples/`), `scalp_engine/contracts.py`, `scalp_eval/contracts.py` | Each side has its own loader (R4) |
| Ed25519 signing and verification | `scalp_engine/signing.py`, `scalp_eval/verify.py` | Signed recommendation envelopes are the only engine → evaluator channel |
| Deterministic replay | `scalp_engine/replay.py`, `scalp_engine/live_source.py` | Per-day SHA-256 of the event stream |
| L7 evaluator and cost model | `scalp_eval/fills.py`, `scalp_eval/fills_variants.py`, `scalp_eval/costs.py`, `scalp_eval/account.py`, `scalp_eval/metrics.py` | IBKR Tiered/Fixed with regulatory fees; cash account with T+1 settlement; marketable-entry variant from P3D |
| EDGAR submissions fetcher and dilution rules | `scalp_engine/proxies.py`, `scalp_engine/dilution.py` | SEC fair-access User-Agent from `SEC_USER_AGENT`; `acceptanceDateTime` handling; Form 25/15 deregistration check |
| Purged CV, trials ledger, LightGBM baseline | `scalp_engine/purge.py`, `scalp_engine/baseline.py` | Reusable only as tooling. The intraday models themselves are not reused |

Not reusable as is: `scalp_engine/features.py`, `scalp_engine/screener.py`, `scalp_engine/decision.py` (intraday
features, screener and decision grid). They stay on the branches as reference.

## 4. Holdout status

Unchanged by this ADR:
- `legacy_2016_2021` (2016-01-01..2021-12-31) stays `veto_only`.
- `forward_2026q4` (2026-11-02..2027-01-29) stays `confirmation`, and it is **unopened**.
- `scalp_contracts/holdout_ledger.jsonl` is empty: no unseal record has ever been written.
- Any change of role or use for a new hypothesis needs a user decision. It is laid out as E1 in
  [ADR 0008](0008-insider-validation-design.md).

## 5. Consequences

- Rule added to `scalp_engine/CLAUDE.md`: do not start P4 or retrain intraday models without a new user decision.
- `docs/engine/ARCHITECTURE.md` carries a status banner pointing here. Its content stays as the historical design.
- The next research line is the insider-signal validation (I0 scoping; ADR 0008, Proposed).
