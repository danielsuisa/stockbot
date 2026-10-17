# P0 audit: stockbot repo for the scalping engine (2026-10-17)

Scope: read-only audit of `danielsuisa/stockbot` at `origin/main` **1939f52**. Branch `engine/p0` was cut from that
commit. The local `D:\investor` checkout of `main` is stale at 2ffda0a (371 commits behind), so it was not used.
Nothing outside `docs/engine/`, `scalp_engine/`, `scalp_eval/` and `scalp_contracts/` was edited.

Holdout handling during the audit: for the holdout artifacts, only paths, row counts, min/max dates, the CSV header
and the GO/NO-GO verdict line were read. No holdout price, quote or news rows were loaded.

Format: **F** finding · **E** evidence · **I** impact · **R** recommendation.

---

## A1. Repo map, tests and CI

**A1.1 Layout and toolchain.**
- **F:** The repo is a stdlib-only Python project with zero third-party dependencies. Layout:
  - `bot/` (55 modules)
  - `tests/` (56 `test_*.py` plus probes)
  - `docs/` (`backtest/`, `research/`, `superpowers/` specs and plans, `intraday/`)
  - `data/` (bot state JSON committed by workflows)
  - `.github/workflows/` (14 workflows)
- **E:** No requirements file, pyproject or setup.cfg exists, and no workflow runs `pip`. The CI test command is
  `python -m unittest discover -s tests` (`.github/workflows/tests.yml:29`), run on a Python 3.11/3.12 matrix
  (`tests.yml:20`).
- **I:** The engine's planned stack (Polars, DuckDB, PyTorch, LightGBM, cryptography) would be the repo's first
  dependencies.
- **R:** Keep engine dependencies in per-package requirement files (`scalp_engine/requirements.txt`,
  `scalp_eval/requirements.txt`), so the stockbot CI stays dependency-free. See ADR 0001 §6.

**A1.2 Test baseline.**
- **F:** 902 tests, all passing, on Python 3.11.9 locally. Two environment variables are needed:
  - `PYTHONTZPATH` pointing at a tzdata `zoneinfo` directory, because Windows has no IANA tz database.
  - `PYTHONUTF8=1`, because the Hebrew report text fails under cp1252.
- **E:** A bare run gives `Ran 132 tests … FAILED (failures=2, errors=47)`. Every error is
  `ZoneInfoNotFoundError: 'Asia/Jerusalem'`, raised at import time by `bot/common.py:321`. With both variables set the
  output is `Ran 902 tests in 13.0s OK`.
- **I:** This is a local environment issue, not a code defect. CI runs on Ubuntu, which ships tzdata.
- **R:** Engine tests use the same two variables locally. Recorded in `scalp_engine/CLAUDE.md`.

**A1.3 Can a push to a non-`main` branch trigger anything?**
- **F:** A push to `engine/p0` triggers no workflow that uses secrets or sends Telegram messages.
- **E:** Push triggers by workflow:

  | Workflow | Push trigger | Notes |
  |---|---|---|
  | `tests.yml` | `main` only (`:3-8`) | Also runs on `pull_request`; no secrets |
  | `telegram-listen.yml` | `main` only, paths `bot/**` (`:7-26`) | |
  | `ensemble.yml` | `ensemble-run-blind` only (`:7-9`) | |
  | `index-blind.yml` | `index-run-blind` only (`:6-8`) | |
  | `index-verdict.yml` | `index-run-verdict` only (`:9-11`) | |
  | `zba-v2.yml` | `zba-v2-run-dev` and `zba-v2-run-holdout` only (`:6-8`) | |
  | `intraday-count.yml` | any branch, but only when `bot/intraday_count.py` changes (`:6-9`) | No secrets, but it **pushes commits back** to the branch (`:45-47`) |

  Every other workflow runs only on `workflow_dispatch` or a cron schedule.
- **I:** Pushing an engine branch is safe as long as it does not touch `bot/intraday_count.py`. Merging anything under
  `bot/**` into `main` redeploys the live Telegram listener within minutes (`telegram-listen.yml:24-26`).
