# Squeeze Screener — Phase 1 (Backtest) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the short-interest data layer, the shared squeeze scoring functions and a point-in-time backtest that ends in a GO / NO-GO report.

**Architecture:** `bot/shorts.py` (FINRA + SEC, point-in-time rules) → `bot/squeeze.py` (pure features / eligibility / scoring / outcome, reused by the live run later) → `bot/backtest.py` (cached downloads, day-by-day replay, in-sample selection, out-of-sample verdict, Markdown + CSV report). Phases 2–3 get their own plan only after a GO.

**Tech Stack:** Python 3.11/3.12 stdlib only (`urllib`, `json`, `bisect`, `array`, `concurrent.futures`), `unittest` offline tests.

**Spec:** `docs/superpowers/specs/2026-09-27-squeeze-screener-design.md`

## Global Constraints

- Stdlib only; no new dependencies (repo has no requirements file; CI runs `python -m unittest discover -s tests`).
- Tests are offline: every network call mocked.
- Point-in-time: SI usable from the 8th session after settlement; shares usable from fact date + 15 days, max age 400 days; features from bars ≤ D−1; entry = open[D]; window = sessions D … D+9.
- Eligible: FINRA class ∈ {NNM, SC, NYSE, AMEX}; primary common symbol of an SEC CIK; close[D−1] ≥ $1; mean dollar volume (21 sessions) ≥ $2,000,000; a bar on D−1.
- Hit = max(high[D … D+9]) ≥ 1.5 × open[D]. Repeat picks of a ticker within 10 sessions count once.
- Grid: w ∈ {0.3, 0.5, 0.7} × g_si ∈ {0.10, 0.15, 0.20} × g_rv ∈ {1.0, 1.5, 2.0}; top 10.
- In-sample 2018-02-01 … 2023-12-31; out-of-sample from 2024-01-01. GO iff lift ≥ 3×, ≥ 30 hits, ≥ 3× in ≥ 2 of the 3 years.
- Existing modules keep their behavior; `.cache/` is gitignored.

## Review Focus

1. FINRA class-share symbols (`BRK.B`) must match SEC/Yahoo (`BRK-B`), not silently drop → `norm` test in Task 1.
2. A settlement date that is an exchange holiday or a holiday inside the lag must make a report usable later, never earlier → holiday test in Task 1.
3. FINRA rows with `daysToCoverQuantity` / `changePercent` = null must not crash or rank as missing-high → `slim` test in Task 1.
4. A Yahoo bar with no open (None / NaN / 0) on D must yield no trade, not a crash or a fake hit → outcome tests in Task 2.
5. A stale shares figure (filer went quiet > 400 days) must exclude the stock instead of inflating `si_pct` → `shares_at` test in Task 1.
Also covered: empty candidate day, zero-hit baseline (no ZeroDivisionError) → Task 3.

---

### Task 1: `bot/shorts.py` — FINRA short interest, SEC shares, point-in-time rules

**Files:**
- Create: `bot/shorts.py`
- Test: `tests/test_shorts.py`

**Interfaces:**
- Consumes: `common.fetch(url, data=None, headers=None, tries=5, timeout=60) -> bytes | None`, `common.get_json(url) -> obj | None`
- Produces:
  - `norm(symbol: str) -> str`
  - `slim(row: dict) -> {"si": int, "dtc": float, "chg": float|None, "adv", "cls": str, "date": str}`
  - `settlement_dates(symbol="AAPL") -> list[str]` (ISO, ascending)
  - `report(date: str) -> dict[str, slim-row]`
  - `usable_from(settle: str, cal: list[str]) -> str | None`
  - `frame(y: int, q: int) -> dict[int, tuple[str, int]]`
  - `shares_index(frames: iterable[dict]) -> dict[int, list[tuple[str, int]]]`
  - `shares_at(idx, cik: int, day: str) -> int | None`
  - constants `CLASSES, PAGE, SI_LAG=8, SHARES_LAG=15, SHARES_MAX_AGE=400`

- [ ] **Step 1: Write the failing tests** — `tests/test_shorts.py`

```python
"""Squeeze screener data layer: FINRA short interest, SEC shares, point-in-time rules."""
import datetime as dt
import json
import unittest
from unittest import mock

from bot import shorts


def weekdays(start, n, skip=()):
    out, d = [], dt.date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5 and d.isoformat() not in skip:
            out.append(d.isoformat())
        d += dt.timedelta(1)
    return out


class Norm(unittest.TestCase):
    def test_class_suffix_matches_sec_and_yahoo(self):
        self.assertEqual(shorts.norm("BRK.B"), "BRK-B")
        self.assertEqual(shorts.norm(" gme "), "GME")


class Slim(unittest.TestCase):
    def test_fields_and_nulls(self):
        r = {"currentShortPositionQuantity": 500, "daysToCoverQuantity": None, "changePercent": None,
             "averageDailyVolumeQuantity": 10, "marketClassCode": "SC", "settlementDate": "2026-09-15"}
        self.assertEqual(shorts.slim(r), {"si": 500, "dtc": 0.0, "chg": None, "adv": 10, "cls": "SC",
                                          "date": "2026-09-15"})
        self.assertAlmostEqual(shorts.slim({**r, "changePercent": 15.49})["chg"], 0.1549)


class Report(unittest.TestCase):
    def test_pages_until_short_page_and_normalizes(self):
        calls = []

        def post(body):
            calls.append(body)
            n = shorts.PAGE if body["offset"] == 0 else 3
            return [{"symbolCode": f"S{body['offset'] + k}", "currentShortPositionQuantity": 1,
                     "marketClassCode": "NNM", "settlementDate": "2026-09-15"} for k in range(n)]
        with mock.patch.object(shorts, "_post", post):
            out = shorts.report("2026-09-15")
        self.assertEqual(len(out), shorts.PAGE + 3)
        self.assertEqual([c["offset"] for c in calls], [0, shorts.PAGE])
        self.assertEqual(calls[0]["domainFilters"][0]["values"], list(shorts.CLASSES))

    def test_no_content_is_empty(self):
        with mock.patch.object(shorts.common, "fetch", return_value=b""):
            self.assertEqual(shorts._post({}), [])

    def test_settlement_dates_sorted_unique(self):
        rows = [{"settlementDate": d} for d in ("2026-09-15", "2018-01-12", "2026-09-15")]
        with mock.patch.object(shorts, "_post", return_value=rows):
            self.assertEqual(shorts.settlement_dates(), ["2018-01-12", "2026-09-15"])


class UsableFrom(unittest.TestCase):
    def test_eighth_session_after_settlement(self):
        cal = weekdays("2026-09-01", 30)
        self.assertEqual(shorts.usable_from("2026-09-15", cal), "2026-09-25")

    def test_holiday_inside_lag_pushes_later(self):
        cal = weekdays("2026-09-01", 30, skip={"2026-09-21"})
        self.assertEqual(shorts.usable_from("2026-09-15", cal), "2026-09-28")

    def test_settlement_on_non_session_counts_from_next_session(self):
        cal = weekdays("2026-09-01", 30, skip={"2026-09-15"})
        self.assertEqual(shorts.usable_from("2026-09-15", cal), "2026-09-28")

    def test_calendar_too_short(self):
        self.assertIsNone(shorts.usable_from("2026-09-15", weekdays("2026-09-01", 12)))


class Shares(unittest.TestCase):
    IDX = shorts.shares_index([{1: ("2025-01-10", 100)}, {1: ("2025-04-10", 120), 2: ("2023-01-01", 50)},
                               {1: ("2025-04-10", 120)}])

    def test_index_collapses_duplicates(self):
        self.assertEqual(self.IDX[1], [("2025-01-10", 100), ("2025-04-10", 120)])

    def test_lag_and_latest(self):
        self.assertIsNone(shorts.shares_at(self.IDX, 1, "2025-01-24"))
        self.assertEqual(shorts.shares_at(self.IDX, 1, "2025-01-25"), 100)
        self.assertEqual(shorts.shares_at(self.IDX, 1, "2025-04-24"), 100)
        self.assertEqual(shorts.shares_at(self.IDX, 1, "2025-04-25"), 120)

    def test_stale_and_unknown(self):
        self.assertIsNone(shorts.shares_at(self.IDX, 2, "2024-06-01"))
        self.assertIsNone(shorts.shares_at(self.IDX, 3, "2025-06-01"))

    def test_frame_parses_sec(self):
        body = {"data": [{"cik": 1750, "end": "2026-06-30", "val": 39892472}, {"cik": 9, "end": "2026-06-30", "val": 0}]}
        with mock.patch.object(shorts.common, "get_json", return_value=body):
            self.assertEqual(shorts.frame(2026, 2), {1750: ("2026-06-30", 39892472)})
        with mock.patch.object(shorts.common, "get_json", return_value=None):
            self.assertEqual(shorts.frame(2027, 1), {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_shorts -v`
