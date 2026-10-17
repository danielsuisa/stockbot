# ADR 0001: Scalping engine foundations

- **Status:** Accepted (2026-10-17). See the decision record at the end of this file.
- **Date:** 2026-10-17
- **Branch:** `engine/p0`
- **Context documents:**
  - [ARCHITECTURE.md](../ARCHITECTURE.md)
  - [P0-audit.md](../P0-audit.md). Audit items are cited as A1–A7.

## 1. Context

**The engine is new code in the existing stockbot repository.** The P0 audit established:
- stockbot is stdlib-only, with 902 passing tests (A1.1, A1.2).
- No local market data exists (A4.1).
- ZBA's ingestion and replay code is a usable reference, but it is not deterministic as it stands: it reads the wall
  clock, makes network calls on a cache miss, and can cache partial days (A2.2).
- The holdout (2016–2021) was evaluated once by ZBA. Its outcome has since informed later work (A3.4).
- Alpaca Basic is the only free source of 3 years of SIP 1-minute bars and NBBO quotes (A6).

## 2. Layout and boundaries

| Path | Holds | Rule |
|---|---|---|
| `scalp_engine/` | L0–L6, L8–L9 | Never imports `scalp_eval` or stockbot's `bot` package |
| `scalp_eval/` | L7 | Never imports `scalp_engine` or `bot` |
| `scalp_contracts/` | Data files only: JSON Schema, table contracts (JSON), YAML, the ledger. **No Python** | Read by both sides through their own loaders |
| `docs/engine/` | Architecture, audit, ADRs, phase reports | |

**No collisions.** None of these names existed before (A1.1), so the default names are kept.

**Duplicated loaders are deliberate (R4).** Each side has its own `contracts.py` and `data_access.py`, including the
contract loaders and the holdout guard. Static tests on both sides enforce two things:
- the import boundary (`tests/test_static.py::test_no_cross_imports`), and
- that only `data_access.py` reads tabular data.

**L7 meets the engine only as a separate process that hands over signed JSON.** The cross-boundary round-trip test
(`scalp_eval/tests/test_verify.py`) runs `python -m scalp_engine.signing` as a subprocess.

**Engine code never lives under `bot/`.** A merge touching `bot/**` redeploys the live Telegram listener (A1.3). The
stockbot modules under change control are read-only for the engine: `intraday`, the squeeze screener, the insider
scans, the workflows and the listener.

## 3. Reuse and discard decisions (from A2)

| Layer | Decision |
|---|---|
| L0 | **ADAPT** `bot/alpaca.py` request logic, re-implemented in `scalp_engine` with four fixes: <ul><li>offline-by-default replay, where a cache miss raises (the pattern of `bot/intraday_count.py:148`)</li><li>a mandatory `end`, with no wall-clock defaults (`ticket_backtest.py:950-955`)</li><li>no caching of the current session for any endpoint (`alpaca.py:196-296`)</li><li>`feed`, `adjustment`, `request_id` and `ingested_at` on every row</li></ul> **REUSE the pattern** of `tests/determinism_probe.py`, which compares output across `PYTHONHASHSEED` values |
| L1 | **REUSE as reference**, re-implemented under feature versioning: `ticket.atr14`, `vwap`, `halted`, `zba_v2.tod_fn`, `dq_reason`. Prefix-sum vs direct-sum differences are resolved by one versioned definition (A2.2) |
| L2 | **ADAPT** the stocks-in-play prefilter logic, on point-in-time universe snapshots instead of today's asset list (A5.1). **DISCARD** universes A and B |
| L3 | **DISCARD** `ticket._zba` and the 22-variant ORB grid: the line was closed after its holdout NO-GO |
| L5 | **ADAPT** `ticket._shares` and `_legs` |
| L7 | **REUSE as reference logic**, copied into `scalp_eval` and never imported: `ticket_backtest.simulate`, `simulate_ticks`, `ssr`, the commission constants, `stats`, `random_means`, `robustness`, `portfolio`. **ADAPT** the ZBA protocol: freeze file, parameter check, refusal of a second holdout run |
| L8 | **ADAPT** the `bot/shadow.py` journal shape |
| L9 | **REUSE** `common.send` and `common.tg` for outbound cards. Callback buttons need a listener change, which is the owner's decision at P8 (A7) |
| Workflows | **DISCARD** the Actions cache as a data store, because it is evicted (A4.1) |

## 4. Holdout lock

