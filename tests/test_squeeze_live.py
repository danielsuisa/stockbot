"""Live squeeze list: the owner's screen in messages, journal, stats, /squeeze TICKER and the runs."""
import copy
import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

from bot import squeeze_live
import msgrules
from test_sessions import day, ny

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

    def test_short_shares_for_the_screen_and_the_movers_universe(self):
        res = squeeze_live.build(TODAY)
        self.assertEqual(res["si_date"], "2026-09-15")  # 2026-09-22 is public only from its 8th session
        self.assertEqual(res["short"], {"AAA": 300_000, "BBB": 50_000, "CCC-B": 400_000})  # FINRA "CCCB" -> CCC-B
        self.assertEqual([u["t"] for u in res["universe"]], ["AAA", "CCC-B"])  # 20 %+ of shares: the movers' stocks
        self.assertNotIn("BBB", self.calls)  # 5 % short interest: no Yahoo bars
        self.assertEqual(res["recent"], CAL[-9:] + [TODAY])

    def test_missing_sec_data_raises_instead_of_an_empty_list(self):
        with mock.patch.object(squeeze_live.shorts, "frame", return_value={}), self.assertRaises(RuntimeError):
            squeeze_live.build(TODAY)
        with mock.patch.object(squeeze_live.common, "cik_tickers", return_value={}), self.assertRaises(RuntimeError):
            squeeze_live.build(TODAY)

    def test_missing_calendar_raises(self):
        self.charts["SPY"] = None
        with self.assertRaises(RuntimeError):
            squeeze_live.build(TODAY)


class FramesNow(unittest.TestCase):
    def test_six_quarters_reach_the_400_day_age_limit(self):
        with mock.patch.object(squeeze_live.shorts, "frame", side_effect=lambda y, q: {(y, q): 1}) as f:
            got = squeeze_live.frames_now(dt.date(2026, 10, 2))
        self.assertEqual([c.args for c in f.call_args_list],
                         [(2026, 4), (2026, 3), (2026, 2), (2026, 1), (2025, 4), (2025, 3)])
        self.assertEqual(len(got), 6)


ROW = {"t": "AAA", "float": 31_500_000, "short_float": 0.828, "short_ratio": 7.2, "avg_vol": 3_600_000,
       "rel_vol": 4.7, "price": 30.42, "chg": -0.03, "options": True}
ROW2 = {**ROW, "t": "BBB", "short_float": 0.478, "price": 19.53, "chg": 0.022}
SCR = {"rows": [ROW, ROW2], "screened": 2467, "failed": 0, "no_float": 0, "no_options": 0, "date": TODAY}
PRE_ROW = {"t": "AAA", "float": 31_500_000, "short_float": 0.828, "short_ratio": 7.2, "avg_vol": 3_600_000,
           "pre_vol": 0.15, "price": 31.5, "chg": 0.05, "options": True}
PRE = {"kind": "pre", "rows": [PRE_ROW], "checked": 70, "failed": 2, "no_options": 3, "date": TODAY}
STATIC = {"rows": {"AAA": {"float": 31_500_000, "short_float": 0.828, "short_ratio": 7.2, "avg_vol": 3_600_000}},
          "screened": 2658, "failed": 0, "no_float": 4, "no_options": 8}  # 8 kept in the set and asked again
RES = {"date": TODAY, "prev": "2026-09-25", "si_date": "2026-09-15", "short": {"AAA": 26e6, "BBB": 13e6},
       "recent": CAL[-9:] + [TODAY], "universe": []}
AT = ny(f"{TODAY}T12:30")