- **R:** Engine code never lives under `bot/`. P0 does not push; the user decides when to.

---

## A2. ZBA inventory (verdicts mapped to engine layers)

**A2.1 Component verdicts.**

| Component | Purpose | Tests | Verdict |
|---|---|---|---|
| `bot/alpaca.py` | Alpaca REST client; gz-JSON disk cache under `.cache/alpaca/` (`:20`, atomic write `:53-57`) | `test_alpaca.py` (16), plus 3 cache tests in `test_zba_v2.py` | **L0 ADAPT**: good stdlib base, but has the determinism gaps in A2.2 |
| `ticket_backtest.simulate`, `_fill`, `_after`, `_mirror`, `simulate_ticks`, `ssr`; commission constants (`:24-143`) | Conservative 1-minute fills, fill-minute replay on trades, Rule 201, commission plus half-spread | `test_ticket_backtest.py` (40) | **L7 REUSE as reference logic**, copied into `scalp_eval` (R4 forbids importing `bot`) and extended for bracket legs |
| `stats`, `pctl`, `verdict`, `random_means`, `robustness`, `portfolio` (`ticket_backtest.py:316-370`, `:448-469`, `:774-787`) | Statistics, 20-seed random-side benchmark, stress tests, 1%-risk book | same | **L7 REUSE** (deterministic) |
| `asset_universe`, `prefilter`, `prefilter_days`, `stocks_in_play` (`ticket_backtest.py:379-445`); `zba_v2.dollar_days`, `qr_top` (`:45-74`) | Stocks-in-play universe | `test_ticket_backtest.py`, `test_zba_v2_run.py` (9) | **L2 ADAPT**: logic is sound, but the universe is survivorship-biased (A5.1) |
| `universe_a`, `fixed_b`, `load_b`, `_gate_tickers`, `_day` (`ticket_backtest.py:236-301`, `:554-642`) | Squeeze-specific universes | `test_ticket_backtest.py` | **DISCARD**: uses SEC's current ticker map (`:476-477`) |
| `ticket.atr14`, `to5`, `vwap`, `halted`, `mins`, `hhmm` (`bot/ticket.py:7-94`) | Bar and feature primitives | `test_ticket.py` (24) | **L1 REUSE as reference** (re-implemented under feature versioning) |
| `ticket._zba` and v2 options (`ticket.py:267-341`) | 5-minute ORB signal | `test_zba_v2.py` (12) | **L3 DISCARD**: the strategy failed its holdout. **L5 ADAPT** only `_shares` and `_legs` (`:117-123`) |
| `zba_v2.tod_fn`, `dq_reason`, `quote_fns` (`:77-132`) | Time-of-day RVOL, data-quality gate, spread and depth checks | `test_zba_v2_run.py` | **L1/L0 REUSE**: no look-ahead after the spec 14.11 fix |
| `zba_v2` runner: `run_grid`, `select`, `holdout`, `holdout_checks`, freeze check (`:162-361`) | Pre-registered grid, selection, freeze, single-shot holdout | `test_zba_v2_run.py` | **L7 ADAPT the protocol** (freeze file, parameter check, refuse a second run). **DISCARD the grid** |
| `bot/intraday.py` | Live screener and `bracket()`; under change control | `test_intraday.py` (8), `test_intraday_screener.py` (22) | **Read only.** L2/L5 ideas; L9 message formatting |
| `bot/intraday_count.py` | Cache-only replay that blocks every network request (`:148`) | `test_intraday_count.py` (4) | **L0 REUSE the pattern**: offline-by-default replay |
| `bot/sessions.py`, `bot/clock.py` | Live scheduling; reads the wall clock and Nasdaq market info (`sessions.py:25-26`, `:40-45`) | `test_sessions.py` (15), `test_clock.py` (9) | **Orchestration ADAPT**; must never run inside replay |
| `bot/blind.py` | Blind report holding only whitelisted keys | `test_blind.py` (5) | **L7 ADAPT** |
| `tests/determinism_probe.py` | Byte-identical output under different `PYTHONHASHSEED` values | `test_ensemble_determinism.py` | **L0/L7 REUSE the pattern**. ZBA itself has no determinism test |
| `bot/shadow.py`, `data/shadow_journal.json` | Forward paper record | `test_shadow.py` | **L8 ADAPT** |
| Spec §14/§15 (`docs/superpowers/specs/2026-10-08-ticket-backtest-design.md:204-440`), `zba-v2-frozen.json` | Pre-registration, power gate, freeze record | n/a | **REUSE as protocol** |
| `.github/workflows/zba-v2.yml` | Chained jobs that carry `.cache/alpaca` through `actions/cache` | none | **DISCARD as the data store**: the Actions cache is evicted (A4.1) |
| ZBA result files under `docs/backtest/*zba*` | Archived results | n/a | **Archive only** |

