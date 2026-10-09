"""Forward shadow tracker of ZBA K10s: one mocked session end to end, day selection, the message and the run."""
import datetime as dt
import os
import pathlib
import re
import tempfile
import unittest
from unittest import mock

from bot import common, shadow
from test_ticket_backtest import flat, run, sessions_before, zba_bars

DAY = "2024-03-01"
SESSIONS = sessions_before(DAY, 40) + [DAY]
HOURS = {d: ("09:30", "16:00") for d in SESSIONS}
ASSETS = [{"symbol": "AAA", "exchange": "NYSE", "name": "A Corp", "status": "active"},
          {"symbol": "BBB", "exchange": "NASDAQ", "name": "B Inc", "status": "active"},
          {"symbol": "OLD", "exchange": "NYSE", "name": "Old Corp", "status": "inactive"},
          {"symbol": "SPY", "exchange": "ARCA", "name": "SPDR S&P 500 ETF Trust", "status": "active"}]
DAILY = [(d, 9.9, 10.4, 9.4, 9.9, 3_000_000) for d in sessions_before(DAY, 40)]  # ATR14 1.00, 3M a day
FELL = DAILY[:-1] + [DAILY[-1][:3] + (8.0, 9.9, 3_000_000)]  # BBB: the session before fell 19% intraday -> Rule 201


def bbb_bars():
    """A falling first candle (10.00 -> 9.60, low 9.50), the low taken out at 09:40, then a drift down."""
    first = run("09:30", [(10.0, 10.0, 9.5, 9.9), (9.9, 9.9, 9.8, 9.8), (9.8, 9.8, 9.7, 9.7), (9.7, 9.7, 9.65, 9.7),
                          (9.7, 9.7, 9.6, 9.6)])
    return first + flat("09:35", "09:40", 9.6) + run("09:40", [(9.6, 9.6, 9.4, 9.45)]) + flat("09:41", "16:00", 9.2)


def opening(syms, d):
    bar = {"AAA": (10.0, 10.5, 9.9, 10.4), "BBB": (10.0, 10.0, 9.5, 9.6)}
    return {s: bar[s] + (3000 if d == DAY else 1000,) for s in syms}


class Session(unittest.TestCase):
    def run_day(self):
        minutes = {"AAA": zba_bars(), "BBB": bbb_bars()}
        daily = {"AAA": DAILY, "BBB": FELL}
        with mock.patch.object(shadow.alpaca, "assets", return_value=ASSETS), \
                mock.patch.object(shadow.alpaca, "daily", side_effect=lambda syms, a, b: {
                    s: [x for x in daily[s] if a <= x[0] <= b] for s in syms if s in daily}) as d, \
                mock.patch.object(shadow.alpaca, "opening_bars", side_effect=opening), \
                mock.patch.object(shadow.alpaca, "minute_bars", side_effect=lambda syms, _: {s: minutes[s] for s in syms}), \
                mock.patch.object(shadow.alpaca, "quote_at", return_value=(10.00, 10.02)), \
                mock.patch.object(shadow.alpaca, "trades", return_value=[("a", 10.45), ("b", 10.50), ("c", 10.52)]):
            res = shadow.run_day(DAY, HOURS, SESSIONS)
        return res, d

    def test_the_backtests_code_end_to_end(self):
        res, daily = self.run_day()
        self.assertEqual(res["top"], ["AAA", "BBB"])
        self.assertEqual(sorted(daily.call_args_list[0].args[0]), ["AAA", "BBB", "OLD"])  # inactive too, like the backtest
        self.assertEqual(daily.call_args_list[0].args[2], DAY)  # D is complete: no recent SIP, only bars before D are used
        self.assertEqual(len(res["optimistic"]), 2)  # both filled under the bar model ...
        self.assertEqual(res["dropped"], {"Rule 201": 1})  # ... the short is blocked once Rule 201 is applied
        (x,) = res["trades"]
        # buy stop 10.50 triggered by the 10.50 trade at 09:40, filled at the next trade 10.52; D = 0.10 x ATR 1.00
        self.assertEqual((x["t"], x["side"], x["at"], x["level"], x["fill"], x["R"], x["shares"], x["kinds"]),
                         ("AAA", 1, "09:40", 10.5, 10.52, 0.1, 1000, "eod"))
        self.assertAlmostEqual(x["gross_r"], (10.8 - 10.52) / 0.1, places=3)
        self.assertAlmostEqual(x["net_r"], x["gross_r"] - (2 * 3.5 + 1000 * 0.02) / 100, places=3)

    def test_the_message_of_a_computed_session(self):
        res, _ = self.run_day()
        text = shadow.message(DAY, res, {"v": 1, "since": DAY, "days": {DAY: res}})
        self.assertEqual(common.rtl_bad_lines(text), [], text)
        for part in ("🟢 <b>קנייה: <code>AAA</code></b>", "כניסה בפריצה: <code>10.52$</code> בשעה <code>16:40</code>",
                     "סטופ: <code>10.42$</code> · כמות: <code>1000</code> מניות", "נסגרה בסוף היום ב־<code>10.80$</code>",
                     "חסימת מכירה בחסר אחרי ירידה של <code>10%</code>", "מדגם קטן מדי למסקנה"):
            self.assertIn(part, text)

    def test_a_session_without_15_sessions_before_it_is_an_error(self):
        with self.assertRaises(ValueError):
            shadow.run_day(SESSIONS[14], HOURS, SESSIONS)


