# Extended-Hours Scans Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the insider scan, the squeeze list and a new movers scan at four New York session slots (07:30, 09:15, 16:30, 19:30), send only new or changed results, and add `/squeeze now`.

**Architecture:** `bot/sessions.py` decides the slot / session / target day from New York time and Nasdaq's market-info; `bot/scan.py` gains a same-day mode (SEC full-text search, the existing merge and alert rules); `bot/movers.py` finds high-short-interest stocks up ≥ 10 % on real volume from Nasdaq quotes; `bot/squeeze_live.py` becomes slot-aware (list after the close, change messages, movers, manual runs); the two workflows get DST-twin schedules.

**Tech Stack:** Python 3.11/3.12 stdlib (`zoneinfo`, `urllib` via `common.fetch`, `concurrent.futures`), `unittest` offline tests, GitHub Actions (+ actionlint).

**Spec:** `docs/superpowers/specs/2026-09-28-extended-hours-scans-design.md`

## Global Constraints

- Stdlib only; tests offline with every network call mocked; `python -m unittest discover -s tests` stays green (257 today).
- Slots (New York): `pre1` 07:30, `pre2` 09:15, `post1` 16:30, `post2` 19:30; a run matches 0–90 minutes after its slot, on a business day, inside the slot's session; each slot runs once per New York date.
- All dates are New York dates (`zoneinfo("America/New_York")`).
- Mover: gain ≥ 10 % vs the session's reference close and session volume ≥ 10 % of FINRA's average daily volume; up only; re-alert only at ≥ 10 more percentage points; quote dated the current session.
- Only new or changed on scheduled runs; manual runs send everything; one warning per source per session.
- Hebrew messages pass `common.rtl_bad_lines`; normal notification sound (no `disable_notification`).
- The squeeze list keeps the backtested method; the insider morning scan stays the complete daily record.

## Review Focus

1. The other daylight-saving twin landing inside the same slot's 90-minute window (summer 12:30 UTC = 08:30 New York) must not run the slot twice → `Claim.test_a_slot_runs_once_per_day_and_old_days_are_dropped` (Task 1), `Intraday.test_alerts_same_day_once_and_the_morning_scan_does_not_repeat` (Task 3), `Sessions.test_after_close_sends_the_list_and_movers_then_nothing_repeats` (Task 4).
2. Nasdaq serving the previous session's extended data at the next pre-market must not alert → `Rule.test_stale_or_missing_inputs_are_not_movers` (Task 2).
3. `/squeeze now` at night or on a weekend → the list plus "market closed", no crash → `Sessions.test_manual_at_night_sends_the_full_list_and_says_the_market_is_closed` (Task 4).
4. A same-day alert followed by the morning scan → no repeat → Task 3 test above.
5. Same-day runs re-downloading every filing of the day because `prune` dropped today's read list → `ScanDayUrls.test_prune_keeps_todays_read_filings` (Task 3).

---

### Task 1: `bot/sessions.py` — slots, sessions, target day

**Files:** Create `bot/sessions.py`, `tests/test_sessions.py`

**Interfaces — Produces:** `NY`, `SLOTS = {"pre1": (time(7,30), "pre"), "pre2": (time(9,15), "pre"), "post1": (time(16,30), "post"), "post2": (time(19,30), "post")}`, `now_ny()`, `status() -> dict | None` (`{"business", "pre", "regular", "post": (start, end) naive NY datetimes, "next": ISO}`), `fallback(now) -> dict`, `slot(now, st) -> str | None`, `session_now(now, st) -> "pre"|"regular"|"post"|None`, `target_session(now, st) -> ISO`, `claim(state, name, today_iso) -> bool`, `first(state, key, today_iso) -> bool`.

- [ ] **Step 1: Write the failing tests** — `tests/test_sessions.py`

```python
"""Session slots for the extended-hours runs: New York time, daylight-saving twins, holidays, early closes."""
import datetime as dt
import json
import unittest
from unittest import mock

from bot import sessions

NY = sessions.NY


def ny(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=NY)


def day(d="2026-09-28", close="16:00", ah="20:00", business=True, nxt="2026-09-29"):
    at = lambda t: dt.datetime.fromisoformat(f"{d}T{t}")  # noqa: E731
    return {"business": business, "pre": (at("04:00"), at("09:30")), "regular": (at("09:30"), at(close)),
            "post": (at(close), at(ah)), "next": nxt}


MARKET_INFO = {"data": {"isBusinessDay": True, "pmOpenRaw": "2026-09-28T04:00:00", "openRaw": "2026-09-28T09:30:00",
                        "closeRaw": "2026-09-28T16:00:00", "ahCloseRaw": "2026-09-28T20:00:00",
                        "nextTradeDate": "Sep 29, 2026"}}


class Slot(unittest.TestCase):
    def test_each_slot_on_time_and_late(self):
        for t, want in (("07:30", "pre1"), ("08:59", "pre1"), ("09:15", "pre2"), ("16:30", "post1"),
                        ("17:59", "post1"), ("19:30", "post2")):
            self.assertEqual(sessions.slot(ny(f"2026-09-28T{t}"), day()), want, t)

    def test_too_early_too_late_or_outside_the_session(self):
        for t in ("07:29", "09:01", "18:01", "20:00", "12:00"):
            self.assertIsNone(sessions.slot(ny(f"2026-09-28T{t}"), day()), t)

    def test_daylight_saving_twins(self):
        summer, winter = day("2026-09-28"), day("2026-12-01", nxt="2026-12-02")
        utc = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).astimezone(NY)  # noqa: E731
        self.assertEqual(sessions.slot(utc("2026-09-28T11:30"), summer), "pre1")
        self.assertIsNone(sessions.slot(utc("2026-12-01T11:30"), winter))  # 06:30 EST
        self.assertEqual(sessions.slot(utc("2026-12-01T12:30"), winter), "pre1")
        self.assertEqual(sessions.slot(utc("2026-09-28T23:30"), summer), "post2")
        self.assertIsNone(sessions.slot(utc("2026-09-29T00:30"), summer))  # 20:30 EDT: after-hours is over
        self.assertEqual(sessions.slot(utc("2026-12-02T00:30"), winter), "post2")  # 19:30 EST, the next UTC day

    def test_holiday_early_close_and_no_status(self):
        self.assertIsNone(sessions.slot(ny("2026-11-26T07:30"), day("2026-11-26", business=False)))
        early = day("2026-11-27", close="13:00", ah="17:00")
        self.assertEqual(sessions.slot(ny("2026-11-27T16:30"), early), "post1")
        self.assertIsNone(sessions.slot(ny("2026-11-27T19:30"), early))
        self.assertIsNone(sessions.slot(ny("2026-09-28T07:30"), None))


class Claim(unittest.TestCase):
    def test_a_slot_runs_once_per_day_and_old_days_are_dropped(self):
        state = {"slots": {"2026-09-01": ["pre1"]}}
        self.assertTrue(sessions.claim(state, "pre1", "2026-09-28"))
        self.assertFalse(sessions.claim(state, "pre1", "2026-09-28"))  # e.g. the other daylight-saving twin
        self.assertTrue(sessions.claim(state, "post1", "2026-09-28"))
        self.assertEqual(state["slots"], {"2026-09-28": ["pre1", "post1"]})

    def test_first_warning_per_key_and_day(self):
        state = {}
        self.assertTrue(sessions.first(state, "efts:post", "2026-09-28"))
        self.assertFalse(sessions.first(state, "efts:post", "2026-09-28"))
        self.assertTrue(sessions.first(state, "efts:pre", "2026-09-28"))


class Status(unittest.TestCase):
    def test_parses_nasdaq_market_info(self):
        with mock.patch.object(sessions.common, "fetch", return_value=json.dumps(MARKET_INFO).encode()) as f:
            self.assertEqual(sessions.status(), day())
        self.assertEqual(f.call_args.kwargs["headers"]["Accept"], "application/json")

    def test_unreadable_is_none_and_the_fallback_uses_weekday_windows(self):
        with mock.patch.object(sessions.common, "fetch", side_effect=OSError("down")), mock.patch("builtins.print"):
            self.assertIsNone(sessions.status())
        self.assertEqual(sessions.fallback(ny("2026-09-28T07:30")), day())
        fri = sessions.fallback(ny("2026-10-02T20:30"))
        self.assertEqual((fri["business"], fri["next"]), (True, "2026-10-05"))
        self.assertFalse(sessions.fallback(ny("2026-10-03T07:30"))["business"])


class Now(unittest.TestCase):
    def test_session_now(self):
        for t, want in (("03:59", None), ("04:00", "pre"), ("09:30", "regular"), ("15:59", "regular"),
                        ("16:00", "post"), ("20:00", None)):
            self.assertEqual(sessions.session_now(ny(f"2026-09-28T{t}"), day()), want, t)
        self.assertIsNone(sessions.session_now(ny("2026-11-26T10:00"), day("2026-11-26", business=False)))

    def test_target_session(self):
        self.assertEqual(sessions.target_session(ny("2026-09-28T07:30"), day()), "2026-09-28")
        self.assertEqual(sessions.target_session(ny("2026-09-28T16:30"), day()), "2026-09-29")
        fri = day("2026-10-02", nxt="2026-10-05")
        self.assertEqual(sessions.target_session(ny("2026-10-02T19:30"), fri), "2026-10-05")
        self.assertEqual(sessions.target_session(ny("2026-10-03T12:00"), None), "2026-10-05")  # Saturday, no status


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run** `python -m unittest tests.test_sessions` — Expected: ERROR `ImportError: cannot import name 'sessions'`

- [ ] **Step 3: Implement `bot/sessions.py`**

```python
"""US market sessions for the extended-hours runs - docs/superpowers/specs/2026-09-28-extended-hours-scans-design.md,
section 3: which run slot a scheduled start serves, which session is trading now, and which trading day a squeeze
list is for. All times are New York times; Nasdaq's market-info decides holidays and early closes."""
import datetime as dt
import json
from zoneinfo import ZoneInfo

