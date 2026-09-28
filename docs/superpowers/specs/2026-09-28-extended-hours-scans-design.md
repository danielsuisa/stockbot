# Extended-hours scans — design

Date: 2026-09-28 · Status: sections 1–4 approved in chat; this spec awaits the owner's review · Branch: `extended-hours`

## 1. Purpose

Scan outside regular US market hours as well, so signals arrive earlier and pre-market / after-hours moves are
caught. Owner's answers (2026-09-28): both earlier alerts and a new extended-hours movers scan · two runs per
extended session · normal alert sound at any hour · only new or changed results · movers universe = high short
interest (≥ 20 %) · mover = up ≥ 10 % with real volume · up moves only · plus a manual `/squeeze now`, which during
regular hours checks live moves with the same rule.

Out of scope: continuous polling, down moves, whole-market movers, changing the backtested squeeze method.

## 2. Measured facts (2026-09-28)

- Nothing ran during market hours before this change: the insider scan runs 05:30 UTC (it reads SEC's daily index,
  published the next morning); the squeeze list ran 12:00 UTC (US pre-market).
- SEC accepts filings until 22:00 New York time; the scan's existing fallback, SEC full-text search (EFTS), returns
  same-day Form 4 filings.
- Yahoo 5-minute bars with `includePrePost=true` carry extended-hours prices but **volume 0**.
- Nasdaq `api/quote/{T}/extended-trading?markettype=pre|post` returns the session's last price and **volume**
  (GME Friday after-hours: $23.42, 198,895 shares) and a data date (`lastUpdateInfo`: "Data last updated Sep 25,
  2026 08:00 PM ET"). Its % change is against the **previous day's** close (not usable for after-hours).
- Nasdaq `api/quote/{T}/info` returns last price, % change vs the previous close and today's volume so far
  (`isRealTime: false`: possibly ~15 minutes delayed).
- Nasdaq `api/market-info` returns `isBusinessDay`, today's pre-market / regular / after-hours times (early closes
  included), `previousTradeDate`, `nextTradeDate`.

## 3. Run times and session detection (`bot/sessions.py`)

| Slot | New York | Israel (summer) | UTC cron (EDT) | UTC cron (EST) |
|---|---|---|---|---|
| `pre0` (squeeze only, added 2026-09-28) | 04:15 | 11:15 | `15 8 * * 1-5` | `15 9 * * 1-5` |
| `pre1` | 07:30 | 14:30 | `30 11 * * 1-5` | `30 12 * * 1-5` |
| `pre2` | 09:15 | 16:15 | `15 13 * * 1-5` | `15 14 * * 1-5` |
| `post1` | 16:30 | 23:30 | `30 20 * * 1-5` | `30 21 * * 1-5` |
| `post2` | 19:30 | 02:30 (+1) | `30 23 * * 1-5` | `30 0 * * 2-6` |

- `slot(now, status)` → the slot this run serves, or None. A slot matches when New York time is 0–90 minutes after
  its time (GitHub starts schedules late), the day is a business day, and New York time lies inside that session
  (`pre*`: pre-market open → regular open; `post*`: regular close → after-hours close). So the wrong daylight-saving
  twin, holidays and sessions that do not exist (19:30 on an early-close day) all return None → the run exits quietly.
- `status()` = Nasdaq market-info; if it fails: weekday + fixed windows (pre 04:00–09:30, post 16:00–20:00).
- Each workflow records the slots it ran per New York date (`slots` in its state file, kept 7 days); a slot that
  already ran is not run again.
- `session_now(now, status)` → `"pre" | "regular" | "post" | None` for manual runs.
- `target_session(now, status)` → the session a squeeze list is for: today while today's regular session has not
  closed, else `nextTradeDate` (fallback: next weekday).
- All dates are New York dates (`zoneinfo("America/New_York")`); in winter the 19:30 run is after midnight UTC.

## 4. Same-day insider scan (`bot/scan.py`, mode `intraday`)

- The insider workflow gains the four slot schedules; those runs start `python -m bot.scan` with mode `intraday`
  (decided from the triggering schedule), in the same concurrency group as the morning scan (one writer of
  `data/state.json`).
- A run: slot check → `day` = New York date → `efts_urls(day)` minus the day's already-read accessions
  (`state["seen"][day]`) → parsed and merged exactly as `scan_day` does → the existing `alerts()` → send.
- The day is **not** marked scanned (`state["days"]` untouched): the 05:30 UTC run still reads the complete daily
  index next morning (filings after 19:30 until 22:00, anything EFTS missed). Its alerts are not repeated there: the
  existing `alerted` accessions rule already suppresses them.