class Message(unittest.TestCase):
    def test_the_owners_fields_in_order_rtl_clean(self):
        text = squeeze_live.message(RES, SCR, AT)
        msgrules.check(self, text)
        parts = ["<code>AAA</code>", "<code>30.42$</code>", "<code>3.0%▼</code>", "<code>82.8%</code>", "<code>7.2</code>",
                 "<code>4.7</code>"]  # ticker, price, change, short float, days to cover, rel vol
        at = [text.index(x) for x in parts]
        self.assertEqual(at, sorted(at))
        self.assertLess(text.index("<code>AAA</code>"), text.index("<code>BBB</code>"))  # short float, highest first
        for part in ("<code>19:30</code>", "יום ב׳ <code>28.9.2026</code>", "לא נבדק היסטורית",
                     "מדוח FINRA של <code>15.9</code>"):  # Israel time and date, the FINRA report date
            self.assertIn(part, text)

    def test_nothing_matches(self):
        text = squeeze_live.message(RES, {**SCR, "rows": [], "failed": 50, "no_float": 3, "no_options": 2}, AT)
        self.assertIn("אף מניה לא עומדת", text)
        msgrules.check(self, text)

    def test_the_pre_market_list(self):
        text = squeeze_live.message(RES, PRE, ny(f"{TODAY}T07:31"), STATIC)
        msgrules.check(self, text)
        for part in ("לפני הפתיחה", "<code>14:31</code>", "מחזור לפני הפתיחה <code>15%</code> מיום רגיל",
                     "<code>31.50$</code>", "<code>5.0%▲</code>", "לא נבדק היסטורית"):
            self.assertIn(part, text)
        self.assertNotIn("<code>8</code>", text)

    def test_change_lists_who_entered_and_who_left(self):
        text = squeeze_live.change_text({**SCR, "rows": [ROW2]}, [ROW], AT)
        for part in ("🔄", "<code>BBB</code>", "<code>47.8%</code>", "יצאו: <code>AAA</code>", "<code>19:30</code>"):
            self.assertIn(part, text)
        msgrules.check(self, text)


class Journal(unittest.TestCase):
    def test_record_counts_a_ticker_once_per_window_with_the_next_open_as_entry(self):
        j = {"entries": []}
        self.assertEqual(squeeze_live.record(j, SCR, "2026-09-29", RES), 2)
        self.assertEqual(squeeze_live.record(j, SCR, "2026-09-29", RES), 0)  # the next hourly run
        e = j["entries"][0]
        self.assertEqual((e["date"], e["seen"], e["t"], e["rank"], e["variant"], e["si_date"], e["outcome"]),
                         ("2026-09-29", TODAY, "AAA", 1, squeeze_live.SCREEN, "2026-09-15", None))
        self.assertEqual((e["float"], e["short_float"], e["rel_vol"]), (31_500_000, 0.828, 4.7))

    def test_the_pre_market_list_is_journaled_apart(self):
        j = {"entries": []}
        self.assertEqual(squeeze_live.record(j, PRE, TODAY, RES), 1)
        e = j["entries"][0]
        self.assertEqual((e["date"], e["variant"], e["pre_vol"], e["rel_vol"]), (TODAY, squeeze_live.SCREEN_PRE, 0.15, None))
        self.assertEqual(squeeze_live.record(j, SCR, "2026-09-29", RES), 1)  # AAA already journaled: only BBB

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

    def test_stats_keep_the_screen_and_the_old_list_apart(self):
        new, old = squeeze_live.SCREEN, squeeze_live.NAME
        j = {"entries": [{"variant": new, "outcome": {"trade": True, "hit": True, "crash": False, "r10": 0.2}},
                         {"variant": new, "outcome": {"trade": True, "hit": False, "crash": True, "r10": -0.4}},
                         {"variant": new, "outcome": None},
                         {"variant": old, "outcome": {"trade": True, "hit": False, "crash": False, "r10": 0.0}}]}
        j["entries"].append({"variant": squeeze_live.SCREEN_PRE,
                             "outcome": {"trade": True, "hit": True, "crash": True, "r10": 0.1}})
        text = squeeze_live.stats_text(j)
        self.assertIn("לפני הפתיחה", text)
        screen_part, old_part = text.split("השיטה הקודמת")
        self.assertIn("<code>50%</code>", screen_part)
        self.assertIn("לא נבדק", screen_part)
        self.assertIn("<code>0%</code>", old_part)
        self.assertIn("<code>6.8%</code>", old_part)  # the old list's backtest, for its own numbers only
        msgrules.check(self, text)
        self.assertIn("עוד אין", squeeze_live.stats_text({"entries": [{"variant": new, "outcome": None}]}))


M_PASS = {"float": 31_500_000, "short_float": 0.828, "short_ratio": 7.2, "avg_vol": 3_600_000, "rel_vol": 4.7,
          "price": 30.42, "chg": -0.03, "options": True}