from bot import common

NY = ZoneInfo("America/New_York")
MARKET_INFO = "https://api.nasdaq.com/api/market-info"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
SLOTS = {"pre1": (dt.time(7, 30), "pre"), "pre2": (dt.time(9, 15), "pre"),
         "post1": (dt.time(16, 30), "post"), "post2": (dt.time(19, 30), "post")}
LATE = dt.timedelta(minutes=90)  # GitHub starts schedules late; a run later than this is stale
WINDOWS = {"pre": (dt.time(4), dt.time(9, 30)), "regular": (dt.time(9, 30), dt.time(16)),
           "post": (dt.time(16), dt.time(20))}  # only when Nasdaq's market-info is unavailable
KEEP = 7  # days of slot and warning records


def now_ny():
    return dt.datetime.now(NY)


def _naive(now):
    return now.astimezone(NY).replace(tzinfo=None) if now.tzinfo else now


def _next_weekday(day):
    day += dt.timedelta(1)
    while day.weekday() >= 5:
        day += dt.timedelta(1)
    return day


def status():
    """Today's sessions from Nasdaq's market-info -> {"business", "pre", "regular", "post" ((start, end), naive New
    York datetimes), "next" (ISO)}, or None when it cannot be read."""
    try:
        d = json.loads(common.fetch(MARKET_INFO, headers=HEADERS, tries=2, timeout=20))["data"]
        at = dt.datetime.fromisoformat
        return {"business": bool(d["isBusinessDay"]), "pre": (at(d["pmOpenRaw"]), at(d["openRaw"])),
                "regular": (at(d["openRaw"]), at(d["closeRaw"])), "post": (at(d["closeRaw"]), at(d["ahCloseRaw"])),
                "next": dt.datetime.strptime(d["nextTradeDate"], "%b %d, %Y").date().isoformat()}
    except Exception as e:  # any failure -> the caller falls back to the weekday rule
        print(f"nasdaq market-info: {type(e).__name__} {e}")
        return None


def fallback(now):
    """The weekday rule with fixed session windows, for when Nasdaq's market-info is unavailable."""
    day = _naive(now).date()
    return {"business": day.weekday() < 5, "next": _next_weekday(day).isoformat(),
            **{s: (dt.datetime.combine(day, a), dt.datetime.combine(day, b)) for s, (a, b) in WINDOWS.items()}}


def slot(now, st):
    """The slot a scheduled run started at `now` serves, or None: 0-90 minutes after the slot's time, on a business
    day, while the slot's session is trading - so the wrong daylight-saving twin, holidays and sessions that do not
    exist that day all give None."""
    now = _naive(now)
    if not st or not st["business"]:
        return None
    for name, (t, session) in SLOTS.items():
        at = dt.datetime.combine(now.date(), t)
        start, end = st[session]
        if at <= now <= at + LATE and start <= now < end:
            return name
    return None


def session_now(now, st):
    """"pre" | "regular" | "post" while that session trades at `now`, else None."""
    now = _naive(now)
    if not st or not st["business"]:
        return None
    return next((s for s in ("pre", "regular", "post") if st[s][0] <= now < st[s][1]), None)


def target_session(now, st):
    """The trading day a squeeze list is for (ISO): today until today's regular session closes, then the next one."""
    now = _naive(now)
    if st and st["business"] and now < st["regular"][1]:
        return now.date().isoformat()
    if st and st.get("next", "") > now.date().isoformat():
        return st["next"]
    return _next_weekday(now.date()).isoformat()


def _mark(state, key, name, today):
    """Record `name` under state[key][today] -> False if it was there already; records older than KEEP days go."""
    rec = state.setdefault(key, {})
    if name in rec.get(today, []):
        return False
    rec[today] = rec.get(today, []) + [name]
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(KEEP)).isoformat()
    for d in [d for d in rec if d < cutoff]:
        del rec[d]
    return True


def claim(state, name, today):
    """Record slot `name` as run on New York date `today` -> False if it already ran (e.g. the other
    daylight-saving twin of the same schedule, or a retry)."""
    return _mark(state, "slots", name, today)


def first(state, key, today):
    """True the first time `key` (e.g. "efts:post") is warned on New York date `today`: one warning per source per
    session, not per run."""
    return _mark(state, "warned", key, today)
```

- [ ] **Step 4: Run** `python -m unittest tests.test_sessions -v` — Expected: PASS
- [ ] **Step 5: Commit** `git add bot/sessions.py tests/test_sessions.py && git commit -m "Sessions: New York slots, daylight-saving twins, holidays, target day"`

---

### Task 2: `bot/movers.py` — Nasdaq quotes and the mover rule

**Files:** Create `bot/movers.py`, `tests/test_movers.py`

**Interfaces — Produces:** `quote(t, session) -> {"price", "volume", "date"} | None | False`, `quotes(tickers, session) -> {t: quote}`, `reference(bars, session, today) -> float | None`, `find(cands, qs, today) -> [cand + {"price","volume","gain","vol_ratio"}]` (cands need `t, ref, adv, si_pct, dtc`), `fresh(found, state, today, session, manual=False) -> list`, `log(book, found, today, session, manual) -> book`, `text(found, session, today, ranks=None, failed=0, manual=False) -> str`, `NAME = {"pre", "post", "regular" -> Hebrew}`.

- [ ] **Step 1: Write the failing tests** — `tests/test_movers.py`

```python
"""Extended-hours movers: Nasdaq quotes, the +10 % / 10 %-volume rule, reference closes, dedupe, log, message."""
import json
import unittest
from unittest import mock

from bot import common, movers

EXT = {"data": {"lastUpdateInfo": ["Data last updated Sep 28, 2026 08:05 AM ET."],
                "infoTable": {"rows": [{"consolidated": "$11.00 +1.00 (+10.00%)", "volume": "120,000"}]}}}
INFO = {"data": {"primaryData": {"lastSalePrice": "$12.50", "volume": "1,500,000",
                                 "lastTradeTimestamp": "Sep 28, 2026 11:02 AM ET"}}}
CAND = {"t": "AAA", "ref": 10.0, "adv": 1_000_000.0, "si_pct": 0.3, "dtc": 4.0}


class Quote(unittest.TestCase):
    def test_extended_and_regular(self):
        with mock.patch.object(movers.common, "fetch", return_value=json.dumps(EXT).encode()) as f:
            self.assertEqual(movers.quote("BRK-B", "pre"), {"price": 11.0, "volume": 120000.0, "date": "2026-09-28"})
        self.assertIn("/BRK.B/extended-trading?markettype=pre", f.call_args[0][0])
        with mock.patch.object(movers.common, "fetch", return_value=json.dumps(INFO).encode()):
            self.assertEqual(movers.quote("GME", "regular"), {"price": 12.5, "volume": 1500000.0, "date": "2026-09-28"})

    def test_no_trades_is_none_and_a_failure_is_false(self):
        empty = {"data": {"lastUpdateInfo": [], "infoTable": None}}
        with mock.patch.object(movers.common, "fetch", return_value=json.dumps(empty).encode()):
            self.assertIsNone(movers.quote("GME", "post"))
        with mock.patch.object(movers.common, "fetch", side_effect=OSError("down")), mock.patch("builtins.print"):
            self.assertIs(movers.quote("GME", "post"), False)