- Messages only for new alerts, header "from today"; no "nothing new" heartbeat; `state["heartbeat"]` stays the
  morning scan's (watchdog and `/status` unchanged); the run's summary goes to `state["intraday"]`.
- EFTS down → logged, one warning per session (see 7), the morning scan covers it.

## 5. Squeeze list moves to after the close (`bot/squeeze_live.py`)

- Built at `post1` (16:30) from the session that just closed, for `target_session` (the next trading day); `post2`
  and the pre-market slots rebuild it and send only when the set of listed tickers changed ("entered / left" message).
- `build(target)` keeps its meaning: sessions before `target` (so after the close today's bar is included), FINRA
  usable on `target`, entry = the open of `target` → the journal stays comparable with the backtest. The list is
  saved in `data/squeeze_last.json` with its `target`.
- The journal records a name when it first appears for a target (existing once-per-10-sessions rule).

## 6. Movers scan (`bot/movers.py`, unvalidated)

- Universe: the stocks past the squeeze list's short-interest gate (≥ 20 % of shares, ≤ 150 %), from the same
  FINRA report and SEC shares (~120 names).
- Session quote (Nasdaq): `pre`/`post` → extended-trading last price + volume, skipped unless its data date is the
  current New York session date; `regular` (manual runs only) → info last price + today's volume.
- Reference price (Yahoo daily bars, already fetched for the list): `pre` and `regular` → the last completed close;
  `post` → today's regular close.
- Mover: `price / reference − 1 ≥ 0.10` and `session volume ≥ 0.10 × FINRA average daily volume`; up only; a missing
  input → not a mover.
- Only new or changed: alerted once per ticker per session (`state["movers"]["YYYY-MM-DD:pre"]`), again only when its
  gain is ≥ 10 percentage points above the last alerted gain.
- Every mover is logged to `data/movers_log.json` (date, session, ticker, price, reference, gain, volume ratio, short
  interest %, manual) for a later backtest; entries older than 400 days are pruned.
- Nasdaq calls: ≤ 4 at a time; a ticker that fails is counted in the message footer.

## 7. Messages and errors

- Hebrew, the bot's RTL rule (`common.rtl_bad_lines` empty), normal notification sound.
- Insider same-day: the existing alert blocks under "🔔 … מהיום".
- Squeeze list: the existing message naming its target session; a change → a short "entered / left" message.
- Movers: 🌅 pre-market / 🌙 after-hours / 📈 live (manual); per line: ticker, gain, session volume vs a normal day,
  short interest %, days to cover, rank on the current list if listed; footer "rule not backtested".
- Manual run (`/squeeze now`): marked ▶️, always the full list and the full movers result for the current session.
- A source down (EFTS, Nasdaq, FINRA): one warning per source per session (`state["warned"]`), not per run; a crash
  → the existing workflow alarm; `/squeeze now` dispatch failure → a message like `/scan`'s.

## 8. Manual run

- `/squeeze now` in the listener → `common.dispatch("squeeze.yml", {"mode": "manual"})` (the listener already has
  `actions: write`) → "⏳ started" reply; results in ~2–3 minutes.
- `squeeze.yml` gains a `workflow_dispatch` input `mode` (`manual` | `slot`); manual runs skip the slot check and do
  not consume a slot, but record the movers they sent.

## 9. Workflows

- `daily-scan.yml`: + 8 slot crons (section 3); the Python entry decides `scan` / `followup` / `intraday` from the
  triggering schedule (passed as `SCHEDULE`); commit step unchanged (`git add data`).
- `squeeze.yml`: the 12:00 UTC cron is replaced by the 8 slot crons; `SQUEEZE_MODE` = `inputs.mode || 'slot'`;
  commit adds `data/squeeze_last.json`, `data/squeeze_journal.json`, `data/squeeze_state.json`,
  `data/movers_log.json`.

## 10. Tests (offline, network mocked; existing suite stays green; actionlint clean)

- sessions: EDT and EST twins (only the right one runs), holiday, early close (`post2` skipped), late start
  (≤ 90 min runs, later skipped), slot already run, market-info down fallback, `target_session` before/after the
  close and on Friday → Monday.
- insider intraday: only unseen accessions fetched; `state["days"]` untouched; a following simulated morning scan
  sends no repeat alert; no message when nothing new.
- squeeze list: after-close build uses today's bar and targets the next session; unchanged → no message; changed →
  entered / left message; manual → full list.
- movers: +9.9 % no / +10 % yes; volume 9.9 % no / 10 % yes; reference close per session; stale data date skipped;
  re-alert only at +10 points; log written and pruned.
- messages: RTL clean; warnings once per source per session; `/squeeze now` dispatch and its failure message.