D8 = "2026-10-08"  # a Thursday; New York and Israel both on summer time (+7 h)
PLTR = {"t": "PLTR", "rank": 1, "side": 1, "at": "09:35", "level": 203.4, "R": 0.55, "shares": 181, "spread": 0.02,
        "fill": 203.47, "kinds": "stop", "gross_r": (202.90 - 203.47) / 0.55, "cost_r": 0.17, "net_r": -1.21}
APLD = {"t": "APLD", "rank": 2, "side": -1, "at": "09:36", "level": 23.77, "R": 0.15, "shares": 647, "spread": 0.01,
        "fill": 23.76, "kinds": "eod", "gross_r": (23.76 - 23.50) / 0.15, "cost_r": 0.07, "net_r": 1.66}
FIXTURE = {"top": ["T%d" % i for i in range(20)], "reasons": {"spread": 15, "trigger": 3}, "dropped": {},
           "trades": [PLTR, APLD], "optimistic": [PLTR, APLD], "gaps": 0}
EXPECTED = """👻 <b>מעקב צל: פריצת הפתיחה</b>
יום ה׳ <code>8.10.2026</code> · מעקב בלבד, לא נשלחה פקודה

🟢 <b>קנייה: <code>PLTR</code></b>
כניסה בפריצה: <code>203.47$</code> בשעה <code>16:35</code>
סטופ: <code>202.92$</code> · כמות: <code>181</code> מניות
תוצאה: נעצרה בסטופ ב־<code>202.90$</code>, הפסד של <code>121$</code>

🔴 <b>מכירה בחסר: <code>APLD</code></b>
כניסה בשבירה: <code>23.76$</code> בשעה <code>16:36</code>
סטופ: <code>23.91$</code> · כמות: <code>647</code> מניות
תוצאה: נסגרה בסוף היום ב־<code>23.50$</code>, רווח של <code>166$</code>

📋 מתוך <code>20</code> המניות הפעילות בפתיחה, <code>15</code> נפסלו בגלל מרווח רחב, וב־<code>3</code> לא הייתה פריצה.

📒 <b>מצטבר מאז <code>8.10</code>:</b> <code>2</code> עסקאות · רווח כולל של <code>45$</code> · <code>1</code> מוצלחות
מדגם קטן מדי למסקנה. ההכרעה תהיה אחרי <code>100</code> עסקאות ו־<code>60</code> ימי מסחר.

⚠️ מעקב בלבד, לא כרטיס פקודה ולא ייעוץ השקעות.
מקור: נתוני <code>Alpaca</code> (מושהים ב־<code>15</code> דקות)"""