class Reference(unittest.TestCase):
    B = {"d": ["2026-09-24", "2026-09-25", "2026-09-28"], "c": [9.0, 10.0, 12.0]}

    def test_per_session(self):
        self.assertEqual(movers.reference(self.B, "pre", "2026-09-28"), 10.0)
        self.assertEqual(movers.reference(self.B, "regular", "2026-09-28"), 10.0)
        self.assertEqual(movers.reference(self.B, "post", "2026-09-28"), 12.0)
        self.assertIsNone(movers.reference(self.B, "post", "2026-09-29"))  # no bar for that day yet
        self.assertIsNone(movers.reference(None, "pre", "2026-09-28"))


class Rule(unittest.TestCase):
    def found(self, price, volume, date="2026-09-28", **c):
        return movers.find([{**CAND, **c}], {"AAA": {"price": price, "volume": volume, "date": date}}, "2026-09-28")

    def test_edges(self):
        self.assertEqual(len(self.found(11.0, 100_000)), 1)  # +10.0 %, 10 % of a normal day
        self.assertEqual(self.found(10.99, 100_000), [])  # +9.9 %
        self.assertEqual(self.found(11.0, 99_999), [])  # 9.9999 % of a normal day
        m = self.found(12.0, 250_000)[0]
        self.assertAlmostEqual(m["gain"], 0.2)
        self.assertAlmostEqual(m["vol_ratio"], 0.25)

    def test_stale_or_missing_inputs_are_not_movers(self):
        self.assertEqual(self.found(20.0, 900_000, date="2026-09-25"), [])  # last session's extended data
        self.assertEqual(self.found(20.0, 900_000, ref=None), [])
        self.assertEqual(self.found(20.0, 900_000, adv=None), [])
        self.assertEqual(movers.find([CAND], {"AAA": None}, "2026-09-28"), [])
        self.assertEqual(movers.find([CAND], {"AAA": False}, "2026-09-28"), [])


class Fresh(unittest.TestCase):
    def test_new_then_only_after_ten_more_points(self):
        state, m = {}, lambda g: [{"t": "AAA", "gain": g}]
        self.assertEqual(len(movers.fresh(m(0.12), state, "2026-09-28", "pre")), 1)
        self.assertEqual(movers.fresh(m(0.20), state, "2026-09-28", "pre"), [])
        self.assertEqual(len(movers.fresh(m(0.22), state, "2026-09-28", "pre")), 1)
        self.assertEqual(len(movers.fresh(m(0.12), state, "2026-09-28", "post")), 1)  # a new session starts over

    def test_manual_returns_all_and_still_marks(self):
        state = {"movers": {"2026-09-01:pre": {"OLD": 0.5}}}
        for _ in range(2):
            self.assertEqual(len(movers.fresh([{"t": "AAA", "gain": 0.15}], state, "2026-09-28", "pre", manual=True)), 1)
        self.assertEqual(movers.fresh([{"t": "AAA", "gain": 0.20}], state, "2026-09-28", "pre"), [])
        self.assertEqual(list(state["movers"]), ["2026-09-28:pre"])


class Log(unittest.TestCase):
    def test_append_and_prune(self):
        book = {"entries": [{"date": "2025-01-01", "t": "OLD"}]}
        movers.log(book, [{"t": "AAA", "price": 11.0, "ref": 10.0, "gain": 0.1, "vol_ratio": 0.2, "si_pct": 0.3}],
                   "2026-09-28", "pre", False)
        self.assertEqual([e["t"] for e in book["entries"]], ["AAA"])
        self.assertEqual((book["entries"][0]["session"], book["entries"][0]["manual"]), ("pre", False))


