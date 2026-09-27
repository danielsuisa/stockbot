# Squeeze Screener — Phases 2–3 (Live list, borrow, options) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send the backtest's frozen squeeze list to Telegram every trading day before the US open, with `/squeeze` commands, a 10-session outcome journal, and unvalidated IBKR-borrow and CBOE-options numbers.

**Architecture:** `bot/squeeze_live.py` assembles today's data (FINRA via `shorts`, SEC frames, Yahoo bars) and ranks it with the same `squeeze` functions the backtest used; `bot/borrow.py` (IBKR FTP) and `bot/options.py` (CBOE) add display-only extras; `bot/listen.py` gains `/squeeze`; `.github/workflows/squeeze.yml` runs it Mon–Fri 12:00 UTC and commits `data/squeeze_*.json`.

**Tech Stack:** Python 3.11/3.12 stdlib (`ftplib`, `urllib` via `common.fetch`, `concurrent.futures`), `unittest` offline tests, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-27-squeeze-screener-design.md` (sections 8–11). Backtest verdict: `docs/backtest/squeeze-2026-09-27.md` (run 3: GO; out-of-sample spike 6.8 %, crash 7.1 %, market 1.0 %).

## Global Constraints

- Stdlib only; offline tests with every network call mocked; `python -m unittest discover -s tests` stays green.
- Frozen variant: `{"w": 0.7, "g_si": 0.20, "g_rv": 2.0}` (`w0.7/si0.2/rv2`); the live list uses `squeeze.make_row` / `squeeze.rank` unchanged.
- Every Hebrew Telegram line starts with a Hebrew letter or an emoji followed by Hebrew (`common.rtl_bad_lines(text) == []`); tickers and numbers go through `common.code`.
- Every list, `/squeeze TICKER` and `/squeeze stats` message carries the backtest line with BOTH the spike rate (6.8 %) and the crash rate (7.1 %).
- Borrow and options values are labelled "לא מאומת" and never change the rank.
- Journal counts a ticker once per 10 sessions (as the backtest); outcome = open of the first session on/after the list date, window 10 sessions, hit ≥ 1.5×, crash ≤ ÷1.5.
- Existing commands and workflows keep their behavior; the new workflow has its own concurrency group.

## Review Focus

1. Pre-market run on a day whose newest FINRA report is not yet public → the previous report is used, never the unpublished one → `test_ranked_list_from_the_newest_usable_report` (Task 4).
2. IBKR FTP unreachable from Actions (the probe we could not run) → the list still goes out, borrow shows as missing → `test_unreachable_is_empty` (Task 2) + `test_extras_attached_and_missing_sources_are_none` (Task 4).
3. A class share (`BRK-B`) asked through `/squeeze` → FINRA is queried as `BRKB` → `test_glue` (Task 5).
4. A failed build (FINRA / Yahoo down) → the owner gets a Telegram message and no files are written → `test_failed_build_is_reported` (Task 5).
5. The same ticker listed on consecutive days → journaled once per 10 sessions → `test_record_counts_a_ticker_once_per_window` (Task 5).

---

### Task 1: Move `to_bars` into `bot/squeeze.py` (shared by backtest and live)

**Files:** Modify `bot/squeeze.py`, `bot/backtest.py`, `tests/test_backtest.py`

**Interfaces:** Produces `squeeze.to_bars(r) -> bars | None` (same behavior as `backtest.to_bars`, including `"s"` split events). `backtest.to_bars` is removed.

- [ ] **Step 1: Point the tests at the new home** (RED)

```python
import re
p = "tests/test_backtest.py"
s = open(p).read()
assert s.count("backtest.to_bars(") == 3
s = s.replace("backtest.to_bars(", "squeeze.to_bars(")
s = s.replace("from bot import backtest, shorts\n", "from bot import backtest, shorts, squeeze\n", 1)
open(p, "w").write(s)
```

- [ ] **Step 2: Run** `python -m unittest tests.test_backtest` — Expected: ERROR `AttributeError: module 'bot.squeeze' has no attribute 'to_bars'`

- [ ] **Step 3: Move the function**

```python
import re
b = open("bot/backtest.py").read()
start = b.index("def to_bars(r):")
end = b.index("def pack(b):")
fn = b[start:end]
b = b[:start] + b[end:]
b = b.replace("lambda: to_bars(market.chart(", "lambda: squeeze.to_bars(market.chart(", 1)
open("bot/backtest.py", "w").write(b)
s = open("bot/squeeze.py").read()
s = s.replace('import heapq\n', 'import datetime as dt\nimport heapq\n', 1)
s = s.rstrip("\n") + "\n\n\n" + fn.rstrip("\n") + "\n"
open("bot/squeeze.py", "w").write(s)
```

- [ ] **Step 4: Run** `python -m unittest discover -s tests` and `python -m pyflakes bot tests/test_backtest.py` — Expected: OK, no warnings

- [ ] **Step 5: Commit** `git commit -am "Squeeze: to_bars moves to squeeze.py (shared by backtest and live)"`

---

### Task 2: `bot/borrow.py` — IBKR short-borrow file

**Files:** Create `bot/borrow.py`, `tests/test_squeeze_extras.py`

**Interfaces:** Produces `borrow.parse(text) -> {ticker: {"fee": float|None, "avail": int|None, "more": bool}}`, `borrow.fetch(timeout=30) -> same dict ({} on failure)`.

- [ ] **Step 1: Write the failing tests** — `tests/test_squeeze_extras.py`

```python
"""Squeeze extras (display-only, unvalidated): IBKR borrow file and CBOE gamma fuel."""
import datetime as dt
import unittest
from unittest import mock

from bot import borrow

USA = ("#BOF|2026.09.25|09:45:02\r\n#SYM|CUR|NAME|CON|ISIN|REBATERATE|FEERATE|AVAILABLE|\r\n"
       "GME|USD|GAMESTOP CORP-CLASS A|36285269|US36467W1099|3.9102|0.2498|>10000000|\r\n"
       "BRK B|USD|BERKSHIRE HATH-CL B|72063691|US0846707026|4.0|0.25|5000000|\r\n"
       "HTZ|USD|HERTZ GLOBAL|1|X|-40.1|45.5|0|\r\n"
       "BAD|USD|BROKEN||||NaNx||\r\n"
       "SAP|EUR|SAP SE|2|DE|1|0.3|1000|\r\n#EOF|5\r\n")


