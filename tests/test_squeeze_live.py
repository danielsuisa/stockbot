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