class Text(unittest.TestCase):
    M = {"t": "AAA", "gain": 0.15, "vol_ratio": 0.25, "si_pct": 0.3, "dtc": 4.0, "price": 11.5}

    def test_messages_are_rtl_clean(self):
        t = movers.text([self.M], "pre", "2026-09-28", ranks={"AAA": 3}, failed=2)
        for part in ("<code>AAA</code>", "<code>+15%</code>", "מקום <code>3</code>", "לא נבדק", "<code>2</code>"):
            self.assertIn(part, t)
        self.assertEqual(movers.text([], "post", "2026-09-28"), "")
        manual = movers.text([], "regular", "2026-09-28", manual=True)
        closed = movers.text([], None, "2026-09-28", manual=True)
        self.assertIn("▶️", manual)
        self.assertIn("סגור", closed)
        for x in (t, manual, closed):
            self.assertEqual(common.rtl_bad_lines(x), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run** `python -m unittest tests.test_movers` — Expected: ERROR `ImportError: cannot import name 'movers'`

- [ ] **Step 3: Implement `bot/movers.py`**

```python
"""Extended-hours movers - docs/superpowers/specs/2026-09-28-extended-hours-scans-design.md, section 6: stocks past the
squeeze list's short-interest gate that are up >= 10 % in the current session on real volume. Unvalidated: every
mover is logged for a later backtest, and none of it changes the squeeze ranking."""
import bisect
import concurrent.futures as cf
import datetime as dt
import json
import re

from bot import common
from bot.common import code

EXT = "https://api.nasdaq.com/api/quote/{t}/extended-trading?markettype={m}&assetclass=stocks&time=0"
INFO = "https://api.nasdaq.com/api/quote/{t}/info?assetclass=stocks"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
MIN_GAIN, MIN_VOLUME, REALERT, EPS = 0.10, 0.10, 0.10, 1e-9
THREADS = 4  # Nasdaq calls at a time
KEEP_DAYS, LOG_DAYS = 7, 400
ICON = {"pre": "🌅", "post": "🌙", "regular": "📈"}
NAME = {"pre": "לפני המסחר", "post": "אחרי המסחר", "regular": "במסחר הרגיל"}


def _number(s):
    """'$23.42 -1.60 (-6.39%)' / '198,895' -> its first number (23.42 / 198895.0), or None."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
    return float(m.group().replace(",", "")) if m else None


def _date(s):
    """The first 'Sep 25, 2026' in a Nasdaq text -> '2026-09-25', or None."""
    m = re.search(r"[A-Z][a-z]{2} \d{1,2}, \d{4}", s or "")
    return dt.datetime.strptime(m.group(), "%b %d, %Y").date().isoformat() if m else None


def quote(t, session):
    """Nasdaq's numbers for ticker t in `session` -> {"price", "volume", "date"}; None when the session has no trades
    for it; False when Nasdaq could not be read. pre / post: the extended session, dated by its "Data last updated"
    line; regular: last sale and today's volume so far, dated by the last trade."""
    sym = t.replace("-", ".")
    try:
        if session == "regular":
            p = json.loads(common.fetch(INFO.format(t=sym), headers=HEADERS, tries=2, timeout=20))["data"]["primaryData"]
            return {"price": _number(p.get("lastSalePrice")), "volume": _number(p.get("volume")),
                    "date": _date(p.get("lastTradeTimestamp"))}
        d = json.loads(common.fetch(EXT.format(t=sym, m=session), headers=HEADERS, tries=2, timeout=20))["data"]
        rows = (d.get("infoTable") or {}).get("rows") or []
        if not rows:
            return None
        info = d.get("lastUpdateInfo")
        return {"price": _number(rows[0].get("consolidated")), "volume": _number(rows[0].get("volume")),
                "date": _date(info[0] if isinstance(info, list) and info else info)}
    except Exception as e:  # an unofficial source: any failure means "could not read"
        print(f"nasdaq {t} {session}: {type(e).__name__} {e}")
        return False


def quotes(tickers, session):
    """{ticker: quote(t, session)} for every ticker, THREADS at a time."""
    with cf.ThreadPoolExecutor(THREADS) as ex:
        return dict(zip(tickers, ex.map(lambda t: quote(t, session), tickers)))


def reference(b, session, today):
    """The close a move is measured from (bars from squeeze.to_bars): pre / regular -> the last close before
    `today`; post -> today's regular close; None without it."""
    if not b:
        return None
    i = bisect.bisect_left(b["d"], today)
    if session == "post":
        return b["c"][i] if i < len(b["d"]) and b["d"][i] == today else None
    return b["c"][i - 1] if i > 0 else None


def find(cands, qs, today):
    """cands: [{"t", "ref" (reference close), "adv" (FINRA average daily volume), ...}], qs: {t: quote} -> movers up
    >= MIN_GAIN on session volume >= MIN_VOLUME x adv with the quote dated `today`, biggest gain first. A missing
    input or a quote from another day is never a mover."""
    out = []
    for c in cands:
        q = qs.get(c["t"])
        if not q or q.get("date") != today or not (q.get("price") and c.get("ref") and c.get("adv")) \
                or q.get("volume") is None:
            continue
        gain, ratio = q["price"] / c["ref"] - 1, q["volume"] / c["adv"]
        if gain >= MIN_GAIN - EPS and ratio >= MIN_VOLUME - EPS:
            out.append({**c, "price": q["price"], "volume": q["volume"], "gain": gain, "vol_ratio": ratio})
    return sorted(out, key=lambda m: (-m["gain"], m["t"]))


def fresh(found, state, today, session, manual=False):
    """Only new or changed: movers not alerted yet in this session, or up >= REALERT above their last alerted gain
    (state["movers"]["<date>:<session>"]); a manual run returns everything. Marks what it returns."""
    book = state.setdefault("movers", {})
    seen = book.setdefault(f"{today}:{session}", {})
    out = [m for m in found if manual or m["t"] not in seen or m["gain"] >= seen[m["t"]] + REALERT - EPS]
    for m in out:
        seen[m["t"]] = max(seen.get(m["t"], m["gain"]), m["gain"])
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(KEEP_DAYS)).isoformat()
    for k in [k for k in book if k[:10] < cutoff]:
        del book[k]
    return out


def log(book, found, today, session, manual):
    """Append the movers to the log (data/movers_log.json) for a later backtest; entries older than LOG_DAYS go."""
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(LOG_DAYS)).isoformat()
    keep = [e for e in book.get("entries", []) if e["date"] >= cutoff]
    new = [{"date": today, "session": session, "t": m["t"], "manual": manual,
            **{k: round(m[k], 4) if isinstance(m.get(k), float) else m.get(k)
               for k in ("price", "ref", "gain", "vol_ratio", "si_pct")}} for m in found]
    book["entries"] = keep + new
    return book


def _pct(x, signed=False):
    return code(f"{x * 100:+.0f}%" if signed else f"{x * 100:.0f}%")


def text(found, session, today, ranks=None, failed=0, manual=False):
    """The Hebrew movers message; '' on a scheduled run with nothing to send."""
    ranks = ranks or {}
    if not found and not manual:
        return ""
    head = f"{ICON[session]} <b>מזנקות {NAME[session]} · {code(today)}</b>" if session else \
        f"🌙 <b>השוק סגור כרגע · {code(today)}</b>"
    lines = [("▶️ הרצה ידנית · " if manual else "") + head]
    for m in found:
        rank = f" · מקום {code(ranks[m['t']])} ברשימת הסקוויז" if m["t"] in ranks else ""
        lines.append(f"מזנקת: {code(m['t'])} {_pct(m['gain'], True)} · מחזור {_pct(m['vol_ratio'])} מיום רגיל · "
                     f"שורט {_pct(m['si_pct'])} מהמניות · {code(format(m.get('dtc') or 0.0, '.1f'))} ימי כיסוי{rank}")
    if not found:
        lines.append(f"🤷 אין כרגע מניה עם שורט גבוה שעלתה {code('10%')} במחזור אמיתי." if session else
                     "אין עכשיו מסחר, גם לא לפני או אחרי השעות הרגילות.")
    lines.append(f"⚠️ הכלל (עלייה של {code('10%')} לפחות ומחזור של {code('10%')} לפחות מיום רגיל) לא נבדק "
                 "היסטורית, ולכן לא מאומת."
                 + (f" · ל־{code(failed)} מניות לא התקבל ציטוט מ־Nasdaq" if failed else ""))
    return "\n".join(lines)
```

- [ ] **Step 4: Run** `python -m unittest tests.test_movers -v` — Expected: PASS
- [ ] **Step 5: Commit** `git add bot/movers.py tests/test_movers.py && git commit -m "Movers: Nasdaq quotes, +10% on real volume, dedupe per session, log"`

---

### Task 3: Same-day insider mode in `bot/scan.py`

**Files:** Modify `bot/scan.py`; Create `tests/test_intraday.py`

**Interfaces:** Consumes `sessions.status/fallback/slot/claim/first/SLOTS`. Produces `scan_day(..., urls=None)`, `alerts(state, today, j=None, title=TITLE)`, `deliver(state, today, j, path, dry, title=TITLE) -> int`, `intraday(now=None, dry=False)`, `mode(a) -> "scan"|"followup"|"intraday"`, constants `TITLE, INTRADAY_TITLE, DAILY, WEEKLY`, CLI flag `--intraday`.

- [ ] **Step 1: Write the failing tests** — `tests/test_intraday.py`

```python
"""Same-day insider runs at the session slots (extended-hours spec, section 4)."""
import datetime as dt
import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from bot import common, market, scan, sessions
from test_bot import NO_ENRICH, buy
from test_sessions import day, ny

TODAY = dt.date(2026, 9, 28)


def cluster():
    return [buy(f"a{k}", 1, who, 50_000, last="2026-09-28") for k, who in enumerate(("x", "y", "z"), 1)]


class ScanDayUrls(unittest.TestCase):
    def test_only_unread_filings_are_fetched_and_the_day_is_not_marked(self):
        state = scan.prune({"seen": {"20260928": ["a1"]}}, TODAY)
        with mock.patch.object(scan, "_get", return_value=None) as get, \
                mock.patch.object(common, "cik_tickers", return_value={}), mock.patch("builtins.print"):
            scan.scan_day("20260928", state, TODAY, rescan=True, urls={"a1": "u1", "a2": "u2"})
        get.assert_called_once_with("u2")
        self.assertEqual(state["days"], {})
        self.assertEqual(state["seen"]["20260928"], ["a1", "a2"])

    def test_prune_keeps_todays_read_filings(self):
        state = scan.prune({"seen": {"20260928": ["a1"], "20260801": ["old"]}}, TODAY)
        self.assertEqual(state["seen"], {"20260928": ["a1"]})


class Intraday(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "state.json")
        self.write({"buys": cluster(), "regime": {"tag": "normal"}})
        mocks = {}
        for name, p in (("env", mock.patch.dict(os.environ, {"STATE_FILE": self.path})),
                        ("status", mock.patch.object(sessions, "status", return_value=day())),
                        ("efts", mock.patch.object(scan, "efts_urls", return_value={})),
                        ("scan_day", mock.patch.object(scan, "scan_day", return_value="ok")),
                        ("foreign", mock.patch.object(scan, "foreign", return_value=False)),
                        ("links", mock.patch.object(scan, "links", return_value={})),
                        ("regime", mock.patch.object(market, "regime", return_value={"tag": "normal"})),
                        ("enrich", NO_ENRICH), ("print", mock.patch("builtins.print")),
                        ("send", mock.patch.object(common, "send"))):
            mocks[name] = p.start()
            self.addCleanup(p.stop)
        self.send, self.efts = mocks["send"], mocks["efts"]

    def write(self, st):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(st, fh)

    def state(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_alerts_same_day_once_and_the_morning_scan_does_not_repeat(self):
        scan.intraday(ny("2026-09-28T16:35"))
        text = self.send.call_args[0][0]
        self.assertIn("מהיום", text)
        self.assertEqual(common.rtl_bad_lines(text), [])
        st = self.state()
        self.assertEqual((st["days"], st["slots"], st["intraday"]["slot"]), ({}, {"2026-09-28": ["post1"]}, "post1"))
        self.assertEqual(sorted(st["alerted"]["1"]), ["a1", "a2", "a3"])
        scan.intraday(ny("2026-09-28T17:30"))  # the other daylight-saving twin of the same slot
        self.assertEqual((self.send.call_count, self.efts.call_count), (1, 1))
        self.assertEqual(scan.alerts(scan.load(Path(self.path)), TODAY), [])  # the morning scan: nothing to repeat

    def test_nothing_new_sends_nothing(self):
        self.write({"buys": cluster(), "alerted": {"1": ["a1", "a2", "a3"]}})
        scan.intraday(ny("2026-09-28T19:40"))
        self.send.assert_not_called()
        self.assertEqual(self.state()["slots"], {"2026-09-28": ["post2"]})

    def test_outside_a_slot_does_nothing(self):
        scan.intraday(ny("2026-09-28T12:00"))
        self.efts.assert_not_called()
        self.assertNotIn("slots", self.state())

    def test_sec_search_down_warns_once_per_session(self):
        self.efts.side_effect = OSError("down")
        scan.intraday(ny("2026-09-28T16:35"))
        scan.intraday(ny("2026-09-28T19:35"))
        self.assertEqual(self.send.call_count, 1)
        self.assertIn("SEC", self.send.call_args[0][0])
        self.assertEqual(common.rtl_bad_lines(self.send.call_args[0][0]), [])


class Mode(unittest.TestCase):
    def test_from_flags_and_schedule(self):
        a = Namespace(followup=False, intraday=False)
        for env, want in (({}, "scan"), ({"SCHEDULE": "30 5 * * 2-6"}, "scan"), ({"SCHEDULE": "0 7 * * 0"}, "followup"),
                          ({"SCHEDULE": "30 20 * * 1-5"}, "intraday"), ({"SCAN_MODE": "followup"}, "followup")):
            with mock.patch.dict(os.environ, {"SCHEDULE": "", "SCAN_MODE": "", **env}):
                self.assertEqual(scan.mode(a), want, env)
        self.assertEqual(scan.mode(Namespace(followup=False, intraday=True)), "intraday")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run** `python -m unittest tests.test_intraday` — Expected: ERRORs (`scan_day() got an unexpected keyword argument 'urls'`, no `intraday`, no `mode`)

- [ ] **Step 3: Implement** (text edits on `bot/scan.py`)

```python
p = "bot/scan.py"
s = open(p).read()
reps = [
    ("from bot import common, form4, fundamentals, journal, market\n",
     "from bot import common, form4, fundamentals, journal, market, sessions\n"),
    ('INDICES = ("SPY", "IWM", "%5EVIX")\n',
     'INDICES = ("SPY", "IWM", "%5EVIX")\n'
     'TITLE = "🔔 <b>רכישות בעלי עניין בשוק הפתוח</b>"\n'
     'INTRADAY_TITLE = "🔔 <b>רכישות בעלי עניין בשוק הפתוח — מהיום</b>"\n'
     'DAILY, WEEKLY = "30 5 * * 2-6", "0 7 * * 0"  # daily-scan.yml\'s morning and Sunday schedules; others = slots\n'),
    ('    keep = sorted(state["days"])[-(RESCAN + MAX_CATCHUP + 1):]  # a catch-up run still knows what its rescans saw\n',
     '    keep = sorted(state["days"])[-(RESCAN + MAX_CATCHUP + 1):]  # a catch-up run still knows what its rescans saw\n'
     '    keep.append(today.strftime("%Y%m%d"))  # and a same-day run what the earlier same-day runs read\n'),
    ("def scan_day(day, state, today, stats=None, rescan=False):\n",
     "def scan_day(day, state, today, stats=None, rescan=False, urls=None):\n"),
    ("    urls, source = index_urls(day)\n    if urls is None:  # holiday, or not published yet\n",
     '    urls, source = index_urls(day) if urls is None else (urls, "efts")  # same-day runs pass SEC search hits\n'
     "    if urls is None:  # holiday, or not published yet\n"),
    ("def alerts(state, today, j=None):\n", "def alerts(state, today, j=None, title=TITLE):\n"),
    ('    head = (f"🔔 <b>רכישות בעלי עניין בשוק הפתוח</b> · {code(today.isoformat())}", {}, {}, {})\n',
     '    head = (f"{title} · {code(today.isoformat())}", {}, {}, {})\n'),
    ('''    n = 0
    for text, marks, snaps, entries in alerts(state, today, j):
        common.send(text, signal=True)
        state["alerted"].update(marks)  # per delivered message: a later failure never re-sends this one
        state["sent"].update(snaps)
        for cik, e in entries.items():  # 5.1: the journal gets every delivered alert (unique id)
            state["sent"][cik]["id"] = journal.record(j, e)["id"]
        n += len(marks)
        if not a.dry:
            save(path, state)
            journal.save(j)
''', "    n = deliver(state, today, j, path, a.dry)\n"),
    ('''def run(a):
    """One scan:''', '''def deliver(state, today, j, path, dry, title=TITLE):
    """Send every alert message; each is marked delivered (alerted accessions, snapshot, journal entry) right after it
    goes out, so a later failure never re-sends it -> issuers alerted."""
    n = 0
    for text, marks, snaps, entries in alerts(state, today, j, title):
        common.send(text, signal=True)
        state["alerted"].update(marks)
        state["sent"].update(snaps)
        for cik, e in entries.items():  # 5.1: the journal gets every delivered alert (unique id)
            state["sent"][cik]["id"] = journal.record(j, e)["id"]
        n += len(marks)
        if not dry:
            save(path, state)
            journal.save(j)
    return n


def intraday(now=None, dry=False):
    """Same-day run at a session slot (docs/superpowers/specs/2026-09-28-extended-hours-scans-design.md, section 4):
    today's Form 4s from SEC full-text search, only the ones not read yet, merged like the morning scan, then alerts
    for filings not alerted before. The day is not marked scanned - the morning scan still reads the full index."""
    now = now or sessions.now_ny()
    st = sessions.status() or sessions.fallback(now)
    name, today = sessions.slot(now, st), now.date()
    path = Path(common.env("STATE_FILE", str(common.DATA / "state.json")))
    state = prune(load(path), today)
    if not name or not sessions.claim(state, name, today.isoformat()):
        print(f"intraday {now:%Y-%m-%d %H:%M} New York: no session slot to run")
        return 0
    day, session = today.strftime("%Y%m%d"), sessions.SLOTS[name][1]
    market.CACHE.update(state["prices"])
    try:
        urls = efts_urls(day)
    except Exception as e:  # SEC search down: one warning per session; the morning scan reads the full index anyway
        common.log("efts_unavailable", day=day, error=f"{type(e).__name__}: {e}")
        if sessions.first(state, f"efts:{session}", today.isoformat()):
            common.send(f"⚠️ חיפוש ההגשות של SEC לא זמין כרגע ({code(type(e).__name__)}); הסריקה של הבוקר תשלים.")
        if not dry:
            save(path, state)
        _mark_reported()
        return 0
    stats = {"days": []}
    scan_day(day, state, today, stats, rescan=True, urls=urls)
    if not state.get("regime"):
        state["regime"] = market.regime()
    j = journal.load()
    n = deliver(state, today, j, path, dry, INTRADAY_TITLE)
    rec = (stats["days"] or [{}])[-1]
    state["intraday"] = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "slot": name,
                         "filings": rec.get("filings", 0), "parsed": rec.get("parsed", 0), "alerts": n}
    state["prices"] = {t: v for t, v in market.CACHE.items()}
    if not dry:
        save(path, prune(state, today))
    common.log("intraday", slot=name, filings=rec.get("filings", 0), alerts=n)
    _mark_reported()
    return 0


def mode(a):
    """"followup" | "intraday" | "scan" from the flags, SCAN_MODE and the triggering schedule (SCHEDULE)."""
    sched = common.env("SCHEDULE")
    if a.followup or common.env("SCAN_MODE") == "followup" or sched == WEEKLY:
        return "followup"
    if a.intraday or common.env("SCAN_MODE") == "intraday" or (sched and sched != DAILY):
        return "intraday"
    return "scan"


def run(a):
    """One scan:'''),
    ('''    ap.add_argument("--followup", action="store_true",''',
     '''    ap.add_argument("--intraday", action="store_true", help="same-day run at a session slot (also from the "
                                                              "schedule: env SCHEDULE)")
    ap.add_argument("--followup", action="store_true",'''),
    ('''        followup(dry=a.dry) if a.followup or common.env("SCAN_MODE") == "followup" else run(a)\n''',
     '''        m = mode(a)
        followup(dry=a.dry) if m == "followup" else intraday(dry=a.dry) if m == "intraday" else run(a)\n'''),
]
for a, b in reps:
    assert s.count(a) == 1, a[:70]
    s = s.replace(a, b, 1)
open(p, "w").write(s)
```

- [ ] **Step 4: Run** `python -m unittest tests.test_intraday -v` then `python -m unittest discover -s tests` and `python -m pyflakes bot/scan.py tests/test_intraday.py` — Expected: PASS, suite OK, no new warnings
- [ ] **Step 5: Commit** `git add bot/scan.py tests/test_intraday.py && git commit -m "Insider scan: same-day runs at the session slots (SEC search, only new alerts)"`

---

### Task 4: Slot-aware squeeze runs, change messages, movers (`bot/squeeze_live.py`)

**Files:** Modify `bot/squeeze_live.py`, `tests/test_squeeze_live.py`

**Interfaces:** Consumes Task 1 (`sessions.*`) and Task 2 (`movers.quotes/reference/find/fresh/log/text/NAME`). Produces `STATE, MOVERS_LOG`, `build(target)` result gains `"universe": [{"t", "si_pct", "dtc", "adv", "bars"}]`, `change_text(res, old_rows) -> str`, `run_movers(res, session, today, state, manual) -> (text, found)`, `main(argv)` with `--manual` / `SQUEEZE_MODE=manual`.

- [ ] **Step 1: Replace the old `Main` tests with the new ones** — in `tests/test_squeeze_live.py` delete the whole `class Main(unittest.TestCase):` block (up to `class Command`), add `import copy` to the imports and `from test_sessions import day, ny` after the `bot` import, and add before `class Command`:

```python
UNI = [{"t": "AAA", "si_pct": 0.3, "dtc": 3.0, "adv": 1_000_000.0,
        "bars": {"d": ["2026-09-25", "2026-09-28"], "c": [9.0, 10.0]}}]
LIVE = dict(RES, date="2026-09-29", universe=UNI)  # built after Monday's close, for Tuesday
AAA_UP = {"AAA": {"price": 11.5, "volume": 300_000.0, "date": "2026-09-28"}}


class Sessions(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def run_at(self, when, res=LIVE, argv=(), quotes=AAA_UP):
        """One squeeze_live.main run at New York time `when`, with everything but its logic mocked."""
        build = res if callable(res) else mock.Mock(return_value=copy.deepcopy(res))
        with mock.patch.dict(os.environ, {"SQUEEZE_DIR": self.dir, "SQUEEZE_MODE": ""}), \
                mock.patch.object(squeeze_live.sessions, "now_ny", return_value=ny(when)), \
                mock.patch.object(squeeze_live.sessions, "status", return_value=day()), \
                mock.patch.object(squeeze_live, "build", build), mock.patch.object(squeeze_live, "enrich"), \
                mock.patch.object(squeeze_live, "followup", return_value=0), \
                mock.patch.object(squeeze_live.movers, "quotes", return_value=quotes), \
                mock.patch.object(squeeze_live.common, "send") as send, mock.patch("builtins.print"):
            code = squeeze_live.main(list(argv))
        return code, [c.args[0] for c in send.call_args_list], build

    def test_after_close_sends_the_list_and_movers_then_nothing_repeats(self):
        code, sent, build = self.run_at("2026-09-28T16:35")
        self.assertEqual(code, 0)
        build.assert_called_once_with("2026-09-29")  # after Monday's close the list is for Tuesday
        self.assertEqual(len(sent), 2)
        self.assertIn("רשימת סקוויז ליום <code>2026-09-29</code>", sent[0])
        self.assertIn("🌙", sent[1])
        self.assertIn("<code>+15%</code>", sent[1])  # 11.5 against today's 10.0 close
        for t in sent:
            self.assertEqual(common.rtl_bad_lines(t), [])
        with mock.patch.dict(os.environ, {"SQUEEZE_DIR": self.dir}):
            self.assertEqual(squeeze_live.load(squeeze_live.MOVERS_LOG, {})["entries"][0]["t"], "AAA")
        self.assertEqual(self.run_at("2026-09-28T17:30")[1], [])  # the same slot again (daylight-saving twin)
        self.assertEqual(self.run_at("2026-09-28T19:35")[1], [])  # post2: same list, same move

    def test_a_changed_list_sends_only_the_change(self):
        self.run_at("2026-09-28T16:35", quotes={})
        sent = self.run_at("2026-09-28T19:35", res=dict(LIVE, rows=[dict(ROW, t="NEW")]), quotes={})[1]
        self.assertEqual(len(sent), 1)
        for part in ("🔄", "<code>NEW</code>", "יצאו מהרשימה: <code>AAA</code>"):
            self.assertIn(part, sent[0])
        self.assertEqual(common.rtl_bad_lines(sent[0]), [])

    def test_manual_at_night_sends_the_full_list_and_says_the_market_is_closed(self):
        self.run_at("2026-09-28T16:35", quotes={})
        code, sent, _ = self.run_at("2026-09-28T21:30", argv=["--manual"])
        self.assertEqual((code, len(sent)), (0, 2))
        self.assertIn("▶️", sent[0])
        self.assertIn("רשימת סקוויז", sent[0])
        self.assertIn("סגור", sent[1])

    def test_manual_during_the_session_shows_live_movers(self):
        live = {"AAA": {"price": 10.5, "volume": 2e6, "date": "2026-09-28"}}
        code, sent, build = self.run_at("2026-09-28T11:00", argv=["--manual"], quotes=live)
        build.assert_called_once_with("2026-09-28")
        self.assertIn("📈", sent[1])
        self.assertIn("<code>+17%</code>", sent[1])  # against Friday's 9.0 close

    def test_failed_build_warns_once_per_session_and_manual_always(self):
        boom = mock.Mock(side_effect=RuntimeError("FINRA: down"))
        code, sent, _ = self.run_at("2026-09-28T16:35", res=boom)
        self.assertEqual(code, 1)
        self.assertIn("נכשלה", sent[0])
        self.assertEqual(self.run_at("2026-09-28T19:35", res=boom)[1], [])
        self.assertIn("נכשלה", self.run_at("2026-09-28T19:50", res=boom, argv=["--manual"])[1][0])

    def test_nasdaq_down_warns_once_per_session(self):
        down = {"AAA": False}
        self.assertTrue(any("Nasdaq" in t for t in self.run_at("2026-09-28T16:35", quotes=down)[1]))
        self.assertFalse(any("Nasdaq" in t for t in self.run_at("2026-09-28T19:35", quotes=down)[1]))

    def test_dry_run_writes_and_sends_nothing(self):
        code, sent, _ = self.run_at("2026-09-28T16:35", argv=["--dry"])
        self.assertEqual((code, sent, os.listdir(self.dir)), (0, [], []))

    def test_outside_a_slot_does_nothing(self):
        code, sent, build = self.run_at("2026-09-28T12:00")
        build.assert_not_called()
        self.assertEqual((code, sent), (0, []))
```

- [ ] **Step 2: Run** `python -m unittest tests.test_squeeze_live` — Expected: FAILs/ERRORs in `Sessions` (no `sessions` / `movers` in squeeze_live, old `main`)

- [ ] **Step 3: Implement** (text edits on `bot/squeeze_live.py`)

```python
p = "bot/squeeze_live.py"
s = open(p).read()
start = s.index("# ---------- run ----------")
end = s.index('if __name__ == "__main__":')
reps = [
    ("from bot import borrow, common, market, options, shorts, squeeze\n",
     "from bot import borrow, common, market, movers, options, sessions, shorts, squeeze\n"),
    ('LAST, JOURNAL = "squeeze_last.json", "squeeze_journal.json"\n',
     'LAST, JOURNAL = "squeeze_last.json", "squeeze_journal.json"\n'
     'STATE, MOVERS_LOG = "squeeze_state.json", "movers_log.json"\n'),
    ('    sessions incl. today)}. RuntimeError',
     '    sessions incl. today), "universe" (the gated stocks with their bars, for the movers scan)}. RuntimeError'),
    ('''            "recent": cal[-(squeeze.WINDOW - 1):] + [today]}''',
     '''            "recent": cal[-(squeeze.WINDOW - 1):] + [today],
            "universe": [{"t": t, "si_pct": si["si"] / n, "dtc": si.get("dtc") or 0.0, "adv": si.get("adv"),
                          "bars": bars.get(t)} for t, si, n in gated]}'''),
    ('''    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    cal = calendar(today)
    usable''', '''    today = today or sessions.target_session(sessions.now_ny(), sessions.status())
    cal = calendar(today)
    usable'''),
    ('''

# ---------- journal ----------''', '''

def change_text(res, old):
    """What changed since the list already sent for this session: the names that entered (with their line) and the
    names that left."""
    was, now = {r["t"] for r in old}, [r["t"] for r in res["rows"]]
    lines = [f"🔄 <b>שינוי ברשימת הסקוויז ליום {code(res['date'])}</b>"]
    lines += [line(k, r) for k, r in enumerate(res["rows"], 1) if r["t"] not in was]
    left = [t for t in sorted(was) if t not in now]
    if left:
        lines.append("יצאו מהרשימה: " + ", ".join(map(code, left)) + ".")
    return "\\n".join(lines + [footer()])


# ---------- journal ----------'''),
]
run_block = '''# ---------- run ----------
def run_movers(res, session, today, state, manual):
    """The movers part of a run -> (message or '', movers found). Nasdaq down for every stock -> one warning per
    session (always on a manual run)."""
    if not session:
        return (movers.text([], None, today, manual=True) if manual else ""), []
    cands = [{**u, "ref": movers.reference(u["bars"], session, today)} for u in res["universe"]]
    qs = movers.quotes([c["t"] for c in cands], session)
    failed = sum(q is False for q in qs.values())
    if cands and failed == len(cands):
        warn = manual or sessions.first(state, f"nasdaq:{session}", today)
        return (f"⚠️ לא התקבלו ציטוטים מ־Nasdaq לאף מניה ({movers.NAME[session]}); אנסה שוב בריצה הבאה."
                if warn else ""), []
    found = movers.fresh(movers.find(cands, qs, today), state, today, session, manual)
    ranks = {r["t"]: k for k, r in enumerate(res["rows"], 1)}
    return movers.text(found, session, today, ranks, failed, manual), found


def main(argv=None):
    ap = argparse.ArgumentParser(description="Squeeze list and extended-hours movers -> Telegram and data/")
    ap.add_argument("--dry", action="store_true", help="print the messages only, write nothing")
    ap.add_argument("--manual", action="store_true", help="run now, whatever the time (also SQUEEZE_MODE=manual)")
    a = ap.parse_args(argv)
    manual = a.manual or common.env("SQUEEZE_MODE") == "manual"
    now = sessions.now_ny()
    st = sessions.status() or sessions.fallback(now)
    today = now.date().isoformat()
    state = load(STATE, {"v": 1})
    if manual:
        session = sessions.session_now(now, st)
    else:
        name = sessions.slot(now, st)
        if not name or not sessions.claim(state, name, today):
            print(f"squeeze {now:%Y-%m-%d %H:%M} New York: no session slot to run")
            return 0
        session = sessions.SLOTS[name][1]
    target = sessions.target_session(now, st)
    try:
        res = build(target)
    except Exception as e:  # the owner hears about it once per session (every time on a manual run)
        traceback.print_exc()
        if not a.dry:
            if manual or sessions.first(state, f"build:{session}", today):
                common.send(f"⚠️ סריקת הסקוויז נכשלה ({code(type(e).__name__)}): {common.esc(str(e)[:200])}")
            save(STATE, state)
        return 1
    try:
        enrich(res["rows"], dt.date.fromisoformat(target))
    except Exception:  # unvalidated extras must never cost the list
        traceback.print_exc()
    j = load(JOURNAL, {"v": 1, "entries": []})
    try:
        filled = followup(j, today)
    except Exception:  # a follow-up problem must not hold back the list; it is retried next run
        traceback.print_exc()
        filled = 0
    added = record(j, res)
    full, last = message(res), load(LAST, {})
    old = last.get("rows") if last.get("date") == target else None
    if manual or old is None:
        list_text = ("▶️ הרצה ידנית\\n" if manual else "") + full
    elif {r["t"] for r in res["rows"]} != {r["t"] for r in old}:
        list_text = change_text(res, old)
    else:
        list_text = ""
    movers_text, found = run_movers(res, session, today, state, manual)
    note = (f"📒 הושלם מעקב של {code(squeeze.WINDOW)} ימים ל־{code(filled)} מניות: {code('/squeeze stats')}"
            if filled else "")
    texts = [t for t in (list_text, movers_text, note) if t]
    if a.dry:
        print("\\n\\n".join(texts) or "nothing new")
        return 0
    for t in texts:
        common.send(t, signal=True)
    if found:
        save(MOVERS_LOG, movers.log(load(MOVERS_LOG, {"v": 1, "entries": []}), found, today, session, manual))
    save(LAST, {"date": target, "text": full, "rows": [{k: _round(v) for k, v in r.items()} for r in res["rows"]]})
    save(JOURNAL, j)
    save(STATE, state)
    common.log("squeeze", target=target, session=session, manual=manual, rows=len(res["rows"]), movers=len(found),
               sent=len(texts), added=added, filled=filled)
    return 0


'''
s = s[:start] + run_block + s[end:]
for a, b in reps:
    assert s.count(a) == 1, a[:70]
    s = s.replace(a, b, 1)
open(p, "w").write(s)
```

- [ ] **Step 4: Run** `python -m unittest tests.test_squeeze_live -v`, `python -m unittest discover -s tests`, `python -m pyflakes bot/squeeze_live.py tests/test_squeeze_live.py` — Expected: PASS, OK, clean
- [ ] **Step 5: Commit** `git commit -am "Squeeze: session slots, list after the close, change messages, movers, manual runs"`

---

### Task 5: `/squeeze now` in the listener

**Files:** Modify `bot/listen.py`, `tests/test_squeeze_live.py`

**Interfaces:** Consumes `common.dispatch(workflow, inputs) -> "" | error`, `squeeze_live.main(argv)`. Produces `listen.squeeze_now() -> int`.

- [ ] **Step 1: Add the failing test** to class `Command` in `tests/test_squeeze_live.py`

```python
    def test_squeeze_now_starts_the_workflow(self):
        from bot import listen
        env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}
        with mock.patch.dict(os.environ, env), mock.patch.object(listen.common, "send") as send, \
                mock.patch.object(listen.common, "dispatch", return_value="") as disp:
            listen.handle("/squeeze now")
        disp.assert_called_once_with("squeeze.yml", {"mode": "manual"})
        self.assertIn("הפעלתי", send.call_args[0][0])
        with mock.patch.dict(os.environ, env), mock.patch.object(listen.common, "send") as send, \
                mock.patch.object(listen.common, "dispatch", return_value="HTTP 403"), mock.patch("builtins.print"):
            self.assertEqual(listen.handle("/squeeze now"), 1)
        self.assertIn("HTTP 403", send.call_args[0][0])
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "", "GITHUB_TOKEN": ""}), \
                mock.patch.object(listen.common, "send"), \
                mock.patch.object(listen.squeeze_live, "main", return_value=0) as run:
            listen.handle("/squeeze now")
        run.assert_called_once_with(["--manual"])
        self.assertIn("/squeeze now", listen.HELP)