Expected: FAIL / ERROR — `ImportError: cannot import name 'shorts'`

- [ ] **Step 3: Implement `bot/shorts.py`**

```python
"""Short-interest inputs for the squeeze screener (free, no key): FINRA consolidated short interest and SEC shares
outstanding, with the point-in-time rules the live screener and its backtest share
(docs/superpowers/specs/2026-09-27-squeeze-screener-design.md, section 5)."""
import bisect
import datetime as dt
import json

from bot import common

FINRA = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
FRAME = "https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{y}Q{q}I.json"
CLASSES = ("NNM", "SC", "NYSE", "AMEX")  # Nasdaq Global (Select), Nasdaq Capital, NYSE, NYSE American
PAGE = 5000  # FINRA's row limit per request
SI_LAG = 8  # sessions after settlement before a report may be used (FINRA publishes after business day 7)
SHARES_LAG = 15  # days after a dei fact's date (the filing's cover date) before it may be used
SHARES_MAX_AGE = 400  # days: an older figure (the filer went quiet) is not used


def norm(symbol):
    """FINRA symbol -> the SEC / Yahoo spelling (BRK.B -> BRK-B)."""
    return symbol.strip().upper().replace(".", "-").replace("/", "-")


def _post(body):
    """One FINRA API query -> rows ([] when FINRA answers 204 No Content)."""
    raw = common.fetch(FINRA, data=json.dumps(body).encode(),
                       headers={"Content-Type": "application/json", "Accept": "application/json"})
    return json.loads(raw) if raw else []


def slim(r):
    """A FINRA row -> what the screener uses: short shares, days to cover (0.0 when FINRA has none), change since the
    prior report as a fraction (None when missing), FINRA's average daily volume, market class, settlement date."""
    chg = r.get("changePercent")
    return {"si": r.get("currentShortPositionQuantity") or 0, "dtc": r.get("daysToCoverQuantity") or 0.0,
            "chg": None if chg is None else chg / 100, "adv": r.get("averageDailyVolumeQuantity"),
            "cls": r.get("marketClassCode"), "date": r.get("settlementDate")}


def settlement_dates(symbol="AAPL"):
    """Every settlement date (ISO, ascending), read from one always-listed symbol's history."""
    rows = _post({"limit": PAGE, "fields": ["settlementDate"],
                  "compareFilters": [{"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": symbol}]})
    return sorted({r["settlementDate"] for r in rows})


def report(date):
    """{symbol (SEC spelling): slim row} for one settlement date, exchange-listed classes only (paged)."""
    out, offset = {}, 0
    while True:
        rows = _post({"limit": PAGE, "offset": offset,
                      "compareFilters": [{"compareType": "EQUAL", "fieldName": "settlementDate", "fieldValue": date}],
                      "domainFilters": [{"fieldName": "marketClassCode", "values": list(CLASSES)}]})
        for r in rows:
            out[norm(r["symbolCode"])] = slim(r)
        if len(rows) < PAGE:
            return out
        offset += PAGE


def usable_from(settle, cal):
    """First session (ISO) on which a report settled on `settle` may be used: the SI_LAG-th session after it in the
    ascending session list `cal`. A settlement date that is not a session counts from the next session (later, never
    earlier). None when `cal` does not reach that far yet."""
    j = bisect.bisect_left(cal, settle) + SI_LAG
    return cal[j] if j < len(cal) else None


def frame(y, q):
    """{cik: (fact date ISO, shares)} from one SEC frame ({} when it is not published)."""
    d = common.get_json(FRAME.format(y=y, q=q)) or {}
    return {int(r["cik"]): (r["end"], int(r["val"])) for r in d.get("data", []) if r.get("val")}


def shares_index(frames):
    """Frames -> {cik: [(fact date ISO, shares), ...] ascending}; the same fact in several frames counts once."""
    idx = {}
    for fr in frames:
        for cik, fact in fr.items():
            idx.setdefault(cik, set()).add(tuple(fact))
    return {cik: sorted(s) for cik, s in idx.items()}


def shares_at(idx, cik, day):
    """Shares outstanding usable on session `day` (ISO): the latest fact dated at least SHARES_LAG days earlier and
    at most SHARES_MAX_AGE days old; None when there is none."""
    facts = idx.get(cik)
    if not facts:
        return None
    d = dt.date.fromisoformat(day)
    i = bisect.bisect_right(facts, ((d - dt.timedelta(SHARES_LAG)).isoformat(), float("inf"))) - 1
    if i < 0:
        return None
    end, val = facts[i]
    return val if val > 0 and (d - dt.date.fromisoformat(end)).days <= SHARES_MAX_AGE else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest tests.test_shorts -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add bot/shorts.py tests/test_shorts.py
git commit -m "Squeeze: FINRA short interest + SEC shares with point-in-time rules"
```

---

### Task 2: `bot/squeeze.py` — features, eligibility, scoring, outcome

**Files:**
- Create: `bot/squeeze.py`
- Test: `tests/test_squeeze.py`

**Interfaces:**
- Consumes: `market.LOW_LIQUIDITY` (2,000,000)
- Produces:
  - bars = `{"d": [ISO], "o", "h", "l", "c", "v": sequences of float}` (open may be None or NaN)
  - `features(b, i) -> {"price","dollar","rvol","ret5","brk"} | None`
  - `make_row(ticker, si_row, shares, feat) -> row | None`; row keys: `t, si_pct, dtc, chg, si_date, shares, price, dollar, rvol, ret5, brk`
  - `pct_ranks(xs) -> list[float]`
  - `scored(rows, g_si) -> list[row + fuel, trigger]`
  - `top(cands, w, g_rv, n=TOP) -> list[row + score]`
  - `rank(rows, v, n=TOP)` where `v = {"w", "g_si", "g_rv"}`
  - `outcome(b, i, end) -> {"hit": bool, "maxup", "dd", "r10"} | None`
  - constants `TOP=10, HIT=1.5, WINDOW=10, MIN_PRICE=1.0, MIN_DOLLAR, GRID` (27 dicts)