**A2.2 Ingestion and replay determinism (`alpaca.py`, `ticket_backtest.py`, `zba_v2.py`).**
- **Seeded randomness: OK.** `random.Random(f"{seed}|{day}|{t}")` (`ticket_backtest.py:366`) is string-seeded, so it
  does not depend on `PYTHONHASHSEED`. No other randomness exists.
- **Wall-clock reads: FAIL.** The default end date is "the last session before today" (`ticket_backtest.py:950,955,
  1071,1074`), and `zba_v2.py:433` reads the clock for the run name. **I:** a run without `--end` cannot be
  reproduced. **R:** in the engine, `end` is mandatory and replay never reads the clock.
- **Network during replay: FAIL.** Every `alpaca.*` function downloads silently on a cache miss. The only offline
  guards are `intraday_count` patching `_get` (`:148`) and `ensemble.offline` (`ensemble.py:49`). **R:** replay must
  be offline by construction, and a cache miss must raise.
- **Partial live days cached as final: FAIL.** The 15-minute `CUTOFF` is checked only in `minute_bars` and `daily`
  (`alpaca.py:92,131`). `opening_bars`, `minute_volumes`, `news_symbols`, `quote_full` and `trades`
  (`:196-296`) can cache a day that is still trading.
- **Tie ordering: OK.** Sorts break ties by ticker or by the stable variant order (`ticket_backtest.py:444,771`;
  `zba_v2.py:73,258,263`). Symbol sets are sorted before fetching (`zba_v2.py:183,216`).
- **Float non-determinism: RISK.** `prefilter_days` uses prefix sums (`ticket_backtest.py:409-426`), while
  `ticket.atr14` sums directly. The result depends on where the cached range starts, which can flip the `> 0.50`
  ATR threshold or the 4-decimal stop rounding (`ticket.py:313`).
- **Cache keys omit feed and adjustment, and there is no ingestion timestamp.** Keys are layouts such as
  `1m/<day>/<sym>` (`alpaca.py:91`), and rows are `[time, o, h, l, c, v, vw]` (`:84-85`).
  - **I:** an IEX file and a SIP file cannot be told apart, and no point-in-time audit is possible.
  - **R:** the engine's raw schema carries `feed`, `adjustment`, `ingested_at` and `source` on every row (ADR §3).

---

## A3. Holdout history (evidence for D1)

**A3.1 Boundaries.**
- **F:** The holdout covers **New York session dates 2016-01-04 → 2021-12-31, both inclusive**.
- **E:**
  - `bot/zba_v2.py:24` defines `HOLDOUT = ("2016-01-04", "2021-12-31")`.
  - The filter is `rng[0] <= d <= rng[1]` on NY session dates (`zba_v2.py:288`).
  - The same range appears in `docs/backtest/zba-v2-frozen.json` (`"holdout"`), spec §14.5 (`:288`) and plan line 4.
- **Inconsistency:** the 2026-10-17 audit writes `2016-01-26 → 2021-12-31` (`docs/research/intraday-audit-2026-10-17.md:85`).
  - Spec §14.12 (`:366-368`) explains why: Alpaca has no bars before 2016-01-04, and the prefilter's 15-day warm-up
    makes 2016-01-26 the first *tradable* day.
  - So 01-26 is the first trade inside the holdout, not a different boundary.