```

- [ ] **Step 2: Run** `python -m unittest tests.test_squeeze_live.Command` — Expected: FAIL (`/squeeze now` is treated as a ticker)

- [ ] **Step 3: Implement** (text edits on `bot/listen.py`)

```python
p = "bot/listen.py"
s = open(p).read()
reps = [
    ('("squeeze", "רשימת סקוויז: /squeeze, /squeeze GME, /squeeze stats")',
     '("squeeze", "סקוויז ומזנקות: /squeeze, /squeeze now, /squeeze GME, /squeeze stats")'),
    ('''    f"• רשימת סקוויז יומית (שורט גבוה ומחזור חריג), בדיקת מניה ויומן: {code('/squeeze')},"
    f" {code('/squeeze GME')}, {code('/squeeze stats')}",''',
     '''    f"• רשימת סקוויז ומזנקות: {code('/squeeze')}, הרצה עכשיו {code('/squeeze now')},"
    f" בדיקת מניה {code('/squeeze GME')}, יומן {code('/squeeze stats')}",'''),
    ('''    if arg == "stats":
        common.send(squeeze_live.stats_text(''', '''    if arg == "now":
        return squeeze_now()
    if arg == "stats":
        common.send(squeeze_live.stats_text('''),
    ('''

class Conflict(Exception):''', '''

def squeeze_now():
    """/squeeze now: on Actions the squeeze workflow is dispatched (the only writer of data/squeeze_*.json); locally
    the run happens in this process. Either way the owner hears back."""
    if common.env("GITHUB_REPOSITORY") and common.env("GITHUB_TOKEN"):
        err = common.dispatch("squeeze.yml", {"mode": "manual"})
        if err:  # a GitHub permission problem must not look like a data problem
            print(f"workflow dispatch failed: {err}")
            common.send(f"⚠️ לא הצלחתי להפעיל את סריקת הסקוויז ב־GitHub ({code(err)}). בדקו שבקובץ"
                        f" {code('telegram-listen.yml')} מופיעה ההרשאה {code('actions: write')}.")
            return 1
        common.send("⏳ הפעלתי את סריקת הסקוויז. הרשימה והמזנקות יגיעו בעוד כ־2–3 דקות.")
        return 0
    common.send("⏳ מריץ את סריקת הסקוויז...")
    return squeeze_live.main(["--manual"])


class Conflict(Exception):'''),
]
for a, b in reps:
    assert s.count(a) == 1, a[:70]
    s = s.replace(a, b, 1)
