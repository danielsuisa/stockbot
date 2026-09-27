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


if __name__ == "__main__":
    unittest.main()
