"""Breakout pings: the screen, the cross, the five-line text, and the size locks. No network and no orders."""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import msgrules
from bot import breakout, breakout_live, common

DAY = "2026-10-14"  # a Wednesday
NOW = dt.datetime(2026, 10, 14, 8, 50, tzinfo=breakout.NY)


def bars(n=25, h=100.0, l=97.0, c=97.5, v=200_000, end=dt.date(2026, 10, 13)):
    out = []
    for i in range(n):
        d = (end - dt.timedelta(days=n - 1 - i)).isoformat()
        out.append((d, c, h, l, c, v))
    return out


def row():
    got = breakout.context_row(bars(), DAY)
    assert got and breakout.passes(got), got
    return got


class Feed:
    """SIP dailies, a Nasdaq pre-market quote and an IEX print, with no network."""

    def __init__(self):
        self.syms = ["AAA"]
        self.pre = {"AAA": {"price": 99.5, "volume": 40_000, "date": DAY}}
        self.iex_px, self.bid, self.ask = 99.5, 99.4, 99.6
        self.E = 10_000
        self.daily_calls, self.symbol_calls = [], 0
        self.open, self.business_raises = True, False

    def symbols(self):
        self.symbol_calls += 1
        return list(self.syms)

    def business(self, now):
        if self.business_raises:
            raise RuntimeError("nasdaq blip")
        return self.open

    def daily(self, syms, start, end):
        self.daily_calls.append((list(syms), start, end))
        return {s: bars() for s in syms}

    def premarket(self, syms, today):
        return {s: q for s, q in self.pre.items() if s in syms}

    def iex(self, syms):
        return {s: {"price": self.iex_px, "bid": self.bid, "ask": self.ask} for s in syms}

    def equity(self):
        return self.E


class Screen(unittest.TestCase):
    def test_uniform_contraction_passes_and_a_wide_or_thin_name_does_not(self):
        got = row()
        self.assertEqual(got["atr"], 3)
        self.assertTrue(got["nr7"])
        self.assertFalse(got["contraction"])  # a range of 3 is wider than 0.75 x ATR
        self.assertAlmostEqual(got["dist"], 0.025)
        self.assertAlmostEqual(got["adv"], 19_500_000)
        wide = bars()
        wide[-1] = (wide[-1][0], 97.5, 100.0, 90.0, 97.5, 200_000)  # last bar is the widest of seven, range10 is wide
        self.assertFalse(breakout.passes(breakout.context_row(wide, DAY)))
        self.assertIsNone(breakout.context_row(bars(v=100), DAY))  # dollar volume under $10M
        self.assertIsNone(breakout.context_row(bars(n=10), DAY))
        split = bars()
        split[-1] = (split[-1][0], 200.0, 200.0, 194.0, 200.0, 200_000)  # a doubled close
        self.assertIsNone(breakout.context_row(split, DAY))

    def test_grades_and_drops(self):
        name = breakout.track(row(), "AAA")
        self.assertEqual(breakout.apply_quote(name, {"price": 99.5, "volume": 40_000, "date": DAY}, DAY), "keep")
        self.assertEqual(name["base"], "B")
        self.assertAlmostEqual(name["pre_vol"], 0.2)
        quiet = breakout.track(row(), "AAA")
        self.assertEqual(breakout.apply_quote(quiet, {"price": 98.475, "volume": 40_000, "date": DAY}, DAY), "keep")
        self.assertEqual(quiet["base"], "C")  # a 1% gap is under the 2% line
        jumped = breakout.track(row(), "AAA")
        self.assertEqual(breakout.apply_quote(jumped, {"price": 110.0, "volume": 40_000, "date": DAY}, DAY), "drop")
        through = breakout.track(row(), "AAA")
        self.assertEqual(breakout.apply_quote(through, {"price": 100.5, "volume": 40_000, "date": DAY}, DAY), "drop")
        self.assertEqual(breakout.apply_quote(breakout.track(row(), "AAA"), None, DAY), "skip")

    def test_the_first_print_above_the_level_does_not_fire(self):
        name = breakout.track(row(), "AAA")
        self.assertIsNone(breakout.observe(name, 100.05))
        self.assertFalse(name["pinged"])
        name = breakout.track(row(), "AAA")
        self.assertIsNone(breakout.observe(name, 99.5))
        self.assertTrue(name["seen_below"])
        self.assertAlmostEqual(breakout.observe(name, 100.05), 100.01)
        self.assertTrue(name["pinged"])
        self.assertIsNone(breakout.observe(name, 101.0))  # one ping per name per day

    def test_bracket_uses_half_an_atr_when_the_seen_low_is_a_single_tick(self):
        name = breakout.track(row(), "AAA")
        breakout.observe(name, 99.5, fire=False)
        order = breakout.bracket(name, 100.01)
        self.assertEqual(order["R"], 1.5)  # the 0.52 structural distance is floored at half the ATR
        self.assertEqual((order["trigger"], order["stop"], order["limit"], order["target"]),
                         (100.01, 98.51, 100.76, 102.26))
        self.assertAlmostEqual(order["stop_pct"], 1.5 / 100.01)
        self.assertEqual(breakout.grade_for_cross(name, 0.20, order["R"]), "C")  # base is still C until a quote
        name["base"] = "B"
        self.assertEqual(breakout.grade_for_cross(name, 0.20, order["R"]), "A")  # 0.20 <= 0.25 x 1.5
        self.assertEqual(breakout.grade_for_cross(name, None, order["R"]), "B")
        self.assertEqual(breakout.grade_for_cross(name, 1.0, order["R"]), "B")
        name["base"] = "C"
        self.assertEqual(breakout.grade_for_cross(name, 0.01, order["R"]), "C")