class Borrow(unittest.TestCase):
    def test_parse(self):
        got = borrow.parse(USA)
        self.assertEqual(got["GME"], {"fee": 0.2498, "avail": 10_000_000, "more": True})
        self.assertEqual(got["BRK-B"], {"fee": 0.25, "avail": 5_000_000, "more": False})
        self.assertEqual(got["HTZ"], {"fee": 45.5, "avail": 0, "more": False})
        self.assertEqual(got["BAD"], {"fee": None, "avail": None, "more": False})
        self.assertNotIn("SAP", got)

    def test_unreachable_is_empty(self):
        with mock.patch.object(borrow.ftplib, "FTP", side_effect=TimeoutError("timed out")), mock.patch("builtins.print"):
            self.assertEqual(borrow.fetch(timeout=1), {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run** `python -m unittest tests.test_squeeze_extras` — Expected: ERROR `ImportError: cannot import name 'borrow'`

- [ ] **Step 3: Implement `bot/borrow.py`**

```python
"""IBKR's public short-borrow file (free: ftp3.interactivebrokers.com, user "shortstock", file usa.txt): the annual
fee rate and the shares available to borrow for US stocks, refreshed during the day. The squeeze list shows it as
unvalidated - there is no free history to backtest it."""
import ftplib
import io

HOST, USER, FILE = "ftp3.interactivebrokers.com", "shortstock", "usa.txt"


def parse(text):
    """usa.txt -> {ticker (SEC spelling): {"fee": annual % or None, "avail": shares or None, "more": bool}}; "more"
    marks IBKR's ">10000000" (at least that many). Columns come from the #SYM header; USD lines only."""
    head, out = None, {}
    for line in text.splitlines():
        if line.startswith("#SYM"):
            head = line[1:].split("|")
            continue
        if head is None or not line.strip() or line.startswith("#"):
            continue
        f = dict(zip(head, line.split("|")))
        sym = (f.get("SYM") or "").strip().upper().replace(" ", "-")  # IBKR writes class shares as "BRK B"
        if not sym or f.get("CUR") != "USD":
            continue
        avail = (f.get("AVAILABLE") or "").strip()
        try:
            n = int(avail.lstrip(">"))
        except ValueError:
            n = None
        try:
            fee = float(f.get("FEERATE") or "")
        except ValueError:
            fee = None
        out[sym] = {"fee": fee, "avail": n, "more": avail.startswith(">")}
    return out


def fetch(timeout=30):
    """All of usa.txt, parsed; {} when the FTP server cannot be reached (the list then shows borrow as missing)."""
    buf = io.BytesIO()
    try:
        ftp = ftplib.FTP(HOST, timeout=timeout)
        ftp.login(USER, "")
        ftp.retrbinary(f"RETR {FILE}", buf.write)
        ftp.quit()
    except (OSError, EOFError, ftplib.Error) as e:
        print(f"ibkr borrow file: {type(e).__name__} {e}")
        return {}
    return parse(buf.getvalue().decode("utf-8", "replace"))
```

- [ ] **Step 4: Run** `python -m unittest tests.test_squeeze_extras -v` — Expected: PASS

- [ ] **Step 5: Commit** `git add bot/borrow.py tests/test_squeeze_extras.py && git commit -m "Squeeze extras: IBKR short-borrow file"`

---

### Task 3: `bot/options.py` — CBOE gamma fuel

**Files:** Create `bot/options.py`; Modify `tests/test_squeeze_extras.py`

**Interfaces:** Produces `options.NEAR = 21`, `options.chain(t) -> dict | None`, `options.gamma(data, shares, today: date) -> {"exposure", "vol_oi", "pc_oi", "iv30"} | None`.

- [ ] **Step 1: Add the failing tests** — change the import line to `from bot import borrow, options` and add above `if __name__`:

```python
CHAIN = {"iv30": 53.07, "options": [
    {"option": "GME261002C00020000", "open_interest": 1000.0, "volume": 500.0, "delta": 0.6},
    {"option": "GME261016C00030000", "open_interest": 2000.0, "volume": 100.0, "delta": 0.2},
    {"option": "GME261218C00030000", "open_interest": 5000.0, "volume": 10.0, "delta": 0.4},  # 81 days out
    {"option": "GME261002P00020000", "open_interest": 4000.0, "volume": 1.0, "delta": -0.4},
    {"option": "GME1261002C00020000", "open_interest": 9e9, "volume": 9e9, "delta": 1.0}]}  # adjusted series


class Gamma(unittest.TestCase):
    def test_near_dated_call_exposure(self):
        g = options.gamma(CHAIN, 1_000_000, dt.date(2026, 9, 28))
        # near calls: 1000 x 100 x 0.6 + 2000 x 100 x 0.2 = 100,000 shares = 10% of 1,000,000
        self.assertAlmostEqual(g["exposure"], 0.10)
        self.assertAlmostEqual(g["vol_oi"], 600 / 3000)
        self.assertAlmostEqual(g["pc_oi"], 4000 / 8000)
        self.assertEqual(g["iv30"], 53.07)

    def test_expired_and_missing(self):
        self.assertIsNone(options.gamma({"options": []}, 1_000_000, dt.date(2026, 9, 28)))
        self.assertIsNone(options.gamma(CHAIN, None, dt.date(2026, 9, 28)))
        late = options.gamma(CHAIN, 1_000_000, dt.date(2026, 12, 1))  # only the December calls are near: 17 days
        self.assertAlmostEqual(late["exposure"], 5000 * 100 * 0.4 / 1_000_000)

    def test_chain_failure_is_none(self):
        with mock.patch.object(options.common, "fetch", side_effect=OSError("down")), mock.patch("builtins.print"):
            self.assertIsNone(options.chain("GME"))
        with mock.patch.object(options.common, "fetch", return_value=b'{"data": {"options": []}}') as f:
            self.assertEqual(options.chain("BRK-B"), {"options": []})
            self.assertIn("/BRK.B.json", f.call_args[0][0])
```

- [ ] **Step 2: Run** `python -m unittest tests.test_squeeze_extras` — Expected: ERROR `ImportError: cannot import name 'options'`

- [ ] **Step 3: Implement `bot/options.py`**

```python
"""CBOE delayed option chains (free, no key) -> the gamma-fuel read for the squeeze list: the shares dealers would
hold against near-dated calls, relative to the company's shares outstanding. Display and journal only -
unvalidated (there is no free history to backtest it)."""
import datetime as dt
import json
import re

from bot import common

URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{t}.json"
NEAR = 21  # days to expiry that count as near-dated
OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")  # adjusted series (GME1...) do not match and are skipped


def chain(t):
    """CBOE's delayed chain of ticker t (class shares as BRK.B) -> its "data" dict, or None (none / unreachable)."""
    try:
        body = common.fetch(URL.format(t=t.replace("-", ".")), tries=2, timeout=30)
        return json.loads(body)["data"] if body else None
    except Exception as e:  # a display-only source: any failure just means "missing"
        print(f"cboe {t}: {type(e).__name__} {e}")
        return None


def gamma(data, shares, today):
    """{"exposure": sum(call OI x 100 x delta, expiry within NEAR days) / shares, "vol_oi": near call volume / near
    call OI, "pc_oi": put OI / call OI (all expiries), "iv30": CBOE's 30-day implied volatility in %}; None
    without calls or shares. today: a date."""
    near_oi = near_vol = expo = call_oi = put_oi = 0.0
    for o in data.get("options") or []:
        m = OCC.match(o.get("option", ""))
        if not m:
            continue
        oi = o.get("open_interest") or 0.0
        if m[3] == "P":
            put_oi += oi
            continue
        call_oi += oi
        days = (dt.datetime.strptime(m[2], "%y%m%d").date() - today).days
        if 0 <= days <= NEAR:
            near_oi += oi
            near_vol += o.get("volume") or 0.0
            expo += oi * 100 * (o.get("delta") or 0.0)
    if not call_oi or not shares:
        return None
    return {"exposure": expo / shares, "vol_oi": near_vol / near_oi if near_oi else None,
            "pc_oi": put_oi / call_oi, "iv30": data.get("iv30")}
```

- [ ] **Step 4: Run** `python -m unittest tests.test_squeeze_extras -v` — Expected: PASS
- [ ] **Step 5: Commit** `git add bot/options.py tests/test_squeeze_extras.py && git commit -m "Squeeze extras: CBOE gamma fuel"`

---

### Task 4: `shorts.symbol_rows` and the live data layer (`bot/squeeze_live.py`, part 1)

**Files:** Modify `bot/shorts.py`, `tests/test_shorts.py`; Create `bot/squeeze_live.py`, `tests/test_squeeze_live.py`

**Interfaces:**
- Consumes: `shorts.settlement_dates/report/usable_from/remap/frame/shares_index/shares_at`, `squeeze.to_bars/features/make_row/rank/MAX_SI_PCT/WINDOW`, `common.cik_tickers`, `market.chart`, `borrow.fetch`, `options.chain/gamma`
- Produces: `shorts.symbol_rows(symbol) -> [slim rows] oldest first`; `squeeze_live.VARIANT, NAME, BACKTEST, CRASH, LAST, JOURNAL, FIELDS`, `path/load/save`, `calendar(today_iso)`, `latest_report(cal, today)`, `frames_now(date)`, `bars_for(tickers)`, `features_at(b, today, prev)`, `build(today_iso) -> res`, `enrich(rows, date)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shorts.py` class `Report`:

```python
    def test_symbol_rows_oldest_first(self):
        rows = [{"symbolCode": "BRKB", "settlementDate": d, "currentShortPositionQuantity": 5}
                for d in ("2026-09-15", "2026-08-29")]
        with mock.patch.object(shorts, "_post", return_value=rows) as post:
            got = shorts.symbol_rows("BRKB")
        self.assertEqual([r["date"] for r in got], ["2026-08-29", "2026-09-15"])
        self.assertEqual(post.call_args[0][0]["compareFilters"][0]["fieldValue"], "BRKB")
```

Create `tests/test_squeeze_live.py`:

```python
"""Live squeeze list: build, extras, message, journal, stats, /squeeze TICKER, run."""
import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

from bot import common, squeeze_live

TODAY = "2026-09-28"  # a Monday


def weekdays(end, n):
    out, d = [], dt.date.fromisoformat(end)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(1)
    return out[::-1]


CAL = weekdays("2026-09-25", 40)


def chart(days, close=10.0, vol=1_000_000.0, last_vol=None, highs=None, lows=None, opens=None):
    """A Yahoo v8 chart result, one bar per day."""
    ts = [int(dt.datetime.fromisoformat(d).replace(hour=13, minute=30, tzinfo=dt.timezone.utc).timestamp())
          for d in days]
    c, v = [close] * len(days), [vol] * len(days)
    if last_vol:
        v[-1] = last_vol
    return {"timestamp": ts, "indicators": {"quote": [{"open": opens or c, "high": highs or c, "low": lows or c,
                                                       "close": c, "volume": v}]}}


def si(n, date="2026-09-15"):
    return {"si": n, "dtc": 3.0, "chg": 0.1, "adv": 1, "cls": "NNM", "date": date}


class Build(unittest.TestCase):
    def setUp(self):
        self.charts = {"SPY": chart(CAL), "AAA": chart(CAL, last_vol=3_000_000.0), "CCC-B": chart(CAL)}
        self.calls = []

        def fake_chart(t, **params):
            self.calls.append(t)
            return self.charts.get(t)
        facts = {1: ("2026-06-30", 1_000_000, "a"), 2: ("2026-06-30", 1_000_000, "b"), 3: ("2026-06-30", 1_000_000, "c")}
        for p in (mock.patch.object(squeeze_live.market, "chart", fake_chart),
                  mock.patch.object(squeeze_live.shorts, "settlement_dates",
                                    return_value=["2026-08-31", "2026-09-15", "2026-09-22"]),
                  mock.patch.object(squeeze_live.shorts, "report", side_effect=lambda d: {
                      "AAA": si(300_000, d), "BBB": si(50_000, d), "CCCB": si(400_000, d)}),
                  mock.patch.object(squeeze_live.common, "cik_tickers", return_value={1: "AAA", 2: "BBB", 3: "CCC-B"}),
                  mock.patch.object(squeeze_live.shorts, "frame", return_value=facts)):
            p.start()
            self.addCleanup(p.stop)

    def test_ranked_list_from_the_newest_usable_report(self):
        res = squeeze_live.build(TODAY)
        self.assertEqual(res["si_date"], "2026-09-15")  # 2026-09-22 is public only from its 8th session
        self.assertEqual([r["t"] for r in res["rows"]], ["AAA"])  # CCC-B (FINRA "CCCB") fails the rvol gate
        self.assertEqual((res["gated"], res["candidates"], res["missing"], res["prev"]), (2, 2, 0, "2026-09-25"))
        self.assertNotIn("BBB", self.calls)  # 5 % short interest: no Yahoo call at all
        self.assertEqual(res["recent"], CAL[-9:] + [TODAY])

    def test_missing_calendar_raises(self):
        self.charts["SPY"] = None
        with self.assertRaises(RuntimeError):
            squeeze_live.build(TODAY)


class Enrich(unittest.TestCase):
    def test_extras_attached_and_missing_sources_are_none(self):
        rows = [{"t": "GME", "shares": 1_000_000}, {"t": "XYZ", "shares": 1_000_000}]
        gme = {"iv30": 50.0, "options": [{"option": "GME261002C00020000", "open_interest": 10.0, "volume": 1.0,
                                          "delta": 0.5}]}
        with mock.patch.object(squeeze_live.borrow, "fetch", return_value={"GME": {"fee": 1.0, "avail": 5,
                                                                                   "more": False}}), \
                mock.patch.object(squeeze_live.options, "chain", side_effect=lambda t: gme if t == "GME" else None):
            squeeze_live.enrich(rows, dt.date(2026, 9, 28))
        self.assertEqual(rows[0]["borrow"]["fee"], 1.0)
        self.assertAlmostEqual(rows[0]["gamma"]["exposure"], 10 * 100 * 0.5 / 1_000_000)
        self.assertEqual((rows[1]["borrow"], rows[1]["gamma"]), (None, None))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run** `python -m unittest tests.test_shorts tests.test_squeeze_live` — Expected: ERROR (`symbol_rows` missing; `cannot import name 'squeeze_live'`)

- [ ] **Step 3: Implement**

In `bot/shorts.py`, after `report(date)`:

```python
def symbol_rows(symbol):
    """Every FINRA report row of one symbol (FINRA spelling, e.g. BRKB), slimmed, oldest first."""
    rows = _post({"limit": PAGE,
                  "compareFilters": [{"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": symbol}]})
    return sorted((slim(r) for r in rows), key=lambda r: r["date"] or "")
```

Create `bot/squeeze_live.py`:

```python
"""Live short-squeeze list - phases 2-3 of docs/superpowers/specs/2026-09-27-squeeze-screener-design.md.

Once a trading day before the US open (.github/workflows/squeeze.yml): rank the market with the variant the
backtest froze, send the top 10 to Telegram with the backtest's spike AND crash rates, journal the names, and fill
in the 10-session outcome of earlier lists. Borrow (IBKR) and options (CBOE) numbers are shown as unvalidated.

    python -m bot.squeeze_live          # run, send, write data/squeeze_last.json and data/squeeze_journal.json
    python -m bot.squeeze_live --dry    # print the message only, write nothing"""
import argparse
import bisect
import concurrent.futures as cf
import datetime as dt
import json
import os
import statistics
import traceback
from pathlib import Path

from bot import borrow, common, market, options, shorts, squeeze
from bot.common import code

VARIANT = {"w": 0.7, "g_si": 0.20, "g_rv": 2.0}  # frozen by the backtest: docs/backtest/squeeze-2026-09-27.md
NAME = "w0.7/si0.2/rv2"
BACKTEST = {"hit": 0.068, "crash": 0.071, "base": 0.010, "years": "2024–2026"}  # out-of-sample, run 3
CRASH = 1 / squeeze.HIT - 1  # -33%: the fall that mirrors the +50% spike
LAST, JOURNAL = "squeeze_last.json", "squeeze_journal.json"
FIELDS = ("score", "si_pct", "dtc", "chg", "rvol", "ret5", "brk", "price", "fuel", "trigger", "shares")


# ---------- files ----------
def path(name):
    return Path(common.env("SQUEEZE_DIR", str(common.DATA))) / name


def load(name, default):
    p = path(name)
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def save(name, obj):
    p = path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)


def _round(x):
    return round(x, 4) if isinstance(x, float) else x


# ---------- data ----------
def calendar(today):
    """Completed US sessions before `today` (ISO, ascending), from SPY's daily bars ([] when Yahoo fails)."""
    b = squeeze.to_bars(market.chart("SPY", range="6mo", interval="1d"))
    return [d for d in (b or {}).get("d", []) if d < today]


def latest_report(cal, today):
    """(settlement date, {FINRA symbol: row}) of the newest FINRA report usable on `today` (its 8th session after
    settlement, `today` counted as a session), or (None, {})."""
    for settle in reversed(shorts.settlement_dates()[-3:]):
        use = shorts.usable_from(settle, cal + [today])
        if use and use <= today:
            return settle, shorts.report(settle)
    return None, {}


def frames_now(today):
    """SEC shares frames of the current calendar quarter and the three before it (today: a date)."""
    y, q, out = today.year, (today.month - 1) // 3 + 1, []
    for _ in range(4):
        out.append(shorts.frame(y, q))
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    return out


def bars_for(tickers, threads=8):
    """{ticker: bars} with 3 months of Yahoo daily bars, for each ticker Yahoo answers for."""
    def one(t):
        return squeeze.to_bars(market.chart(t, range="3mo", interval="1d"))
    with cf.ThreadPoolExecutor(threads) as ex:
        got = dict(zip(tickers, ex.map(one, tickers)))
    return {t: b for t, b in got.items() if b}


def features_at(b, today, prev):
    """Features of a ticker for session `today` from its bars through `prev` (= D-1); None without a bar on D-1."""
    if not b:
        return None
    i = bisect.bisect_left(b["d"], today) - 1
    return squeeze.features(b, i) if i >= 0 and b["d"][i] == prev else None


def build(today):
    """Today's list (today: ISO) -> {"date", "prev", "si_date", "rows" (the top list), "gated" (short interest past
    the gate), "candidates" (also past price / liquidity), "missing" (no Yahoo bars), "recent" (the last WINDOW
    sessions incl. today)}. RuntimeError when Yahoo's calendar or a usable FINRA report is missing."""
    cal = calendar(today)
    if not cal:
        raise RuntimeError("Yahoo: no SPY sessions")
    settle, raw = latest_report(cal, today)
    if not raw:
        raise RuntimeError("FINRA: no usable short-interest report")
    primary = {t: cik for cik, t in common.cik_tickers().items()}
    shares = shorts.shares_index(frames_now(dt.date.fromisoformat(today)))
    gated = []
    for t, si in shorts.remap(raw, primary).items():
        n = shorts.shares_at(shares, primary[t], today) if t in primary else None
        if n and VARIANT["g_si"] <= si["si"] / n <= squeeze.MAX_SI_PCT:
            gated.append((t, si, n))
    bars = bars_for([t for t, _, _ in gated])
    rows = [r for t, si, n in gated if (r := squeeze.make_row(t, si, n, features_at(bars.get(t), today, cal[-1])))]
    return {"date": today, "prev": cal[-1], "si_date": settle, "rows": squeeze.rank(rows, VARIANT),
            "gated": len(gated), "candidates": len(rows), "missing": len(gated) - len(bars),
            "recent": cal[-(squeeze.WINDOW - 1):] + [today]}


def enrich(rows, today):
    """Unvalidated extras for the listed names: IBKR borrow fee / availability and CBOE gamma fuel (None = missing).
    today: a date."""
    fees = borrow.fetch()
    for r in rows:
        r["borrow"] = fees.get(r["t"])
        chain = options.chain(r["t"])
        r["gamma"] = options.gamma(chain, r["shares"], today) if chain else None
    return rows
```

- [ ] **Step 4: Run** `python -m unittest tests.test_shorts tests.test_squeeze_live -v` — Expected: PASS (pyflakes may flag imports used only in Task 5: `argparse`, `statistics`, `traceback`, `code` — they are used there)
- [ ] **Step 5: Commit** `git add bot/shorts.py bot/squeeze_live.py tests/test_shorts.py tests/test_squeeze_live.py && git commit -m "Squeeze live: today's data, frozen ranking, unvalidated extras"`

---

### Task 5: Message, journal, stats, `/squeeze TICKER`, run (`bot/squeeze_live.py`, part 2)

**Files:** Modify `bot/squeeze_live.py` (append), `tests/test_squeeze_live.py` (append)

**Interfaces:** Produces `line(k, r)`, `extras(r) -> str`, `footer()`, `message(res) -> str`, `record(j, res) -> int`, `followup(j, today_iso) -> int`, `stats_text(j) -> str`, `reasons(si_pct, feat) -> [str]`, `ticker_text(t, si, shares, feat, rank=None, extra="") -> str`, `ticker_report(t, today=None) -> str`, `main(argv=None) -> int`.

- [ ] **Step 1: Append the failing tests** to `tests/test_squeeze_live.py` (above `if __name__`)

```python
ROW = {"t": "AAA", "score": 0.91, "si_pct": 0.3, "dtc": 3.0, "rvol": 3.0, "ret5": 0.12, "brk": 1.1, "price": 10.0,
       "fuel": 0.9, "trigger": 0.92, "shares": 1_000_000, "chg": 0.1, "si_date": "2026-09-15", "dollar": 1e7,
       "borrow": {"fee": 12.5, "avail": 20000, "more": False},
       "gamma": {"exposure": 0.05, "vol_oi": 0.3, "pc_oi": 0.5, "iv30": 140.0}}
RES = {"date": TODAY, "prev": "2026-09-25", "si_date": "2026-09-15", "rows": [ROW], "gated": 40, "candidates": 12,
       "missing": 1, "recent": CAL[-9:] + [TODAY]}


class Message(unittest.TestCase):
    def test_list_is_rtl_clean_and_shows_both_rates(self):
        text = squeeze_live.message(RES)
        self.assertEqual(common.rtl_bad_lines(text), [])
        for part in ("<code>AAA</code>", "<code>6.8%</code>", "<code>7.1%</code>", "לא מאומת:", "פריצה",
                     "<code>12.5%</code>", "<code>140%</code>"):
            self.assertIn(part, text)

    def test_empty_day_and_missing_extras(self):
        self.assertIn("אין היום מניה", squeeze_live.message({**RES, "rows": [], "missing": 0}))
        text = squeeze_live.message({**RES, "rows": [{**ROW, "borrow": None, "gamma": None, "brk": 0.9}]})
        self.assertNotIn("לא מאומת:", text)
        self.assertNotIn("פריצה", text)
        self.assertEqual(common.rtl_bad_lines(text), [])


class Journal(unittest.TestCase):
    def test_record_counts_a_ticker_once_per_window(self):
        j = {"entries": []}
        self.assertEqual(squeeze_live.record(j, RES), 1)
        nxt = {**RES, "date": "2026-09-29", "recent": CAL[-8:] + [TODAY, "2026-09-29"]}
        self.assertEqual(squeeze_live.record(j, nxt), 0)
        e = j["entries"][0]
        self.assertEqual((e["date"], e["t"], e["rank"], e["variant"], e["outcome"]),
                         (TODAY, "AAA", 1, squeeze_live.NAME, None))
        self.assertEqual((e["borrow"]["fee"], e["gamma"]["iv30"]), (12.5, 140.0))

    def test_followup_fills_closed_windows_only(self):
        days = weekdays("2026-10-16", 30)
        k = days.index("2026-09-28")
        highs, lows = [10.0] * len(days), [10.0] * len(days)
        highs[k + 3], lows[k + 5] = 15.0, 6.0
        charts = {"SPY": chart(days), "AAA": chart(days, highs=highs, lows=lows),
                  "HALT": chart([d for d in days if d != "2026-09-28"]), "BBB": chart(days)}
        j = {"entries": [{"date": "2026-09-28", "t": "AAA", "outcome": None},
                         {"date": "2026-10-14", "t": "BBB", "outcome": None},
                         {"date": "2026-09-28", "t": "HALT", "outcome": None}]}
        with mock.patch.object(squeeze_live.market, "chart", side_effect=lambda t, **p: charts[t]):
            self.assertEqual(squeeze_live.followup(j, "2026-10-19"), 2)
        aaa, bbb, halt = (e["outcome"] for e in j["entries"])
        self.assertEqual((aaa["trade"], aaa["hit"], aaa["crash"], aaa["start"], aaa["end"], aaa["dd"]),
                         (True, True, True, "2026-09-28", "2026-10-09", -0.4))
        self.assertIsNone(bbb)  # its 10 sessions have not closed yet
        self.assertEqual(halt, {"trade": False, "start": "2026-09-28"})

    def test_stats_text(self):
        j = {"entries": [{"outcome": {"trade": True, "hit": True, "crash": False, "r10": 0.2}},
                         {"outcome": {"trade": True, "hit": False, "crash": True, "r10": -0.4}},
                         {"outcome": {"trade": False}}, {"outcome": None}]}
        text = squeeze_live.stats_text(j)
        self.assertIn("<code>50.0%</code>", text)
        self.assertIn("מדגם קטן", text)
        self.assertIn("<code>7.1%</code>", text)
        self.assertEqual(common.rtl_bad_lines(text), [])
        self.assertIn("עוד אין", squeeze_live.stats_text({"entries": [{"outcome": None}]}))


class TickerText(unittest.TestCase):
    FEAT = {"price": 10.0, "dollar": 1e7, "rvol": 3.0, "ret5": 0.2, "brk": 1.2}

    def test_pass_fail_and_missing(self):
        ok = squeeze_live.ticker_text("AAA", si(300_000), 1_000_000, self.FEAT, rank=2)
        self.assertIn("✅", ok)
        bad = squeeze_live.ticker_text("AAA", si(100_000), 1_000_000, {**self.FEAT, "rvol": 1.2, "price": 0.5, "brk": 0.9})
        self.assertIn("❌", bad)
        for word in ("שורט", "מחזור פי", "מחיר"):
            self.assertIn(word, bad.split("❌")[1])
        none = squeeze_live.ticker_text("ZZZ", None, None, None)
        self.assertIn("אין דוח FINRA", none)
        self.assertIn("אין מספיק נתוני מסחר", none)
        for text in (ok, bad, none):
            self.assertEqual(common.rtl_bad_lines(text), [])
            self.assertIn("<code>7.1%</code>", text)


class TickerReport(unittest.TestCase):
    def test_glue(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SQUEEZE_DIR": d}), \
                mock.patch.object(squeeze_live.market, "chart", side_effect=lambda t, **p: chart(CAL, last_vol=3e6)), \
                mock.patch.object(squeeze_live.shorts, "symbol_rows",
                                  return_value=[si(300_000, "2026-09-15"), si(1, "2026-09-22")]) as rows, \
                mock.patch.object(squeeze_live.shorts, "frame", return_value={1: ("2026-06-30", 1_000_000, "a")}), \
                mock.patch.object(squeeze_live.common, "tickers", return_value={"BRK-B": (1, "Berkshire")}), \
                mock.patch.object(squeeze_live.borrow, "fetch", return_value={}), \
                mock.patch.object(squeeze_live.options, "chain", return_value=None):
            squeeze_live.save(squeeze_live.LAST, {"date": TODAY, "rows": [{"t": "X"}, {"t": "BRK-B"}]})
            text = squeeze_live.ticker_report("BRK-B", TODAY)
        rows.assert_called_once_with("BRKB")
        self.assertIn("<code>300,000</code>", text)  # the 2026-09-22 report is not public yet
        self.assertIn("מקום <code>2</code>", text)
        self.assertEqual(common.rtl_bad_lines(text), [])


class Main(unittest.TestCase):
    def run_main(self, argv, **patches):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SQUEEZE_DIR": d}), \
                mock.patch.object(squeeze_live.common, "send") as send, mock.patch("builtins.print"):
            with mock.patch.multiple(squeeze_live, **patches):
                code = squeeze_live.main(argv)
            files = sorted(os.listdir(d))
            saved = squeeze_live.load(squeeze_live.LAST, {}), squeeze_live.load(squeeze_live.JOURNAL, {})
        return code, send, files, saved

    def test_run_sends_and_saves(self):
        code, send, files, (last, j) = self.run_main([], build=mock.Mock(return_value=dict(RES, rows=[dict(ROW)])),
                                                     enrich=mock.Mock(), followup=mock.Mock(return_value=0))
        self.assertEqual(code, 0)
        self.assertTrue(send.call_args.kwargs["signal"])
        self.assertEqual((last["rows"][0]["t"], len(j["entries"])), ("AAA", 1))

    def test_failed_build_is_reported(self):
        code, send, files, _ = self.run_main([], build=mock.Mock(side_effect=RuntimeError("FINRA: down")))
        self.assertEqual(code, 1)
        self.assertIn("לא נבנתה", send.call_args[0][0])
        self.assertEqual(files, [])

    def test_dry_run_writes_nothing(self):
        code, send, files, _ = self.run_main(["--dry"], build=mock.Mock(return_value=dict(RES, rows=[dict(ROW)])),
                                             enrich=mock.Mock(), followup=mock.Mock(return_value=0))
        self.assertEqual(code, 0)
        send.assert_not_called()
        self.assertEqual(files, [])
```

- [ ] **Step 2: Run** `python -m unittest tests.test_squeeze_live` — Expected: ERRORs (`message`, `record`, … not defined)

- [ ] **Step 3: Append to `bot/squeeze_live.py`**

```python
# ---------- message ----------
def _pct(x, signed=False):
    return code(f"{x * 100:+.0f}%" if signed else f"{x * 100:.1f}%")


def _num(x, fmt):
    return code(format(x, fmt))


def line(k, r):
    """One listed stock (Hebrew first, RTL): rank, ticker, score and the numbers behind it."""
    brk = " · פריצה ⬆️" if r["brk"] >= 1 else ""
    return (f"מקום {code(k)}: {code(r['t'])} · ציון {_num(r['score'], '.2f')} · שורט {_pct(r['si_pct'])} מהמניות · "
            f"{_num(r['dtc'], '.1f')} ימי כיסוי · מחזור פי {_num(r['rvol'], '.1f')} · 5 ימים {_pct(r['ret5'], True)}"
            f" · מחיר {code(common.price(r['price']))}{brk}")


def extras(r):
    """The unvalidated options / borrow numbers of one listed stock, or '' when both sources are missing."""
    out, g, b = [], r.get("gamma"), r.get("borrow")
    if g:
        out.append(f"חשיפת קולים עד {code(options.NEAR)} יום {_pct(g['exposure'])} מהמניות")
        if g.get("iv30") is not None:
            out.append(f"תנודתיות גלומה {code(format(g['iv30'], '.0f') + '%')}")
    if b:
        fee = "חסרה" if b["fee"] is None else code(format(b["fee"], ".1f") + "%")
        avail = "חסרה" if b["avail"] is None else code((">" if b["more"] else "") + format(b["avail"], ","))
        out.append(f"עמלת השאלה {fee} בשנה · זמינות להשאלה {avail}")
    return f"   ↳ לא מאומת: {' · '.join(out)}" if out else ""


def footer():
    """The backtest line every squeeze message carries: spikes AND crashes, so a pick never reads as a buy signal."""
    return (f"📊 בבדיקה ההיסטורית ({BACKTEST['years']}): {_pct(BACKTEST['hit'])} מהמניות ברשימה עלו {code('50%+')} "
            f"תוך {code(squeeze.WINDOW)} ימי מסחר, ו־{_pct(BACKTEST['crash'])} ירדו {code('33%')} ומעלה; בכל השוק "
            f"{_pct(BACKTEST['base'])} עלו {code('50%+')}. זה סימן לתנודה חדה צפויה, לא לכיוון.")


def message(res):
    """The daily Hebrew list for Telegram."""
    head = [f"🚀 <b>רשימת סקוויז ליום {code(res['date'])}</b>",
            f"דירוג בשיטה שנקבעה בבדיקה ההיסטורית ({code(NAME)}): שורט של {code('20%')} מהמניות לפחות, ומחזור ביום "
            f"המסחר האחרון של פי {code(2)} לפחות מהממוצע."]
    body = []
    for k, r in enumerate(res["rows"], 1):
        x = extras(r)
        body += [line(k, r)] + ([x] if x else [])
    missing = f", ל־{code(res['missing'])} אין נתוני Yahoo" if res["missing"] else ""
    foot = [footer(),
            f"🗓️ שורט לפי דוח FINRA מ־{code(res['si_date'])} · מחירים עד {code(res['prev'])} · "
            f"{code(res['gated'])} מניות עם שורט גבוה, {code(res['candidates'])} עברו סינון מחיר ונזילות{missing}.",
            "ℹ️ שורות ״לא מאומת״ (אופציות, השאלה) עוד לא נבדקו היסטורית ואינן משפיעות על הדירוג."]
    return "\n".join(head + [""] + (body or ["🤷 אין היום מניה שעוברת את הסינון."]) + [""] + foot)


# ---------- journal ----------
def record(j, res):
    """Journal today's names; a ticker journaled within the last WINDOW sessions counts once (as in the backtest)
    -> the number added."""
    recent = set(res["recent"])
    seen = {e["t"] for e in j["entries"] if e["date"] in recent}
    added = 0
    for k, r in enumerate(res["rows"], 1):
        if r["t"] in seen:
            continue
        g = r.get("gamma")
        j["entries"].append({"date": res["date"], "t": r["t"], "rank": k, "variant": NAME, "si_date": res["si_date"],
                             **{f: _round(r.get(f)) for f in FIELDS}, "borrow": r.get("borrow"),
                             "gamma": {x: _round(v) for x, v in g.items()} if g else None, "outcome": None})
        added += 1
    return added


def followup(j, today):
    """Fill the outcome of every journal entry whose 10 sessions have closed: entry = the open of the first session
    on/after the list date, hit = high >= 1.5x entry, crash = low <= entry / 1.5 -> the number filled. Missing data
    is retried on the next run, never estimated; no bar on the entry session -> no trade."""
    todo = [e for e in j["entries"] if e.get("outcome") is None]
    if not todo:
        return 0
    p1 = int(dt.datetime.fromisoformat(min(e["date"] for e in todo)).replace(tzinfo=dt.timezone.utc).timestamp())
    p1 -= 7 * 86400
    p2 = int(dt.datetime.now(dt.timezone.utc).timestamp())
    spy = squeeze.to_bars(market.chart("SPY", period1=p1, period2=p2, interval="1d"))
    cal = [d for d in (spy or {}).get("d", []) if d < today]
    filled = 0
    for e in todo:
        k = bisect.bisect_left(cal, e["date"])
        if k + squeeze.WINDOW - 1 >= len(cal):
            continue
        b = squeeze.to_bars(market.chart(e["t"], period1=p1, period2=p2, interval="1d"))
        if not b:
            continue
        start, end = cal[k], cal[k + squeeze.WINDOW - 1]
        i = bisect.bisect_left(b["d"], start)
        o = squeeze.outcome(b, i, end) if i < len(b["d"]) and b["d"][i] == start else None
        e["outcome"] = {"trade": False, "start": start} if o is None else {
            "trade": True, "start": start, "end": end, "hit": o["hit"], "crash": o["dd"] <= CRASH,
            **{x: round(o[x], 4) for x in ("maxup", "dd", "r10")}}
        filled += 1
    return filled


def stats_text(j):
    """/squeeze stats: the journal's closed outcomes against the backtest's out-of-sample rates."""
    done = [e["outcome"] for e in j["entries"] if e.get("outcome") and e["outcome"].get("trade")]
    waiting = sum(1 for e in j["entries"] if not e.get("outcome"))
    head = "📒 <b>יומן רשימת הסקוויז</b>"
    if not done:
        return (f"{head}\nעוד אין מניות שעברו {code(squeeze.WINDOW)} ימי מסחר מאז שהופיעו ברשימה "
                f"({code(waiting)} בהמתנה).")
    n = len(done)
    small = f" (מדגם קטן, פחות מ־{code(30)})" if n < 30 else ""
    return "\n".join([
        head,
        f"הושלמו {code(n)} מניות{small}, {code(waiting)} בהמתנה: {_pct(sum(o['hit'] for o in done) / n)} עלו "
        f"{code('50%+')} ו־{_pct(sum(o['crash'] for o in done) / n)} ירדו {code('33%')} ומעלה.",
        f"חציון התשואה אחרי {code(squeeze.WINDOW)} ימי מסחר: {_pct(statistics.median(o['r10'] for o in done), True)}.",
        footer()])


# ---------- /squeeze TICKER ----------
def reasons(si_pct, feat):
    """Why a stock is not on the list (Hebrew); [] when it passes every gate."""
    out = []
    if si_pct is None:
        out.append("אין נתוני שורט או מספר מניות")
    elif si_pct < VARIANT["g_si"]:
        out.append(f"שורט {_pct(si_pct)} נמוך מ־{code('20%')}")
    elif si_pct > squeeze.MAX_SI_PCT:
        out.append(f"שורט גבוה מ־{code('150%')} מהמניות, כנראה נתון שגוי")
    if feat is None:
        out.append("אין מספיק נתוני מסחר")
        return out
    if feat["rvol"] < VARIANT["g_rv"]:
        out.append(f"מחזור פי {_num(feat['rvol'], '.1f')} נמוך מפי {code(2)}")
    if feat["price"] < squeeze.MIN_PRICE:
        out.append(f"מחיר {code(common.price(feat['price']))} נמוך מ־{code('1$')}")
    if feat["dollar"] < squeeze.MIN_DOLLAR:
        out.append(f"מחזור דולרי {code(common.money(feat['dollar']))} נמוך מ־{code('2.0M$')}")
    return out


def ticker_text(t, si, shares, feat, rank=None, extra=""):
    """/squeeze TICKER reply from its parts (si: slim FINRA row or None; feat: squeeze.features or None)."""
    si_pct = si["si"] / shares if si and shares else None
    lines = [f"🔎 <b>בדיקת סקוויז: {code(t)}</b>"]
    if si:
        pct = f" · {_pct(si_pct)} מהמניות" if si_pct is not None else ""
        lines.append(f"שורט: {code(format(si['si'], ','))} מניות בדוח FINRA מ־{code(si['date'])}{pct} · "
                     f"{_num(si['dtc'] or 0.0, '.1f')} ימי כיסוי")
    else:
        lines.append("שורט: אין דוח FINRA לטיקר הזה.")
    if feat:
        brk = " · פריצה ⬆️" if feat["brk"] >= 1 else ""
        lines.append(f"מסחר: מחזור ביום האחרון פי {_num(feat['rvol'], '.1f')} מהממוצע · 5 ימים "
                     f"{_pct(feat['ret5'], True)} · מחיר {code(common.price(feat['price']))}{brk}")
    why = reasons(si_pct, feat)
    if why:
        lines.append("❌ לא עובר את הסינון: " + "; ".join(why) + ".")
    else:
        where = f" · מקום {code(rank)} ברשימה של היום" if rank else " (הדירוג נקבע מול כל המועמדים ברשימה היומית)"
        lines.append(f"✅ עובר את הסינון{where}.")
    if extra:
        lines.append(extra.strip())
    return "\n".join(lines + [footer()])


def ticker_report(t, today=None):
    """/squeeze TICKER: fetch the stock's numbers and explain where it stands against the list's gates."""
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    cal = calendar(today)
    usable = [r for r in shorts.symbol_rows(t.replace("-", ""))
              if (u := shorts.usable_from(r["date"], cal + [today])) and u <= today]
    cik = (common.tickers().get(t) or (None,))[0]
    shares = shorts.shares_at(shorts.shares_index(frames_now(dt.date.fromisoformat(today))), cik, today) if cik else None
    feat = features_at(squeeze.to_bars(market.chart(t, range="3mo", interval="1d")), today, cal[-1]) if cal else None
    last = load(LAST, {})
    rank = next((k for k, r in enumerate(last.get("rows", []), 1) if r["t"] == t), None) \
        if last.get("date") == today else None
    row = {"t": t, "shares": shares}
    enrich([row], dt.date.fromisoformat(today))
    return ticker_text(t, usable[-1] if usable else None, shares, feat, rank, extras(row))


# ---------- run ----------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Daily squeeze list -> Telegram and data/squeeze_*.json")
    ap.add_argument("--dry", action="store_true", help="print the message only, write nothing")
    a = ap.parse_args(argv)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    try:
        res = build(today)
        enrich(res["rows"], dt.date.fromisoformat(today))
    except Exception as e:  # the owner must hear about a failed list, not silence
        traceback.print_exc()
        if not a.dry:
            common.send(f"⚠️ רשימת הסקוויז של היום לא נבנתה ({code(type(e).__name__)}): {common.esc(str(e)[:200])}")
        return 1
    j = load(JOURNAL, {"v": 1, "entries": []})
    try:
        filled = followup(j, today)
    except Exception:  # a follow-up problem must not hold back today's list; it is retried tomorrow
        traceback.print_exc()
        filled = 0
    added = record(j, res)
    text = message(res)
    if filled:
        text += f"\n📒 הושלם מעקב של {code(squeeze.WINDOW)} ימים ל־{code(filled)} מניות: {code('/squeeze stats')}"
    if a.dry:
        print(text)
        return 0
    common.send(text, signal=True)
    save(LAST, {"date": today, "text": text, "rows": [{k: _round(v) for k, v in r.items()} for r in res["rows"]]})
    save(JOURNAL, j)
    common.log("squeeze", rows=len(res["rows"]), gated=res["gated"], added=added, filled=filled)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run** `python -m unittest discover -s tests` and `python -m pyflakes bot tests` — Expected: OK, no warnings in the new files
- [ ] **Step 5: Commit** `git add bot/squeeze_live.py tests/test_squeeze_live.py && git commit -m "Squeeze live: Hebrew list, journal with 10-session outcomes, stats, /squeeze TICKER, run"`

---

### Task 6: `/squeeze` in the Telegram listener

**Files:** Modify `bot/listen.py`, `tests/test_squeeze_live.py`

**Interfaces:** Consumes `squeeze_live.load/save/LAST/JOURNAL/stats_text/ticker_report`; Produces `listen.squeeze_cmd(rest) -> int` (failed-report count), `/squeeze` in `COMMANDS` and `HELP`.

- [ ] **Step 1: Append the failing test** to `tests/test_squeeze_live.py`

```python
class Command(unittest.TestCase):
    def test_squeeze_last_list_stats_and_ticker(self):
        from bot import listen
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SQUEEZE_DIR": d}), \
                mock.patch.object(listen.common, "send") as send, \
                mock.patch.object(listen.common, "tickers", return_value={"GME": (1, "GameStop")}), \
                mock.patch.object(listen.squeeze_live, "ticker_report", return_value="REPORT") as rep:
            listen.handle("/squeeze")
            self.assertIn("עוד אין רשימת סקוויז", send.call_args[0][0])
            squeeze_live.save(squeeze_live.LAST, {"date": TODAY, "text": "LIST", "rows": []})
            listen.handle("/squeeze")
            self.assertEqual(send.call_args[0][0], "LIST")
            listen.handle("/squeeze stats")
            self.assertIn("יומן רשימת הסקוויז", send.call_args[0][0])
            listen.handle("/squeeze gme")
            rep.assert_called_once_with("GME")
            self.assertEqual(send.call_args, mock.call("REPORT", signal=True))
            listen.handle("/squeeze zzzz")
            self.assertIn("לא מצאתי", send.call_args[0][0])
        self.assertIn("/squeeze", listen.HELP)
        self.assertIn("squeeze", [c for c, _ in listen.COMMANDS])
```

- [ ] **Step 2: Run** `python -m unittest tests.test_squeeze_live.Command` — Expected: FAIL/ERROR (`squeeze_live` not in listen; `/squeeze` falls through to HELP)

- [ ] **Step 3: Implement in `bot/listen.py`** (text edits)

```python
p = "bot/listen.py"
s = open(p).read()
reps = [
    ("from bot import check, common, fundamentals, health, journal, market, scan\n",
     "from bot import check, common, fundamentals, health, journal, market, scan, squeeze_live\n"),
    ('            ("health", "בדיקה חיה של המקורות והריצות"), ("help", "רשימת הפקודות"))',
     '            ("health", "בדיקה חיה של המקורות והריצות"),\n'
     '            ("squeeze", "רשימת סקוויז: /squeeze, /squeeze GME, /squeeze stats"), ("help", "רשימת הפקודות"))'),
    ('    f"• רשימת הפקודות: {code(\'/help\')}",',
     '    f"• רשימת סקוויז יומית (שורט גבוה ומחזור חריג), בדיקת מניה ויומן: {code(\'/squeeze\')},"\n'
     '    f" {code(\'/squeeze GME\')}, {code(\'/squeeze stats\')}",\n'
     '    f"• רשימת הפקודות: {code(\'/help\')}",'),
    ('    common.send(HELP)  # /help, /start and any unknown command',
     '    if cmd == "squeeze":\n        return squeeze_cmd(rest)\n    common.send(HELP)  # /help, /start and any unknown command'),
    ('\n\nclass Conflict(Exception):',
     '''

def squeeze_cmd(rest):
    """/squeeze: the last daily list; /squeeze stats: the journal; /squeeze TICKER: one stock against the gates."""
    arg = rest.split()[0].lower() if rest.split() else ""
    if not arg:
        last = squeeze_live.load(squeeze_live.LAST, None)
        common.send(last["text"] if last else "עוד אין רשימת סקוויז. היא נשלחת בכל יום מסחר לפני הפתיחה בניו יורק.")
        return 0
    if arg == "stats":
        common.send(squeeze_live.stats_text(squeeze_live.load(squeeze_live.JOURNAL, {"entries": []})))
        return 0
    known, unknown = extract(rest, loose=True)
    if not known:
        common.send(f"לא מצאתי ברשימת החברות של SEC: {', '.join(map(code, unknown))}." if unknown else
                    f"כתבו טיקר אחרי הפקודה, למשל {code('/squeeze GME')}.")
        return 0
    try:
        common.send(squeeze_live.ticker_report(known[0]), signal=True)
    except Exception as e:  # a failed lookup must be answered, not swallowed
        traceback.print_exc()
        common.send(f"⚠️ בדיקת הסקוויז עבור {code(known[0])} נכשלה ({code(type(e).__name__)}). נסו שוב מאוחר יותר.")
        return 1
    return 0


class Conflict(Exception):'''),
]
for a, b in reps:
    assert s.count(a) == 1, a[:60]
    s = s.replace(a, b, 1)
open(p, "w").write(s)
```

- [ ] **Step 4: Run** `python -m unittest discover -s tests` — Expected: OK (existing HELP / command tests still pass)
- [ ] **Step 5: Commit** `git commit -am "Listener: /squeeze, /squeeze TICKER, /squeeze stats"`

---

### Task 7: Workflow and README

**Files:** Create `.github/workflows/squeeze.yml`; Modify `README.md`

- [ ] **Step 1: Create `.github/workflows/squeeze.yml`**

```yaml
name: Squeeze list

on:
  schedule:
    - cron: "0 12 * * 1-5"  # 12:00 UTC = 08:00 New York (summer) / 07:00 (winter): before the US open
  workflow_dispatch:

permissions:
  contents: write  # commit data/squeeze_last.json and data/squeeze_journal.json

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
          ref: ${{ github.ref_name }}  # branch tip: the journal committed by the last run
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Rank, send and journal today's squeeze list
        timeout-minutes: 20  # a step timeout fails the step, so the alarm below runs
        run: python -m bot.squeeze_live
        env:
          SEC_UA: ${{ secrets.SEC_UA }}
          TG_TOKEN: ${{ secrets.TG_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
      - name: Commit the list and the journal
        if: always()
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/squeeze_last.json data/squeeze_journal.json 2>/dev/null || true
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

- [ ] **Step 2: Lint** — `pip install --break-system-packages actionlint-py` then `actionlint .github/workflows/squeeze.yml` — Expected: no output (if the package cannot install, record it and rely on CI's `actionlint` job)

- [ ] **Step 3: README** — insert before `### פקודות בבוט`:

```markdown
### רשימת סקוויז יומית

בכל יום מסחר ב־12:00 UTC, לפני הפתיחה בניו יורק, מגיעה רשימה של עד 10 מניות עם שורט גבוה (לפחות 20% מהמניות, לפי FINRA ו־SEC) ומחזור חריג ביום המסחר האחרון (לפחות פי 2 מהממוצע). הדירוג הוא השיטה שנבחרה בבדיקה היסטורית של 2018–2026 (<code dir="ltr">docs/backtest/squeeze-2026-09-27.md</code>).

- **מה הבדיקה מצאה:** ב־2024–2026, 6.8% מהמניות ברשימה עלו 50% ומעלה תוך 10 ימי מסחר, פי 7 מכלל השוק (1.0%). אבל 7.1% מהן ירדו 33% ומעלה באותו זמן. לעומת מניות תנודתיות באותה מידה ההבדל קטן, והשורט עצמו כמעט לא מוסיף. הרשימה מזהה תנודה חדה צפויה, לא כיוון.
- **אופציות והשאלה:** לכל מניה מוצגות חשיפת הקולים הקרובים (CBOE) ועמלת ההשאלה (IBKR) בסימון "לא מאומת": אין להן היסטוריה חינמית לבדיקה, והן לא משפיעות על הדירוג.
- **יומן:** כל מניה נרשמת ב־<code dir="ltr">data/squeeze_journal.json</code>, ואחרי 10 ימי מסחר נרשם אם עלתה 50% או ירדה 33%. הפקודה <code dir="ltr">/squeeze stats</code> משווה את התוצאות לבדיקה ההיסטורית.
```

Commands table — add after the `/health` row:

```markdown
| <code dir="ltr">/squeeze</code> | רשימת הסקוויז האחרונה; <code dir="ltr">/squeeze GME</code> בודק מניה מול הסינון; <code dir="ltr">/squeeze stats</code> תוצאות היומן מול הבדיקה ההיסטורית |
```

Limits list — add after the `**שומר ימים חסרים:**` item:

```markdown
- **רשימת הסקוויז:** FINRA מפרסמת את נתוני השורט פעמיים בחודש, כשבוע וחצי אחרי תאריך הדיווח, ולכן השורט ברשימה ישן בכמה ימים עד שלושה שבועות. קובץ ההשאלה של IBKR מגיע דרך FTP ועלול להיות חסום בשרתי GitHub; אז הוא מוצג כחסר. הבדיקה ההיסטורית כוללת רק מניות שעדיין נסחרות היום, ומניות שנמחקו חסרות בה.
```

Structure table — add after the `watchdog.yml` row and after the `bot/listen.py` row, and after `data/journal.json`:

```markdown
| <code dir="ltr">.github/workflows/squeeze.yml</code> | רשימת הסקוויז היומית והיומן שלה (`Squeeze list`) |
```

```markdown
| `bot/shorts.py` | נתוני שורט מ־FINRA ומספר מניות מ־SEC, עם כללי "מה היה ידוע באותו יום" |
| `bot/squeeze.py` | חישובי הסקוויז המשותפים לבדיקה ההיסטורית ולרשימה היומית |
| `bot/squeeze_live.py` | הרשימה היומית, היומן ו־<code dir="ltr">/squeeze</code> |
| `bot/borrow.py` | עמלת ההשאלה והזמינות מ־IBKR (לא מאומת) |
| `bot/options.py` | חשיפת הקולים מ־CBOE (לא מאומת) |
| `bot/backtest.py` | הבדיקה ההיסטורית של הרשימה (<code dir="ltr">python -m bot.backtest</code>) |
```

```markdown
| `data/squeeze_last.json`, `data/squeeze_journal.json` | הרשימה האחרונה ויומן התוצאות שלה (נכתבים אוטומטית) |
| `docs/backtest/` | דוח הבדיקה ההיסטורית ורשימת הבחירות שלה |
```

- [ ] **Step 4: Run** `python -m unittest discover -s tests` — Expected: OK
- [ ] **Step 5: Commit** `git add .github/workflows/squeeze.yml README.md && git commit -m "Squeeze list workflow (Mon-Fri 12:00 UTC) and README"`

---

### Task 8: Live dry run and hand-off

- [ ] **Step 1:** `SEC_UA="<name> <email>" python -m bot.squeeze_live --dry` against real data. Expected: a Hebrew list (or "אין היום מניה"), `common.rtl_bad_lines` clean, borrow shown missing in the sandbox (FTP blocked here), runtime under 5 minutes. Check 2 listed names by hand: FINRA short shares ÷ SEC shares ≥ 20 %, yesterday's volume ≥ 2× the 20-day average.
- [ ] **Step 2:** `python -m unittest discover -s tests` green; `python -m pyflakes bot tests` clean for new files.
- [ ] **Step 3:** Final whole-branch review by a fresh reviewer; fix Critical/Important test-first.
- [ ] **Step 4:** Push `squeeze-screener` and open a PR to `main`. Merging (which starts the live schedule and restarts the Telegram listener) is the owner's call.
