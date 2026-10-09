"""Forward shadow tracker of ZBA K10s: one mocked session end to end, day selection, the message and the run."""
import os
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

    def test_the_message(self):
        res, _ = self.run_day()
        j = {"v": 1, "since": DAY, "days": {DAY: res}}
        text = shadow.message(DAY, res, j)
        self.assertEqual(common.rtl_bad_lines(text), [], text)
        for part in ("קנייה: <code>AAA</code>", "פקודת עצירה ב־<code>10.50</code>", "<code>09:40</code>",
                     "מילוי <code>10.52</code>", "סטופ <code>10.42</code>", "<code>1000</code> מניות",
                     "יציאה <code>10.80</code> (סגירה)", "שורט חסום בכלל 201 <code>1</code>", "מדגם קטן",
                     "לא כרטיס פקודה", "לא נשלחה שום פקודה"):
            self.assertIn(part, text)
        empty = {"top": ["AAA"], "reasons": {"trigger": 1}, "dropped": {}, "trades": [], "optimistic": [], "gaps": 0}
        text = shadow.message(DAY, empty, {"v": 1, "since": DAY, "days": {DAY: empty}})
        self.assertIn("היום לא הייתה עסקה", text)
        self.assertIn("עוד אין עסקאות", text)
        self.assertEqual(common.rtl_bad_lines(text), [], text)

    def test_a_short_line(self):
        x = {"t": "BBB", "side": -1, "at": "09:40", "level": 9.5, "fill": 9.48, "R": 0.1, "shares": 1000,
             "kinds": "eod", "gross_r": 2.8, "net_r": 2.53}
        line = shadow.trade_line(x)
        for part in ("מכירה בחסר: <code>BBB</code>", "סטופ <code>9.58</code>", "יציאה <code>9.20</code> (סגירה)",
                     "<code>+2.53R</code>"):
            self.assertIn(part, line)

    def test_a_session_without_15_sessions_before_it_is_an_error(self):
        with self.assertRaises(ValueError):
            shadow.run_day(SESSIONS[14], HOURS, SESSIONS)


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
                mock.patch.object(shadow.common, "send") as send, mock.patch.object(shadow, "today_ny", return_value=today):
            self.assertEqual(shadow.main(list(argv)), 0)
        return [c.args[0] for c in rd.call_args_list], [c.args[0] for c in send.call_args_list]

    def test_a_day_is_sent_once_and_recorded(self):
        days, sent = self.main()
        self.assertEqual((days, len(sent)), ([DAY], 1))
        j = shadow.load()
        self.assertEqual((j["since"], list(j["days"])), (DAY, [DAY]))
        self.assertEqual(self.main(), ([], []))  # the next scheduled run: silence
        days, sent = self.main("--manual")
        self.assertEqual(days, [])
        self.assertIn("אין יום מסחר חדש", sent[0])
        self.assertEqual(common.rtl_bad_lines(sent[0]), [])

    def test_the_calendar_reaches_back_before_the_last_processed_session(self):
        shadow.save({"v": 1, "since": "2023-11-01", "days": {"2023-11-01": dict(self.RES)}})
        with mock.patch.object(shadow.alpaca, "calendar", return_value=HOURS) as cal:
            self.main()
        self.assertEqual(cal.call_args.args, ("2023-08-03", "2024-03-02"))  # 90 days before the last one, up to today

    def test_dry_run_writes_nothing(self):
        days, sent = self.main("--dry")
        self.assertEqual((days, sent), ([DAY], []))
        self.assertFalse(shadow.path().exists())


if __name__ == "__main__":
    unittest.main()