def clean(test, text):
    """The owner's rules for every shadow message."""
    test.assertEqual(common.rtl_bad_lines(text), [], text)
    test.assertIsNone(re.search(r"\d[\d.,]*\$?-", text), text)  # no minus flipped behind a number
    test.assertIsNone(re.search(r"\d(\.\d+)?R\b", text), text)  # money, never R
    test.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", text), text)  # Israeli dates, never ISO
    test.assertNotIn("SEC", text)
    test.assertNotIn("EDGAR", text)


def sent(text, **kw):
    with mock.patch.dict(os.environ, {"TG_TOKEN": "", "TG_CHAT_ID": "", "GITHUB_ACTIONS": ""}), \
            mock.patch("builtins.print") as out:
        common.send(text, **kw)
    return [c.args[0].rstrip("\n") for c in out.call_args_list]


class Message(unittest.TestCase):
    def test_the_approved_template_line_by_line(self):
        (m,) = sent(shadow.message(D8, FIXTURE, {"v": 1, "since": D8, "days": {D8: FIXTURE}}), source=shadow.SOURCE)
        self.assertEqual(m.split("\n"), EXPECTED.split("\n"))
        clean(self, m)

    def test_a_day_without_trades(self):
        day = dict(FIXTURE, trades=[], reasons={"trigger": 12, "doji": 2, "no atr": 1, "size below minimum": 1,
                                                 "weird": 2}, dropped={"Rule 201": 2})
        text = shadow.message(D8, day, {"v": 1, "since": D8, "days": {D8: day}})
        clean(self, text)
        self.assertIn("אף עסקה לא נפתחה היום.", text)
        for part in ("ב־<code>12</code> לא הייתה פריצה, ", "ב־<code>2</code> הייתה חסימת מכירה בחסר אחרי ירידה של "
                     "<code>10%</code>", "נר הפתיחה היה ניטרלי", "חסרה היסטוריה",
                     ", וב־<code>1</code> הפוזיציה יצאה קטנה מדי.", "ב־<code>2</code>: <code>weird</code>",
                     "עוד אין עסקאות"):
            self.assertIn(part, text)
        self.assertNotIn("🟢", text)

    def test_the_tally_hides_t_below_30_trades(self):
        def journal(n):
            days = {}
            for i in range(n):
                d = (dt.date(2026, 10, 8) + dt.timedelta(i)).isoformat()
                days[d] = dict(FIXTURE, trades=[dict(PLTR, net_r=(1.5 if i % 3 else -1.0) + i / 100)])
            return {"v": 1, "since": D8, "days": days}
        few = shadow.message(D8, FIXTURE, journal(29))
        self.assertNotIn("t=", few)
        self.assertNotIn("ממוצע", few)
        self.assertIn("מדגם קטן מדי למסקנה", few)
        many = shadow.message(D8, FIXTURE, journal(30))
        self.assertRegex(many, r"מובהקות t=<code>-?\d+\.\d\d</code>")
        self.assertIn("ממוצע לעסקה", many)
        self.assertNotIn("מדגם קטן מדי", many)
        self.assertIn("ההכרעה תהיה אחרי <code>100</code> עסקאות", many)
        clean(self, few)
        clean(self, many)

    def test_israel_time_in_summer_winter_and_the_gap_weeks(self):
        self.assertEqual(shadow.il_time("2026-07-15", "09:35"), "16:35")
        self.assertEqual(shadow.il_time("2026-12-15", "09:35"), "16:35")
        self.assertEqual(shadow.il_time("2026-10-26", "09:35"), "15:35")  # Israel back on winter time, New York not yet

    def test_israeli_dates(self):
        self.assertEqual(shadow.il_date("2026-10-08"), "8.10.2026")
        self.assertEqual(shadow.il_date("2026-10-08", year=False), "8.10")
        self.assertEqual([shadow.weekday(f"2026-10-{d:02d}") for d in range(4, 11)],
                         ["א׳", "ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳"])

    def test_other_messages_keep_the_sec_footer(self):
        with mock.patch.dict(common.STAMPS, clear=True):  # nothing stamped: no freshness line at all
            (m,) = sent("🚀 סקוויז")
            self.assertTrue(m.endswith(f"\n\n{common.DISCLAIMER}"))
            self.assertIn("SEC EDGAR", m)
            self.assertNotIn("נתונים עדכניים", m)
            common.stamp("sec")  # a source answered: its freshness line sits between the text and the disclaimer
            (m,) = sent("🚀 סקוויז")
            self.assertIn(common.freshness(), m)
            self.assertTrue(m.endswith(f"\n\n{common.freshness()}\n\n{common.DISCLAIMER}"))
            self.assertIn("🕒 נתונים עדכניים ל: SEC <code>", m)


