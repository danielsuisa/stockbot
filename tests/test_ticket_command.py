"""`/ticket SYMBOL` (spec 2026-10-10 revision 7, sections 3, 5, 8; phase B): the data path and the Hebrew message.
Network mocked; no outcome of any ticket is computed."""
import datetime as dt
import os
import unittest
from unittest import mock

from bot import alpaca, common, levels, ticket_command as tc
from tests import msgrules


def weekday_calendar(start, end, half=(), holidays=()):
    d, e, out = dt.date.fromisoformat(start), dt.date.fromisoformat(end), {}
    while d <= e:
        s = d.isoformat()
        if d.weekday() < 5 and s not in holidays:
            out[s] = ("09:30", "13:00" if s in half else "16:00")
        d += dt.timedelta(1)
    return out


def bars_until(day, n, close=50.0, rng=1.0, vol=1_000_000):
    d, out = dt.date.fromisoformat(day), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d.isoformat(), close, close + rng / 2, close - rng / 2, close, vol))
        d -= dt.timedelta(1)
    return out[::-1]


def ny(day, hhmm):
    return dt.datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=alpaca.NY)


CAL = weekday_calendar("2025-01-01", "2026-12-31", half=("2026-11-27",), holidays=("2026-01-19", "2026-11-26"))
QUOTE = (50.50, 50.52, 1, 1, 3)


class Build(unittest.TestCase):
    def run_build(self, day="2026-03-09", now=None, bars=None, quote=QUOTE, env=None):
        calls = []

        def quote_full(symbol, at):
            calls.append(at)
            return quote
        with mock.patch.object(alpaca, "calendar", return_value=CAL), \
                mock.patch.object(alpaca, "daily", return_value={"AAPL": bars or bars_until(day, 300)}), \
                mock.patch.object(alpaca, "quote_full", side_effect=quote_full), \
                mock.patch.dict(os.environ, env or {}, clear=False):
            t = tc.build("AAPL", now or ny(day, "17:00"))
        return t, calls

    def test_signal_day(self):
        self.assertEqual(tc.signal_day(ny("2026-03-09", "16:20"), CAL), "2026-03-09")
        self.assertEqual(tc.signal_day(ny("2026-03-09", "16:10"), CAL), "2026-03-06")  # within the 15-minute lag
        self.assertEqual(tc.signal_day(ny("2026-03-14", "12:00"), CAL), "2026-03-13")  # Saturday
        self.assertEqual(tc.signal_day(ny("2026-01-20", "10:00"), CAL), "2026-01-16")  # after the holiday
        self.assertEqual(tc.signal_day(ny("2026-11-27", "13:20"), CAL), "2026-11-27")  # a 13:00 close

    def test_build_ticket(self):
        t, calls = self.run_build()
        self.assertTrue(t["ok"])
        self.assertEqual(t["shares"], 50)
        self.assertEqual(t["valid_sessions"], ["2026-03-10", "2026-03-11", "2026-03-12"])
        self.assertEqual(calls, [alpaca.utc("2026-03-09", "15:55")])
        self.assertAlmostEqual(t["cost_r"], (1.40 + 50 * 0.02) / 100)
        self.assertEqual(t["universe"], [])
        self.assertEqual(t["time_exit_if_first"], tc.sessions_after("2026-03-10", CAL, 21)[-1])
        self.assertFalse(t["first_session_started"])

    def test_half_day_guard_quote(self):
        t, calls = self.run_build(day="2026-11-27", now=ny("2026-11-27", "14:00"))
        self.assertEqual(calls, [alpaca.utc("2026-11-27", "12:55")])  # 5 minutes before a 13:00 close
        self.assertTrue(t["ok"])

    def test_first_session_started(self):
        t, _ = self.run_build(day="2026-03-09", now=ny("2026-03-10", "11:00"))
        self.assertEqual(t["day"], "2026-03-09")
        self.assertTrue(t["first_session_started"])

    def test_risk_from_env(self):
        t, _ = self.run_build(env={"TICKET_RISK_USD": "200"})
        self.assertEqual(t["shares"], 100)

    def test_missing_signal_day_bar_is_not_cached(self):
        # review M1: a fetch made before D's bar exists must not stay cached for the listener's whole run
        import tempfile
        import pathlib
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(alpaca, "CACHE", pathlib.Path(d)):
                stale = alpaca.CACHE / "1d" / "2025-02-02_2026-03-09" / "AAPL.json.gz"
                stale.parent.mkdir(parents=True)
                stale.write_bytes(b"x")
                t, _ = self.run_build(bars=bars_until("2026-03-06", 300))
                self.assertEqual(t["reason"], "no bar on the signal day")
                self.assertFalse(stale.exists())

    def test_refusals_pass_through(self):
        t, calls = self.run_build(bars=bars_until("2026-03-09", 14))
        self.assertEqual((t["ok"], t["reason"], t["symbol"]), (False, "fewer than 15 bars", "AAPL"))
        self.assertEqual(calls, [])  # no quote is read for a quote-free refusal
        t, _ = self.run_build(quote=None)
        self.assertEqual(t["reason"], "no usable quote")