class TickerText(unittest.TestCase):
    def test_every_criterion_with_its_value(self):
        ok = squeeze_live.ticker_text("AAA", si(26_000_000), M_PASS, rank=2)
        self.assertEqual(ok.count("✅"), 8)  # 7 criteria + the verdict
        self.assertIn("מקום <code>2</code>", ok)
        bad = squeeze_live.ticker_text("AAA", si(26_000_000), {**M_PASS, "float": 60e6, "rel_vol": 1.2,
                                                               "options": False})
        self.assertEqual(bad.count("❌"), 4)  # 3 criteria + the verdict
        for part in ("<code>60</code> מיליון", "פי <code>1.2</code>", "לא עומדת ב־<code>3</code>"):
            self.assertIn(part, bad)
        none = squeeze_live.ticker_text("ZZZ", None, squeeze_live.screen.metrics(None, None))
        self.assertIn("אין נתון ב־FINRA", none)
        self.assertIn("חסר", none)
        for text in (ok, bad, none):
            msgrules.check(self, text)
            self.assertIn("לא נבדק היסטורית", text)


class TickerReport(unittest.TestCase):
    def test_glue(self):
        q = {"price": 30.42, "chg": -0.03, "vol": 16_920_000, "avg": 3_600_000, "date": TODAY}
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SQUEEZE_DIR": d}), \
                mock.patch.object(squeeze_live.sessions, "now_ny", return_value=AT), \
                mock.patch.object(squeeze_live.market, "chart", side_effect=lambda t, **p: chart(CAL)), \
                mock.patch.object(squeeze_live.shorts, "symbol_rows",
                                  return_value=[si(26_000_000, "2026-09-15"), si(1, "2026-09-22")]) as rows, \
                mock.patch.object(squeeze_live.screen, "connect", return_value="GET"), \
                mock.patch.object(squeeze_live.screen, "quotes", return_value={"BRK-B": q}) as quotes, \
                mock.patch.object(squeeze_live.screen, "float_shares", return_value=31_500_000), \
                mock.patch.object(squeeze_live.screen, "optionable", return_value=True):
            squeeze_live.save(squeeze_live.LAST, {"date": TODAY, "rows": [{"t": "X"}, {"t": "BRK-B"}]})
            text = squeeze_live.ticker_report("BRK-B")
        rows.assert_called_once_with("BRKB")
        quotes.assert_called_once_with("GET", ["BRK-B"])
        self.assertIn("<code>26</code> מיליון מניות", text)  # the 2026-09-22 report is not public yet
        self.assertIn("<code>82.5%</code>", text)  # 26M short / 31.5M float
        self.assertIn("מקום <code>2</code>", text)
        msgrules.check(self, text)

    def test_yahoo_down_still_answers(self):
        with mock.patch.object(squeeze_live.sessions, "now_ny", return_value=AT), \
                mock.patch.object(squeeze_live.market, "chart", side_effect=lambda t, **p: chart(CAL)), \
                mock.patch.object(squeeze_live.shorts, "symbol_rows", return_value=[]), \
                mock.patch.object(squeeze_live.screen, "connect", side_effect=OSError("down")), \
                mock.patch.object(squeeze_live.screen, "optionable", return_value=None), mock.patch("builtins.print"):
            text = squeeze_live.ticker_report("GME")
        self.assertIn("Yahoo", text)
        msgrules.check(self, text)


UNI = [{"t": "AAA", "si_pct": 0.3, "dtc": 3.0, "adv": 1_000_000.0,
        "bars": {"d": ["2026-09-25", "2026-09-28"], "c": [9.0, 10.0]}}]
LIVE = dict(RES, universe=UNI)
AAA_UP = {"AAA": {"price": 11.5, "volume": 300_000.0, "date": "2026-09-28"}}