- [ ] **Step 1: Write the failing tests** — `tests/test_squeeze.py`

```python
"""Squeeze screener: features, eligibility, ranking and outcome."""
import unittest

from bot import squeeze


def bars(n, price=10.0, vol=1_000_000.0):
    return {"d": [f"2026-01-{k + 1:02d}" if k < 31 else f"2026-02-{k - 30:02d}" for k in range(n)],
            "o": [price] * n, "h": [price] * n, "l": [price] * n, "c": [price] * n, "v": [vol] * n}


class Features(unittest.TestCase):
    def test_windows_and_values(self):
        b = bars(30)
        b["v"][25] = 3_000_000.0
        b["c"][20] = 8.0
        b["c"][25] = 12.0
        f = squeeze.features(b, 25)
        self.assertAlmostEqual(f["rvol"], 3.0)  # v[25] / mean(v[5:25])
        self.assertAlmostEqual(f["ret5"], 12.0 / 8.0 - 1)  # c[25] / c[20]
        self.assertAlmostEqual(f["brk"], 12.0 / 10.0)  # c[25] / max(c[5:25])
        self.assertEqual(f["price"], 12.0)

    def test_never_reads_bar_d(self):
        b = bars(30)
        before = squeeze.features(b, 25)
        b["v"][26], b["c"][26] = 9e9, 99.0
        self.assertEqual(squeeze.features(b, 25), before)

    def test_short_history_and_zero_volume(self):
        self.assertIsNone(squeeze.features(bars(30), 19))
        self.assertIsNone(squeeze.features(bars(30, vol=0.0), 25))


class MakeRow(unittest.TestCase):
    FEAT = {"price": 5.0, "dollar": 3e6, "rvol": 2.0, "ret5": 0.1, "brk": 1.05}

    def test_row(self):
        r = squeeze.make_row("GME", {"si": 30, "dtc": 0.0, "chg": None, "date": "2026-09-15"}, 100, self.FEAT)
        self.assertAlmostEqual(r["si_pct"], 0.3)
        self.assertEqual((r["t"], r["dtc"], r["si_date"], r["rvol"]), ("GME", 0.0, "2026-09-15", 2.0))

    def test_filters(self):
        si = {"si": 30, "dtc": 2.0}
        self.assertIsNone(squeeze.make_row("X", si, None, self.FEAT))
        self.assertIsNone(squeeze.make_row("X", None, 100, self.FEAT))
        self.assertIsNone(squeeze.make_row("X", si, 100, None))
        self.assertIsNone(squeeze.make_row("X", si, 100, {**self.FEAT, "price": 0.99}))
        self.assertIsNone(squeeze.make_row("X", si, 100, {**self.FEAT, "dollar": 1_999_999}))


def row(t, si_pct, dtc=1.0, rvol=1.0, ret5=0.0, brk=1.0):
    return {"t": t, "si_pct": si_pct, "dtc": dtc, "rvol": rvol, "ret5": ret5, "brk": brk}


class Ranking(unittest.TestCase):
    def test_pct_ranks_ties(self):
        self.assertEqual([round(x, 4) for x in squeeze.pct_ranks([5, 1, 5, 3])], [0.8333, 0.0, 0.8333, 0.3333])
        self.assertEqual(squeeze.pct_ranks([7]), [1.0])
        self.assertEqual(squeeze.pct_ranks([]), [])

    def test_gates_order_and_cut(self):
        rows = [row("LOW", 0.05, rvol=9), row("A", 0.30, dtc=5, rvol=3, ret5=0.2, brk=1.2),
                row("B", 0.20, dtc=2, rvol=2, ret5=0.1, brk=1.1), row("QUIET", 0.40, dtc=9, rvol=1.2)]
        got = squeeze.rank(rows, {"w": 0.5, "g_si": 0.10, "g_rv": 1.5})
        self.assertEqual([r["t"] for r in got], ["A", "B"])  # LOW fails the SI gate, QUIET the rvol gate
        self.assertGreater(got[0]["score"], got[1]["score"])
        self.assertEqual(len(squeeze.rank([row(f"T{k}", 0.2, rvol=2) for k in range(15)],
                                          {"w": 0.5, "g_si": 0.1, "g_rv": 1.5})), squeeze.TOP)

    def test_ties_break_by_ticker_and_ranks_are_within_candidates(self):
        got = squeeze.rank([row("ZZ", 0.2, rvol=2), row("AA", 0.2, rvol=2)], {"w": 0.5, "g_si": 0.1, "g_rv": 1.5})
        self.assertEqual([r["t"] for r in got], ["AA", "ZZ"])
        c = squeeze.scored([row("X", 0.05, rvol=50), row("Y", 0.3, rvol=2), row("Z", 0.2, rvol=1)], 0.1)
        self.assertEqual([r["t"] for r in c], ["Y", "Z"])
        self.assertEqual(c[0]["trigger"] > c[1]["trigger"], True)

    def test_empty_day(self):
        self.assertEqual(squeeze.rank([], squeeze.GRID[0]), [])
        self.assertEqual(len(squeeze.GRID), 27)


class Outcome(unittest.TestCase):
    def test_hit_edge_and_values(self):
        b = bars(40)
        b["h"][25], b["l"][27], b["c"][30] = 15.0, 5.0, 12.5  # binary-exact ratios
        o = squeeze.outcome(b, 21, b["d"][30])
        self.assertEqual(o, {"hit": True, "maxup": 0.5, "dd": -0.5, "r10": 0.25})
        b["h"][25] = 14.99
        self.assertFalse(squeeze.outcome(b, 21, b["d"][30])["hit"])

    def test_window_stops_at_end(self):
        b = bars(40)
        b["h"][31] = 30.0
        self.assertFalse(squeeze.outcome(b, 21, b["d"][30])["hit"])

    def test_halt_uses_existing_bars(self):
        b = bars(40)
        end = b["d"][30]
        for k in ("d", "o", "h", "l", "c", "v"):
            del b[k][28:31]  # no bars for sessions 28-30
        self.assertAlmostEqual(squeeze.outcome(b, 21, end)["r10"], 0.0)

    def test_no_open_is_no_trade(self):
        for bad in (None, float("nan"), 0.0):
            b = bars(40)
            b["o"][21] = bad
            self.assertIsNone(squeeze.outcome(b, 21, b["d"][30]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_squeeze -v`
Expected: ERROR — `ImportError: cannot import name 'squeeze'`

- [ ] **Step 3: Implement `bot/squeeze.py`**