class Message(unittest.TestCase):
    def ticket(self, **over):
        bars = bars_until("2026-03-09", 300)
        t = levels.guard(levels.plan("AAPL", bars, "2026-03-09"), QUOTE)
        t.update(valid_sessions=["2026-03-10", "2026-03-11", "2026-03-12"], time_exit_if_first="2026-04-09",
                 universe=[], first_session_started=False)
        t.update(over)
        return t

    def test_message_full(self):
        text = tc.message(self.ticket())
        msgrules.check(self, text)
        for part in (common.code("AAPL"), "אין אות תומך", "קניית פריצה", common.code(common.price(50.51)),
                     common.code(common.price(50.76)), "סטופ לוס", common.code(common.price(48.51)), "יעד",
                     common.code(common.price(53.51)), "טריילינג", common.code(common.price(2.0)), "יציאה בזמן",
                     common.il_date("2026-04-09"), "עלות", "%", "ב-IBKR", "ללא יתרון מוכח", "10.3.2026"):
            self.assertIn(part, text)
        self.assertNotIn("מחוץ ליקום שנבדק", text)
        self.assertNotIn("כבר במסחר", text)

    def test_position_value(self):
        text = tc.message(self.ticket())
        self.assertIn("שווי פוזיציה", text)
        self.assertIn(common.code(common.price(50 * 50.51)), text)

    def test_message_universe_mark(self):
        text = tc.message(self.ticket(universe=["price below $5", "fewer than 252 sessions"]))
        msgrules.check(self, text)
        self.assertIn("מחוץ ליקום שנבדק", text)
        self.assertIn("מחיר מתחת", text)
        self.assertIn("פחות מ-252 ימי מסחר", text)
        self.assertIn("דיווחי", text)  # the insider-filing condition is not checked until phase C

    def test_first_session_note(self):
        text = tc.message(self.ticket(first_session_started=True))
        msgrules.check(self, text)
        self.assertIn("כבר במסחר", text)

    def test_sub_dollar_prices_keep_four_decimals(self):
        # review m1: a $0.47 stock's levels are shown as ordered (4 decimals), not rounded to cents
        bars = bars_until("2026-03-09", 300, close=0.45, rng=0.3, vol=200_000_000)  # R 0.6: 166 shares
        t = levels.guard(levels.plan("SUB", bars, "2026-03-09"), (0.45, 0.4502, 1, 1, 3))
        t.update(valid_sessions=["2026-03-10"], time_exit_if_first=None, universe=[], first_session_started=False)
        text = tc.message(t)
        msgrules.check(self, text)
        self.assertIn(f"<code>{t['entry_stop']:.4f}$</code>", text)
        self.assertIn(f"<code>{t['stop_loss']:.4f}$</code>", text)

    def test_message_refusals(self):
        for reason in ("fewer than 15 bars", "no bar on the signal day", "shares below 2", "size above 1% of dollar volume", "no usable quote",
                       "cost above 10% of risk"):
            text = tc.message({"ok": False, "reason": reason, "symbol": "AAPL", "day": "2026-03-09"})
            msgrules.check(self, text)
            self.assertTrue(text.startswith("אין כרטיס"), text)
            self.assertIn(tc.REASONS[reason], text)

    def test_main_dry_run(self):
        refusal = {"ok": False, "reason": "no usable quote", "day": "2026-10-09"}
        with (mock.patch.object(tc, "build", side_effect=lambda s: {**refusal, "symbol": s}) as build,
              mock.patch.object(common, "send") as send, mock.patch("builtins.print") as out):
            self.assertEqual(tc.main(["aapl", "brk.b"]), 0)
        self.assertEqual([c.args[0] for c in build.call_args_list], ["AAPL", "BRK.B"])
        send.assert_not_called()  # a dry run prints; common.send refuses to run without a Telegram token in Actions
        printed = "\n".join(str(c.args[0]) for c in out.call_args_list if c.args)  # print() alone: a blank line
        self.assertEqual(printed.count("אין כרטיס"), 2)
        self.assertIn(tc.SOURCE, printed)

    def test_no_simulation_on_market_data(self):
        import inspect
        src = inspect.getsource(tc)
        for name in ("simulate(", "fill(", "exits(", "gross_r("):
            self.assertNotIn(name, src)


if __name__ == "__main__":
    unittest.main()
