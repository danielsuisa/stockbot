# Engine index

**Status (2026-10-21, S1): engine active only for the swing-ticket line
([ADR 0012](adr/0012-engine-resumed-swing-tickets.md), [ADR 0013](adr/0013-swing-ticket-validation-design.md)).**
Closed lines: intraday models ([ADR 0007](adr/0007-intraday-line-closed.md)), intraday H1 tickets
([ADR 0018](adr/0018-intraday-h1-ticket-line-closed.md)), insider signals
([ADR 0011](adr/0011-insider-line-closed-engine-paused.md)). Rules: [`scalp_engine/CLAUDE.md`](../../scalp_engine/CLAUDE.md). Historical design:
[ARCHITECTURE.md](ARCHITECTURE.md).

## Phases

| Phase | Scope | Gate | Head (branch) | Report |
|---|---|---|---|---|
| P0 | Repo audit, contracts, holdout lock | PASS | 07c4c50 (`engine/p0`) | [P0](P0-report.md), [audit](P0-audit.md) |
| P1 | L0 ingestion, deterministic replay | PASS | 48e6f24 (`engine/p1`) | [P1](P1-report.md), [data quality](P1-data-quality.md) |
| P2 | Features, regime, L2 screener | FAIL: ratio 1.43 < 1.5 | 9828781 (`engine/p2`) | [P2](P2-report.md) |
| P3 | Labels, LightGBM baseline, L7 evaluator | FAIL (a): no gross edge | b3a0452 (`engine/p3`) | [P3](P3-report.md) |
| P3D | Pre-registered P3 diagnostics | Outcome A: no ranking signal | 908f25d (`engine/p3d`) | [P3D](P3D-report.md) |
| I0 | Close intraday; insider scoping | PASS | 6f7161a (`engine/i0`) | [I0](I0-report.md), [audit](I0-audit.md) |
| I1 | Pre-registration, ingestion, rule, parity, events | FAIL on duplicate keys, then PASS after the I2 remedy | 28a47e9 (`engine/i1`) | [I1](I1-report.md), [addendum](I1-addendum.md) |
| I2 | I1 remedy; one confirmation measurement | Process PASS; verdict **NO-GO (a)** | aeea512 (`engine/i2`) | [I2](I2-report.md) |
| I2C | Close the insider line, pause the engine | See the I2C commits | `engine/i2c` | ADR 0011, this index |
| S0 | Swing tickets scoping | ADR 0012, 0013 Accepted | 886693e (`engine/s0`) | [S0](S0-report.md), [audit](S0-audit.md) |
| S1 | Swing tickets implementation | **On hold** until after T2 (ADR 0015) | `engine/s1` (local) | — |
| T1 | Intraday tickets: pre-registration, data, signed tickets, evaluator (no outcomes) | PASS | `engine/t1` | [T1](T1-report.md) |
| T2 | Intraday tickets: survivorship, parity, development run | Stopped before confirmation (ORB 5 band, no defect); development H1 −0.154R, t −5.9 | `engine/t2` | [T2](T2-report.md), ADR 0017 |

Decisions: [ADR 0001](adr/0001-engine-foundations.md) … [ADR 0018](adr/0018-intraday-h1-ticket-line-closed.md),
in `adr/`.

## The two research lines

- **Intraday scalping** (P0–P3D, ADR 0001–0007).
  - Hypothesis: an ML model ranks 09:45–11:30 decision points well enough to profit after costs at $500.
  - **Closed:** no gross edge. P3 PF before costs 0.73–0.84. P3D: signal present in 0 of 3 windows; the
    target-first label measures volatility.
- **Insider clusters** (I0–I2, ADR 0008–0011).
  - Hypothesis: stocks with a stockbot cluster (≥ 3 officers or directors, ≥ $100K, 30 days, at `2ffda0a`)
    outperform matched controls over 20 sessions from acceptance + 5 min.
  - **Closed, NO-GO (a):** confirmation mean abnormal return +1.08%, t 1.61 < 2.0; tradable −0.14% vs SPY.

## Reusable components (test counts as of T2: engine 194, eval 119)