open(p, "w").write(s)
```

- [ ] **Step 4: Run** `python -m unittest discover -s tests` — Expected: OK (the menu test and HELP RTL test still pass)
- [ ] **Step 5: Commit** `git commit -am "Listener: /squeeze now starts the squeeze workflow"`

---

### Task 6: Workflows and README

**Files:** Modify `.github/workflows/daily-scan.yml`, `.github/workflows/squeeze.yml`, `README.md`

- [ ] **Step 1: daily-scan.yml** — after the line `    - cron: "0 7 * * 0"     # Sunday 07:00 UTC: weekly journal follow-up (returns vs SPY + summary)` insert:

```yaml
    # Same-day insider runs at 07:30 / 09:15 / 16:30 / 19:30 New York, each with a summer (EDT) and a winter (EST)
    # UTC twin; bot/sessions.py runs only the twin that lands on its slot (extended-hours spec, section 3)
    - cron: "30 11 * * 1-5"
    - cron: "30 12 * * 1-5"
    - cron: "15 13 * * 1-5"
    - cron: "15 14 * * 1-5"
    - cron: "30 20 * * 1-5"
    - cron: "30 21 * * 1-5"
    - cron: "30 23 * * 1-5"
    - cron: "30 0 * * 2-6"
```

and after the `SCAN_MODE:` env line insert `          SCHEDULE: ${{ github.event.schedule }}  # morning / Sunday / session slot: bot.scan picks the mode`.