- **Unambiguous?** Yes. 2016-01-04 is the first NYSE session of 2016, so this is exactly the set of trading days of
  calendar years 2016–2021, which matches ARCHITECTURE R2.
- **Engine encoding:** `scalp_contracts/holdout.yaml` seals the ET calendar range **2016-01-01 → 2021-12-31
  inclusive**, i.e. UTC `[2016-01-01T05:00Z, 2022-01-01T05:00Z)`.
  - This covers the same trading days, and also seals non-trading-day rows (news on 2016-01-01..03).

**A3.2 Code that reads or could read holdout rows.**
- **F:** Several stockbot paths can read holdout rows:
  - `zba_v2.holdout` (`:329-361`), selected by pushing to branch `zba-v2-run-holdout` (`zba-v2.yml:8,18`).
  - `zba_v2.dev --start/--end` (`:301,429-430`), with no guard.
  - `ticket_backtest --zba --start/--end` (`:951-956`).
  - Every `alpaca.*` function, which accepts any dates.
  - The single-shot guard globs only `<out>/ticket-zba-v2-holdout-*.md` (`:333`), so a different `--out` defeats it.
    The workflow adds a git-log check (`zba-v2.yml:26-30`).
- **F:** Development runs read into the holdout for warm-up. The dev window starts 2022-01-03, so the lookback
  `days[0] − 60` reaches back to 2021-11-04 (`zba_v2.py:165,285`; `ticket_backtest.py:952,970`). Daily bars, opening
  bars and minute volumes from Nov–Dec 2021 fed features for early-2022 development days. No outcomes were computed
  on those days.
- **I:** This is a minor feature-window bleed into the holdout's last two months.
- **R:** The engine's data guard refuses any read whose range overlaps the holdout, warm-up included. The first engine
  feature date that needs N sessions of history is therefore N sessions after 2022-01-03.

**A3.3 Periods ZBA used.**
- §11–13: tuning 2022–2023; verdict from 2024-01-02 / 2024-02-01 (`ticket_backtest.py:734-737`).
- v2 development: **2022-01-03 → 2026-10-08** (`zba_v2.py:23`).
- Holdout as above.
- The ZBA paper's own sample was 2016–2023 (`ticket_backtest.py:757`). So the repo's "unseen" holdout was in-sample
  for the published strategy.

**A3.4 How often the holdout was touched.**
- **Full evaluations: 1.** Run 38003155445, commit **3cbde91** (2026-10-10).
  - Artifacts: `docs/backtest/ticket-zba-v2-holdout-2026-10-10.md`, and
    `docs/backtest/ticket-zba-v2-holdout-2026-10-10-realistic.csv` (2,971 rows; dates 2016-01-26..2021-12-27, from
    metadata).
  - **Verdict: X5 NO-GO** (`.md:5`).
- **Earlier partial contact:**
  - STEP 0 probe run 37917832860 (`bot/probe_v2.py:27-29`): top-20 lists, news, quotes and survivorship on 12 holdout
    sessions (spec `:213-214`).
  - `bot/probe_v2b.py:33-37` (2016 and 2019 dates).
  - A pre-2016 availability probe, commit cb31ab7.
- **Later contact, after the verdict:**
  - Forensics on branch `zba-v2-forensics` (4fc80e2 / 7cd0def, not merged).
  - `docs/backtest/zba-v2-closing-analysis.py:64-67` pools the development and holdout CSVs.
  - Spec §15 (`:416-418`) sizes later power gates from the pooled figure.
  - The 2026-10-17 audit quotes holdout statistics (`intraday-audit-2026-10-17.md:26-27,37`) while calling the
    holdout "untouched" for its new plan (`:85`).
- **Parameters changed after the holdout: none found.** The only change since 3cbde91 to the modules involved is the
  IEX `feed` argument in `alpaca.py`.