class Sessions(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def run_at(self, when, res=LIVE, argv=(), quotes=AAA_UP, send=None, scr=SCR, pre=PRE, cands=STATIC):
        """One squeeze_live.main run at New York time `when`, with everything but its logic mocked."""
        build = res if callable(res) else mock.Mock(return_value=copy.deepcopy(res))
        mk = lambda x: x if callable(x) else mock.Mock(return_value=copy.deepcopy(x))  # noqa: E731
        self.scr, self.pre, self.cands = mk(scr), mk(pre), mk(cands)
        with mock.patch.dict(os.environ, {"SQUEEZE_DIR": self.dir, "SQUEEZE_MODE": ""}), \
                mock.patch.object(squeeze_live.sessions, "now_ny", return_value=ny(when)), \
                mock.patch.object(squeeze_live.sessions, "status", return_value=day()), \
                mock.patch.object(squeeze_live, "build", build), \
                mock.patch.object(squeeze_live.screen, "connect", return_value="GET"), \
                mock.patch.object(squeeze_live.screen, "screen", self.scr), \
                mock.patch.object(squeeze_live.screen, "candidates", self.cands), \
                mock.patch.object(squeeze_live.screen, "premarket", self.pre), \
                mock.patch.object(squeeze_live, "followup", return_value=0), \
                mock.patch.object(squeeze_live.movers, "quotes", return_value=quotes) as self.quotes, \
                mock.patch.object(squeeze_live.common, "send", side_effect=send) as sent, mock.patch("builtins.print"):
            code = squeeze_live.main(list(argv))
        self.sources = [c.kwargs.get("source") for c in sent.call_args_list]
        return code, [c.args[0] for c in sent.call_args_list], build

    def file(self, name):
        with mock.patch.dict(os.environ, {"SQUEEZE_DIR": self.dir}):
            return squeeze_live.load(name, {})

    def test_hourly_runs_send_the_full_list_once_a_day_then_only_changes_and_no_movers(self):
        code, sent, build = self.run_at("2026-09-28T10:31")
        self.assertEqual((code, len(sent)), (0, 1))
        build.assert_called_once_with("2026-09-28")
        self.scr.assert_called_once_with(LIVE["short"], "GET")
        self.assertIn("רשימת סקוויז", sent[0])
        self.assertEqual(self.sources, [squeeze_live.SOURCE])
        msgrules.check(self, sent[0])
        self.assertFalse(self.quotes.called)  # movers stay in the extended sessions (and manual runs)
        self.assertEqual(self.run_at("2026-09-28T11:31")[1], [])  # same names: nothing
        sent = self.run_at("2026-09-28T12:31", scr={**SCR, "rows": [ROW2]})[1]
        self.assertEqual(len(sent), 1)
        self.assertIn("יצאו: <code>AAA</code>", sent[0])
        msgrules.check(self, sent[0])
        last = self.file(squeeze_live.LAST)
        self.assertEqual((last["date"], [r["t"] for r in last["rows"]]), (TODAY, ["BBB"]))
        self.assertIn("<code>BBB</code>", last["text"])  # /squeeze shows the full current list
        journal = self.file(squeeze_live.JOURNAL)["entries"]
        self.assertEqual([(e["t"], e["date"]) for e in journal], [("AAA", "2026-09-29"), ("BBB", "2026-09-29")])

    def test_after_close_list_changes_and_movers_then_nothing_repeats(self):
        self.run_at("2026-09-28T15:31")
        code, sent, _ = self.run_at("2026-09-28T16:35", scr={**SCR, "rows": [ROW, ROW2, {**ROW, "t": "CCC"}]})
        self.assertEqual(len(sent), 2)
        self.assertIn("🔄", sent[0])
        self.assertIn("🌙", sent[1])
        self.assertIn("ב־<code>15%</code>", sent[1])  # 11.5 against today's 10.0 close
        self.assertEqual(self.sources, [squeeze_live.SOURCE] * 2)
        for t in sent:
            msgrules.check(self, t)
        self.assertEqual(self.file(squeeze_live.MOVERS_LOG)["entries"][0]["t"], "AAA")
        self.assertEqual(self.run_at("2026-09-28T17:30")[1], [])  # the same slot again (daylight-saving twin)
        self.assertEqual(self.run_at("2026-09-28T19:35", scr={**SCR, "rows": [ROW, ROW2, {**ROW, "t": "CCC"}]})[1], [])

    def test_pre_market_runs_hourly_list_then_changes_with_the_fixed_set_once_a_day(self):
        code, sent, _ = self.run_at("2026-09-28T07:31")
        self.assertEqual((code, len(sent)), (0, 2))
        self.assertIn("לפני הפתיחה", sent[0])
        self.assertIn("🌅", sent[1])  # the movers too
        self.scr.assert_not_called()  # today's relative volume means nothing before the open
        self.cands.assert_called_once_with(LIVE["short"], "GET")
        static, pre_quote, day_ = self.pre.call_args[0]
        self.assertEqual((static["rows"], day_), (STATIC["rows"], TODAY))
        with mock.patch.object(squeeze_live.movers, "quote", return_value="Q") as q:
            self.assertEqual(pre_quote("AAA"), "Q")
        q.assert_called_once_with("AAA", "pre")
        self.assertEqual(self.run_at("2026-09-28T08:16")[1], [])  # same names, same move: nothing
        self.cands.assert_not_called()  # the fixed set is computed once a day
        sent = self.run_at("2026-09-28T09:16", pre={**PRE, "rows": []})[1]
        self.assertEqual(len(sent), 1)
        self.assertIn("יצאו: <code>AAA</code>", sent[0])
        msgrules.check(self, sent[0])
        journal = self.file(squeeze_live.JOURNAL)["entries"]
        self.assertEqual([(e["t"], e["date"], e["variant"]) for e in journal], [("AAA", TODAY, squeeze_live.SCREEN_PRE)])
        sent = self.run_at("2026-09-28T10:31")[1]  # the first regular-session list of the day goes out in full
        self.assertIn("🚀", sent[0])
        self.assertNotIn("🔄", sent[0])

    def test_the_first_list_after_the_switch_goes_out_in_full(self):
        with mock.patch.dict(os.environ, {"SQUEEZE_DIR": self.dir}):  # the replaced method's last list, same date
            squeeze_live.save(squeeze_live.LAST, {"date": TODAY, "text": "OLD", "rows": [{"t": "HTZ"}]})
        sent = self.run_at("2026-09-28T13:31")[1]
        self.assertIn("🚀", sent[0])
        self.assertNotIn("🔄", sent[0])

    def test_manual_before_the_open_sends_the_pre_market_list(self):
        code, sent, _ = self.run_at("2026-09-28T06:00", argv=["--manual"])
        self.assertIn("לפני הפתיחה", sent[0])
        self.assertEqual(self.sources, [squeeze_live.SOURCE] * len(sent))
        msgrules.check(self, sent[0])
        self.scr.assert_not_called()

    def test_the_fixed_set_failing_warns_once_per_session(self):
        down = mock.Mock(side_effect=RuntimeError("Yahoo: no quotes"))
        sent = self.run_at("2026-09-28T07:31", cands=down, quotes={})[1]
        self.assertEqual(len(sent), 1)
        self.assertIn("Yahoo", sent[0])
        self.assertEqual(self.run_at("2026-09-28T08:16", cands=down, quotes={})[1], [])

    def test_manual_at_night_sends_the_full_list_and_says_the_market_is_closed(self):
        self.run_at("2026-09-28T16:35", quotes={})
        code, sent, _ = self.run_at("2026-09-28T21:30", argv=["--manual"])
        self.assertEqual((code, len(sent)), (0, 2))
        self.assertIn("🚀", sent[0])  # a manual run sends the full list again, not the changes
        self.assertIn("רשימת סקוויז", sent[0])
        self.assertIn("סגור", sent[1])
        self.assertEqual(self.sources, [squeeze_live.SOURCE] * 2)
        for t in sent:
            msgrules.check(self, t)

    def test_manual_during_the_session_shows_live_movers(self):
        live = {"AAA": {"price": 10.5, "volume": 2e6, "date": "2026-09-28"}}
        code, sent, build = self.run_at("2026-09-28T11:00", argv=["--manual"], quotes=live)
        build.assert_called_once_with("2026-09-28")
        self.assertIn("רשימת סקוויז", sent[0])
        self.assertIn("📈", sent[1])
        self.assertIn("ב־<code>17%</code>", sent[1])  # against Friday's 9.0 close
        for t in sent:
            msgrules.check(self, t)

    def test_failed_build_warns_once_per_session_and_manual_always(self):
        boom = mock.Mock(side_effect=RuntimeError("FINRA: down"))
        code, sent, _ = self.run_at("2026-09-28T16:35", res=boom)
        self.assertEqual(code, 0)  # reported here; exit 1 would add the workflow alarm on every run of an outage
        self.assertIn("לא רצה", sent[0])
        self.assertEqual(self.sources, [squeeze_live.SOURCE])
        msgrules.check(self, sent[0])
        self.assertEqual(self.run_at("2026-09-28T19:35", res=boom)[1], [])
        self.assertIn("לא רצה", self.run_at("2026-09-28T19:50", res=boom, argv=["--manual"])[1][0])

    def test_yahoo_down_warns_once_per_session_and_the_movers_still_go_out(self):
        down = mock.Mock(side_effect=RuntimeError("Yahoo: no crumb"))
        sent = self.run_at("2026-09-28T16:35", scr=down)[1]
        self.assertEqual(len(sent), 2)
        self.assertIn("Yahoo", sent[0])
        self.assertIn("🌙", sent[1])
        msgrules.check(self, sent[0])
        self.assertEqual(self.run_at("2026-09-28T19:35", scr=down, quotes={})[1], [])
        self.assertEqual(self.file(squeeze_live.LAST), {})  # no list was made: nothing saved as one

    def test_a_crash_after_the_list_went_out_never_resends_it(self):
        with self.assertRaises(RuntimeError):
            self.run_at("2026-09-28T16:35", send=[None, RuntimeError("telegram down")])  # the movers message fails
        self.assertEqual(self.file(squeeze_live.LAST).get("date"), TODAY)
        self.assertEqual(self.run_at("2026-09-28T17:30")[1], [])  # the twin: slot already ran, list already sent

    def test_nasdaq_down_warns_once_per_session(self):
        down = {"AAA": False}
        self.assertTrue(any("Nasdaq" in t for t in self.run_at("2026-09-28T16:35", quotes=down)[1]))
        self.assertFalse(any("Nasdaq" in t for t in self.run_at("2026-09-28T19:35", quotes=down)[1]))

    def test_dry_run_writes_and_sends_nothing(self):
        code, sent, _ = self.run_at("2026-09-28T16:35", argv=["--dry"])
        self.assertEqual((code, sent, os.listdir(self.dir)), (0, [], []))

    def test_outside_a_slot_does_nothing(self):
        code, sent, build = self.run_at("2026-09-28T03:00")
        build.assert_not_called()
        self.assertEqual((code, sent), (0, []))


class Command(unittest.TestCase):
    def test_squeeze_last_list_stats_and_ticker(self):
        from bot import listen
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SQUEEZE_DIR": d}), \
                mock.patch.object(listen.common, "send") as send, \
                mock.patch.object(listen.common, "tickers", return_value={"GME": (1, "GameStop")}), \
                mock.patch.object(listen.squeeze_live, "ticker_report", return_value="REPORT") as rep:
            listen.handle("/squeeze")
            empty = send.call_args[0][0]
            self.assertIn("עוד אין רשימת סקוויז", empty)
            self.assertIn("<code>/squeeze now</code>", empty)  # the way to get one right away, at any hour
            self.assertNotIn("לפני הפתיחה", empty)  # the list is built after the close since the extended-hours runs
            msgrules.check(self, empty)
            self.assertEqual(send.call_args.kwargs["source"], squeeze_live.SOURCE)
            squeeze_live.save(squeeze_live.LAST, {"date": TODAY, "text": "LIST", "rows": []})
            listen.handle("/squeeze")
            self.assertEqual(send.call_args[0][0], "LIST")
            listen.handle("/squeeze stats")
            self.assertIn("תוצאות רשימת הסקוויז", send.call_args[0][0])
            msgrules.check(self, send.call_args[0][0])
            self.assertEqual(send.call_args.kwargs["source"], squeeze_live.SOURCE)
            listen.handle("/squeeze gme")
            rep.assert_called_once_with("GME")
            self.assertEqual(send.call_args, mock.call("REPORT", signal=True, source=squeeze_live.SOURCE))
            listen.handle("/squeeze zzzz")
            self.assertIn("לא מצאתי", send.call_args[0][0])
        self.assertIn("/squeeze", listen.HELP)
        self.assertIn("squeeze", [c for c, _ in listen.COMMANDS])


    def test_squeeze_now_starts_the_workflow(self):
        from bot import listen
        env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}
        with mock.patch.dict(os.environ, env), mock.patch.object(listen.common, "send") as send, \
                mock.patch.object(listen.common, "dispatch", return_value="") as disp:
            listen.handle("/squeeze now")
        disp.assert_called_once_with("squeeze.yml", {"mode": "manual"})
        self.assertIn("מתעדכנת", send.call_args[0][0])
        self.assertEqual(send.call_args.kwargs["source"], squeeze_live.SOURCE)
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

if __name__ == "__main__":
    unittest.main()