class Pending(unittest.TestCase):
    S = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]

    def j(self, *days):
        return {"v": 1, "since": None, "days": {d: {} for d in days}}

    def test_first_run_takes_only_the_latest_completed_session(self):
        self.assertEqual(shadow.pending(self.S, self.j(), "2026-10-09"), ["2026-10-08"])  # today's is not complete

    def test_catch_up_after_the_last_processed_session_at_most_five(self):
        self.assertEqual(shadow.pending(self.S, self.j("2026-10-05"), "2026-10-09"),
                         ["2026-10-06", "2026-10-07", "2026-10-08"])
        self.assertEqual(shadow.pending(self.S, self.j("2026-09-01"), "2026-10-10"), self.S[:5])
        self.assertEqual(shadow.pending(self.S, self.j("2026-10-08"), "2026-10-09"), [])  # nothing twice


class Main(unittest.TestCase):
    RES = {"top": [], "reasons": {}, "dropped": {}, "trades": [], "optimistic": [], "gaps": 0}

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for p in (mock.patch.dict(os.environ, {"SHADOW_DIR": d.name, "SHADOW_MODE": ""}),
                  mock.patch.object(shadow.alpaca, "calendar", return_value=HOURS), mock.patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)

    def main(self, *argv, today="2024-03-02"):
        with mock.patch.object(shadow, "run_day", return_value=dict(self.RES)) as rd, \
                mock.patch.object(shadow.common, "send") as send, mock.patch.object(shadow, "today_ny", return_value=today), \
                mock.patch.object(shadow, "live", return_value="👻 היום") as self.live:
            self.assertEqual(shadow.main(list(argv)), 0)
        self.assertTrue(all(c.kwargs.get("source") == shadow.SOURCE for c in send.call_args_list))  # Alpaca footer
        return [c.args[0] for c in rd.call_args_list], [c.args[0] for c in send.call_args_list]

    def test_a_day_is_sent_once_and_recorded(self):
        days, sent = self.main()
        self.assertEqual((days, len(sent)), ([DAY], 1))
        j = shadow.load()
        self.assertEqual((j["since"], list(j["days"])), (DAY, [DAY]))
        self.assertEqual(self.main(), ([], []))  # the next scheduled run: silence
        days, sent = self.main("--manual")  # /shadow: nothing new to journal, today's live view
        self.assertEqual((days, sent), ([], ["👻 היום"]))
        self.assertEqual(list(shadow.load()["days"]), [DAY])  # the live view never touches the journal
        self.live.assert_called_once()

    def test_the_calendar_reaches_back_before_the_last_processed_session(self):
        shadow.save({"v": 1, "since": "2023-11-01", "days": {"2023-11-01": dict(self.RES)}})
        with mock.patch.object(shadow.alpaca, "calendar", return_value=HOURS) as cal:
            self.main()
        self.assertEqual(cal.call_args.args, ("2023-08-03", "2024-03-02"))  # 90 days before the last one, up to today

    def test_dry_run_writes_nothing(self):
        days, sent = self.main("--dry")
        self.assertEqual((days, sent), ([DAY], []))
        self.assertFalse(shadow.path().exists())


