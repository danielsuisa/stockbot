# Screener engine — final report

## Follow-up: float from SEC bulk data, stage 2 sized by budget (this PR)

**Status: COMPLETE.** The done criteria F1–F6 are met; RTH evidence (F4) was taken in today's session.
Branch `feature/screener-float` (from `origin/main` `c700bfe`, after PR #28); not merged.
The squeeze is untouched, and moving it onto the engine is the next step (gated on this PR).

### What changed

| item | change | where |
|---|---|---|
| A insiders | The last 8 quarters of SEC's **Insider Transactions Data Sets** (flattened Forms 3/4/5, one ZIP a quarter, streamed; 343,199 filings for universe issuers; 9 SEC requests). The daily index covers the days after, skipping accessions already read. Holdings are kept **per line** (class × direct/indirect × nature): a Form 4 that reports one line keeps the others. An amendment supersedes the original in any read order; joint filings, and owners that file jointly on some forms and alone on others, count once. | `stores/ownership.py` (D-045, D-046, D-055) |
| B 5%+ holders | **Schedules 13D/13G as XML** (OBSERVED on a 13G and a 13D/A). Filings are discovered from the quarterly and daily form indexes, then only the latest per (issuer, filer) is fetched: small caps first, ≤ 300 a night, known 13d-1(b) institutions skipped. Coverage is tracked per issuer. A group counts at its largest reporting person; a holder that also files Forms 3/4/5 counts once, at the larger amount. | `stores/holders.py` (D-047, D-048) |
| C float | **outstanding − officers/directors − 10% owners − other ≥ 5% holders** (qualified institutions under Rule 13d-1(b) stay in the float), point in time. `float_method`: `insiders+5pct` / `insiders` / `issuer_public_float` (dei:EntityPublicFloat carried by affiliate count × split factor, only without any holdings data) / `outstanding_proxy`. Never ≤ 0 (`float_flag`). Shares outstanding fall back to us-gaap frames where the dei cover count is missing (1,564 → 1,150 stocks without one). Rule 144 shares stay unavailable. The message footnotes the methods. | `metrics/registry.py`, `stores/publicfloat.py`, `stores/fundamentals.py`, `format.py` (D-049, D-053, D-054) |
| D stage 2 | The fixed 1500 cap is replaced by a **planner**. Estimate = ceil(n/800) requests + ceil(n × bars so far / PAGE_POINTS) pages; every survivor is fetched when the run's remaining budget allows (150 − 10 kept back − calls made). Otherwise the existing ranking cuts and the cut is reported. **OBSERVED:** Alpaca pages 30-minute multi-symbol bars at 588–840 bars, not 10,000, so PAGE_POINTS = 580 and the whole universe (~10,000 symbols, ≈ 255 calls) does not fit. `unusual_volume_liquid`'s stage 1 does. | `evaluate.py` (D-040 revised) |
| E rel. volume | A **static time-of-day curve** (the median of 4,800 symbol-days, OBSERVED), used from the open when no profile exists ("≈"). A slot whose daily bars stopped at the budget **sends no first list**; the next slot does. | `stores/bars.py`, `evaluate.py`, `run.py` (D-050, D-051) |
| splits | Holdings, 13D/13G amounts and the share count are **scaled by the splits since their date** (Alpaca corporate actions; BKYI's 1:10 reverse split had left a float of 304k of 1.09M shares). | `stores/splits.py` (D-057) |
| maintenance | A **watchdog** ends the refresh at 23 min, so the cache is still saved (main's first full refresh 39806511832 was killed at the 25-min step timeout and saved nothing). Each store logs a step line. The long store is bounded to 6 min a run and resumes; 13D/13G runs before it. The guard diffs against the merge base. | `run.py`, `stores/bars.py`, `guard.py` (D-052, D-056) |

### Done criteria

| | result | evidence |
|---|---|---|
| **F1 (a)** float vs Yahoo (squeeze journal, 23 entries, point in time) | **median 6.4%, P90 17.5%** (n = 22; target ≤ 15% / ≤ 35%). PR #28: 41.5% / 246.7% (n = 17) | [parity-report.md § F1](parity-report.md#float-follow-up-2026-10-22-f1f3) |
| F1 (b) vs dei:EntityPublicFloat (raw close on/before `end`) | all 2,780 companies: median 6.5%, P90 63.1% vs (ii); shares outstanding 10.7% / 99.9%. The 23: 17.9% / 34.1%. Issuer-reported and high by definition, not ground truth | same |
| F1 (c) hand checks (DEF 14A tables) | BKV: engine and (ii) right (37.44M from the proxy), Yahoo 16% low. BCAX: Yahoo right, engine +22% (an IPO-era Form 3 outside the 8 quarters). AVTX: undetermined (a later offering; 13d-1(c) funds a definition choice) | same |
| F1 decision | `issuer_public_float` = (ii), only without any holdings data (D-053) | DECISIONS.md |
| **F2** squeeze replay, 7 sessions (16:30 ET lists of 10-13..10-21) | **mean Jaccard 0.664, mean recall 0.915** (target ≥ 0.50 / ≥ 0.80). PR #28's float: 0.143 / 0.160. Misses: 3 × TNXP (float source: 15.1M vs Yahoo's 14.1M → short float 19.4%) | [parity-report.md § F2](parity-report.md#f2-squeeze-parity-by-historical-replay) |
| **F3** screens 6 and 7 | engine 15 → **69** and 0 → **6**; the finviz side stays PENDING_MANUAL (robots.txt) | parity-report.md, parity-manual-checklist.md |
| **F4** Actions on the branch | Maintenance [39810920705](https://github.com/danielsuisa/stockbot/actions/runs/39810920705): green, 23 min (watchdog), SEC 1000, Alpaca 1415, bulk 8 quarters + 2 index days, cache saved. RTH slot [39815969748](https://github.com/danielsuisa/stockbot/actions/runs/39815969748) at 12:41 ET: green, **Alpaca 51 < 150, SEC 0**, `unusual_volume_liquid` stage 1 = stage 2 = 2,980, **truncated 0**, 200 rows, sent. MAINT2 | Actions logs |
| **F5** tests | **1034 → 1065**, all green. Known values: a 13G holder that also files Form 4 counts once; a 13D group larger than its Form 4 lines counts once at the larger amount; amendment supersedes in any order; joint filing counts once; a holder filing jointly and alone counts once; float never ≤ 0; holdings and share counts from before a reverse split scaled (BKYI); planner estimate vs the paging rule; budget < 150 at full universe; relative volume with an empty profile store at 10:30; a partial slot defers its first list | `tests/test_screener_*.py` |
| F6 | this report, STATE.md, DECISIONS.md (D-040 revised, D-045..D-057), metric-spec / data-sources / architecture updated; PR opened, not merged | |

Guardrails: no finviz load in this task (G1/G2), no Yahoo/nasdaq.com in the engine (G3: the Yahoo floats are
the squeeze's own records, read from `data/squeeze_journal.json`). No trading endpoint (G5). Every existing test
unchanged and green, apart from the screener's own truncation and Budget tests, which the brief asked to update (G6).
Secrets only via the existing env (G7). Each commit passes the tests and the guard (G8). Every approximation is
marked "≈" (G9). The FROZEN list is unchanged, and `data/squeeze_*` was read only, from history. Cost $0.

### Remaining gaps

1. **13D/13G coverage builds over nights.** Discovery found 27,347 latest filings for 5,358 issuers. Fetched ≤ 300 a
   night, small caps first, with known 13d-1(b) institutions skipped, the backlog takes weeks. Until an issuer is
   covered its float is `insiders` (Forms 3/4/5 plus the holders read so far), which overstates the float where 5%+
   holders exist.
2. **Insiders older than 8 quarters** are missed (BCAX's IPO Form 3s of 2024q3). `BULK_QUARTERS` is one constant:
   12 quarters would cost 4 more requests once.
3. **1,150 stocks have no share count** in any SEC frame (20-F filers such as MMYT, SPACs), so no float. Reading
   each filing's inline XBRL would cost 2–3 requests a company.
4. Point in time is approximate: Forms 3/4/5 by period of report, 13D/13G by filing date, and only each holder's
   latest 13D/13G is kept.
5. 13d-1(c) funds (specialist biotech holders) are subtracted. That is a definition choice that F1 (c) could not
   settle (AVTX).
6. The relative-volume basis differs from the squeeze's (whole day vs time curve, against RTH volume / 3-month
   average). It is the main cause of F2's engine-only names. finviz's own basis is "intraday adjusted" (PR #28).
7. The whole universe does not fit stage 2 at a 30-minute grain (≈ 255 calls). Screens without a stage-1 filter are
   still cut to the budget, and the cut is reported.
8. Main's first refresh (39806511832) timed out before this PR's watchdog existed. Main's cache is still partial,
   and the first nightly refresh after the merge carries on from it.

---

## PR #28 (the engine)

**Status (PR #28): PARTIALLY COMPLETE.** The engine, Telegram commands, scheduling and workflow are built, tested and
verified on Actions and in the bot chat. Parity is partial:
- per-metric values match finviz within tolerance, exactly for price, change, volume, previous close and the
  52-week distances;
- set overlap passes on 1 of 5 allowed signal screens;
- the 7 filter screens are **PENDING_MANUAL**, because robots.txt disallows loading them.

Branch `feature/screener-engine`; not merged.

## What was built

A finviz-compatible screening engine in `bot/screener/` (stdlib only, like the rest of the repo). It takes finviz
codes, for example `cap_small,sh_float_u50,sh_short_o20,ta_rsi_os30 o=-change s=ta_unusualvolume`, or a pasted
screener URL, which is parsed and never fetched. It returns matching tickers with their values, and every
approximated value carries a "≈" marker.

| part | where |
|---|---|
| Filter catalog: 82 filters, 2459 options, 35 signals, statuses, evidence | `bot/screener/catalog.yaml`, `catalog.py` |
| Parser: codes, `o=` `s=` `v=` `t=`, URLs, strict Telegram input rules | `parser.py` |
| Stores (Alpaca SIP, Nasdaq Trader, OCC, SEC frames/submissions/Form 4, FINRA), cached in `.cache/screener` | `stores/` |
| 120+ metric calculators with source / approx / evidence | `metrics/technical.py`, `metrics/fundamental.py`, `metrics/registry.py` |
| Two-stage evaluation (1500-symbol stage-2 cap), signals, sorting | `evaluate.py`, `signals.py` |
| Hebrew messages, presets, scheduled runs, `/screen`, `/screens` | `format.py`, `presets.py`, `presets.yaml`, `schedule.py`, `commands.py`, `run.py` |
| Workflow (dispatch only, own concurrency group, cache, commit-with-retry, alarm) | `.github/workflows/screener.yml` |
| Listener wiring (routes, `/help`, the "/" menu, schedule tick) | `bot/listen.py` (+18 lines) |
| Docs: audit, metric spec, data sources, architecture, parity, decisions, state | `docs/screener/` |

## Coverage (D1)

Every public (non-Elite) filter and signal read from finviz's own filter definitions (OBSERVED 2026-10-21) is in
the catalog with a status and, for non-exact ones, a one-line reason. Elite-only filters (5) are catalogued too and
flagged.

| | IMPLEMENTED_EXACT | IMPLEMENTED_APPROX | UNAVAILABLE | total |
|---|---|---|---|---|
| Public filters | 19 (24.7%) | 44 (57.1%) | 14 (18.2%) | 77 |
| Their options | 747 (31.4%) | 1115 (46.9%) | 515 (21.7%) | 2377 |
| Signals | 9 (25.7%) | 6 (17.1%) | 20 (57.1%) | 35 |
| Metrics (source map) | 60 EXACT (43.8%) | 61 APPROX (44.5%) | 16 UNAVAILABLE_FREE (11.7%) | 137 |

Unavailable, and why:
- **Licensed estimates and analyst data:** forward P/E, PEG, EPS growth this/next year, long-term growth,
  EPS/revenue revisions, analyst recommendation, target price, upgrades/downgrades.
- **Proprietary:** chart patterns (`ta_pattern_*`, `ta_p_*`), themes.
- **No ToS-clean free source:** index membership (D-020), institutional ownership (D-021).
- **No free real-time source:** halts, shares available to short.

Option evidence: 2205 OBSERVED, 172 INFERRED.

## Parity (D4) — [parity-report.md](parity-report.md)

53 of the allowed 60 finviz loads were used:
- 6 for robots.txt, the help page and the filter definitions;
- 47 for parity: the robots-allowed preset pages only, ≥ 20.5 s apart, unauthenticated, at most 3 pages per screen.

Only aggregates were kept.

| screen | Jaccard (top 30) | recall within the engine's list | per-metric | result |
|---|---|---|---|---|
| 1 `s=ta_topgainers` | 0.500 | 1.00 | all within tolerance | FAIL (set) |
| 2 `s=ta_unusualvolume` | 0.579 | 1.00 | all within tolerance | FAIL (set) |
| 3 `s=ta_newhigh` | 0.463 | 0.90 | all within tolerance | FAIL (set) |
| 4 `s=ta_oversold` | 0.600 | 1.00 | (charts view: tickers only) | FAIL (set) |
| 5 `s=ta_mostactive` | 0.935 | 0.97 | all within tolerance | **PASS** |
| 6–12 filter screens | — | — | — | **PENDING_MANUAL** ([checklist](parity-manual-checklist.md)) |

**Values.** Price, change, previous close, volume and the 52-week high/low distances deviate by a median of 0.0.
Average volume is within 0.8–3.1% (tolerance 5%), and market cap within 0–1.9%.

**Sets.** Every finviz top-30 name is in the engine's list, but the engine also ranks names finviz's top 30 does not
show (common stocks, often under $1). New High finds 112 against finviz's capped 200. These are
universe-composition and high-basis differences. A single calibration round was used (G3), followed by one
measurement of the final configuration:
- average volume leaves out the measured session (D-043);
- 52-week distances are measured from the prior sessions (D-044);
- ETFs stay out of stock screens (D-036b).

## Verification

| check | evidence |
|---|---|
| D3 tests | baseline **904** → **1034** (`python -m unittest discover -s tests`, all green; Windows needs `tzdata` and `PYTHONUTF8=1`, Linux CI does not) |
| Known-value fixtures (F2) | Wilder RSI(14) on the canonical series (70.46 first value), ATR with gap days (1/14 and 2/15), SMA crosses, 52-week high including today, relative volume at 10:30 from a synthetic profile, TTM with Q4 = FY − 9M, float fallback with its flag, the $2B cap boundary, strict `o20` at exactly 20 |
| D5 workflow on the branch | refresh [39707815644](https://github.com/danielsuisa/stockbot/actions/runs/39707815644): green, 21.5 min on an empty cache (the full backfill). Alpaca 2687, SEC 2232; the SEC count is now capped below 2000 per run (`fab63c3`). Verify [39710097398](https://github.com/danielsuisa/stockbot/actions/runs/39710097398): green, 20 s, Alpaca 0, SEC 0, FINRA 1. Preset [39710188677](https://github.com/danielsuisa/stockbot/actions/runs/39710188677): green, 20 s, sent the `unusual_volume_liquid` list to the bot chat |
| Scheduled-run budget | `tests/test_screener_run.py::Budget`: a 10,000-symbol RTH run = 18 Alpaca calls (< 150), 0 SEC (< 2000) |
| D6 Telegram | Sent from the listener's handler locally against the real bot on 2026-10-22 02:10–02:11 UTC (messages 492–497): `/screen cap_small,sh_float_u50,sh_short_o20 o=-shortinterestshare`, `/screen unusual_volume_liquid`, `/screens`, plus the existing `/squeeze` and `/status` and the help text (lists `/screen`, `/screens`). The live listener runs `main`, so `/screen` there starts working after the merge |
| D7 sweep | No FROZEN path changed (`python -m bot.screener.guard`). No token patterns or secret values in the diff. No `finviz.com` outside `docs/` (test `NoFinviz`). No cache files tracked. The workflow uses only the existing secrets (TG_TOKEN, TG_CHAT_ID, SEC_UA, ALPACA_KEY_ID, ALPACA_SECRET_KEY). Cost $0: public-repo standard runners and free APIs |
| G5 | No Alpaca trading endpoint is called. The read-only calendar and asset list come from the trading host (D-005) |

## Squeeze migration (H.5): not done

The squeeze reads float, quotes and average volume from Yahoo, which the engine may not use (G3), so its output
cannot be byte-identical. Example: on 2026-10-21 the live squeeze listed 3 stocks (Yahoo float, short ratio > 5).
The engine's squeeze-like screen 7 (SEC shares-outstanding proxy for float, no short-ratio filter) matched 0. The
squeeze, its frozen variant `w0.7/si0.2/rv2`, its journal and its schedule are untouched (D-003, G10).

## Known gaps

1. **Float** = shares outstanding minus officer, director and 10%-owner holdings, which are read from Form 4s from
   the first refresh onward. Until coverage builds up, float falls back to outstanding (flagged "≈"), so short float
   reads low and the low-float screens under-match.
   **Addressed by the follow-up** (bulk Form 3/4/5 backfill, 13D/13G holders, public-float fallback).
2. **Signal set overlap**, as described above. A follow-up could test the open hypotheses on the allowed pages: price
   floor, listing status, the pre/post-market high basis for new highs.
3. **Stage 2 in the regular session is capped at 1500 symbols** (E3; **replaced by the budget planner in the follow-up**). The flagship `unusual_volume_liquid` preset
   can be truncated in RTH. The message says so, and raising `SCREENER_STAGE2_MAX` costs ≈ 54 calls at full
   universe (D-040).
4. **Sector and industry** come from a SIC crosswalk. 15 finviz industries cannot be separated by SIC (for example
   banksdiversified, oilgasintegrated). Reference data fills at 500 companies a night.
5. **Partial daily bars** during the session could not be verified after hours (D-040); stage 2 uses 30-minute bars.
6. `ta_alltime` / IPO date are limited by Alpaca's history start (2016-01).
7. The screener's schedule runs only after the merge (the listener runs `main`). Until then, run it with
   `gh workflow run 392366826 --ref feature/screener-engine -f mode=...`.

## Decisions and assumptions

All are in [DECISIONS.md](DECISIONS.md) (D-001 … D-044). The ones with the most effect:
- D-001: JSON-syntax YAML;
- D-004: the screener's own schedule list;
- D-005: the Alpaca calendar;
- D-006: whole-day volume, OBSERVED;
- D-010: strict over/under;
- D-011: finviz's own code semantics;
- D-035: SEC acceptance times decoded;
- D-036b: no ETFs;
- D-040: stage 2 on 30-minute bars;
- D-041: filters before signals;
- D-042: the "/" menu list;
- D-043 / D-044: the parity calibrations.

## How to add a filter

1. Add or extend the filter in `bot/screener/catalog.yaml` (prefix, metric, unit, family, status, reason, options
   with predicates), or regenerate it from finviz's definitions.
2. If it needs a new metric, add the calculator to `bot/screener/metrics/registry.py` (with its source, approx flag
   and evidence), and a row to `docs/screener/metric-spec.md` and `docs/screener/data-sources.md`. The spec test
   fails until both exist.
3. Add a known-value test. `tests/test_screener_catalog.py::Implemented` checks that every available option has a
   calculator.

## Acceptance checklist

- [x] D1: catalog coverage 100% of public filters and signals, with statuses, reasons and counts (above).
- [x] D2: METRIC_SPEC defines every metric (formula, window, source, latency, evidence), enforced by test.
- [x] D3: known-value tests for every calculator family; 1034 tests green (from 904).
- [~] D4: parity report with Jaccard, precision, recall and per-metric median/P90. 1/5 signal screens pass on sets,
  all metrics within tolerance, 7 screens PENDING_MANUAL.
- [x] D5: workflow dispatched on the branch: green, within the time budget, request counts logged.
- [x] D6: `/screen` (codes), `/screen` (preset) and `/screens` delivered in the bot chat; `/help` lists them;
  existing commands spot-checked.
- [x] D7: security sweep clean; cost $0.
- [x] D8: this report.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
