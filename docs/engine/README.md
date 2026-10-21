# Engine index

**Status (2026-10-21, S2C): engine paused ([ADR 0021](adr/0021-swing-h1-line-closed-engine-paused.md)).** No M1
line passed. All four research lines are closed (below). Nothing starts without a new user decision that names a new
hypothesis family, and a pre-registered ADR that addresses the lessons in "Before any new line". Rules: [`scalp_engine/CLAUDE.md`](../../scalp_engine/CLAUDE.md). Historical design:
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
| T1 | Intraday tickets: pre-registration, data, signed tickets, evaluator (no outcomes) | PASS | 2252531 (`engine/t1`) | [T1](T1-report.md), ADR 0015, 0016 |
| T2 | Intraday tickets: survivorship, parity, development run | Stopped before confirmation (ORB 5 band, no defect); development H1 **−0.154R**, t −5.9 | a8d0a7b (`engine/t2`) | [T2](T2-report.md), ADR 0017 |
| S1 | Close intraday H1; swing pre-registration, data, signed tickets, evaluator (no outcomes) | PASS | 49063a0 (`engine/s1`; the on-hold attempt is the local `engine/s1-hold`) | [S1](S1-report.md), ADR 0018, 0019 |
| S2 | Swing development run, re-simulation check, one confirmation | Process PASS; verdict **NO-GO (a)**: confirmation H1 **−0.142R**, t −1.60 | ba18283 (`engine/s2`) | [S2](S2-report.md), ADR 0020 |
| S2C | Close the swing H1 line, pause the engine | See the S2C commits | `engine/s2c` | ADR 0021, this index |

Decisions: [ADR 0001](adr/0001-engine-foundations.md) … [ADR 0021](adr/0021-swing-h1-line-closed-engine-paused.md),
in `adr/`.

## The four research lines (all closed)

- **Intraday scalping models** (P0–P3D, ADR 0001–0007).
  - Hypothesis: an ML model ranks 09:45–11:30 decision points well enough to profit after costs at $500.
  - **Closed:** no gross edge. P3 PF before costs 0.73–0.84. P3D: signal present in 0 of 3 windows; the
    target-first label measures volatility.
- **Insider clusters** (I0–I2, ADR 0008–0011).
  - Hypothesis: stocks with a stockbot cluster (≥ 3 officers or directors, ≥ $100K, 30 days, at `2ffda0a`)
    outperform matched controls over 20 sessions from acceptance + 5 min.
  - **Closed, NO-GO (a):** confirmation mean abnormal return +1.08%, t 1.61 < 2.0; tradable −0.14% vs SPY.
- **Intraday H1 tickets** (T1–T2, ADR 0015–0018).
  - Hypothesis: the live screener's VWAP-pullback and bull-flag setups on stocks in play earn net R after IBKR
    costs at $2,500.
  - **Closed** (ADR 0018) on the development run: −0.154R, t −5.9; random entries −0.145R; setups vs random −0.006R.
    The confirmation window was never read for it.
- **Swing H1 tickets** (S0–S2, ADR 0012, 0013, 0019–0021).
  - Hypothesis: non-earnings gap-and-hold continuation over 20 sessions, as conditional bracket tickets.
  - **Closed, NO-GO (a)** (ADR 0021): confirmation −0.142R, t −1.60 (gross −0.109R); development +0.062R, t 0.62.
    S-a and S-b were negative in confirmation and are not promoted.

## Before any new line

ADR 0021 §3. A new hypothesis must address all four lessons before it is pre-registered.

1. **A different source of edge.** The gross edge was about 0 or negative on every set tested: intraday ML, insider
   clusters, intraday setups and swing gap-and-hold. Price patterns, insider clusters and ML on OHLCV features are
   exhausted here.
2. **Cost and power at $2,500, both shown first.**
   - Intraday costs about 0.2R per ticket.
   - Swing is cheap (about 0.03R) but too sparse to decide anything in 4–5 years (MDE 0.25R).
   - A break-even case and adequate power are required before any data work.
3. **Power from measured dispersion.** Use the existing controls and populations, never the new hypothesis's
   outcomes, and include the clustering design effect. S2 planned an MDE of 0.154R and realized 0.251R.
4. **Keep every proven safeguard:**
   - fill realism;
   - point-in-time earnings exclusion by header acceptance;
   - a survivorship universe;
   - requested-symbol fetches with held keys dropped;
   - the issued-basis control comparison;
   - independent re-simulation before confirmation.

