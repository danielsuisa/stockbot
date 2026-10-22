# Screener engine — STATE

## Follow-up: float and stage 2 (`feature/screener-float`)

| | |
|---|---|
| Branch | `feature/screener-float` from `origin/main` `c700bfe` (PR #28 merged as `720fdfc`) |
| Status | DONE: PR opened, not merged (FINAL_REPORT.md is its body) |
| A insider backfill | DONE: 8 quarters of SEC's Insider Transactions Data Sets (343,199 filings on Actions), line-level store v2, accession dedupe (D-045, D-046, D-055) |
| B 5%+ holders | DONE: XML Schedules 13D/13G (D-047, D-048); nightly backlog ≤ 300 filings, small caps first, known 13d-1(b) filers skipped |
| C float | DONE: outstanding − insiders − other ≥ 5% holders; methods `insiders+5pct` / `insiders` / `issuer_public_float` / `outstanding_proxy`; never ≤ 0 (D-049, D-053); shares outstanding fallback (D-054) |
| D stage 2 | DONE: budget planner at the OBSERVED intraday page size (D-040 revised) |
| E relative volume | DONE: static fallback curve (D-050); a partial slot defers the first list (D-051) |
| F1 | PASS on (a): median 6.4%, P90 17.5% (n = 22; MMYT has no share count); (b) and (c) in parity-report.md |
| F2 | PASS: 7 sessions, mean Jaccard 0.664, mean recall 0.915 |
| F3 | Screen 6: 15 → 69; screen 7: 0 → 6 (finviz side PENDING_MANUAL) |
| F4 | Maintenance 39810920705 green (SEC 1000, watchdog at 23 min, cache saved); RTH slot 39815969748 green (Alpaca 51, SEC 0, stage 2 = all 2,980 survivors, not truncated); maintenance 39816377737 (14.9 min, SEC 1233, every step) and 39818693372 on the final code (16.0 min, SEC 1593) green |
| F5 | 1034 → 1065 tests, all green (`PYTHONUTF8=1`, `tzdata`) |
| Guard | `python -m bot.screener.guard` clean before every commit (now against the merge base, D-056) |

### Commits

| SHA | Subject |
|---|---|
| 4157cc5 | feat(screener): size stage 2 by the run's Alpaca budget; static relative-volume curve; defer a partial first list |
| 1afb9b7 | feat(screener): insider holdings backfilled from SEC's quarterly Form 3/4/5 data sets |
| 9178b87 | feat(screener): 5%+ holders from Schedules 13D/13G (XML); float = outstanding - insiders - 5%+ holders |
| 7ee509a | fix(screener): refresh watchdog and step log; skip known passive 13G filers |
| 5e95303 | docs(screener): decisions D-045..D-052, D-040 revised; float, rel_volume, sources and architecture |
| bb6a24d | fix(screener): a holder filing both Forms 3/4/5 and 13D/13G counts once, at the larger amount |
| 93289db | feat(screener): issuer_public_float fallback (dei:EntityPublicFloat carried by affiliate count x split factor) |
| 9a80d98 | feat(screener): shares outstanding from us-gaap frames when the dei cover count is missing |
| 8b45824 | fix(screener): owner groups, a bounded long store, 13D/13G before it; guard diffs the merge base |
| 9e8cbcb | fix(screener): scale holdings and share counts by the splits since they were filed |
| 780eeb0 | docs(screener): F1-F5 evidence, state and final report; D-053..D-057 |
| b2b23b2 | style(screener): wrap long lines |
| (this commit) | docs(screener): F4 maintenance runs |

### Open items

- The 13D/13G backlog (27,347 latest filings at discovery, minus known passive filers) clears at ≤ 300 a night;
  until then most issuers' float is `insiders` (Forms 3/4/5 plus the holders read so far).
- Insiders whose last Form 3/4/5 is older than the 8 bulk quarters are missed (BCAX's IPO-era Form 3s).
- 1,150 stocks have no share count in any SEC frame (20-F filers, SPACs): no float, no market cap.
- Parity screens 6–12: PENDING_MANUAL (owner, in a browser).
- The squeeze migration onto the engine: the next step, gated on this PR's results.

## PR #28 (the engine)

| | |
|---|---|
| Branch | `feature/screener-engine` (from `origin/main` `4b5f46d`) |
| Phase | I — DONE (PR opened, not merged) |
| Gate A | PASSED (audit committed; FROZEN list verified with `git ls-files`: 38 exact paths + 3 globs) |
| Gate B | PASSED (catalog parses; `tests/test_screener_catalog.py` checks 700+ prompt codes and 35 signals) |
| Gate C | PASSED (`tests/test_screener_spec.py`: every catalog metric has one metric-spec row with an evidence tag) |
| Gate D | PASSED (data-sources.md: 137 rows — 60 EXACT, 61 APPROX, 16 UNAVAILABLE_FREE) |
| Gate E | PASSED (architecture.md; parser suite: 53 code strings) |
| Gate F | PASSED (1034 tests, all green: 904 baseline + 130 screener; no coverage tooling in the repo, none added) |
| Gate G | PASSED (parity-report.md: screens 1–5 PASS/FAIL with causes, 6–12 PENDING_MANUAL; 53/60 finviz loads) |
| Gate H | PASSED (D5: runs 39707815644 refresh, 39710097398 verify, 39710188677 preset — all green; D6: Telegram messages 492–497 at 2026-10-22 02:10–02:11 UTC + the Actions preset message) |
| Gate I | PASSED (sweep clean; FINAL_REPORT.md status PARTIALLY COMPLETE) |
| Baseline | 904 tests, all passing (`PYTHONUTF8=1`, `tzdata` installed locally) |
| Next action | DONE. Owner: fill in parity-manual-checklist.md, review the PR, merge (the listener then serves /screen and starts the schedule) |

## Commits

| Phase | SHA | Subject |
|---|---|---|
| A | c5bfb2d | docs(screener): repo audit |
| B | a5af3bc | feat(screener): filter catalog |
| C | 6bd16a8 | docs(screener): metric spec |
| D | 14905e3 | docs(screener): data sources and caching design |
| E | 812d874 | feat(screener): architecture and parser |
| F | 03c3230 | feat(screener): stores, metrics and two-stage evaluation |
| F/H | dd16610 | feat(screener): /screen, /screens, presets, schedule and workflow |
| F | 2e8eabd | fix(screener): ask a symbol without new trades once per session; bounded refresh |
| H | 2161cc5 | ci(screener): temporary push trigger on the feature branch (removed before the PR) |
| G | 0c9519f | fix(screener): parity calibration (average volume, 52-week distances, ETFs) |
| G | 20338b7 | docs(screener): parity report and manual checklist |
| H | fab63c3 | fix(screener): keep a maintenance run under 2000 SEC calls; drop the temporary push trigger |
| I | 540d8e9 | docs(screener): spec, architecture and sources match the calibrated engine; news for news_date |
| I | (this commit) | docs(screener): final report |

## Cache locations

- Local: `D:\investor\.cache\screener\` (gitignored through `.cache/`)
- Actions: `.cache/screener/`, restored and saved with `actions/cache` (key = store + ISO week)

## Catalog verification (Phase B)

finviz loads so far: 6 of 60 (robots.txt, help/screener ×2, the allowed preset `v=340&s=ta_topgainers` ×3 to read its
embedded filter definitions). Paced ≥ 20.5 s apart, unauthenticated. Only codes, short labels and Elite flags were kept.

| | count |
|---|---|
| Filters (OBSERVED definitions) | 82 (77 public + 5 Elite: sh_trades, ah_close, ah_change, news_keywords, etf_heldby) |
| Option codes | 2459: **2205 OBSERVED**, **172 INFERRED** (prompt codes the UI does not list), the rest of each filter's grammar is INFERRED |
| Signals | 35 (18 signal presets + 17 chart patterns, OBSERVED in robots.txt and the help page) |
| Planned status, public filters | 19 EXACT · 44 APPROX · 14 UNAVAILABLE (final statuses re-checked in Phase I) |
| Planned status, signals | 9 EXACT · 6 APPROX · 20 UNAVAILABLE |

## Open items

- Parity screens 6–12: PENDING_MANUAL (owner, in a browser, outside market hours).
- Actions cache `screener-stores-39707815644` holds the first full store build; the nightly maintenance run keeps it
  current after the merge.

## FROZEN paths (G4)

`python -m bot.screener.guard` reads this block and fails when `git diff --name-only origin/main` contains a match
(exact path, or an `fnmatch` glob). Run it before every commit.

<!-- FROZEN-BEGIN -->
- `bot/intraday.py`
- `bot/intraday_count.py`
- `.github/workflows/intraday.yml`
- `.github/workflows/intraday-count.yml`
- `data/intraday_state.json`
- `docs/intraday/index.html`
- `docs/research/intraday-audit-2026-10-17.md`
- `docs/research/intraday-strategies-2026-10-17.md`
- `docs/backtest/intraday-signal-count-2024.csv`
- `docs/backtest/intraday-signal-count-2024.md`
- `tests/test_intraday.py`
- `tests/test_intraday_count.py`
- `tests/test_intraday_screener.py`
- `bot/squeeze.py`
- `bot/screen.py`
- `bot/squeeze_live.py`
- `bot/squeeze_forward.py`
- `bot/list_tickets.py`
- `bot/movers.py`
- `.github/workflows/squeeze.yml`
- `data/squeeze_journal.json`
- `data/squeeze_last.json`
- `data/squeeze_state.json`
- `data/movers_log.json`
- `docs/superpowers/specs/2026-09-27-squeeze-screener-design.md`
- `docs/superpowers/specs/2026-10-13-squeeze-forward-test.md`
- `docs/superpowers/specs/2026-10-13-list-tickets-design.md`
- `docs/backtest/squeeze*`
- `data/ticket_journal.json`
- `bot/ticket_journal.py`
- `data/shadow_journal.json`
- `bot/shadow.py`
- `.github/workflows/shadow.yml`
- `bot/zba_v2.py`
- `.github/workflows/zba-v2.yml`
- `docs/backtest/zba-v2*`
- `docs/backtest/ticket-*`
- `data/journal.json`
- `data/state.json`
- `bot/clock.py`
- `bot/sessions.py`
<!-- FROZEN-END -->