- **Other research lines** read 2016–2021 **daily** data:
  - Ensemble: bars from 2016-01-04, signals from 2017-01-03 (`probe_ensemble.py:224`, `ensemble.py:26`).
  - Insider: from 2016-01-04 (`probe_insider.py:28`).
  - Squeeze: from 2017-09-01 warm-up (`backtest.py:23,27`).
  - Index: from 2005 (`index_data.py:11`).
  - Only ZBA used 2016–2021 **intraday** data.

**A3.5 Stop-condition assessment.**
- **F:** The boundaries are unambiguous (A3.1).
- **F:** No evidence was found that holdout *labels or outcomes* trained or tuned any code the engine would reuse.
  - The reusable pieces (`alpaca.py`, `simulate`, `stats`, `ticket` primitives, `tod_fn`, `dq_reason`) were frozen
    before the holdout run, and no parameters changed after it.
  - The Nov–Dec 2021 warm-up reads (A3.2) are feature inputs to development days, not training on holdout outcomes.
- **Judgement:** the stop condition is **not met**, so B3 proceeds.
- **Caveat:** the holdout *outcome* demonstrably shaped later decisions: §15 planning, the intraday audit, and this
  engine's own §0 ("ZBA failed on exactly this"). That is the substance of **D1**, not of the stop condition.

---

## A4. Data inventory (metadata only)

**A4.1 No local market data exists.**
- **F:** There is no Alpaca cache anywhere on this machine.
  - `D:\investor\.cache` does not exist; nor does `.cache` in any worktree or under `C:\Users\t14`.
  - No `.parquet`, `.sqlite`, `.db`, `.duckdb`, `.pkl` or `.gz` files exist under `D:\investor`.
- **E:** The caches exist only inside GitHub Actions caches: keys `zba-v2-<mode>-<run_id>-1..6`
  (`zba-v2.yml:41-42,57-58`) and `ensemble-inputs-<run_id>-1..4` (`ensemble.yml:32-33,132-135`). The most recent
  cache-only job found nothing: "coverage 0 sessions" (`docs/backtest/intraday-signal-count-2024.md:3,7`, commit
  1939f52). That is consistent with the Actions cache's 7-day eviction (inferred, not verified).
- **I:** P1 starts from zero data, and every engine dataset must be fetched fresh.
- **R:** Store engine raw data locally under a gitignored path, never in the Actions cache (see A6 redistribution
  terms).

**A4.2 Cache format the stockbot code would produce (from code, not files).**

| Property | Value |
|---|---|
| Source | Alpaca |
| Historical feed | SIP: `feed="sip"` (`alpaca.py:70`); quotes and trades SIP (`:152,261,289`) |
| Live feed | IEX: `live_bars(feed="iex")` (`:105`), never cached (`:109`) |
| Adjustment | raw (`:75`) |
| Timeframes | 1-minute, 5-minute and daily |
| Timestamps | converted to New York local strings with no offset (`_ny`, `:60-61`) |
| Ingestion timestamp | none |

**A4.3 What is committed.** None of it is market microdata.
- `data/index/h15-3m-tbill.csv`: Fed H.15, 8,566 rows, 1993-12-01 → 2026-09-30, columns `date,value`, sha256
  checked by its manifest (`bot/index_data.py:17-25`).
- `data/index/manifest.json` also describes Stooq SPY daily (2005-02-25 → 2026-10-12). That file lives only in the
  private repo `danielsuisa/stockbot-data`, cloned with `DATA_REPO_KEY` (`index-blind.yml:28-37`).
- `data/*.json`: bot state and journals, 2–634 KB.
- `docs/backtest/*.csv`: backtest outputs, not market data. The largest is `ensemble-tickets.csv`: 35,083 rows,
  2017-01-04 → 2026-03-31.

---

## A5. Look-ahead risks in reusable code

1. **Survivorship.**
   - **F:** Universes are built from today's Alpaca asset list.
   - **E:** `ticket_backtest.py:379-383`, `alpaca.py:171-182`, `intraday.py:247` (active only). Spec 14.0c (`:222-228`)
     notes that 2016–2018 delistings are missing or their symbols reused.
   - **I:** An estimated 10–25% of 2016 names are absent, so backtests are biased upward.
   - **R:** A daily universe snapshot contract with `snapshot_date` and `ingested_at` (ADR §3.5); a symbol-history
     table later.