class Sizing(unittest.TestCase):
    def test_the_suggestion_is_capped_and_his_percents_never_count_shares(self):
        self.assertEqual(breakout.SUGGESTED, {"C": 0.0, "B": 0.005, "A": 0.01})
        self.assertEqual(breakout.OPTION, {"B": 0.05, "A": 0.15})
        self.assertEqual((breakout.HARD_CAP, breakout.DAILY_STOP, breakout.OPEN_RISK), (0.02, 0.02, 0.03))
        got = breakout.allocation("A", 10_000, 2, 0, 0)
        self.assertEqual((got["pct"], got["dollars"], got["shares"], got["option"], got["block"]),
                         (0.01, 100, 50, 0.15, None))  # 15% of 10,000 would be 750 shares
        try:
            breakout.SUGGESTED["A"] = 0.05  # even a forced 5% cannot pass the hard cap
            capped = breakout.allocation("A", 10_000, 2, 0, 0)
        finally:
            breakout.SUGGESTED["A"] = 0.01
        self.assertEqual((capped["pct"], capped["dollars"], capped["shares"], capped["option"]),
                         (0.02, 200, 100, 0.15))

    def test_blocks_still_describe_a_ping_and_do_not_invent_shares(self):
        self.assertEqual(breakout.allocation("C", 10_000, 2, 0, 0)["block"], "grade")
        self.assertEqual(breakout.allocation("A", None, 2, 0, 0)["block"], "equity")
        self.assertEqual(breakout.allocation("A", 10_000, 2, 0.015, 0)["block"], "daily")
        self.assertEqual(breakout.allocation("B", 10_000, 2, 0.02, 0)["block"], "daily")  # 0.5% more would pass 2%
        self.assertEqual(breakout.allocation("A", 10_000, 2, 0, 0.025)["block"], "open")
        self.assertEqual(breakout.allocation("A", 1, 2, 0, 0)["block"], "dust")
        self.assertIsNone(breakout.allocation("A", 10_000, 2, 0.015, 0)["shares"])

    def test_a_blocked_ping_does_not_spend_the_budget(self):
        state = breakout_live.fresh(DAY)
        state["allocated"] = 0.015
        name = breakout.track(row(), "AAA")
        name["base"] = "B"
        breakout.observe(name, 99.5, fire=False)
        sent = []
        breakout_live._ping(state, name, 100.01, 0.20, 10_000, DAY, "IEX", sent.append)
        self.assertEqual(state["allocated"], 0.015)
        self.assertEqual(state["open_risk"], 0)
        self.assertEqual(len(sent), 1)
        self.assertIn("בלי כמות", sent[0])