- [ ] **Step 2: squeeze.yml** — replace the whole file with:

```yaml
name: Squeeze list

on:
  schedule:  # 07:30 / 09:15 / 16:30 / 19:30 New York, each with a summer (EDT) and a winter (EST) UTC twin;
    # bot/sessions.py runs only the twin that lands on its slot (extended-hours spec, section 3)
    - cron: "30 11 * * 1-5"
    - cron: "30 12 * * 1-5"
    - cron: "15 13 * * 1-5"
    - cron: "15 14 * * 1-5"
    - cron: "30 20 * * 1-5"
    - cron: "30 21 * * 1-5"
    - cron: "30 23 * * 1-5"
    - cron: "30 0 * * 2-6"
  workflow_dispatch:
    inputs:
      mode:
        description: "manual = run now, whatever the time (like /squeeze now); slot = behave like a scheduled run"
        type: choice
        options: [manual, slot]
        default: manual

permissions:
  contents: write  # commit the squeeze list, journal, state and movers log

concurrency:
  group: squeeze
  cancel-in-progress: false

jobs:
  squeeze:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.ref_name }}  # branch tip: the journal and state committed by the last run
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Squeeze list and extended-hours movers
        timeout-minutes: 20  # a step timeout fails the step, so the alarm below runs
        run: python -m bot.squeeze_live
        env:
          SQUEEZE_MODE: ${{ inputs.mode || 'slot' }}
          SEC_UA: ${{ secrets.SEC_UA }}
          TG_TOKEN: ${{ secrets.TG_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
      - name: Commit the list, journal, state and movers log
        if: always()
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          for f in data/squeeze_last.json data/squeeze_journal.json data/squeeze_state.json data/movers_log.json; do
            if [ -f "$f" ]; then git add "$f"; fi
          done
          if git diff --cached --quiet; then echo "nothing to commit"; exit 0; fi
          git commit -m "Update squeeze list"
          for i in 1 2 3 4 5; do
            git push && exit 0
            sleep $((i * 5))
            git pull --rebase || git rebase --abort || true
          done
          exit 1
      - name: Report a failure the run could not report itself
        if: failure() || cancelled()
        run: |
          text="$(printf '❌ רשימת הסקוויז נכשלה ב־GitHub Actions: %s\n\n%s' "$RUN_URL" "$DISCLAIMER")"
          curl -fsS --retry 3 -o /dev/null "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${TG_CHAT_ID}" --data-urlencode "text=${text}"
        env:
          DISCLAIMER: "⚠️ מידע לצורכי מחקר בלבד ואינו ייעוץ השקעות."
          TG_TOKEN: ${{ secrets.TG_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
```