**Window.** `scalp_contracts/holdout.yaml` holds America/New_York calendar dates **2016-01-01 → 2021-12-31, both
inclusive**. Loaders convert them to UTC **[2016-01-01T05:00Z, 2022-01-01T05:00Z)**.
- These dates cover exactly the trading days of ZBA's holdout, `2016-01-04 → 2021-12-31` (`bot/zba_v2.py:24`).
- They also cover the non-trading days 2016-01-01..03, so dated non-price rows are sealed too.
- **Deviation from the P0 prompt, which asked for trading dates:** calendar bounds are a strict superset of the
  trading-date bounds. With trading-date bounds, news dated on the New Year holiday would be unsealed.

**One data-access module per side.** `scalp_engine/data_access.py` and `scalp_eval/data_access.py` are the only
allowed paths for reading tabular data. A read goes through four steps:
1. **Range check.** The `[start, end)` range must be timezone-aware. An unbounded range counts as overlapping, and an
   overlap raises `HoldoutSealedError` before any file is opened.
2. **Partition pruning.** Pruning on `date=` directories means sealed and out-of-range files are never opened.
3. **Row predicate.** Applied while streaming, before a row is materialized.
4. **Post-read assertion.** No returned row may carry a holdout event time.

**Unseal path.** `unseal_record(run_id)` raises unless the append-only ledger `scalp_contracts/holdout_ledger.jsonl`
holds a record for that run.
- A record has exactly the keys `run_id`, `approved_by`, `approved_at`, `purpose` and `holdout_sha256`.
- `holdout_sha256` binds the approval to the boundaries in force.
- No code writes the ledger; only the owner adds a line. It is committed empty, and a test asserts it is empty.

**Effect on development data.** Warm-up reads count as reads. The first engine feature date that needs N sessions of
history is therefore N sessions after 2022-01-03. ZBA's development runs read Nov–Dec 2021 for warm-up (A3.2); the
engine cannot.

## 5. Recommendation contract and signing

### 5.1 Payload

`scalp_contracts/schemas/recommendation.schema.json` defines the payload:
- `schema_version`, `rec_id`, `created_at` (UTC, `Z`)
- `symbol`, `side` (`long` | `short`), `qty`
- `entry`: either `LMT` with `limit_price`, or `STP_LMT` with `stop_price` and `limit_price`
- `stop_price`, `target_price`, `ttl_seconds`
- `p`, `horizon_minutes`
- `model_version`, `feature_versions` (feature ids, §5.4), `regime` (`trend` | `chop` | `event`)
- `risk_checks`: a list of `{check, passed, detail}`

`additionalProperties` is false at every level. A worked example is at
`scalp_contracts/examples/recommendation.example.json`.

### 5.2 Canonical serialization

