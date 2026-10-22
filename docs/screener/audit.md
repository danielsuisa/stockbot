# Screener engine — repository and environment audit (Phase A, 2026-10-21 New York)

Branch `feature/screener-engine` from `origin/main` at `4b5f46d`. Python 3.11.9 locally; CI runs 3.11 and 3.12
(`.github/workflows/tests.yml`). The repo has **no third-party dependencies**: no requirements file, and no workflow
runs `pip install`. The engine is stdlib-only for the same reason (DECISION D-001: the YAML files are written in JSON
syntax, which is valid YAML 1.2 and loads with `json`).

## 1. Baseline

| | |
|---|---|
| Command | `python -m unittest discover -s tests` |
| Tests | **904, all passing** (OBSERVED, 12.98 s) |
| Local caveats | Windows only. (1) Without the `tzdata` package, `zoneinfo` cannot find `Asia/Jerusalem` and 47 test modules fail to import. (2) Without `PYTHONUTF8=1`, 4 tests fail reading or printing Hebrew in the cp1252 code page. Linux CI has neither problem. Local baseline = `PYTHONUTF8=1` + `tzdata`. |
| Known date-dependent test | PR #25 pinned the clock in `test_corrections_are_saved_before_alerts`. It is not failing now. |

## 2. Reusable components (used by the engine instead of new code)

| Need | Existing component | Notes |
|---|---|---|
| HTTP with backoff | `bot/common.fetch` | Exponential backoff on 429/5xx. SEC hosts get the `SEC_UA` User-Agent and a global lock at ≤ 8 req/s (`SEC_RPS`, capped at 8). |
| SEC ticker map | `common.tickers()`, `common.cik_tickers()` | `company_tickers.json`. `cik_tickers` picks the common-stock symbol per CIK. |
| SEC submissions | `common.submissions(cik)` | lru-cached, `data.sec.gov/submissions/CIK##########.json`. |
| Telegram | `common.send(text, source=…)` | Splits at 4000 visible units and waits 1.1 s between messages. `source=` replaces the SEC footer with the message's own source and disclaimer. When `TG_TOKEN` is unset it prints instead (dry run). On Actions it exits loudly if the secret is missing. |
| Hebrew formatting | `common.code / price / arrow / change / count / il_when / il_date / weekday` | Message rules in `tests/msgrules.py`: Hebrew first on every line, no sign glued to a number, Israeli dates, Israel time, no "UTC". |
| Command router | `bot/listen.handle` | `/cmd` dispatch. Unknown commands get `HELP` and a `setMyCommands` call. `COMMANDS` feeds Telegram's "/" menu. Tests require every command to appear in `HELP` and `HELP` to pass `msgrules`. |
| Plain-text tickers | `listen.extract` / `listen.reports` | `$xyz` or ALL-CAPS words. Untouched by the engine (G6). |
| Listener clock | `bot/clock.py` (`JOBS`, `due`, `tick`) | Called from `listen.serve` every poll (≤ 50 s). It dispatches workflows by `workflow_dispatch` because GitHub starts schedules 3.5–5.5 h late. **`tests/test_clock.py` asserts the exact set of due jobs**, so new jobs cannot be added to `clock.JOBS` without editing an existing test (G6). The engine gets its own job list, ticked from `listen.serve` (DECISION D-004). |
| Session slots | `bot/sessions.py` | `SLOTS` (New York): `pm1–pm6` 04:15–09:15 hourly, `pre1` 07:30, `pre2` 09:15, `reg1–reg6` 10:30–15:30 hourly, `post1` 16:30, `post2` 19:30. Also `slot()`, `claim()` (one run per slot per day), `first()` (one warning per session). Holidays come from `api.nasdaq.com/market-info` with a weekday fallback. The engine does not call Nasdaq (G3); it uses the Alpaca calendar instead (D-005). |
| Self-dispatching listener | `.github/workflows/telegram-listen.yml` | Serves about 5 h 20 m, then dispatches its successor on the same ref. Backup cron is `*/10`. A push to `main` touching `bot/**` takes over (the older poller gets 409 and exits). |
| Dispatch / run status | `common.dispatch(workflow, inputs)`, `common.runs(workflow)` | Use the run's `GITHUB_TOKEN`. |
| Concurrency groups | `squeeze.yml: group squeeze`, etc. | Each writer workflow has its own group and `cancel-in-progress: false`. |
| Commit-with-retry | `squeeze.yml` "Commit …" step | `git add` named files only, then 5 × push with `pull --rebase` and growing sleeps. |
| Failure alarm | Last step of each workflow | `if: failure() \|\| cancelled()`: plain `curl` to Telegram with the run URL. |
| Alpaca market data | `bot/alpaca.py` | `_get` paces at 190/min and counts `REQUESTS`. `_bars` pages multi-symbol bars (`limit` = 10000 points across symbols). `utc()`, `calendar()`, `assets()`. It has a gz-JSON cache under `.cache/alpaca/` with atomic writes. `_bars` hard-codes `adjustment=raw`, so the engine sends its own query through `_get` (same pacer and counter). |
| FINRA short interest | `bot/shorts.py` | `settlement_dates`, `report(date)` (exchange classes NNM/SC/NYSE/AMEX, paged), `usable_from` (8th session after settlement), `norm`/`to_sec`/`remap` (FINRA BRKB → BRK-B). |
| SEC shares outstanding | `shorts.frame(y, q)`, `shares_index`, `shares_at` | `dei:EntityCommonStockSharesOutstanding` frames. Usable from the fact date live. Facts older than 400 days or under 100K shares are rejected. |
| XBRL tag fallbacks | `bot/fundamentals.FLOW / INST` | Revenue, cost of revenue, gross profit, net income, CFO, EBIT, … with tag fallbacks in priority order. |
| Form 4 parsing | `bot/form4.py` (`_DOC` regex, `parse`) | Purchases and other acquisitions only. It does not read holdings after the transaction, so the engine adds a small holdings reader on the same XML (D-012). |
| SEC daily index | `bot/scan.index_urls(day)` | Form 4 URLs of one day from EDGAR's daily index. |
| Alarm / heartbeat / watchdog | `scan.alarm`, `watchdog.yml` | The watchdog keeps its own GitHub schedule. |