2. **News availability.**
   - **F:** `news_symbols` keeps tags but no `created_at`, `updated_at` or fetch time.
   - **E:** `alpaca.py:234-251`.
   - **I:** Tags added after the fact can leak into a pre-09:35 catalyst set.
   - **R:** The raw news contract stores `event_at`, `updated_at` and `ingested_at`; features filter on
     `available_at`.
3. **Corporate actions.**
   - **F:** Raw prices are point-in-time correct, but splits are handled only by a ≥2× close-jump exclusion.
     `index_probe.py:323` uses `adjustment="all"`, which is retroactive.
   - **E:** `alpaca.py:75`; `ticket_backtest.py:397,414`.
   - **I:** A 3:2 split distorts ATR and average volume; `adjustment="all"` leaks future splits.
   - **R:** Store raw bars only, plus a corporate-actions table with `ex_date` and `ingested_at`. Adjust only as of
     the decision time.
4. **Daily features.**
   - **F:** OK. ATR, ADV and prior close use only bars strictly before the day.
   - **E:** `ticket.py:74`; `ticket_backtest.py:417-426`; `zba_v2.py:52-54`; `intraday.py:256`.
   - **R:** Keep, and enforce through `available_at` assertions.
5. **Intraday rules.**
   - **F:** OK. The opening bar is used only at 09:35 or later (`ticket.py:282-297`); `tod_fn` and `dq_reason` use
     only earlier minutes (`zba_v2.py:93,104-107`); candles are completed only (`intraday.py:198-201`).
   - **Residual risk:** the intra-minute path assumption on the fill minute (`ticket_backtest.py:28-35`).
6. **Partial live day cached as complete.** See A2.2.
   - **R:** Never cache an `event_at` date ≥ the current session, and record `ingested_at` on every row.
7. **Feed mismatch.**
   - **F:** Live uses IEX, research uses SIP.
   - **E:** `intraday.py:273,324` vs `alpaca.py:70`.
   - **I:** RVOL and volume thresholds do not transfer between the two feeds.
   - **R:** `feed` is a mandatory column; features are versioned per feed.

---

## A6. Free data-source capability matrix

Sources are official vendor docs fetched 2026-10-17.
- IBKR: interactivebrokers.github.io/tws-api (`historical_limitations.html`, `historical_data.html`,
  `historical_bars.html`, `historical_time_and_sales.html`, `market_data.html`). The campus and pricing pages
  returned HTTP 403.
- Alpaca: docs.alpaca.markets (`/docs/about-market-data-api`, `/us/docs/market-data-faq`,
  `/docs/historical-stock-data-1`, `/reference/stockbars`, `/us/reference/stockquotes-1`,
  `/reference/corporateactions-1`, `/changelog/marketdata-bid-and-ask-size-display-change`);
  alpaca.markets/support/redistribute-alpaca-api.
- yfinance: ranaroussi.github.io/yfinance/reference/yfinance.price_history.html; pypi.org/project/yfinance.
- SEC: sec.gov/os/accessing-edgar-data; sec.gov/search-filings/edgar-application-programming-interfaces;
  sec.gov/edgar/search/efts-faq.html.
- GDELT: blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime; blog.gdeltproject.org/gdelt-doc-2-0-api-debuts;
  gdeltproject.org/about.html.