The canonicalization is **RFC 8785 (JCS), restricted to documents with no floats and ASCII-only strings**. On that
subset, `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` is byte-identical to JCS:
- Key order: JCS sorts by UTF-16 code units, which for ASCII keys is the same as Python's sort.
- String escaping: both escape `"` and `\`, use the short forms `\b \f \n \r \t`, and write other control characters
  as lowercase `\u00xx`.
- Floats: they are the only place JCS needs ES6 number formatting, and the subset bans them. Integers are bounded to
  ±(2^53−1).

The canonicalizer raises on anything outside the subset. The schema already demands decimal strings for prices and
`p`, so the subset costs nothing.

**Why JCS over plain "sorted keys, no whitespace":** it is a published standard, and any JCS library in another
language can re-derive the signed bytes. **Why the subset over full JCS:** full JCS would need an ES6
number-formatting implementation, which is a new dependency or hand-written code, for values the contract does not
allow anyway.

### 5.3 Signing

- **Algorithm:** Ed25519.
- **Signed bytes:** `b"scalp-engine/recommendation/v1" + b"\n" + canonical(payload)`. The domain prefix stops a
  signature from being replayed as some other message type.
- **Envelope** (`signed_recommendation.schema.json`): `{schema_version, payload, signature: {alg, canonicalization,
  key_id, value}}`.
  - `key_id` is the first 16 hex characters of sha256 of the raw public key.
  - `value` is base64url without padding.
- **Private key:** a PKCS#8 PEM file **outside the repository**.
  - Its path comes from `--key` or `SCALP_ENGINE_SIGNING_KEY`; an optional passphrase comes from
    `SCALP_ENGINE_SIGNING_KEY_PASSPHRASE`.
  - `load_private_key` and `keygen` refuse any path inside the repo.
  - No production key exists yet. The owner creates one with
    `python -m scalp_engine.signing keygen --out <outside-repo>.pem --pub-out <pub>.pem`.
- **L7 holds public keys only.** It can verify but cannot forge.

### 5.4 Feature versioning

- **Feature id:** `name@MAJOR.MINOR.PATCH+<first 12 hex of sha256(defining source)>`.
  - The hash input is the defining function's source, as returned by `inspect.getsource`, with line endings normalized
    to `\n`.
  - Registry entries follow `feature_definition.schema.json` and are never edited.
- **Any change to the code hash requires a new version.** A P1 test will recompute every hash and fail on a mismatch.
- **Every feature row carries `available_at`:**
  - It is the latest availability time of its inputs, plus compute latency.
  - For raw rows, availability is `ingested_at` for live data. For backfills it is the documented vendor availability:
    for example, bar close plus 15 min for Alpaca free SIP.
  - Readers filter on `available_at <= decision time`.

## 6. Data contracts and dependencies

### 6.1 Table contracts

Table contracts live in `scalp_contracts/tables/*.json` and are validated by `schemas/table_contract.schema.json`:

| Table | Notes |
|---|---|
| `raw_bars_1m` | |
| `raw_quotes` | Includes `size_unit`, for Alpaca's 2025-11-03 change from round lots to shares |
| `raw_news` | |
| `corporate_actions` | |
| `universe_snapshots` | Includes `backfilled`, which flags survivorship-biased rows |
| `feature_values` | |

Rules for every table:
- **Mandatory columns:** `event_at` (timestamp[us, UTC]), `ingested_at` (timestamp[us, UTC]), `source` and
  `schema_version`. Tests check them on both sides.
- **Partitioning:** Hive-style `source={source}/[feed={feed}/]date={YYYY-MM-DD}`, where `date` is the
  America/New_York date of `event_at`. Feature values use `feature=/version=/date=`.
- **Append-only:** one part file per ingestion batch. A re-fetch appends a new part and never overwrites. Readers
  resolve duplicates by the latest `ingested_at` that is not after their `as_of` time.
- **Raw bars stay unadjusted.** Corporate actions apply at read time, as of the decision.

### 6.2 Where data lives

- **Raw data is stored outside git**, under a gitignored path. The default for P1 is `D:\investor\.cache\scalp\`;
  `.cache/` is already ignored.
- **Reason:** Alpaca's terms forbid redistribution (A6), and the repo is public (D2).

### 6.3 Dependencies

| Dependency | Status | Why |
|---|---|---|
| `cryptography>=42` | **Added**, in `scalp_engine/requirements.txt` and `scalp_eval/requirements.txt` | The stdlib has no Ed25519. PyCA `cryptography` is the de-facto standard, backed by OpenSSL, and has wheels for 3.11/3.12 on Windows and Linux. PyNaCl would also work, but has no PKCS#8/PEM handling. A pure-Python Ed25519 was rejected: it is unaudited and not constant-time |
| YAML parser | **Not added** | `holdout.yaml` uses a flat `key: value` subset, parsed by a strict ~30-line loader that rejects anything richer |
| `jsonschema` | **Not added** | Each side has a small validator for the keyword subset the contracts use. It fails closed on unknown keywords |
| Parquet backend: `pyarrow`, or `polars` + `duckdb` per ARCHITECTURE §6 | **Deferred to P1**, with its own justification | P0 ships stdlib CSV/JSONL backends for the data-access modules; `.parquet` raises `NotImplementedError` |

Two consequences:
- stockbot's CI (`tests.yml`) stays dependency-free and does not run engine tests. Adding an engine CI job means
  editing a workflow, which P0 forbids; it is a P1 decision for the owner.
- **Running the engine tests locally** (from the repo root):
  `python -m unittest discover -s scalp_engine/tests -t .`, and the same for `scalp_eval`. On Windows, set
  `PYTHONTZPATH` to a tzdata `zoneinfo` directory and `PYTHONUTF8=1` (A1.2).

## 7. PDT amendment to ARCHITECTURE §0 and L6 (proposed text)

The rule changed:
- The SEC approved FINRA's Rule 4210 amendments on **2026-04-14**. They replace the day-trading margin requirements in
  their entirety, which eliminates the pattern-day-trader designation and the $25,000 minimum.
- The amendments took effect on **2026-06-04**. Firms may phase in until **2027-10-20** (FINRA Regulatory Notice
  26-10, finra.org/rules-guidance/notices/26-10, verified 2026-10-17).
- An account stays under the old limit until its broker transitions it.

Proposed edits:
- **§0, replace the PDT clause with:** "the PDT rule (below $25,000, 3 day trades per 5 trading days) until the
  broker moves the account to FINRA's 2026 intraday-margin standard; brokers may phase in until 2027-10-20".
- **L6, replace the PDT bullet with:** "PDT enforcement is a config flag `broker_pdt_active` (default `true`). While
  `true` and equity is below $25,000, the 3-in-5 day-trade counter is part of the system, not of memory. The owner
  sets it to `false` only after confirming that IBKR has transitioned the account. Every recommendation records the
  flag in its `pdt` risk check."

ARCHITECTURE.md is kept verbatim on this branch. These edits are applied when this ADR is Accepted.

## 8. Open decisions for the owner

These are laid out, not decided.

### D1. Holdout status

**Evidence (A3):**
- ZBA evaluated 2016–2021 once (run 38003155445, verdict NO-GO).
- After the verdict, holdout trades were analysed: forensics, a closing analysis that pools development and holdout,
  and the §15 planning edge.
- The 2026-10-17 intraday audit quotes holdout statistics.
- This engine's §0 cites that outcome.
- The ZBA paper's own sample covered 2016–2023, so the holdout was in-sample for the published rule.
- Other lines (ensemble, insider, squeeze) read 2016–2021 *daily* data.
- Alpaca's 2016 universe misses an estimated 10–25% of names (survivorship).

**Options:**

| Option | What it means | Statistical trade-off |
|---|---|---|
| **(a)** Keep 2016–2021 as the single P7 confirmation run, as ARCHITECTURE R2/P7 say | One run on 2016–2021 at P7 | **For:** six years and many regimes (2018 volatility, 2020 crash, 2021 retail mania), so it is high-powered. **Against:** it is no longer independent of the design. The design space was pruned using its outcome (adaptive data analysis), so a PASS is biased upward by an unknown amount; a FAIL is still informative. Partial mitigation: count ZBA's trials (22 variants and more) in the deflated-Sharpe trial count |
| **(b)** Declare it exhausted, never read it again, and seal a new untouched window | Concrete candidate: **2026-11-02 → 2027-04-30** (about 124 NYSE sessions), sealed now, before the data exists. P1–P6 development uses data up to 2026-10-30. P7 live shadow starts after 2027-04-30, followed by one run on the sealed window | **For:** clean, because nobody can have seen it. **Against:** one regime only. Power suffices only for edges near the GO threshold: n ≈ (2.49 × σ/μ)², about 335 trades for μ = 0.2R at σ ≈ 1.47R, i.e. about 3 trades per session. It cannot confirm smaller edges. It also sets a calendar floor: P7 cannot finish before May 2027 |
| **(c)** Hybrid | Keep 2016–2021 sealed as a secondary stress test (any FAIL is NO-GO), and make the prospective window from (b) the primary confirmation | **For:** a clean primary test plus a high-power falsification check. **Against:** requiring both passes costs power (more false negatives), and it still waits for May 2027 |

**Other windows considered and rejected:**
- Any part of **2022 → 2026-10-08**: ZBA development saw it.
- **Pre-2016 intraday**: Alpaca has nothing before 2016-01-04 (spec §14.12), and IBKR needs a paid subscription and
  has no delisted names (A6), which conflicts with R3.

If (b) or (c) is chosen, `holdout.yaml` moves to `schema_version: 2` with multiple named windows.

**Recommendation (mine, not a decision): (c).** Sealing 2016–2021 costs nothing, and a fail there is still a cheap
NO-GO. The prospective window is the only test this engine's design has not been fitted to.

### D2. Repository visibility

**Facts:**
- `stockbot` is public.
- Engine code, the journal of real recommendations and fills, and model artifacts would all be public by default.
- Alpaca data cannot be committed anywhere public (A6).
- GitHub Free includes 2,000 Actions minutes per month for private repos. Public repos on standard runners are free
  (docs.github.com/en/billing/concepts/product-billing/github-actions).
- stockbot's Telegram listener re-dispatches itself every ~5h20m and polls continuously (A7). That is roughly 40,000+
  runner-minutes per month, so **making stockbot private would break the live bot** unless it moves to a self-hosted
  runner.

**Options:**

| Option | What it means | Trade-off |
|---|---|---|
| **(a)** Keep everything public | | **For:** free. **Against:** strategy, journal (positions, P&L) and models are public |
| **(b)** Make stockbot private | | 2,000-minute budget; the listener needs a self-hosted runner or a host |
| **(c)** Separate private repo for the engine | e.g. `stockbot-engine` | **For:** clean. **Against:** engine CI must fit 2,000 min/month, which is plausible if heavy jobs run locally as ARCHITECTURE §6 says. This branch's commits move with it |
| **(d)** Code public; journal, models and data in the existing private data repo | The private repo is `danielsuisa/stockbot-data`, already cloned in CI via `DATA_REPO_KEY` | **For:** no move needed. **Against:** the strategy logic is public |

**Recommendation (mine, not a decision): (d) now, and revisit (c) at P8.** By P8 the journal holds real fills.

### D3. Blocking data gaps (A6)

| Field or need | Free historical source | Options |
|---|---|---|
| NBBO spread and bid/ask imbalance (L1, L2 spread filter) | Alpaca SIP quotes. Free, but about 13 days to fetch the full tape for 500 symbols × 3 years | (i) Fetch quotes only around decision minutes for screener candidates: a few minutes per candidate-day. (ii) A bar-based spread proxy (Corwin–Schultz / Abdi–Ranaldo) for the full universe. (iii) A paid tick source, which needs R3 approval. Imbalance as `bid_size/(bid_size+ask_size)` is unaffected by the 2025-11-03 unit change, because both sides change together |
| Intraday VIX | **None** | Daily VIX from Cboe, free; VIXY/VXX 1-minute bars from Alpaca as a proxy; SPY realized volatility |
| NYSE intraday advance/decline | **None** | Compute breadth from the engine's own universe bars (500 names, or more at extra fetch cost) |
| LULD halt history | **None** beyond about a month at Nasdaq Trader | Detect halts from gaps in SIP bars and trades (the `ticket.halted` pattern); archive the Nasdaq Trader halt feed going forward from P1 |
| Earnings calendar history | **None** | EDGAR 8-K Item 2.02 timestamps (actual release, point-in-time) as the catalyst flag; no scheduled dates |
| IBKR historical API data | Needs a Level-1 streaming subscription; no delisted names | Confirm whether the owner's existing IBKR subscription covers API historical data. Otherwise IBKR is for live L1 only |
| Live vs. historical feed | Live free real-time from Alpaca is IEX only; history is SIP | Version features per feed, and measure the gap in P7 shadow (A5.7) |

**Recommendation (mine, not a decision):** options (i) + (ii) for spread; the proxies for VIX and breadth; detected
halts plus forward archiving. No paid source. Revisit after P3 if the baseline's cost model proves quote-sensitive.

## 9. Consequences

- P1 cannot read 2016–2021 at all, warm-up included, and must fetch fresh data: there is no cache (A4.1).
- Adding a Parquet backend, and any CI for the engine, are explicit P1 decisions.
- Any change to `holdout.yaml` needs a new ADR, and invalidates existing unseal approvals through `holdout_sha256`.

## Decision record

- **Date:** 2026-10-17
- **Decision:** ADR 0001 accepted, including the PDT amendment (§7). Accepted by the user via the orchestrator.
- **D1, holdout:** option (c), refined to two sealed windows; specified in ADR 0002 §2.
  - `legacy_2016_2021`: ET trading days 2016-01-04..2021-12-31, role `veto_only`. Run once at P7; a fail blocks GO,
    a pass adds no evidence.
  - `forward_2026q4`: ET trading days 2026-11-02..2027-01-29 (61 sessions), role `confirmation`. Evaluated once, on
    a frozen model, after the window closes and before the P7 live shadow, which cannot start before 2027-02-01.
- **D2, repository:**
  - Code stays public in stockbot.
  - The journal, models and processed artifacts go to the private `stockbot-data` repo, from P3 on.
  - Raw vendor data never enters any git repository (Alpaca forbids redistribution).
- **D3, data gaps:** no paid data.
  - Spread and imbalance come from SIP NBBO quotes sampled at fixed times, plus a bar-based spread estimator (P2).
  - VIX, breadth, halts and earnings use free proxies (P2).
- **Live feed:** no IBKR real-time US subscription; Alpaca's free live feed is IEX. P1 ingests both SIP and IEX
  1-minute bars. Whether to pay for a consolidated live feed is decided before P7 (ADR 0002 §3).
- **Approved:** fast-forwarding local `main`; the pinned dependencies `tzdata` and one Parquet library; no CI workflow
  in P1.