- [ ] **Step 3: Lint** `actionlint .github/workflows/*.yml` — Expected: no output

- [ ] **Step 4: README** (text edits)

```python
p = "README.md"
s = open(p).read()
reps = [
    ("(<code dir=\"ltr\">4/A</code>). לכל חברה מופיעים:",
     "(<code dir=\"ltr\">4/A</code>). בנוסף, בכל יום מסחר ב־07:30, 09:15, 16:30 ו־19:30 שעון ניו יורק (14:30, 16:15,"
     " 23:30 ו־02:30 שעון ישראל) נבדקים גם הדיווחים של אותו יום בחיפוש של SEC, והתראה חדשה נשלחת כבר באותו"
     " ערב, בסימון \"מהיום\". הסריקה של הבוקר לא חוזרת עליה. לכל חברה מופיעים:"),
    ("בכל יום מסחר ב־12:00 UTC, לפני הפתיחה בניו יורק, מגיעה רשימה של עד 10 מניות",
     "מיד אחרי סגירת המסחר (16:30 שעון ניו יורק, 23:30 שעון ישראל) מגיעה רשימה ליום המסחר הבא, של עד 10 מניות"),
    ("- **אופציות והשאלה:**",
     "- **שינויים:** ב־19:30 ולפני הפתיחה הרשימה נבנית שוב, ונשלחת רק אם השתנתה: מי נכנס ומי יצא.\n"
     "- **מזנקות לפני ואחרי המסחר:** ב־07:30 ו־09:15 (לפני הפתיחה) וב־16:30 ו־19:30 (אחרי הסגירה) שעון ניו יורק"
     " נבדקות כ־120 המניות עם שורט של 20% ומעלה. מניה שעלתה 10% לפחות מהסגירה, במחזור של 10% לפחות מיום רגיל"
     " (לפי Nasdaq), נשלחת פעם אחת בכל סשן, ושוב רק אם עלתה עוד 10 נקודות. הכלל לא נבדק היסטורית ומסומן"
     " \"לא מאומת\"; כל מזנקת נרשמת ב־<code dir=\"ltr\">data/movers_log.json</code>.\n"
     "- **הרצה ידנית:** <code dir=\"ltr\">/squeeze now</code> מריץ את הרשימה ואת המזנקות מיד, בכל שעה. בשעות"
     " המסחר הרגילות נבדקת התנועה החיה באותו כלל. התוצאה מגיעה תוך 2–3 דקות.\n"
     "- **אופציות והשאלה:**"),
    ("רשימת הסקוויז האחרונה; <code dir=\"ltr\">/squeeze GME</code>",
     "רשימת הסקוויז האחרונה; <code dir=\"ltr\">/squeeze now</code> מריץ עכשיו את הרשימה והמזנקות;"
     " <code dir=\"ltr\">/squeeze GME</code>"),
    ("| `bot/squeeze_live.py` | הרשימה היומית, היומן ו־<code dir=\"ltr\">/squeeze</code> |",
     "| `bot/squeeze_live.py` | הרשימה היומית, היומן, המזנקות ו־<code dir=\"ltr\">/squeeze</code> |\n"
     "| `bot/sessions.py` | שעות הריצה לפי שעון ניו יורק, חגים, ימים קצרים ושעון קיץ |\n"
     "| `bot/movers.py` | מזנקות לפני ואחרי המסחר מ־Nasdaq (לא מאומת) |"),
    ("| `data/squeeze_last.json`, `data/squeeze_journal.json` |",
     "| `data/squeeze_last.json`, `data/squeeze_journal.json`, `data/squeeze_state.json`, `data/movers_log.json` |"),
]
for a, b in reps:
    assert s.count(a) == 1, a[:60]
    s = s.replace(a, b, 1)
i = s.index("- **רשימת הסקוויז:** FINRA")
j = s.index("\n", i)
s = s[:j] + (" נתוני Nasdaq (מזנקות, שעות המסחר) הם מקור לא רשמי שעשוי להתעכב בכ־15 דקות; כשהוא לא זמין נשלחת"
             " אזהרה אחת לכל סשן.") + s[j:]
open(p, "w").write(s)
```

- [ ] **Step 5: Run** `python -m unittest discover -s tests` and commit `git add .github/workflows README.md && git commit -m "Workflows: session-slot schedules for the insider and squeeze runs; README"`

---

### Task 7: Real-data checks and hand-off

- [ ] **Step 1:** `python -c "from bot import sessions; print(sessions.status())"` — Expected: today's session times from Nasdaq.
- [ ] **Step 2:** `SEC_UA="<name> <email>" python -m bot.squeeze_live --manual --dry` — Expected: the list (target session named) plus the movers part for the current session (or "market closed"), RTL clean, no files written.
- [ ] **Step 3:** Same-day insider dry run on a copy of the state: `STATE_FILE=<scratch copy of data/state.json> python -c "from bot import scan, sessions; scan.intraday(sessions.now_ny().replace(hour=16, minute=35), dry=True)"` with `SEC_UA` set and `TG_TOKEN` unset — Expected: EFTS fetched for today, parsed counts logged, alerts printed (not sent), the copy untouched.
- [ ] **Step 4:** Final whole-branch review by a fresh reviewer; fix Critical/Important test-first; push `extended-hours`; open the PR; confirm CI green. Merging is the owner's call.