class Command(unittest.TestCase):
    """/shadow in Telegram: like /squeeze now, the workflow is started on Actions (the only writer of the journal)."""

    def test_shadow_starts_the_workflow(self):
        from bot import listen
        env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}
        with mock.patch.dict(os.environ, env), mock.patch.object(listen.common, "send") as send, \
                mock.patch.object(listen.common, "dispatch", return_value="") as disp:
            self.assertEqual(listen.handle("/shadow"), 0)
        disp.assert_called_once_with("shadow.yml", {"mode": "manual"})
        self.assertIn("מעקב הצל", send.call_args[0][0])
        self.assertEqual(send.call_args.kwargs["source"], shadow.SOURCE)
        clean(self, send.call_args[0][0])
        with mock.patch.dict(os.environ, env), mock.patch.object(listen.common, "send") as send, \
                mock.patch.object(listen.common, "dispatch", return_value="HTTP 403"), mock.patch("builtins.print"):
            self.assertEqual(listen.handle("/shadow"), 1)
        self.assertIn("HTTP 403", send.call_args[0][0])
        clean(self, send.call_args[0][0])
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "", "GITHUB_TOKEN": ""}), \
                mock.patch.object(listen.common, "send"), mock.patch.object(listen.shadow, "main", return_value=0) as run:
            listen.handle("/shadow")
        run.assert_called_once_with(["--manual"])
        self.assertIn("/shadow", listen.HELP)
        self.assertIn("shadow", [c for c, _ in listen.COMMANDS])


LIVE_DAY = "2026-10-09"  # a Friday, summer time in both cities (+7 h)
LIVE_SESSIONS = sessions_before(LIVE_DAY, 40) + [LIVE_DAY]
LIVE_HOURS = {d: ("09:30", "16:00") for d in LIVE_SESSIONS}


def ny_at(hhmm, day=LIVE_DAY):
    return dt.datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=shadow.alpaca.NY)


class Live(unittest.TestCase):
    """/shadow during the day: the pre-market watch list, then today's trades so far; never the journal."""

    def test_no_trading_today_and_too_early(self):
        text = shadow.live(ny_at("10:00", "2026-10-10"), LIVE_HOURS, LIVE_SESSIONS)  # a Saturday
        self.assertIn("אין מסחר", text)
        clean(self, text)
        text = shadow.live(ny_at("04:10"), LIVE_HOURS, LIVE_SESSIONS)  # pre-market data from 04:15 New York
        self.assertIn("מ־<code>11:15</code>", text)
        clean(self, text)

    def test_pre_market_watch_list(self):
        daily = {"AAA": DAILY, "BBB": DAILY}
        pre = {"AAA": {"atr": 1.0, "avg_vol": 3_000_000}, "BBB": {"atr": 1.0, "avg_vol": 3_000_000}}
        with mock.patch.object(shadow.alpaca, "assets", return_value=ASSETS), \
                mock.patch.object(shadow.alpaca, "daily", side_effect=lambda syms, a, b: {s: daily[s] for s in syms if s in daily}), \
                mock.patch.object(shadow.tb, "prefilter", return_value=pre), \
                mock.patch.object(shadow.alpaca, "volume_since", return_value={"AAA": (150_000, 10.4), "BBB": (600_000, 9.4)}) as vol:
            text = shadow.live(ny_at("08:15"), LIVE_HOURS, LIVE_SESSIONS)
        vol.assert_called_once_with(["AAA", "BBB"], LIVE_DAY, "04:00", "08:00")  # 15 minutes behind
        self.assertIn("לפני הפתיחה", text)
        self.assertIn("נכון ל־<code>15:00</code>", text)  # 08:00 New York in Israel time
        self.assertIn("ב־<code>16:35</code>", text)  # when the method picks
        self.assertLess(text.index("<code>BBB</code>"), text.index("<code>AAA</code>"))  # 20% of a day before 5%
        self.assertIn("<code>5.1%▲</code>", text)  # AAA 10.40 against the 9.90 close
        self.assertIn("נפח <code>20%</code> מיום רגיל", text)
        self.assertEqual(shadow.alpaca.CUTOFF, {})
        clean(self, text)

    def test_during_the_session_open_and_closed_trades(self):
        closed = dict(PLTR, at="09:40")
        running = dict(APLD, at="10:05", kinds="eod", gross_r=(23.76 - 23.60) / 0.15, net_r=0.95)
        res = dict(FIXTURE, trades=[closed, running])
        seen = {}

        def run_day(day, hours, sessions, until=None):
            seen["until"] = until
            return {**res, "trades": [dict(x) for x in res["trades"]]}
        with mock.patch.object(shadow, "run_day", side_effect=run_day):
            # the real run_day marks a still-running "eod" trade open; here the mock gets the flag from the wrapper
            text = shadow.live(ny_at("11:20"), LIVE_HOURS, LIVE_SESSIONS)
        self.assertEqual(seen["until"], "11:05")
        self.assertIn("בזמן אמת", text)
        self.assertIn("לא נשמר ביומן", text)
        self.assertIn("נסגרת בסוף היום, ב־<code>23:00</code>", text)
        clean(self, text)

    def test_live_message_open_trade(self):
        running = dict(APLD, kinds="eod", gross_r=(23.76 - 23.60) / 0.15, net_r=0.95, open=True)
        text = shadow.live_message(LIVE_DAY, dict(FIXTURE, trades=[dict(PLTR, open=False), running]), "11:05", "16:00")
        self.assertIn("תוצאה: נעצרה בסטופ", text)  # the closed one as in the daily message
        self.assertIn("תוצאה: עדיין פתוחה, עכשיו <code>23.60$</code>, רווח של <code>95$</code> עד עכשיו", text)
        clean(self, text)
        done = shadow.live_message(LIVE_DAY, dict(FIXTURE, trades=[]), "16:00", "16:00")
        self.assertIn("יום המסחר הסתיים", done)
        self.assertIn("עוד לא נפתחה עסקה היום", done)
        clean(self, done)