## Reusable components (test counts as of S2C: engine 204, eval 155)

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
| L7 evaluator, intraday tickets (fills, exits, costs, book, CR1, T2 guard) | `scalp_eval/intraday_eval.py`, `intraday_t2.py` | `test_intraday_eval` 20 (golden set), `test_intraday_t2` |
| Swing ingestion, signals, tickets, controls (signed), survivorship | `scalp_engine/swing_data.py`, `swing_signals.py` | `test_swing_data`, `test_swing_signals` |
| L7 evaluator, swing brackets (A1 entry, A4 exits, corporate actions, book), S2 driver, independent re-simulation | `scalp_eval/swing.py`, `swing_s2.py`, `swing_resim.py` | `test_swing` (21 hand-computed golden cases), `test_swing_s2`, `test_swing_resim` |
| Insider rule reimplementation, clock, parity, inputs | `scalp_engine/insider_rule.py`, `insider_events.py`, `insider_inputs.py`, `i1_remedy.py` | `test_insider_rule` 17, `test_insider_events` 2, `test_insider_inputs` 3, `test_i1_remedy` 2 |
| Power / MDE | `scalp_engine/insider_power.py` (multiplier 2.8416 = t 2.0 + z 0.80) | `test_insider_power` 4 |
| Backup-push checks | `D:\scalp-env\push_check.py` (outside the repo; system Python; credentials via `envload`) | none |
| Credentials from the environment | `scalp_engine/envload.py` (process env, then the user environment; never prints values) | none dedicated |

Run the suites: engine `D:\scalp-env\engine\Scripts\python -m unittest discover -s scalp_engine/tests -t .`, and eval
the same with `scalp_eval`. stockbot (902 tests) runs on the system Python with `PYTHONTZPATH` and `PYTHONUTF8=1` set.

## Data store

- **Location:** `D:\scalp-data` (`SCALP_DATA_ROOT`), about 13 GB after T1–S1 (9.4 GB left free on `D:`).
  - Append-only Parquet, with a manifest per table in `manifests/`.
  - Set-aside files end in `.orphan-N` and are never read.
- **Main tables:**
  - Alpaca: `raw_bars_1d`, `raw_bars_1m`, `raw_quote_samples`, `corporate_actions`, `calendar`, `asset_snapshots`;
  - SEC: `insider_filings`, `insider_lines`, `filing_acceptance`, `issuer_sic`, `edgar_filings`, `cik_symbol_map`;
  - SEC (swing): `issuer_filings` (8-K items, annual forms), plus the 8-K header times in `filing_acceptance`;
  - derived: `insider_events`, `universe_b_daily`, `decision_points`, `feature_daily`, `oos_scores`, `swing_tickets`.
- **Results:**
  - Run outputs are in `derived/`: `t1/` (intraday sets), `t2/` (intraday runs), `s2/` (swing runs), and the I2
    ledger `trials_i2.jsonl`.
  - The T and S trials ledgers are tracked in git: `scalp_contracts/t_trials.jsonl` and `s_trials.jsonl`.
  - The P3 models and ledger are backed up in the private repository `danielsuisa/stockbot-data` (d2de648).
- **Alpaca market data may not be redistributed.** Nothing in the store is tracked in git. The tracked ledgers hold
  run records and hashes only, and `scalp_contracts/holdout_ledger.jsonl` is empty.

## Sealed windows and the ledger

- `legacy_2016_2021` (2016-01-01 → 2021-12-31): veto only. Unchanged and never run.
- `forward_2026q4` (2026-11-02 → 2027-01-29): confirmation role. Unchanged and **never ingested**. It is available as
  an untouched window for a future hypothesis, under a new user decision and its own ADR.
- The intraday confirmation window (2025-01-02 → 2026-10-16) was never read for intraday H1. The swing confirmation
  (2025-01-01 → 2026-09-17) has been read for the swing families.
- `scalp_contracts/holdout_ledger.jsonl`: **empty**. No unseal record exists.

## Resuming: prerequisites for any new line

1. **A new user decision** that lifts the pause and names a **new hypothesis family** (ADR 0021), with a pre-registered
   ADR that addresses "Before any new line".
2. **An ADR with a pre-registered spec and gate**, committed before any outcome is computed, with a trials ledger and
   one confirmation run.
3. **An untouched evaluation window.** The insider, intraday and swing windows used so far are not untouched for
   their signal families.
4. **Credentials only from the environment, through `envload`.** Never a value that appeared in chat; keys pasted in
   chat must be revoked.
5. Keep at least 8 GB free on `D:`. Back up branches under the P2 0.5 push checks; never merge.

## Branches

- **Pushed to `origin` and unmerged:** `engine/p0`, `p1`, `p2`, `p3`, `p3d`, `i0`, `i1`, `i2`, `i2c`, `s0`, `t1`,
  `t2`, `s1`, `s2` and `s2c`.
- `engine/s1-hold`, the on-hold S1 attempt of 2026-10-20, is local only.
- stockbot `main` is untouched by the engine.
- The live bot (`bot/`, `data/journal.json`, `.github/`) is unchanged, including its insider alerts and `/stats` (see
  ADR 0011 §2, lesson 4).
