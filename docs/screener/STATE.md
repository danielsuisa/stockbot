# Screener engine — STATE

| | |
|---|---|
| Branch | `feature/screener-engine` (from `origin/main` `4b5f46d`) |
| Phase | A — repository and environment audit |
| Gate A | PASSED (audit committed; FROZEN list verified with `git ls-files`: 42 exact paths + 3 globs) |
| Baseline | 904 tests, all passing (`PYTHONUTF8=1`, `tzdata` installed locally) |
| Next action | Phase B: catalog |

## Commits

| Phase | SHA | Subject |
|---|---|---|
| A | (this commit) | docs(screener): repo audit |

## Cache locations

- Local: `D:\investor\.cache\screener\` (gitignored through `.cache/`)
- Actions: `.cache/screener/`, restored and saved with `actions/cache` (key = store + ISO week)

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