class LiveData(unittest.TestCase):
    """alpaca.CUTOFF: a live day's data ends 15 minutes ago and is never cached."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = mock.patch.object(shadow.alpaca, "CACHE", pathlib.Path(d.name))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shadow.alpaca.CUTOFF.clear)

    def test_minute_and_daily_bars_end_at_the_cutoff_uncached(self):
        bar = {"t": "2026-10-09T13:31:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 10, "vw": 1}
        shadow.alpaca.CUTOFF[LIVE_DAY] = "11:05"
        with mock.patch.object(shadow.alpaca, "_bars", return_value={"AAA": [bar]}) as b:
            shadow.alpaca.minute_bars(["AAA"], LIVE_DAY)
            shadow.alpaca.daily(["AAA"], "2026-08-10", LIVE_DAY)
        self.assertEqual(b.call_args_list[0].args[1:], (shadow.alpaca.utc(LIVE_DAY, "04:00"),
                                                        shadow.alpaca.utc(LIVE_DAY, "11:05"), "1Min"))
        self.assertEqual(b.call_args_list[1].args[1:], ("2026-08-10", shadow.alpaca.utc(LIVE_DAY, "00:00"), "1Day"))
        self.assertFalse(any(shadow.alpaca.CACHE.rglob("*.gz")))  # nothing cached
        shadow.alpaca.CUTOFF.clear()
        with mock.patch.object(shadow.alpaca, "_bars", return_value={"AAA": [bar]}) as b:
            shadow.alpaca.minute_bars(["AAA"], LIVE_DAY)  # a completed day: the whole session, cached
        self.assertEqual(b.call_args.args[2], shadow.alpaca.utc(LIVE_DAY, "20:00"))
        self.assertTrue(any(shadow.alpaca.CACHE.rglob("*.gz")))

    def test_run_day_live_marks_a_running_trade_open_and_clears_the_cutoff(self):
        res = dict(FIXTURE, trades=[dict(PLTR), dict(APLD)])  # PLTR stopped, APLD "eod" so far
        seen = []
        real = shadow.run_day

        def inner(day, hours, sessions, until=None):  # the completed-day computation, called back by the live wrapper
            seen.append(dict(shadow.alpaca.CUTOFF))
            return {**res, "trades": [dict(x) for x in res["trades"]]}
        with mock.patch.object(shadow, "run_day", side_effect=inner):
            got = real(LIVE_DAY, LIVE_HOURS, LIVE_SESSIONS, "11:05")
        self.assertEqual(seen, [{LIVE_DAY: "11:05"}])
        self.assertEqual(shadow.alpaca.CUTOFF, {})
        self.assertEqual([x["open"] for x in got["trades"]], [False, True])


if __name__ == "__main__":
    unittest.main()