```python
"""Short-squeeze screener: features, eligibility, scoring and outcome - pure functions shared by the live run and
the backtest (docs/superpowers/specs/2026-09-27-squeeze-screener-design.md, sections 5-6)."""
import heapq

from bot import market

TOP = 10
HIT = 1.5  # "exploded": the highest price in the window reaches 1.5x the entry
WINDOW = 10  # sessions D .. D+9
MIN_PRICE = 1.0
MIN_DOLLAR = market.LOW_LIQUIDITY  # $2M a day
GRID = tuple({"w": w, "g_si": g, "g_rv": r}
             for w in (0.3, 0.5, 0.7) for g in (0.10, 0.15, 0.20) for r in (1.0, 1.5, 2.0))


def features(b, i):
    """Price/volume features known before session D, from a ticker's bars through index i (= session D-1):
    price; dollar = mean close x volume over the last 21 sessions; rvol = volume[D-1] / mean volume[D-21..D-2];
    ret5 = close[D-1] / close[D-6] - 1; brk = close[D-1] / max close[D-21..D-2]. None when the history is shorter
    than 21 sessions or a divisor is not positive."""
    if i < 20:
        return None
    c, v = b["c"], b["v"]
    avg = sum(v[i - 20:i]) / 20
    prior = max(c[i - 20:i])
    if not (avg > 0 and prior > 0 and c[i - 5] > 0):
        return None
    dollar = sum(c[k] * v[k] for k in range(i - 20, i + 1)) / 21
    return {"price": c[i], "dollar": dollar, "rvol": v[i] / avg, "ret5": c[i] / c[i - 5] - 1, "brk": c[i] / prior}


def make_row(ticker, si_row, shares, feat):
    """One eligible stock for the day, or None: needs a short-interest row, shares outstanding, features,
    price >= MIN_PRICE and dollar volume >= MIN_DOLLAR."""
    if not si_row or not shares or not feat or feat["price"] < MIN_PRICE or feat["dollar"] < MIN_DOLLAR:
        return None
    return {"t": ticker, "si_pct": si_row["si"] / shares, "dtc": si_row.get("dtc") or 0.0, "chg": si_row.get("chg"),
            "si_date": si_row.get("date"), "shares": shares, **feat}


def pct_ranks(xs):
    """Percentile rank of each value in [0, 1] (0 = lowest); ties share their mean rank; a single value -> 1.0."""
    n = len(xs)
    if n < 2:
        return [1.0] * n
    order = sorted(range(n), key=xs.__getitem__)
    out, i = [0.0] * n, 0
    while i < n:
        j = i
        while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2 / (n - 1)
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def scored(rows, g_si):
    """Candidate set C = rows with si_pct >= g_si, each with fuel = mean rank of si_pct and dtc and
    trigger = mean rank of rvol, ret5 and brk (ranks taken within C)."""
    c = [r for r in rows if r["si_pct"] >= g_si]
    if not c:
        return []
    rk = {k: pct_ranks([r[k] for r in c]) for k in ("si_pct", "dtc", "rvol", "ret5", "brk")}
    return [{**r, "fuel": (rk["si_pct"][n] + rk["dtc"][n]) / 2,
             "trigger": (rk["rvol"][n] + rk["ret5"][n] + rk["brk"][n]) / 3} for n, r in enumerate(c)]


def top(cands, w, g_rv, n=TOP):
    """The day's list from scored candidates: rvol >= g_rv, score = w x fuel + (1 - w) x trigger, best first
    (ties by ticker), at most n."""
    best = heapq.nsmallest(n, ((-(w * r["fuel"] + (1 - w) * r["trigger"]), r["t"], k)
                               for k, r in enumerate(cands) if r["rvol"] >= g_rv))
    return [{**cands[k], "score": -s} for s, _, k in best]


def rank(rows, v, n=TOP):
    """The day's list for variant v = {"w", "g_si", "g_rv"}."""
    return top(scored(rows, v["g_si"]), v["w"], v["g_rv"], n)


def outcome(b, i, end):
    """Buying at the open of bar i (session D) and holding through session `end` (ISO, = D+9): hit (max high >=
    HIT x open), maxup, dd (min low / open - 1), r10 (last close in the window / open - 1). None when bar i has no
    positive open."""
    o = b["o"][i]
    if o is None or not o > 0:
        return None
    j = i
    while j + 1 < len(b["d"]) and b["d"][j + 1] <= end:
        j += 1
    hi, lo = max(b["h"][i:j + 1]), min(b["l"][i:j + 1])
    return {"hit": hi >= HIT * o, "maxup": hi / o - 1, "dd": lo / o - 1, "r10": b["c"][j] / o - 1}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest tests.test_squeeze -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add bot/squeeze.py tests/test_squeeze.py
git commit -m "Squeeze: shared features, eligibility, ranking and outcome"
```

---

### Task 3: `bot/backtest.py` engine — replay, selection, verdict

**Files:**
- Create: `bot/backtest.py` (engine part)
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: `shorts.usable_from`, `shorts.shares_index`, `shorts.shares_at`; `squeeze.features/make_row/scored/top/outcome/WINDOW`
- Produces:
  - `acc(keep=False) -> {"n", "hits", "years": {year: [n, hits]}, ("dd","r10","maxup" lists)}`
  - `add(a, year, o)`, `rate(a)`, `lift(a, base, year=None) -> float | None`
  - `day_rows(day, prev, end, rep, shares, primary, bars) -> (rows, {ticker: outcome|None})`
  - `replay(days, cal, reports, shares, primary, bars, lists, bases, keep=()) -> {"lists", "bases", "picks", "days", "coverage"}`
  - `select(res, names, base="all", min_hits=30) -> name | None`
  - `verdict(a, base, years, lift_min=3.0, min_hits=30, min_years=2) -> ([(label, ok, value)], go)`
  - `vname(v) -> str`, `PICK_FIELDS`, constants `START, SPLIT, LIFT, MIN_HITS, MIN_YEARS, EPS`

- [ ] **Step 1: Write the failing tests** — `tests/test_backtest.py`