| | IBKR TWS API | Alpaca Basic (free) | yfinance | SEC EDGAR | GDELT 2.0 |
|---|---|---|---|---|---|
| 1-min bar depth | No 6-month cap for ≥1-min bars; about 1 day per request | **Since 2016** | 60 days | n/a | n/a |
| Feed | IB's filtered feed. The API **requires a Level-1 streaming subscription** for historical data | IEX by default; **SIP if `end` is at least 15 min old** | Yahoo, unofficial | n/a | n/a |
| Historical quotes / NBBO | `reqHistoricalTicks` BID_ASK, 1,000 ticks per request (subscription needed) | **SIP quotes free if at least 15 min old**, 10k per page | none | n/a | n/a |
| Corporate actions | TRADES split-adjusted only | `/v1/corporate-actions`; "no guarantees on creation time" | splits and dividends | 8-K filings | n/a |
| Delisted symbols | **Not available** | Partial, not guaranteed | no | yes (all filers) | n/a |
| Rate / pacing | 60 req per 10 min; 15 s identical-request rule; BID_ASK counts double | 200 req/min | undocumented | 10 req/s with a User-Agent | undocumented |
| Storage terms | Exchange-licensed, no publishing | **No redistribution** | personal use only | free | free; redistribution allowed with citation |
| 3y × 500 sym × 1-min (≈147M bars) | 378,000 requests at 60/10 min ≈ **44 days** | 14,742 pages at 200/min ≈ 74 min, **≈1.5–3 h** with overhead | impossible | n/a | n/a |

**Fields with no free historical source.** This is the input to D3.

| Field | Status |
|---|---|
| Intraday VIX | **No free source.** Cboe publishes daily history only |
| NYSE intraday advance/decline breadth | **No free source** |
| Multi-year LULD halt history | **No free source.** Nasdaq Trader keeps about 1 month of halt history (about 1 year via search, unverified) |
| Earnings calendar history | **No free source.** Proxy: EDGAR 8-K Item 2.02 filing timestamps |

Available free, but with a cost:
- **Historical NBBO spread and bid/ask imbalance:** Alpaca SIP quotes.
  - **Fetch time:** ~13 days for the full 500-symbol universe, assuming ~100k quotes per symbol-day.
  - **Unit change:** quote sizes switch from round lots to shares on 2025-11-03.
  - **Feasible plan:** quotes only at decision minutes, or for a subset.
- **SIP volume for RVOL:** Alpaca SIP bars.

**Regulatory check (input to the PDT amendment).**
- FINRA Regulatory Notice 26-10 confirms the change (finra.org/rules-guidance/notices/26-10).
  - SEC approval on 2026-04-14.
  - Effective 2026-06-04.
  - Firms may phase in until 2027-10-20.
  - The day-trading margin requirements are "replace[d] in their entirety". That removes the PDT designation and
    the $25,000 minimum.

---

## A7. Telegram infrastructure (for L9)

- **F:** These building blocks are reusable:

  | Function | Behaviour |
  |---|---|
  | `common.tg(method, **params)` (`bot/common.py:135-155`) | POSTs to the Bot API; waits out 429s; raises on 401/404 |
  | `common.send(text, chat_id=None, signal=False, source=None, buttons=None)` (`:186-212`) | HTML; chunks under 4,000 characters; about 1 message/s; `buttons` is a raw `reply_markup` on the last part; plain-text fallback on 400; dry-run when `TG_TOKEN` is unset |

- **Auth:** environment variables named the same as the GitHub secrets: `TG_TOKEN` and `TG_CHAT_ID`. Messages from
  any other chat are ignored (`listen.py:368-370`).
- **Listener:** long-poll, `POLL = 50` s (`listen.py:176`). It relays itself every ~5h20m through `workflow_dispatch`
  and passes the offset along (`:437-498`).
- **F:** The three L9 buttons are not supported today: there is **no `callback_data` handling anywhere**.
  - The listener requests `allowed_updates=["message"]` only (`listen.py:458,501`).
  - Buttons are URL-only (`tests/test_buttons.py:18,33`).
- **I:** Callback buttons need `callback_query` added to the listener's updates, plus an `answerCallbackQuery`
  handler. That is a change to the live listener, which is forbidden here. A second `getUpdates` consumer would
  collide with the live one (409; `listen.py:343-357`).
- **R:** L9 may reuse `common.send` as-is for outbound cards. For button feedback, there are two options for the
  user at P8:
  - an approved change to `listen.py`, or
  - a separate bot token used only by the engine.
