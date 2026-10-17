# Scalping engine: rules for agents

These rules apply to `scalp_engine/`, `scalp_eval/`, `scalp_contracts/` and `docs/engine/`.
Design: `docs/engine/ARCHITECTURE.md`. Decisions: `docs/engine/adr/`.

## Rules that never break
- **R1. Recommendations only.** No code path places, modifies or cancels orders. Broker clients are read-only.
- **R2. The holdout is sealed.** Do not load, plot, summarize or compute anything on rows whose event time falls in
  `scalp_contracts/holdout.yaml`. That includes warm-up reads and "just a look". Metadata only: paths, sizes, schemas,
  counts, min/max timestamps.
- **R3. Zero spend.** No paid API, data, model or cloud resource without the owner's explicit approval.
- **R4. Independent evaluator.**
  - `scalp_eval` never imports `scalp_engine`, and `scalp_engine` never imports `scalp_eval`.
  - Each side has its own loaders for the shared contract files. Neither side imports stockbot's `bot` package.
- **R5. Baseline before network.** A deep model ships only if it beats LightGBM on the same features out of sample.
- **R6. Realistic costs** in every measurement: actual spread, volume-dependent slippage, IBKR commissions, and 2–5 s
  of latency.

## Reading data: the holdout guard
- Read tabular data **only** through `data_access.read_table(root, start, end)` on your own side.
- `start` and `end` are timezone-aware datetimes, and the range is half-open. A range that could touch the holdout
  raises `HoldoutSealedError`; so does a missing bound.
- Never call polars, pandas, pyarrow, duckdb, csv or sqlite3 readers anywhere else. `tests/test_static.py` fails if
  you do.
- Never catch or suppress `HoldoutSealedError`, never widen the allowlist, and never edit `holdout.yaml` or the
  ledger. Only the owner writes `scalp_contracts/holdout_ledger.jsonl`, and only for the single P7 run.
- If a task seems to need holdout rows, stop and ask.

## Phase-gate protocol
- **One phase per run.** Do only the phase you were given.
- **Close at the gate.** Evaluate the phase gate (ARCHITECTURE §4), write `docs/engine/P<n>-report.md`, report, and
  stop.
- **A failed gate means stop and report the failing condition.** Never work around it: no loosened thresholds, no
  re-runs until it passes, no relabelling.
- **Never mark an ADR Accepted.** Only the owner does.

## stockbot is live: do not touch it
- Never modify these: `bot/` (including `intraday`, the squeeze screener, the insider scans and the Telegram
  listener), `tests/`, `.github/workflows/`, `data/`.
- Engine code never goes under `bot/`. A merge touching `bot/**` redeploys the live listener.
- Never start a second Telegram `getUpdates` consumer: it collides with the live listener (409).

## Git
- Work on an `engine/p<n>` branch with local commits only. **Never push or merge without the owner.**
- No secrets in git. Keys come from environment variables (`SCALP_ENGINE_SIGNING_KEY`, `TG_TOKEN`, `ALPACA_*`) or from
  paths outside the repo; the signing code refuses key paths inside the repo.
- Market data is never committed: Alpaca forbids redistribution. Store it under the gitignored `.cache/`.

## Tests
- From the repo root: `python -m unittest discover -s scalp_engine/tests -t .`, and the same for `scalp_eval/tests`.
- stockbot's suite: `python -m unittest discover -s tests`. It must stay green and unchanged.
- On Windows, set `PYTHONTZPATH=<tzdata>/zoneinfo` and `PYTHONUTF8=1` first.
- Dependencies: `scalp_engine/requirements.txt` and `scalp_eval/requirements.txt`. Any new one needs a justification
  in an ADR.