```python
"""Squeeze backtest engine: a synthetic market with one planted squeeze and two look-ahead traps."""
import datetime as dt
import unittest

from bot import backtest, shorts, squeeze


def weekdays(start, n):
    out, d = [], dt.date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(1)
    return out


CAL = weekdays("2024-01-01", 60)
K0 = 35  # the squeeze session


def flat(vol=1_000_000.0, price=10.0):
    n = len(CAL)
    return {"d": list(CAL), "o": [price] * n, "h": [price] * n, "l": [price] * n, "c": [price] * n, "v": [vol] * n}


def market():
    aaa, bbb, ccc = flat(), flat(), flat()
    for b in (aaa, bbb):  # the day before: 5x volume and a breakout close; squeeze day: high 2x the open
        b["v"][K0 - 1], b["c"][K0 - 1], b["h"][K0 - 1] = 5_000_000.0, 11.0, 11.0
        b["h"][K0] = 20.0
    aaa["v"][K0] = 5_000_000.0  # still busy on the squeeze day -> qualifies again next day (repeat pick)
    ccc["v"][K0] = 5_000_000.0  # spike ON day K0: must not be visible to the list of K0
    shares = shorts.shares_index([{1: ("2023-06-30", 1_000_000), 2: ("2023-06-30", 1_000_000),
                                   3: ("2023-06-30", 1_000_000)}])
    row = lambda si: {"si": si, "dtc": 3.0, "chg": 0.0, "date": "x"}
    early = {"AAA": row(400_000), "BBB": row(10_000), "CCC": row(300_000)}
    late = {"AAA": row(400_000), "BBB": row(500_000), "CCC": row(300_000)}  # BBB's big SI settles 3 days pre-squeeze
    reports = [(shorts.usable_from(CAL[0], CAL), early), (shorts.usable_from(CAL[K0 - 3], CAL), late)]
    return reports, shares, {"AAA": 1, "BBB": 2, "CCC": 3}, {"AAA": aaa, "BBB": bbb, "CCC": ccc}


V = {"w": 0.5, "g_si": 0.10, "g_rv": 1.5}


class Replay(unittest.TestCase):
    def setUp(self):
        reports, shares, primary, bars = market()
        self.res = backtest.replay(CAL[25:45], CAL, reports, shares, primary, bars, {"v": V}, {"all": None,
                                   "gate": 0.10}, keep=("v",))

    def test_planted_squeeze_is_found_once_and_traps_hold(self):
        picks = [(p["date"], p["t"], p["hit"]) for p in self.res["picks"]["v"]]
        # AAA on the squeeze day (a hit, counted once despite qualifying again on K0+1);
        # BBB never (its big SI was not public yet); CCC only the day after its own spike (no hit)
        self.assertEqual(picks, [(CAL[K0], "AAA", True), (CAL[K0 + 1], "CCC", False)])
        a = self.res["lists"]["v"]
        self.assertEqual((a["n"], a["hits"], a["years"]), (2, 1, {"2024": [2, 1]}))

    def test_baselines(self):
        base = self.res["bases"]["all"]
        self.assertEqual((base["n"], base["hits"]), (60, 20))  # 20 days x 3 stocks; AAA, BBB hit for D in K0-9..K0
        self.assertEqual(self.res["days"], 20)
        self.assertNotIn("dd", base)

    def test_coverage(self):
        c = self.res["coverage"]["2024"]
        self.assertEqual(len(c["finra"]), 3)
        self.assertEqual(len(c["shares"]), 3)


def tally(n, hits, years):
    return {"n": n, "hits": hits, "years": years}


BASE = tally(30000, 3000, {"2024": [10000, 1000], "2025": [10000, 1000], "2026": [10000, 1000]})


class Verdict(unittest.TestCase):
    def test_exact_edges_pass(self):
        a = tally(100, 30, {"2024": [40, 12], "2025": [40, 12], "2026": [20, 6]})
        checks, go = backtest.verdict(a, BASE, ["2024", "2025", "2026"])
        self.assertTrue(go, checks)

    def test_too_few_hits(self):
        a = tally(50, 29, {"2024": [25, 15], "2025": [25, 14]})
        self.assertFalse(backtest.verdict(a, BASE, ["2024", "2025", "2026"])[1])

    def test_one_good_year_is_not_enough(self):
        a = tally(100, 40, {"2024": [40, 30], "2025": [40, 8], "2026": [20, 2]})
        checks, go = backtest.verdict(a, BASE, ["2024", "2025", "2026"])
        self.assertTrue(checks[0][1])
        self.assertFalse(go)

    def test_zero_hit_baseline_is_no_go_not_a_crash(self):
        a = tally(100, 40, {"2024": [100, 40]})
        self.assertFalse(backtest.verdict(a, tally(10, 0, {"2024": [10, 0]}), ["2024"])[1])


class Select(unittest.TestCase):
    def test_highest_lift_with_enough_hits(self):
        res = {"bases": {"all": tally(1000, 100, {})},
               "lists": {"best-but-thin": tally(50, 29, {}), "good": tally(100, 40, {}), "ok": tally(200, 60, {}),
                         "tie-more-hits": tally(200, 80, {})}}
        self.assertEqual(backtest.select(res, list(res["lists"])), "tie-more-hits")
        self.assertIsNone(backtest.select({"bases": res["bases"], "lists": {"x": tally(10, 5, {})}}, ["x"]))


if __name__ == "__main__":
    unittest.main()
```

Expected numbers, derived by hand: with `V`, on K0 only AAA is in C with rvol ≥ 1.5 (BBB's SI is 1 % until the late report is usable at CAL[K0+5]; CCC's rvol on K0 reads bar K0−1 = 1.0). On K0+1 AAA qualifies again (bar K0 volume 5×) but is inside its 10-session window; CCC qualifies (bar K0 spike), opens at 10, never reaches 15. On K0+5 BBB is in C but its rvol reads a normal bar (≈0.83 < 1.5). Baseline: 20 days × 3 stocks; AAA and BBB hit for every D with D ≤ K0 ≤ D+9 inside CAL[25:45] → D = 26 … 35 → 2 × 10 = 20.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_backtest -v`
Expected: ERROR — `ImportError: cannot import name 'backtest'`

- [ ] **Step 3: Implement the engine in `bot/backtest.py`**

```python
"""Squeeze-screener backtest - phase 1 of docs/superpowers/specs/2026-09-27-squeeze-screener-design.md.

    SEC_UA="stockbot backtest (github.com/danielsuisa/stockbot)" python -m bot.backtest

Downloads FINRA short interest, SEC shares-outstanding frames and Yahoo daily bars into .cache/squeeze/
(gitignored), replays every session with bot.squeeze's functions, chooses one scoring variant on the in-sample years
only, then judges it on the out-of-sample years and writes docs/backtest/squeeze-<date>.md plus a CSV of its picks."""
import bisect

from bot import shorts, squeeze

START, SPLIT = "2018-02-01", "2024-01-01"
LIFT, MIN_HITS, MIN_YEARS, EPS = 3.0, 30, 2, 1e-9
PICK_FIELDS = ("t", "score", "si_pct", "dtc", "rvol", "ret5", "brk", "price")


def vname(v):
    return f"w{v['w']:g}/si{v['g_si']:g}/rv{v['g_rv']:g}"


# ---------- tallies ----------
def acc(keep=False):
    """Tally of picks or stock-days: n, hits, per year [n, hits]; with keep, also every dd / r10 / maxup."""
    return {"n": 0, "hits": 0, "years": {}, **({"dd": [], "r10": [], "maxup": []} if keep else {})}


def add(a, year, o):
    a["n"] += 1
    a["hits"] += o["hit"]
    y = a["years"].setdefault(year, [0, 0])
    y[0] += 1
    y[1] += o["hit"]
    if "dd" in a:
        for k in ("dd", "r10", "maxup"):
            a[k].append(o[k])


def rate(a):
    return a["hits"] / a["n"] if a["n"] else None


def lift(a, base, year=None):
    """Hit rate of `a` / hit rate of `base` (overall, or one year); None when either is empty or the base has no hit."""
    if year is None:
        (n, h), (bn, bh) = (a["n"], a["hits"]), (base["n"], base["hits"])
    else:
        (n, h), (bn, bh) = a["years"].get(year, [0, 0]), base["years"].get(year, [0, 0])
    return (h / n) / (bh / bn) if n and bn and bh else None


# ---------- replay ----------
def day_rows(day, prev, end, rep, shares, primary, bars):
    """Eligible rows for session `day`, with features from the bar of `prev` (= D-1), and {ticker: outcome or None}
    (None: no bar or no open on `day`)."""
    rows, outs = [], {}
    for sym, si in rep.items():
        cik, b = primary.get(sym), bars.get(sym)
        if cik is None or not b:
            continue
        i = bisect.bisect_left(b["d"], day) - 1
        if i < 0 or b["d"][i] != prev:
            continue
        row = squeeze.make_row(sym, si, shorts.shares_at(shares, cik, day), squeeze.features(b, i))
        if row is None:
            continue
        rows.append(row)
        nxt = i + 1
        outs[sym] = squeeze.outcome(b, nxt, end) if nxt < len(b["d"]) and b["d"][nxt] == day else None
    return rows, outs


def coverage(c, rep, primary, bars, shares, day):
    """Add one report's symbols to a year's coverage sets: in FINRA, mapped to an SEC company, with Yahoo bars,
    with usable shares outstanding on `day`."""
    for sym in rep:
        c["finra"].add(sym)
        cik = primary.get(sym)
        if cik is None:
            continue
        c["sec_map"].add(sym)
        if bars.get(sym):
            c["yahoo"].add(sym)
            if shorts.shares_at(shares, cik, day):
                c["shares"].add(sym)


