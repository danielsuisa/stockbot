# Screener engine — STATE

| | |
|---|---|
| Branch | `feature/screener-engine` (from `origin/main` `4b5f46d`) |
| Phase | G — parity (F implementation done) |
| Gate A | PASSED (audit committed; FROZEN list verified with `git ls-files`: 38 exact paths + 3 globs) |
| Gate B | PASSED (catalog parses; `tests/test_screener_catalog.py` checks 700+ prompt codes and 35 signals) |
| Gate C | PASSED (`tests/test_screener_spec.py`: every catalog metric has one metric-spec row with an evidence tag) |
| Gate D | PASSED (data-sources.md: 137 rows — 60 EXACT, 61 APPROX, 16 UNAVAILABLE_FREE) |
| Gate E | PASSED (architecture.md; parser suite: 53 code strings) |
| Gate F | PASSED (1032 tests, all green: 904 baseline + 128 screener; no coverage tooling in the repo, none added) |
| Baseline | 904 tests, all passing (`PYTHONUTF8=1`, `tzdata` installed locally) |
| Next action | Phase G parity run (signal screens) + manual checklist, then H verification |

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

- (none yet)

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