## 3. How the current squeeze screen computes its numbers (`bot/screen.py`, `bot/squeeze_live.py`)

| Metric | Source | Formula |
|---|---|---|
| Short shares | FINRA consolidated short interest, newest report usable today (8th session after settlement) | `currentShortPositionQuantity` |
| Float | **Yahoo** `quoteSummary/defaultKeyStatistics.floatShares` (one call per stock, only for stocks that pass the other numbers) | as given |
| Short float | FINRA short shares / Yahoo float | |
| Average volume | **Yahoo** `averageDailyVolume3Month` (batch quote) | as given |
| Relative volume | **Yahoo** `regularMarketVolume` / `averageDailyVolume3Month` | No time-of-day adjustment |
| Short ratio | FINRA short shares / Yahoo 3-month average volume | Not FINRA's `daysToCover` |
| Price / change | **Yahoo** batch quote (`regularMarketPrice`, `regularMarketChangePercent`) | |
| Optionable | **CBOE** delayed option chain (`cdn.cboe.com`); 403 means no options | |
| Criteria | float < 50M, short float > 20%, short ratio > 5, avg vol > 500K, price > $2, rel vol > 2, options | All strict. Sorted by short float, descending. |
| Pre-market list | Same fixed set, plus pre-market price > $2 and pre-market volume ≥ 10% of a normal day | Quotes from `movers.quote` |
| Schedule | `clock.JOBS` → `squeeze.yml mode=slot` at `pm1–pm6`, `reg1–reg6`, `post1`, `post2` | `/squeeze now` → `mode=manual` |

The squeeze depends on Yahoo, and G3 bars the engine from it, so an engine-expressed squeeze cannot reproduce the
squeeze byte for byte. Phase H.5 therefore keeps the squeeze untouched (G10). The record is in FINAL_REPORT.

## 4. Environment findings

- **TLS:** this machine intercepts TLS for Python's bundled certifi (pip fails with `CERTIFICATE_VERIFY_FAILED`). The
  stdlib `urllib` uses the Windows certificate store and works. Local engine runs therefore work. Actions is unaffected.
- **Secrets locally:** `TG_TOKEN`, `TG_CHAT_ID` and `SEC_UA` are set in the user environment. `ALPACA_KEY_ID` and
  `ALPACA_SECRET_KEY` are set at user level too, but not in this session's process. Every local dry run unsets
  `TG_TOKEN` so nothing reaches Telegram by accident.
- **Alpaca probe** (OBSERVED, 2026-10-21, AAPL/NVDA/TSLA, 6 requests):
  - The free plan serves `feed=sip` daily and minute history.
  - The `1Day` bar's open, high, low and close are regular-session values. Its open and close are the auction prints
    (the close differs from the last RTH minute bar's close by a few cents).
  - Its **volume is the whole 04:00–20:00 day**: AAPL 10/20 daily 35,997,106 vs 1-minute total 35,993,965 vs RTH only
    25,244,691.
  - `adjustment=split/all/raw` agree on days without corporate actions.
  - The assets endpoint returns `shortable`, `easy_to_borrow`, `borrow_status`, `marginable` and an `attributes` list
    that contains `has_options`.

## 5. FROZEN paths (G4)

The authoritative list is in `docs/screener/STATE.md` between the `FROZEN-BEGIN` / `FROZEN-END` markers. Before every
commit, `python -m bot.screener.guard` checks `git diff --name-only origin/main` against it. Every listed path was
verified with `git ls-files`.

Scope:
- the intraday module: code, workflows, state, page, tests, the pre-registered plan and its research notes, its
  signal-count results;
- the squeeze and its frozen variant `w0.7/si0.2/rv2`: code, workflow, list, state, journal, forward test, backtest
  docs;
- the ticket, shadow and ZBA experiments: code, journals, frozen JSON, backtest docs;
- the insider scan's journal and state.

## 6. Files the engine will change outside its own directories

| File | Change | Why it is safe |
|---|---|---|
| `bot/listen.py` | `/screen` and `/screens` routes, `COMMANDS` and `HELP` lines, one guarded `schedule.tick(done)` call next to `clock.tick(done)` | Existing commands, the plain-text ticker rule and the menu order are kept. The new calls are wrapped like the clock's (a failure never stops replies). |
| `.github/workflows/screener.yml` | New, `workflow_dispatch` | Its own concurrency group. |
| `tests/test_screener_*.py` | New | Offline. |

`.gitignore` already ignores `.cache/`. The engine's stores live under `.cache/screener/`.