def replay(days, cal, reports, shares, primary, bars, lists, bases, keep=()):
    """Walk the sessions `days` (ISO, each in `cal`, the ascending session list).
    reports: [(usable ISO, {symbol: shorts.slim row})] ascending by usable date; shares: shorts.shares_index();
    primary: {symbol: cik}; bars: {symbol: bars}; lists: {name: variant} -> each day's top list (a ticker counts once
    per squeeze.WINDOW sessions); bases: {name: g_si or None} -> every eligible stock-day with si_pct >= g_si
    (None: all) as a hit-rate baseline; keep: list names whose picks are kept.
    -> {"lists": {name: tally}, "bases": {name: tally}, "picks": {name: [...]}, "days": n, "coverage": {year: sets}}"""
    pos = {d: k for k, d in enumerate(cal)}
    usable = [u for u, _ in reports]
    res = {"lists": {n: acc(keep=True) for n in lists}, "bases": {n: acc() for n in bases},
           "picks": {n: [] for n in keep}, "days": 0, "coverage": {}}
    last = {n: {} for n in lists}
    gates = sorted({v["g_si"] for v in lists.values()})
    seen = set()
    for day in days:
        k, j = pos[day], bisect.bisect_right(usable, day) - 1
        if k < 1 or k + squeeze.WINDOW - 1 >= len(cal) or j < 0:
            continue
        rep, year = reports[j][1], day[:4]
        if (j, year) not in seen:
            seen.add((j, year))
            sets = res["coverage"].setdefault(year, {"finra": set(), "sec_map": set(), "yahoo": set(), "shares": set()})
            coverage(sets, rep, primary, bars, shares, day)
        rows, outs = day_rows(day, cal[k - 1], cal[k + squeeze.WINDOW - 1], rep, shares, primary, bars)
        res["days"] += 1
        cands = {g: squeeze.scored(rows, g) for g in gates}
        for name, v in lists.items():
            for r in squeeze.top(cands[v["g_si"]], v["w"], v["g_rv"]):
                o = outs.get(r["t"])
                if o is None or k - last[name].get(r["t"], -squeeze.WINDOW) < squeeze.WINDOW:
                    continue
                last[name][r["t"]] = k
                add(res["lists"][name], year, o)
                if name in res["picks"]:
                    res["picks"][name].append({"date": day, **{f: r[f] for f in PICK_FIELDS}, **o})
        for name, g in bases.items():
            for r in rows:
                o = outs[r["t"]]
                if o is not None and (g is None or r["si_pct"] >= g):
                    add(res["bases"][name], year, o)
    return res


# ---------- decision ----------
def select(res, names, base="all", min_hits=MIN_HITS):
    """In-sample choice among `names`: the highest lift with >= min_hits hits; ties -> more hits, then name.
    None when no list qualifies."""
    ok = [(lift(res["lists"][n], res["bases"][base]) or 0.0, res["lists"][n]["hits"], n) for n in names
          if res["lists"][n]["hits"] >= min_hits]
    return min(ok, key=lambda t: (-t[0], -t[1], t[2]))[2] if ok else None


def verdict(a, base, years, lift_min=LIFT, min_hits=MIN_HITS, min_years=MIN_YEARS):
    """Out-of-sample GO / NO-GO for list tally `a` against `base`: ([(check, passed, value)], go)."""
    total = lift(a, base)
    per = {y: lift(a, base, y) for y in years}
    good = sum(1 for x in per.values() if x is not None and x >= lift_min - EPS)
    checks = [(f"lift ≥ {lift_min:g}× baseline", total is not None and total >= lift_min - EPS, total),
              (f"≥ {min_hits} distinct hits", a["hits"] >= min_hits, a["hits"]),
              (f"lift ≥ {lift_min:g}× in ≥ {min_years} of {len(years)} years", good >= min_years, per)]
    return checks, all(ok for _, ok, _ in checks)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest tests.test_backtest -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add bot/backtest.py tests/test_backtest.py
git commit -m "Squeeze backtest engine: point-in-time replay, in-sample selection, verdict"
```

---

### Task 4: `bot/backtest.py` — downloads, cache, report, CLI

**Files:**
- Modify: `bot/backtest.py` (append loaders, report, `main`; extend imports)
- Test: `tests/test_backtest.py` (append)

**Interfaces:**
- Consumes: `common.ROOT`, `common.cik_tickers()`, `market.chart(ticker, **params)`, `shorts.settlement_dates/report/frame/usable_from/shares_index`, Task 3 engine
- Produces: `to_bars(r) -> bars | None`, `pack(b)`, `_cached(path, make)`, `load_reports`, `load_frames`, `load_bars`, `render(meta, r_in, grid, chosen, r_oos, checks, go) -> str`, `write_csv(path, picks)`, `main(argv=None)`

- [ ] **Step 1: Append the failing tests** to `tests/test_backtest.py` (classes above the `if __name__` line; the new imports go into the import block at the top of the file)

```python
import gzip
import json
import math
import tempfile
from pathlib import Path
from unittest import mock


class Loaders(unittest.TestCase):
    def test_to_bars(self):
        r = {"timestamp": [1704205800, 1704292200, 1704292300, 1704378600],
             "indicators": {"quote": [{"open": [None, 10.0, 10.5, 11.0], "high": [None, 12.0, 12.5, None],
                                       "low": [9.0, 9.5, 9.6, None], "close": [9.5, 11.0, 11.5, None],
                                       "volume": [100, None, 300, 400]}]}}
        b = backtest.to_bars(r)
        self.assertEqual(b["d"], ["2024-01-02", "2024-01-03"])  # duplicate session kept once (last), no-close dropped
        self.assertEqual((b["o"], b["h"], b["c"], b["v"]), ([None, 10.5], [9.5, 12.5], [9.5, 11.5], [100, 300]))
        self.assertIsNone(backtest.to_bars(None))
        p = backtest.pack(b)
        self.assertTrue(math.isnan(p["o"][0]))
        self.assertEqual(list(p["c"]), [9.5, 11.5])

    def test_cache_saves_only_real_values(self):
        with tempfile.TemporaryDirectory() as d:
            path, calls = Path(d, "x.json.gz"), []
            make = lambda: calls.append(1) or {"a": 1}
            self.assertEqual(backtest._cached(path, make), {"a": 1})
            self.assertEqual(backtest._cached(path, make), {"a": 1})
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads(gzip.decompress(path.read_bytes())), {"a": 1})
            empty = Path(d, "none.json.gz")
            self.assertIsNone(backtest._cached(empty, lambda: None))
            self.assertFalse(empty.exists())


class Render(unittest.TestCase):
    def test_report_shows_verdict_tables_and_choice(self):
        reports, shares, primary, bars = market()
        grid = {backtest.vname(V): V}
        r = backtest.replay(CAL[25:45], CAL, reports, shares, primary, bars, {**grid, "trigger-only":
                            {"w": 0.0, "g_si": -1.0, "g_rv": 0.0}}, {"all": None})
        checks, go = backtest.verdict(r["lists"][backtest.vname(V)], r["bases"]["all"], ["2024"])
        md = backtest.render({"run": "2026-09-27", "lines": ["x"]}, r, grid, backtest.vname(V),
                             {**r, "lists": {"selected": r["lists"][backtest.vname(V)], **r["lists"]}}, checks, go)
        self.assertIn("NO-GO", md)
        self.assertIn("w0.5/si0.1/rv1.5", md)
        self.assertIn("| 2024 |", md)
        none = backtest.render({"run": "d", "lines": []}, r, grid, None, None, [], False)
        self.assertIn("No variant reached", none)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_backtest -v`
Expected: ERROR — `AttributeError: module 'bot.backtest' has no attribute 'to_bars'`

- [ ] **Step 3: Implement** — replace the import block of `bot/backtest.py` with:

```python
import argparse
import array
import bisect
import concurrent.futures as cf
import csv
import datetime as dt
import gzip
import json
import statistics
import time