class Text(unittest.TestCase):
    def ping(self):
        name = breakout.track(row(), "AAA")
        name["base"] = "B"
        breakout.apply_quote(name, {"price": 99.5, "volume": 40_000, "date": DAY}, DAY)
        breakout.observe(name, 99.5, fire=False)
        order = breakout.bracket(name, 100.01)
        grade = breakout.grade_for_cross(name, 0.20, order["R"])
        sizing = breakout.allocation(grade, 10_000, order["R"], 0, 0)
        return breakout.ping_text(name, order, sizing, DAY, "IEX"), sizing

    def test_the_ping_is_five_hebrew_lines_and_prints_fifteen_percent_as_information(self):
        text, sizing = self.ping()
        self.assertEqual(len(text.splitlines()), 5)
        self.assertEqual(sizing["grade"], "A")
        self.assertEqual(sizing["shares"], 66)  # 1% of 10,000 is $100; $100 / 1.5
        self.assertIn("<code>66</code>", text)
        self.assertIn("<code>33</code>", text)
        self.assertIn("<code>100.01$</code>", text)
        self.assertIn("<code>98.51$</code>", text)
        self.assertIn("<code>100.76$</code>", text)
        self.assertIn("<code>102.26$</code>", text)
        self.assertIn("<code>22:55</code>", text)
        self.assertIn("אפשרות <code>15%</code> אינה הפקודה", text)
        self.assertNotIn(common.DISCLAIMER, text)
        self.assertNotIn(common.CHECK_PROMPT, text)
        msgrules.check(self, text)
        self.assertEqual(breakout.flat_clock("2026-01-15"), "22:55")  # winter, same seven-hour gap

    def test_the_watchlist_shows_at_most_eight_and_puts_b_first(self):
        names = []
        for i in range(10):
            got = breakout.track({**row(), "dist": (10 - i) / 1000}, f"N{i:02d}")
            got["base"] = "B" if i < 3 else "C"
            names.append(got)
        names[0]["pinged"] = True
        text = breakout.watch_text(names, NOW)
        lines = [ln for ln in text.splitlines() if ln.startswith("מניה")]
        self.assertEqual(len(lines), 8)
        self.assertIn("<code>N02</code>", lines[0])  # the pinged B is gone; the closest remaining B is first
        self.assertIn("<code>15:50</code>", text)  # 08:50 New York is 15:50 in Israel
        self.assertIn("בלי פקודה", text)
        msgrules.check(self, text)
        empty = breakout.watch_text([], NOW)
        self.assertIn("אין מניה שעומדת בתנאים.", empty)
        msgrules.check(self, empty)

    def test_footer_false_sends_the_text_alone(self):
        with mock.patch.dict(os.environ, {"TG_TOKEN": "", "TG_CHAT_ID": "", "GITHUB_ACTIONS": ""}), \
                mock.patch("builtins.print") as out:
            common.send("פינג", footer=False)
        self.assertEqual(out.call_args.args[0], "פינג")
        self.assertNotIn(common.DISCLAIMER, out.call_args.args[0])


