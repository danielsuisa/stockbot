# P0 report: repo audit and data contracts (2026-10-17)

- **Branch:** `engine/p0`, cut from `origin/main` 1939f52. Local commits only; nothing pushed or merged.
- **Worktree:** `D:\investor\.claude\worktrees\engine-p0`. The `D:\investor` checkout of `main` is stale at 2ffda0a
  (371 commits behind GitHub) and was left untouched.

## Audit

Full findings are in [P0-audit.md](P0-audit.md).

| # | Finding | Evidence | Impact | Recommendation |
|---|---|---|---|---|
| A1 | stockbot is stdlib-only; 902 tests pass. A push to a non-`main` branch runs no workflow that uses secrets or Telegram | `tests.yml:3-8,29`; `intraday-count.yml:6-9` (no branch filter, but no secrets) | Engine dependencies would be the repo's first | Keep them in per-package `requirements.txt`; keep engine code out of `bot/` |
| A2 | ZBA ingestion and replay are a good base but not deterministic: wall-clock default end date, silent network fetch on a cache miss, partial live days can be cached. Seeding and tie order are fine | `ticket_backtest.py:950-955`; `alpaca.py:92,131,196-296`; `ticket_backtest.py:366` | Replay cannot be bit-reproduced as it stands | L0 **ADAPT**; L7 fills and statistics **REUSE as copied reference**; ORB signal **DISCARD** |
| A3 | Holdout = NY sessions 2016-01-04..2021-12-31, both inclusive. Evaluated **once** (run 38003155445, 3cbde91, **NO-GO**), then analysed post hoc. Dev runs read Nov–Dec 2021 for warm-up | `zba_v2.py:24,288,165`; `zba-v2-closing-analysis.py:64-67`; spec `:416-418` | The window is not independent of this design (D1) | Stop condition **not met**: the boundaries are unambiguous, and no reusable code was trained or tuned on holdout outcomes |
| A4 | No local market data: no `.cache/alpaca` anywhere. Caches existed only in the evicted Actions cache. Code fetches SIP history, IEX live, raw prices, NY-local timestamps, no `ingested_at` | `alpaca.py:20,70,75,105`; `intraday-signal-count-2024.md:3,7` | P1 starts from zero | Store data locally, gitignored |
| A5 | Look-ahead risks: survivorship (today's asset list), news without availability time, split handling by heuristic, IEX/SIP feed mismatch, partial-day caching. Daily and intraday features themselves are point-in-time | `ticket_backtest.py:379-383`; `alpaca.py:234-251`; `intraday.py:273` | Biased universes; feature drift between live and backtest | Contracts carry `ingested_at`, `available_at`, `feed`, `backfilled` |
| A6 | Alpaca Basic is the only free source of 3y SIP 1-min bars (≈1.5–3 h for 500 symbols) and NBBO quotes (≈13 days for the full tape). No redistribution. **No free history** for intraday VIX, NYSE A/D, multi-year LULD halts, or an earnings calendar. The IBKR API needs an L1 subscription and has no delisted names | docs.alpaca.markets, interactivebrokers.github.io, cboe.com, nasdaqtrader.com (URLs in the audit) | Spread and imbalance features need sampling or a proxy (D3) | Proxies; no paid source |
| A7 | `common.send` and `common.tg` are reusable for outbound cards. There are no callback buttons: the listener takes `message` updates only | `common.py:135-212`; `listen.py:458,501` | The L9 buttons need a listener change | Owner's decision at P8 |

## Implementation

**What changed:**
- `docs/engine/`: architecture (verbatim), audit, ADR 0001 (Proposed).
- `scalp_engine/CLAUDE.md` (54 lines).
- `scalp_contracts/`:
  - `holdout.yaml` and an empty `holdout_ledger.jsonl`
  - 4 JSON Schemas
  - 6 table contracts
  - an example recommendation
  - LF-pinned `.gitattributes`
- Each side has its own `contracts.py` (flat-YAML loader, JSON Schema subset validator, JCS-subset canonical JSON) and
  `data_access.py` (holdout guard; CSV/JSONL backends).
- `scalp_engine/signing.py`: Ed25519 sign, keygen and CLI; refuses key paths inside the repo.
- `scalp_eval/verify.py`.
- **New dependency:** `cryptography>=42`, both sides, justified in ADR §6.

**Tests:**

| Suite | Count | Covers |
|---|---|---|
| `scalp_engine` | 41 | Overlap raises; adjacent ET days pass (2015-12-31, 2022-01-03); UTC-midnight traps on both ends; offset normalization; naive and unbounded ranges rejected; partition pruning; post-read assertion; ledger empty; unseal binds run and holdout hash; AST reader ban with a checker self-test; import boundary; contract validation; canonical JSON; sign/verify round trip |
| `scalp_eval` | 38 | The same guards, plus a cross-process round trip: the engine CLI signs with a throwaway key generated at test time, and L7 verifies it and rejects tampering, untrusted keys and malformed envelopes |

**Measured results:**
- `scalp_engine`: Ran 41, OK.
- `scalp_eval`: Ran 38, OK.
- Planted violations made the static guards fail as intended: `duckdb.connect` in an engine module, and a
  `scalp_engine` import plus `csv.DictReader` in an eval module. The plants were then removed.

**Regressions:** none. The stockbot suite gave Ran 902, OK before; Ran 902, OK after.

**Commits:**
- af083cc `docs: add engine architecture`
- df5d5a9 `docs: P0 audit`
- 40c2a4f `docs: ADR 0001 + engine rules`
- 07c4c50 `feat: holdout lock + contracts + tests`

**Environment note:** Windows Python 3.11 has no tz database, and pip cannot reach PyPI here: Avast intercepts TLS
and the 3.11 pip fails verification. Tests ran with:
- `PYTHONTZPATH` pointing at Python 3.10's tzdata,
- `PYTHONUTF8=1`,
- a throwaway venv at `%TEMP%\p0venv` with `cryptography` 50.0.2, installed from wheels that Python 3.10's pip 25
  downloaded with TLS verification.

## Gate

| Criterion | Result | Evidence |
|---|---|---|
| ADR 0001 complete, D1–D3 laid out, status Proposed | **PASS** (Accepted is the owner's step) | `docs/engine/adr/0001-engine-foundations.md` |
| Holdout defined in one file and enforced; all B3 tests pass on both sides | **PASS** | `scalp_contracts/holdout.yaml`; 41/41 and 38/38 OK |
| Existing stockbot suite passes unchanged | **PASS** | 902 before, 902 after, OK; no file under `tests/` or `bot/` touched |
| Diff only under `docs/engine/`, `scalp_engine/`, `scalp_eval/`, `scalp_contracts/` (plus justified dependency lines) | **PASS** | `git diff --stat origin/main...engine/p0`: 38 files, all in those paths. Dependency lines are in `scalp_*/requirements.txt` (ADR §6) |

**Note on the diff check:** the local `main` ref is stale. `git diff main...engine/p0` therefore shows GitHub's 371
newer commits as well. The check above uses `origin/main`, which is GitHub's `main`.

## Decisions needed

Full options are in ADR §8.
- **D1. Holdout status.**
  - Options:
    - (a) keep 2016–2021 for the single P7 run;
    - (b) declare it exhausted and seal a prospective window, 2026-11-02 → 2027-04-30 (≈124 sessions);
    - (c) hybrid: (b) as the primary test, with 2016–2021 kept sealed as a fail-only stress test.
  - *Recommendation: (c).*
- **D2. Repo visibility.**
  - Options:
    - (a) everything public;
    - (b) make stockbot private: this breaks the always-on listener on 2,000 free minutes;
    - (c) a separate private engine repo;
    - (d) code public, with journal, models and data in the private `stockbot-data` repo.
  - *Recommendation: (d) now, revisit (c) at P8.*
- **D3. Data gaps.**
  - Spread and imbalance: decision-minute SIP quote sampling plus a bar-based proxy, or a paid source (needs R3
    approval).
  - VIX, breadth, halts and earnings: proxies.
  - Confirm whether your IBKR subscription covers API historical data.
  - *Recommendation: proxies; no paid source.*
- **Also yours:**
  - Accept or amend ADR 0001, including the PDT text, with `broker_pdt_active` defaulting to `true`.
  - Generate the production signing key outside the repo.

## Next step: P1 prerequisites only (P1 not started)

1. ADR 0001 Accepted, with D1 decided. If (b) or (c), `holdout.yaml` moves to schema v2 before any ingestion.
2. D2 decided: where raw data, journal and models live.
3. D3 decided: the quote-sampling plan and proxies.
4. Approval of the P1 dependencies: a Parquet backend (pyarrow, or polars + duckdb), and whether to add an engine CI
   workflow (a workflow edit).
5. The local `main` fast-forwarded to `origin/main`, or the gate diff command updated.