from bot import common, market, shorts, squeeze

CACHE = common.ROOT / ".cache" / "squeeze"
OUT = common.ROOT / "docs" / "backtest"
HISTORY_FROM = "2017-09-01"  # Yahoo warm-up before START
NAN = float("nan")
_DATES = {}  # one shared str object per session date (memory)
```

and append at the end of the file:

```python
# ---------- downloads (cached) ----------
def _cached(path, make):
    """gz-JSON disk cache: the saved value, else make() - saved only when it is a real value, so a failed download
    is retried on the next run."""
    if path.exists():
        return json.loads(gzip.decompress(path.read_bytes()))
    val = make()
    if val:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(val, separators=(",", ":")).encode()))
    return val


def _parallel(fn, items, threads):
    with cf.ThreadPoolExecutor(max(1, threads)) as ex:
        return dict(zip(items, ex.map(fn, items)))


def to_bars(r):
    """Yahoo chart result -> bars {"d","o","h","l","c","v"}: oldest first, UTC session dates, one bar per date (the
    last), bars without a close dropped; a missing high/low falls back to the close, volume to 0, open stays None."""
    if not r:
        return None
    ts = r.get("timestamp") or []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    cols = [q.get(k) or [None] * len(ts) for k in ("open", "high", "low", "close", "volume")]
    b = {k: [] for k in "dohlcv"}
    for t, o, h, l, c, v in zip(ts, *cols):
        if not c:
            continue
        day = dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()
        if b["d"] and b["d"][-1] == day:
            for k in "dohlcv":
                b[k].pop()
        for k, x in zip("dohlcv", (day, o or None, h or c, l or c, c, v or 0)):
            b[k].append(x)
    return b if b["d"] else None


def pack(b):
    """Cached bars -> compact in-memory bars: shared date strings, float arrays (a missing open -> NaN)."""
    return {"d": [_DATES.setdefault(d, d) for d in b["d"]],
            "o": array.array("d", (NAN if x is None else x for x in b["o"])),
            **{k: array.array("d", b[k]) for k in "hlcv"}}


def load_reports(dates, threads=4):
    """{settlement date: {symbol: slim row}} (one cached file per date)."""
    return _parallel(lambda d: _cached(CACHE / "finra" / f"{d}.json.gz", lambda: shorts.report(d)), dates, threads)


def load_frames(first=2017, last=None):
    """Every quarterly SEC shares-outstanding frame from `first` to this year (cached; unpublished ones skipped)."""
    out = []
    for y in range(first, (last or dt.date.today().year) + 1):
        for q in (1, 2, 3, 4):
            rows = _cached(CACHE / "sec" / f"CY{y}Q{q}I.json.gz",
                           lambda: [[c, e, v] for c, (e, v) in shorts.frame(y, q).items()])
            out.append({int(c): (e, v) for c, e, v in rows or []})
    return out


def load_bars(symbols, threads=8):
    """{symbol: packed bars} for every symbol Yahoo has daily bars for since HISTORY_FROM (cached per symbol)."""
    p1 = int(dt.datetime.fromisoformat(HISTORY_FROM).replace(tzinfo=dt.timezone.utc).timestamp())
    p2 = int(time.time())

    def one(sym):
        b = _cached(CACHE / "yahoo" / f"{sym}.json.gz",
                    lambda: to_bars(market.chart(sym, period1=p1, period2=p2, interval="1d")))
        return pack(b) if b else None
    return {s: b for s, b in _parallel(one, symbols, threads).items() if b}


# ---------- report ----------
def _pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def _x(x):
    return "—" if x is None else f"{x:.2f}×"


def _med(xs):
    return statistics.median(xs) if xs else None


HEAD = ["| List | Picks | Hits | Hit rate | Lift | Median worst drop | Median 10-session return |",
        "|---|---:|---:|---:|---:|---:|---:|"]


def _line(name, a, base):
    return (f"| {name} | {a['n']} | {a['hits']} | {_pct(rate(a))} | {_x(lift(a, base))} | "
            f"{_pct(_med(a.get('dd', [])))} | {_pct(_med(a.get('r10', [])))} |")


def _base_line(name, a):
    return f"| {name} (every stock-day) | {a['n']} | {a['hits']} | {_pct(rate(a))} | 1.00× | — | — |"


def render(meta, r_in, grid, chosen, r_oos=None, checks=(), go=False):
    """The Markdown report."""
    L = [f"# Squeeze screener backtest — {meta['run']}", "", f"**Verdict: {'GO ✅' if go else 'NO-GO ❌'}**", "",
         "Rule, fixed on 2026-09-27 before any result (out-of-sample years only): the top-10 hit rate is ≥ 3× the "
         "baseline hit rate, ≥ 30 distinct hits, and ≥ 3× in at least 2 of the 3 years. Hit = the highest price in "
         "sessions D … D+9 reaches ≥ 1.5× the open of D. Baseline = every eligible stock-day.", ""]
    if checks:
        L += ["| Check | Result | Value |", "|---|---|---|"]
        for label, ok, val in checks:
            shown = ", ".join(f"{y}: {_x(x)}" for y, x in val.items()) if isinstance(val, dict) else \
                (_x(val) if isinstance(val, float) or val is None else str(val))
            L.append(f"| {label} | {'pass' if ok else 'fail'} | {shown} |")
        L.append("")
    L += ["## Data", "", *[f"- {x}" for x in meta["lines"]], ""]
    base_in = r_in["bases"]["all"]
    L += [f"## In-sample ({START} … {SPLIT}, exclusive): every grid variant", "",
          "Selection: highest lift among variants with ≥ 30 in-sample hits (ties → more hits).", "", *HEAD]
    order = sorted(grid, key=lambda n: -(lift(r_in["lists"][n], base_in) or 0))
    L += [_line(("**" + n + "** ← chosen") if n == chosen else n, r_in["lists"][n], base_in) for n in order]
    L += [_line(n, a, base_in) for n, a in r_in["lists"].items() if n not in grid]
    L += [_base_line(n, a) for n, a in r_in["bases"].items()]
    L.append("")
    if chosen is None or r_oos is None:
        L += ["No variant reached 30 in-sample hits, so nothing was frozen and the out-of-sample years were not "
              "evaluated → NO-GO.", ""]
        return "\n".join(L)
    base = r_oos["bases"]["all"]
    L += [f"## Out-of-sample ({SPLIT} → last full window): the frozen variant `{chosen}`", "", *HEAD]
    L += [_line(n, a, base) for n, a in r_oos["lists"].items()]
    L += [_base_line(n, a) for n, a in r_oos["bases"].items()]
    sel = r_oos["lists"]["selected"]
    L += ["", "### By year", "", "| Year | Picks | Hits | Hit rate | Baseline rate | Lift |", "|---|---:|---:|---:|---:|---:|"]
    for y in sorted(base["years"]):
        n, h = sel["years"].get(y, [0, 0])
        bn, bh = base["years"][y]
        L.append(f"| {y} | {n} | {h} | {_pct(h / n if n else None)} | {_pct(bh / bn if bn else None)} | "
                 f"{_x(lift(sel, base, y))} |")
    L += ["", "## Coverage (survivorship check)", "",
          "| Year | FINRA symbols | Mapped to an SEC company | With Yahoo bars | With usable shares |",
          "|---|---:|---:|---:|---:|"]
    cov = {**r_in["coverage"], **r_oos["coverage"]}
    for y in sorted(cov):
        c = cov[y]
        n = len(c["finra"]) or 1
        L.append(f"| {y} | {len(c['finra'])} | {len(c['sec_map'])} ({len(c['sec_map']) / n:.0%}) | "
                 f"{len(c['yahoo'])} ({len(c['yahoo']) / n:.0%}) | {len(c['shares'])} ({len(c['shares']) / n:.0%}) |")
    L += ["", "## Known biases", "",
          "- Survivorship: symbols are matched through today's SEC ticker list and Yahoo only serves live tickers, so "
          "delisted and renamed stocks are missing (see coverage). The baseline comes from the same covered stocks, so "
          "the lift is like-for-like; the absolute hit rates are not.",
          "- Multi-class issuers and splits between the SEC figure and the FINRA date add noise to the short-interest %.",
          "- Yahoo bad ticks can create or hide a hit; the picks CSV lets any hit be checked by hand.", ""]
    return "\n".join(L)


