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


SIZING = {"E": 31_337.17, "date": "2026-10-01", "p_raw": 0.005, "p_note": None}  # synthetic equity
CAL = weekday_calendar("2025-01-01", "2026-12-31", half=("2026-11-27",), holidays=("2026-01-19", "2026-11-26"))
QUOTE = (50.50, 50.52, 1, 1, 3)


class Build(unittest.TestCase):
    def run_build(self, day="2026-03-09", now=None, bars=None, quote=QUOTE, env=None, sizing=None):
        calls = []

        def quote_full(symbol, at):
            calls.append(at)
            return quote
        with mock.patch.object(alpaca, "calendar", return_value=CAL), \
                mock.patch.object(alpaca, "daily", return_value={"AAPL": bars or bars_until(day, 300)}), \
                mock.patch.object(alpaca, "quote_full", side_effect=quote_full), \
                mock.patch.dict(os.environ, env or {}, clear=False):
            t = tc.build("AAPL", now or ny(day, "17:00"), sizing=sizing)
        return t, calls

    # ---------- sizing by equity (phase B.1; synthetic E) ----------
    def test_build_with_equity(self):
        t, _ = self.run_build(sizing=SIZING)
        self.assertEqual(t["shares"], 78)  # floor(0.005 x 31,337.17 / 2)
        self.assertEqual(t["sizing"]["position_capped"], False)
        self.assertEqual(t["sizing"]["open_positions"], 6)

    def test_build_position_capped(self):
        bars = bars_until("2026-03-09", 300, rng=0.5)  # R 1.0: 156 shares by risk, cap floor(6267.43 / 50.26) = 124
        t, _ = self.run_build(bars=bars, sizing=SIZING)
        self.assertEqual((t["shares"], t["sizing"]["position_capped"]), (124, True))

    def test_build_without_equity_keeps_the_fixed_risk(self):
        t, _ = self.run_build()
        self.assertEqual((t["shares"], t["sizing"]), (50, None))

    def test_small_account_refusal_keeps_the_sizing(self):
        t, _ = self.run_build(sizing={**SIZING, "E": 3_000.0})  # risk 15: 7 shares, cost 11% of risk
        self.assertEqual(t["reason"], "cost above 10% of risk")
        self.assertAlmostEqual(t["sizing"]["risk"], 15.0)

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

    def sized(self, **over):
        bars = bars_until("2026-03-09", 300)
        sizing = {**SIZING, **over}
        t = levels.plan("AAPL", bars, "2026-03-09", risk=0.005 * sizing["E"])
        t = levels.guard(t, QUOTE)
        t.update(valid_sessions=["2026-03-10"], time_exit_if_first="2026-04-09", universe=[], first_session_started=False,
                 sizing=tc.sizing_info(sizing, t))
        return t

    def test_message_with_equity(self):
        text = tc.message(self.sized())
        msgrules.check(self, text)
        for part in (common.code(common.price(78 * 2.0)), common.code("0.5%"), common.code(common.price(78 * 50.51)),
                     common.code("12.6%"), common.code(6), "1.10.2026"):
            self.assertIn(part, text)
        self.assertNotIn("לא קשור לחשבון", text)

    def test_message_caps_noted(self):
        capped = self.sized(p_raw=0.01)
        text = tc.message(capped)
        msgrules.check(self, text)
        self.assertIn("הוגבל ל-", text)
        t = self.sized()
        t["sizing"]["position_capped"] = True
        self.assertIn("20%", tc.message(t))
        t = self.sized(p_note="non-numeric")
        self.assertIn("ברירת המחדל", tc.message(t))

    def test_ticket_fallback_line(self):
        text = tc.message(self.ticket(sizing=None))
        msgrules.check(self, text)
        self.assertIn("הגודל לא קשור לחשבון", text)

    def test_small_account_refusal_reason(self):
        t = {"ok": False, "reason": "cost above 10% of risk", "symbol": "AAPL", "day": "2026-03-09",
             "sizing": {"E": 3_000.0, "risk": 15.0}}
        text = tc.message(t)
        msgrules.check(self, text)
        self.assertIn("חשבון קטן", text)
        t["reason"] = "shares below 2"
        self.assertIn("חשבון קטן", tc.message(t))  # never a bare "shares below 2" for a small account

    def test_message_full(self):
        text = tc.message(self.ticket())
        msgrules.check(self, text)
        for part in (common.code("AAPL"), "אין אות תומך", "קניית פריצה", common.code(common.price(50.51)),
                     common.code(common.price(50.76)), "סטופ לוס", common.code(common.price(48.51)), "יעד",
                     common.code(common.price(53.51)), "טריילינג", common.code(common.price(2.0)), "יציאה בזמן",
                     common.il_date("2026-04-09"), "עלות", "%", "ב-IBKR", "ללא יתרון מוכח", "10.3.2026"):
            self.assertIn(part, text)
        self.assertNotIn("מחוץ ליקום שנבדק", text)
        self.assertIn("פקודת bracket אחת לכל מנה", text)  # review minor 4: odd share counts give unequal legs
        self.assertNotIn("בחצי כמות", text)
        self.assertNotIn("כבר במסחר", text)

    def test_measured_line_on_every_issued_ticket(self):
        """The verdict's measured fact (spec 6.6, "After the result"), next to the label, with and without E."""
        line = (f"נמדד על {common.code('35,083')} כרטיסים היסטוריים בסטופ של 2×ATR ({common.code(2017)} עד "
                f"{common.code(2026)}): "
                f"לפני עלויות התוצאה הממוצעת הייתה כמעט אפס ({common.code('0.04%')} מהסיכון), "
                f"והעלות הממוצעת הייתה כ-{common.code('6%')} מהסיכון לכרטיס")
        self.assertEqual(tc.measured_line(), line)
        for t in (self.ticket(), self.ticket(sizing=None), self.sized()):
            text = tc.message(t)
            msgrules.check(self, text)
            lines = text.splitlines()
            self.assertIn(line, lines)
            self.assertEqual(lines.index(line) + 1, lines.index(f"תווית: {tc.LABEL}"))  # next to the label
        self.assertEqual((tc.VERDICT_N, tc.VERDICT_GROSS_R, tc.VERDICT_COST_R), (35_083, 0.0004, 0.0610))

    def test_stop_as_a_percentage_of_the_entry(self):
        """List-tickets spec section 1: the stop line shows the stop as a percentage of P; the cap is named."""
        calm = self.ticket()  # R = 2.0 on P = 50.51: the stop is 3.96% below the entry
        text = tc.message(calm)
        msgrules.check(self, text)
        pct = (calm["planned_entry"] - calm["stop_loss"]) / calm["planned_entry"] * 100
        self.assertIn(f"{common.code(f'{pct:.1f}%')} מתחת לכניסה", text)
        self.assertNotIn("סטופ מוגבל", text)
        bars = bars_until("2026-03-09", 300, rng=12.0)  # 2 x ATR = 24, about 43% of P: the cap applies
        capped = levels.guard(levels.plan("AAPL", bars, "2026-03-09"), QUOTE)
        capped.update(valid_sessions=["2026-03-10"], time_exit_if_first=None, universe=[], first_session_started=False)
        text = tc.message(capped)
        msgrules.check(self, text)
        self.assertIn(f"{common.code('10.0%')} מתחת לכניסה", text)
        self.assertIn("סטופ מוגבל ל-10%", text)

    def test_message_without_the_measured_line(self):
        text = tc.message(self.ticket(), measured=False)  # the list message carries its own fact once
        self.assertNotIn(tc.measured_line(), text)
        self.assertIn(f"תווית: {tc.LABEL}", text)

    def test_no_measured_line_on_a_refusal(self):
        for reason in tc.REASONS:
            for sizing in (None, {"E": 3_000.0, "risk": 15.0}):
                text = tc.message({"ok": False, "reason": reason, "symbol": "AAPL", "day": "2026-03-09",
                                   "sizing": sizing})
                self.assertNotIn("נמדד על", text)
                self.assertNotIn(tc.measured_line(), text)

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
        bars = bars_until("2026-03-09", 300, close=0.45, rng=0.3, vol=200_000_000)  # R capped at 10% of P: 0.061
        t = levels.plan("SUB", bars, "2026-03-09")
        # the stop cap makes R <= $0.10 below $1, so a one-cent spread is >= 10% of R and the guard always refuses a
        # sub-dollar ticket; this test is about the message's decimals, so it sets the guard's fields directly
        self.assertEqual(levels.guard(t, (0.45, 0.4502, 1, 1, 3))["reason"], "cost above 10% of risk")
        t = {**t, "guard_spread": 0.0002, "cost_r": 0.05}
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