| Component | Paths | Tests |
|---|---|---|
| L0 ingestion (Alpaca, SEC) | `scalp_engine/ingest.py`, `alpaca_client.py`, `universe.py`, `quality.py`, `proxies.py`, `insider_data.py`, `insider_prices.py` | `test_l0` 21, `test_proxies` 9, `test_insider_data` 7, `test_insider_prices` 8 |
| Holdout guard | `scalp_engine/data_access.py`, `scalp_eval/data_access.py`, `scalp_contracts/holdout.yaml` | `test_holdout` 25 + 25, `test_parquet_guard` 3, `test_static` 5 + 5 |
| Contracts (tables, schemas) | `scalp_contracts/`, `scalp_engine/contracts.py`, `scalp_eval/contracts.py` | `test_contracts` 11 + 11 |
| Ed25519 signing | `scalp_engine/signing.py` (recommendations, artifact digests), `scalp_eval/verify.py` | `test_signing` 7, `test_verify` 5 |
| Replay | `scalp_engine/replay.py`, `live_source.py` | `test_replay` 7, `test_replay_days` 2 |
| L7 evaluator, intraday | `scalp_eval/fills.py`, `fills_variants.py`, `costs.py`, `account.py`, `metrics.py` | `test_l7` 17, `test_p3d` 5 |
| L7 evaluator, daily (event study, controls, trades vs SPY) | `scalp_eval/insider_study.py` | `test_insider_study` 19 (15 hand-computed golden cases) |
| Live screener rules (copied verbatim from `bot/intraday.py` at 09bfa55), replay, tickets, controls, parity, leakage | `scalp_engine/intraday_rules.py`, `t1_replay.py` | `test_intraday_rules` 13, `test_t1_replay` 8 |
| T1 ingestion (top-up, stage-1 inputs, top 20, NBBO grid, coverage, remedy) | `scalp_engine/t1_data.py` | `test_t1_data` 7 |
| L7 evaluator, intraday tickets (fills, exits, costs, book, CR1, T2 guard) | `scalp_eval/intraday_eval.py` | `test_intraday_eval` 20 (golden set) |
| Insider rule reimplementation, clock, parity, inputs | `scalp_engine/insider_rule.py`, `insider_events.py`, `insider_inputs.py`, `i1_remedy.py` | `test_insider_rule` 17, `test_insider_events` 2, `test_insider_inputs` 3, `test_i1_remedy` 2 |
| Power / MDE | `scalp_engine/insider_power.py` (multiplier 2.8416 = t 2.0 + z 0.80) | `test_insider_power` 4 |
| Backup-push checks | `D:\scalp-env\push_check.py` (outside the repo; system Python; credentials via `envload`) | none |
| Credentials from the environment | `scalp_engine/envload.py` (process env, then the user environment; never prints values) | none dedicated |

Run the suites: engine `D:\scalp-env\engine\Scripts\python -m unittest discover -s scalp_engine/tests -t .`, and eval
the same with `scalp_eval`. stockbot (902 tests) runs on the system Python with `PYTHONTZPATH` and `PYTHONUTF8=1` set.

## Data store

- **Location:** `D:\scalp-data` (`SCALP_DATA_ROOT`), about 11 GB.
  - Append-only Parquet, with a manifest per table in `manifests/`.
  - Set-aside files end in `.orphan-N` and are never read.
- **Main tables:**
  - Alpaca: `raw_bars_1d`, `raw_bars_1m`, `raw_quote_samples`, `corporate_actions`, `calendar`, `asset_snapshots`;
  - SEC: `insider_filings`, `insider_lines`, `filing_acceptance`, `issuer_sic`, `edgar_filings`, `cik_symbol_map`;
  - derived: `insider_events`, `universe_b_daily`, `decision_points`, `feature_daily`, `oos_scores`.
- **Results:** run outputs and the trials ledgers (`trials_i2.jsonl`) are in `derived/`. The P3 models and ledger
  are backed up in the private repository `danielsuisa/stockbot-data` (d2de648).
- **Alpaca market data may not be redistributed.** Nothing in the store is tracked in git; the only tracked
  data-format file is the empty `scalp_contracts/holdout_ledger.jsonl`.

## Sealed windows and the ledger

- `legacy_2016_2021` (2016-01-01 → 2021-12-31): veto only. Unchanged and never run.
- `forward_2026q4` (2026-11-02 → 2027-01-29): confirmation role. Unchanged and **never ingested**. It is available as
  an untouched window for a future hypothesis, under a new user decision and its own ADR.
- `scalp_contracts/holdout_ledger.jsonl`: **empty**. No unseal record exists.

## Resuming: prerequisites for any new line

1. **A new user decision** that lifts the pause for a named line (ADR 0011).
2. **An ADR with a pre-registered spec and gate**, committed before any outcome is computed, with a trials ledger and
   one confirmation run.
3. **An untouched evaluation window.** The insider and intraday windows used so far are not untouched for their
   signal families.
4. **Credentials only from the environment, through `envload`.** Never a value that appeared in chat; keys pasted in
   chat must be revoked.
5. Keep at least 8 GB free on `D:`. Back up branches under the P2 0.5 push checks; never merge.

## Branches

- `engine/p0`, `p1`, `p2`, `p3`, `p3d`, `i0`, `i1`, `i2` and `i2c` are pushed to `origin` and **unmerged**.
- stockbot `main` is untouched by the engine.
- The live bot (`bot/`, `data/journal.json`, `.github/`) is unchanged, including its insider alerts and `/stats` (see
  ADR 0011 §2, lesson 4).