def write_csv(path, picks):
    cols = ["date", *PICK_FIELDS, "hit", "maxup", "dd", "r10"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for p in picks:
            w.writerow({k: round(p[k], 4) if isinstance(p[k], float) else p[k] for k in cols})


# ---------- run ----------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Squeeze screener backtest -> docs/backtest/squeeze-<date>.md")
    ap.add_argument("--start", default=START)
    ap.add_argument("--split", default=SPLIT)
    ap.add_argument("--threads", type=int, default=8)
    a = ap.parse_args(argv)
    t0, run = time.time(), dt.date.today().isoformat()
    primary = {t: cik for cik, t in common.cik_tickers().items()}
    dates = [d for d in shorts.settlement_dates() if d >= "2017-12-01"]
    raw = load_reports(dates, a.threads // 2)
    cal = load_bars(["SPY"])["SPY"]["d"]
    reports = [(u, raw[d]) for d in dates if raw.get(d) and (u := shorts.usable_from(d, cal))]
    frames = load_frames()
    shares = shorts.shares_index(frames)
    syms = sorted({s for _, rep in reports for s in rep if s in primary})
    bars = load_bars(syms, a.threads)
    print(f"data: {len(reports)} reports, {len(shares)} SEC filers, {len(bars)}/{len(syms)} symbols with bars "
          f"({time.time() - t0:.0f}s)", flush=True)
    grid = {vname(v): v for v in squeeze.GRID}
    gates = sorted({v["g_si"] for v in squeeze.GRID})
    trig = {"w": 0.0, "g_si": -1.0, "g_rv": 0.0}
    diag = {**{f"fuel-only si{g:g}": {"w": 1.0, "g_si": g, "g_rv": 0.0} for g in gates}, "trigger-only": trig}
    ins = [d for d in cal if a.start <= d < a.split]
    r_in = replay(ins, cal, reports, shares, primary, bars, {**grid, **diag},
                  {"all": None, **{f"SI ≥ {g:.0%}": g for g in gates}})
    chosen = select(r_in, list(grid))
    print(f"in-sample: {r_in['days']} sessions, chosen {chosen} ({time.time() - t0:.0f}s)", flush=True)
    r_oos, checks, go, picks = None, [], False, []
    if chosen:
        v = grid[chosen]
        oos = [d for d in cal if d >= a.split]
        lists = {"selected": v, "fuel-only": {"w": 1.0, "g_si": v["g_si"], "g_rv": 0.0}, "trigger-only": trig}
        r_oos = replay(oos, cal, reports, shares, primary, bars, lists,
                       {"all": None, f"SI ≥ {v['g_si']:.0%}": v["g_si"]}, keep=("selected",))
        checks, go = verdict(r_oos["lists"]["selected"], r_oos["bases"]["all"], sorted(r_oos["bases"]["all"]["years"]))
        picks = r_oos["picks"]["selected"]
    last_full = cal[-squeeze.WINDOW]
    meta = {"run": run, "lines": [
        f"FINRA reports: {len(reports)} (settlements {dates[0]} … {dates[-1]}), usable from the 8th session after "
        f"settlement", f"SEC shares-outstanding frames: {sum(1 for f in frames if f)}, {len(shares)} filers",
        f"Symbols (FINRA NNM/SC/NYSE/AMEX, primary SEC ticker): {len(syms)}, with Yahoo bars: {len(bars)}",
        f"Sessions: in-sample {r_in['days']}, out-of-sample {r_oos['days'] if r_oos else 0} (last full 10-session "
        f"window starts {last_full})", f"Runtime {time.time() - t0:.0f}s · `python -m bot.backtest`"]}
    OUT.mkdir(parents=True, exist_ok=True)
    md = OUT / f"squeeze-{run}.md"
    md.write_text(render(meta, r_in, grid, chosen, r_oos, checks, go), "utf-8")
    if picks:
        write_csv(OUT / f"squeeze-{run}-picks.csv", picks)
    print(f"{'GO' if go else 'NO-GO'} -> {md}")
    return 0 if go else 3


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the whole suite**

Run: `python -m unittest discover -s tests`
Expected: all PASS (existing tests unchanged)

- [ ] **Step 5: Commit**

```bash
git add bot/backtest.py tests/test_backtest.py
git commit -m "Squeeze backtest: cached downloads, Markdown/CSV report, CLI"
```

---

### Task 5: Run the backtest and record the verdict

**Files:**
- Create: `docs/backtest/squeeze-<run date>.md`, `docs/backtest/squeeze-<run date>-picks.csv` (generated)

- [ ] **Step 1: Run**

Run: `SEC_UA="stockbot backtest (github.com/danielsuisa/stockbot)" python -m bot.backtest`
Expected: a `data:` line, an `in-sample:` line, then `GO -> …` or `NO-GO -> …`. If Yahoo throttles (many symbols without bars), rerun — the cache keeps what was fetched.

- [ ] **Step 2: Sanity checks (evidence before claims)**
  - Baseline hit rate is plausible (a small single-digit %), eligible stock-days per session in the low thousands.
  - Coverage table: mapped/Yahoo share per year, noting the older-year drop.
  - Spot-check 3 hits and 2 misses from the picks CSV against the raw Yahoo bars (open on D, max high D … D+9).
  - Spot-check one pick's `si_pct` against FINRA short shares ÷ SEC shares for that date.

- [ ] **Step 3: Commit the report**

```bash
git add docs/backtest
git commit -m "Squeeze backtest report: <GO|NO-GO>"
```

---

### Task 6: Branch review and hand-off

- [ ] **Step 1:** A fresh reviewer (subagent, not this session) reviews the branch diff against the spec: look-ahead, off-by-one in windows, de-dup, verdict arithmetic, test coverage.
- [ ] **Step 2:** Fix confirmed findings with a test each; rerun `python -m unittest discover -s tests`; rerun the backtest if engine code changed.
- [ ] **Step 3:** Push branch `squeeze-screener`; on GO, write the phase-2 plan (live scanner) and continue; on NO-GO, stop and report.
