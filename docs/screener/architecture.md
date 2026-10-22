# Screener engine — architecture (Phase E)

The engine is stdlib-only (D-001) and lives in `bot/screener/`. It reproduces finviz's screening *mechanism*: filter
codes, thresholds, metric definitions and screen semantics, on free data (`data-sources.md`).

```
text ─▶ parser ─▶ Query{filters:[Predicate], signal, sort, view, ignored}
                     │
stores (cached) ─▶ evaluate ── stage 1: static + daily filters on the stores (no network)
                     │        stage 2: today's 5-minute SIP bars for the survivors (≤ 1500) → intraday metrics
                     ▼
               signals / sort ─▶ Result{rows, ignored, counts, as_of, truncated} ─▶ format (Hebrew, ≈ marks) ─▶ Telegram
```

## Modules

| module | role |
|---|---|
| `catalog.py` + `catalog.yaml` | The filter catalog. `resolve(code)` returns a `Predicate` (prefix, option, metric, predicate tuple, status, evidence, approx). `check(pred, value, scale)` evaluates it. Custom grammar and `|` OR. |
| `parser.py` | Text (codes, `o=` `s=` `v=` `t=`, query strings, pasted URLs, never fetched) → `Query`. Unknown or unavailable codes go to `Query.ignored` with a reason (E2). `strict=True` applies the Telegram input rules (H1). |
| `stores/__init__.py` | gz-JSON stores under `.cache/screener/` (`SCREENER_CACHE` overrides): atomic write (temp + rename), `meta` (`as_of`, `source`, `rows`), max-age staleness, per-source request counters. |
| `stores/reference.py` | Universe (Nasdaq Trader ∪ OCC ∪ Alpaca assets ∪ SEC ticker map), the trading calendar (Alpaca, weekday fallback), per-CIK reference (SIC, country, 8-K Item 2.02 times) from SEC submissions. |
| `stores/bars.py` | Daily split-adjusted SIP bars (300 sessions, incremental, split-safe refetch), the long store (monthly bars since 2016, 3Y/5Y/10Y anchors), the 30-minute volume profile, and the live intraday fetch for stage 2. |
| `stores/shortint.py` | Newest usable FINRA short-interest report (reuses `bot/shorts`). |
| `stores/fundamentals.py` | SEC XBRL frames → per-CIK quarterly/annual facts with tag fallbacks; TTM with Q4 = FY − 9M. |
| `stores/ownership.py` | Form 3/4/5 holdings per issuer and reporting owner (EDGAR daily index; own small XML reader, D-012). |
| `metrics/` | Pure calculators. `technical.py` (SMA, RSI, ATR, volatility, high/low, performance, crosses, beta, candlesticks, SSR), `fundamental.py` (TTM, ratios, growth), `volume.py` (average and relative volume). `metrics/__init__.py` holds the registry: metric id → calculator + source + approx flag + evidence. `value(ctx, metric)` returns a `MetricValue(value, as_of, source, approx, evidence)`. |
| `sic.py` | Static SIC → finviz-style sector/industry crosswalk. `geo.py`: SEC country/state codes → finviz geo slugs. |
| `evaluate.py` | Builds one lazy per-symbol `Ctx` over the stores and runs the two stages. Stage 2 is capped at 1500 symbols; beyond that it ranks by the sort key on daily data and reports the truncation. |
| `signals.py` | The `s=` signals: candidate rule, ranking and cap (Top 200 / Top 100 / Top 40 / all). |
| `sort.py` | `o=` ordering (`None` last), finviz default = ticker ascending, a signal's own ranking when there is no `o=`. |
| `format.py` | The Telegram text. Up to 15 rows: ticker, price, change and the 3 metrics the filters use, with `≈` on approximated values. Footer: ignored codes and reasons, data time (New York + Israel), sources. Follows `tests/msgrules.py`. |
| `presets.py` + `presets.yaml` | Default presets (version-controlled), user presets in `data/screener_presets.json`, result hashes for dedup in `data/screener_state.json`. |
| `schedule.py` | The screener's own job list (D-004), ticked from `listen.serve` next to `clock.tick`. Dispatches `screener.yml mode=slot`. |
| `commands.py` | `/screen` and `/screens` handlers for `bot/listen.py`. On Actions, the listener dispatches `screener.yml` (it has the stores and `contents: write`). Locally they run in-process. |
| `run.py` | CLI and workflow entry. `python -m bot.screener.run --filters … --sort … --signal … --dry`, plus modes `slot` / `query` / `save` / `delete` / `refresh`. Logs request counts per source. |
| `guard.py` | Pre-commit FROZEN check (G4). |

## Sessions and data time (E4)

`reference.clock(now)` → `{now, day, session ∈ PRE|RTH|POST|CLOSED, prev (last completed session), open, close}` from
the Alpaca calendar (weekday fallback, D-005).

| session | daily history ends at | today | price | change | gap / change from open |
|---|---|---|---|---|---|
| PRE | previous session | 5-min bars 04:00 → now − 16 min | last pre-market trade ("pre-market") | vs previous close | gap = pre-market price vs previous close ("pre-market"); change from open `None` |
| RTH | previous session | 5-min bars | last RTH bar close | vs previous close | as defined |
| POST | previous session | 5-min bars (RTH OHLC + whole-day volume) | RTH close (as finviz); `ah_close` = last POST trade | vs previous close | as defined |
| CLOSED | the last session (its completed daily bar) | none | last close | last close vs the close before | from the last bar |

Every message states "as of HH:MM New York (≈ 15-min delayed)" and the Israel time.

## Two-stage evaluation (E3)

1. **Stage 1** splits the predicates into *static/daily* (computable from the stores alone) and *intraday* (needs
   today's bar: price, change, gap, change from open, volume, relative volume, and any technical computed with today's
   price) and evaluates the static/daily ones for the whole universe.
   - In PRE/RTH/POST the intraday ones are pre-checked on the last close only when that is safe, which never holds for
     a strict screen. So stage 1 applies only the static/daily ones.
   - When the stage-1 survivors exceed 1500, they are ranked by the sort key (or the signal's ranking) on daily data,
     and the cut is reported as "truncated".
2. **Stage 2** fetches today's 5-minute bars for the survivors (one request per ≤ 800 symbols, paged), appends
   today's partial bar to their daily series, and evaluates every predicate.
3. In CLOSED, there is no stage 2: everything comes from completed daily bars.

## Failure behavior

- An unknown or unavailable code is ignored and listed (E2).
- A missing store or a stale store makes its metrics `None` and adds a "stale: <store>" note. A screen that depends
  only on such metrics returns no rows and says why.
- A data source that fails during a run → the run reports it once (the existing `sessions.first`-style warning) and
  exits 0. The workflow alarm is kept for crashes.

## Budget

`run.py` logs `{"alpaca": n, "sec": n, "finra": n, "nt": n, "occ": n}` per run. `tests/test_screener_budget.py` asserts
that the planner keeps a full scheduled run under 150 Alpaca and 2000 SEC calls for a 10,000-symbol universe.