class Loop(unittest.TestCase):
    def test_quiet_outside_the_window_and_without_keys(self):
        for when in (dt.datetime(2026, 10, 17, 10, 0), dt.datetime(2026, 10, 14, 3, 0),
                     dt.datetime(2026, 10, 14, 16, 0)):
            feed = Feed()
            state, changed = breakout_live.tick(None, now=when.replace(tzinfo=breakout.NY), feed=feed)
            self.assertEqual((feed.symbol_calls, changed, state), (0, False, None))
        with mock.patch.dict(os.environ, {"ALPACA_KEY_ID": "", "ALPACA_SECRET_KEY": ""}), \
                mock.patch.object(breakout_live, "LiveFeed", side_effect=AssertionError("no live feed")):
            self.assertEqual(breakout_live.tick(None, now=NOW), (None, False))

    def test_a_holiday_is_quiet_and_a_status_blip_does_not_skip_the_day(self):
        feed = Feed()
        feed.open = False
        self.assertEqual(breakout_live.tick(None, now=NOW, feed=feed)[1], False)
        self.assertEqual(feed.symbol_calls, 0)
        feed.open, feed.business_raises = True, True
        state, changed = breakout_live.tick(None, now=NOW, feed=feed, send=lambda text: None)
        self.assertTrue(changed)
        self.assertGreater(feed.symbol_calls, 0)

    def test_watchlist_then_one_ping_then_silence(self):
        feed, sent = Feed(), []
        state, changed = breakout_live.tick(None, now=NOW, feed=feed, send=sent.append)
        self.assertTrue(changed)
        self.assertEqual(feed.daily_calls[0][2], "2026-10-13")  # SIP through yesterday, never today
        self.assertNotIn(DAY, [end for _, _, end in feed.daily_calls])
        self.assertEqual(len(sent), 1)
        self.assertIn("<code>B</code>", sent[0])
        self.assertIn("<code>AAA</code>", sent[0])
        self.assertNotIn(common.DISCLAIMER, sent[0])
        msgrules.check(self, sent[0])
        feed.iex_px, feed.bid, feed.ask = 100.05, 100.0, 100.2
        with mock.patch.object(common, "log") as log:
            state, changed = breakout_live.tick(state, now=NOW, feed=feed, send=sent.append)
        self.assertTrue(changed)
        self.assertEqual(len(sent), 2)
        ping = sent[1]
        self.assertEqual(len(ping.splitlines()), 5)
        self.assertIn("<code>66</code>", ping)
        self.assertIn("אפשרות <code>15%</code> אינה הפקודה", ping)
        self.assertNotIn("10000", ping)  # the equity never enters the message
        msgrules.check(self, ping)
        fields = log.call_args.kwargs
        self.assertEqual((log.call_args.args[0], fields["symbol"], fields["grade"], fields["sized"]),
                         ("breakout_ping", "AAA", "A", True))
        self.assertNotIn("10000", str(fields))
        self.assertNotIn("shares", fields)
        state, changed = breakout_live.tick(state, now=NOW, feed=feed, send=sent.append)
        self.assertFalse(changed)
        self.assertEqual(len(sent), 2)
        self.assertAlmostEqual(state["allocated"], 0.01)
        self.assertAlmostEqual(state["open_risk"], 0.01)

    def test_a_gap_over_ten_percent_and_a_late_name_leave_an_empty_list(self):
        for price in (110.0, 100.5):
            feed, sent = Feed(), []
            feed.pre = {"AAA": {"price": price, "volume": 40_000, "date": DAY}}
            state, _ = breakout_live.tick(None, now=NOW, feed=feed, send=sent.append)
            self.assertEqual(len(sent), 1)
            self.assertIn("אין מניה שעומדת בתנאים.", sent[0])
            self.assertEqual(state["names"], [])

    def test_before_0830_one_chunk_and_after_it_the_scan_catches_up(self):
        early = NOW.replace(hour=8, minute=0)
        feed = Feed()
        feed.syms = [f"S{i:03d}" for i in range(150)]
        state, _ = breakout_live.tick(None, now=early, feed=feed, send=lambda text: None)
        self.assertEqual((state["cursor"], state["ready"], len(feed.daily_calls)), (100, False, 1))
        late = Feed()
        late.syms = feed.syms
        state, _ = breakout_live.tick(None, now=NOW.replace(hour=8, minute=31), feed=late, send=lambda text: None)
        self.assertTrue(state["ready"])
        self.assertEqual(sum(len(part) for part, _, _ in late.daily_calls), 150)
        self.assertLessEqual(len(state["names"]), 30)

    def test_state_roundtrip_has_no_equity_and_dump_stays_on_the_given_path(self):
        state = breakout_live.fresh(DAY)
        self.assertEqual(breakout_live.decode(breakout_live.encode(state))["day"], DAY)
        self.assertEqual(breakout_live.encode(None), "")
        self.assertIsNone(breakout_live.decode("not-base64"))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "breakout_state.json")
            with mock.patch.dict(os.environ, {"BREAKOUT_PATH": str(path), "GITHUB_ACTIONS": ""}):
                breakout_live.dump(state)
            text = path.read_text("utf-8")
            self.assertNotIn("equity", text)
            self.assertNotIn("shares", text)
            self.assertFalse((common.DATA / "breakout_state.json").exists())


if __name__ == "__main__":
    unittest.main()
