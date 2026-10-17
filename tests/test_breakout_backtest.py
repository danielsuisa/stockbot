"""The pre-registered breakout replay: bar path, fills, costs, and the GO gate. No network."""
import datetime as dt
import unittest
from pathlib import Path

from bot import breakout, breakout_backtest as bt


def bars(n=25, end=dt.date(2024, 6, 2)):
    out = []
    for i in range(n):
        d = (end - dt.timedelta(days=n - 1 - i)).isoformat()
        out.append((d, 97.5, 100.0, 97.0, 97.5, 200_000))
    return out


def name():
    row = breakout.context_row(bars(), "2024-06-03")
    assert breakout.passes(row)
    got = breakout.track(row, "AAA")
    return got


class Rule(unittest.TestCase):
    def test_the_window_and_the_gate_are_the_ones_locked_in_the_module(self):
        self.assertEqual(bt.START, "2024-01-02")
        self.assertEqual(bt.YEARS, ("2024", "2025", "2026"))
        self.assertEqual((bt.RISK_USD, bt.MIN_SHARES, bt.EXTRA_SLIP), (100.0, 10, 0.01))
        text = Path(bt.__file__).read_text(encoding="utf-8")
        self.assertIn("at least 100 trades", text)
        self.assertIn("t >= 2", text)
        self.assertNotIn("ib_insync", text)
        self.assertNotIn("submit", text.lower())

    def test_flat_minute_and_the_bar_path(self):
        self.assertEqual(bt.flat_minute("16:00"), "15:55")
        self.assertEqual(bt.flat_minute("13:00"), "12:59")
        self.assertEqual(bt.bar_path(1, 3, 0, 2), (1, 0, 3, 2))
        self.assertEqual(bt.bar_path(2, 3, 0, 1), (2, 3, 0, 1))

    def test_a_close_already_through_the_level_drops_and_a_later_cross_fires_once(self):
        dropped = name()
        self.assertEqual(bt.session_events(dropped, [("08:00", 110, 110, 110, 110, 1000)], "2024-06-03")["status"],
                         "drop")
        held = name()
        minutes = [("08:00", 99.5, 99.6, 99.4, 99.5, 40_000),
                   ("10:00", 99.8, 100.2, 99.7, 100.05, 1000),
                   ("10:01", 101, 102, 100.5, 101.5, 1000)]
        event = bt.session_events(held, minutes, "2024-06-03")
        self.assertEqual(event["status"], "fire")
        self.assertEqual(event["minute"], "10:00")
        self.assertEqual(event["base"], "B")
        self.assertEqual(event["order"]["trigger"], 100.01)
        self.assertEqual(event["order"]["R"], 1.5)
        self.assertTrue(held["pinged"])

    def test_a_session_bar_is_not_visible_to_the_screen(self):
        history = bars()
        poisoned = history + [("2024-06-03", 97.5, 200.0, 50.0, 97.5, 200_000)]
        indexed = bt.index_daily({"AAA": poisoned, "ZZZ": bars(n=5)})
        names = bt.screen_day(indexed, "2024-06-03")
        self.assertEqual([n["t"] for n in names], ["AAA"])

    def test_fills_stop_limit_and_the_stop_on_the_entry_minute(self):
        self.assertEqual(bt.fill_from_ticks([99.0, 100.2, 101.0], 100.01, 100.5, 98.0)[2], "limit")
        fill, stopped, status = bt.fill_from_ticks([99.0, 100.2, 100.3, 98.0, 97.5], 100.01, 101.0, 98.5)
        self.assertEqual((fill, stopped, status), (100.3, 97.5, "filled"))
        self.assertEqual(bt.fill_from_ticks([99.0], 100.01, 101.0, 98.0)[2], "no trigger")
        px, minute, kind = bt.exit_after(100, 98, 110, [("10:05", 100, 112, 90, 100, 1)], "15:55")
        self.assertEqual((px, minute, kind), (98, "10:05", "stop"))
        px, minute, kind = bt.exit_after(100, 98, 110, [("10:05", 80, 80, 80, 80, 1)], "15:55")
        self.assertEqual((px, kind), (80, "stop"))
        px, minute, kind = bt.exit_after(100, 98, 110, [("15:55", 101, 101.2, 100.8, 101.0, 1)], "15:55")
        self.assertEqual((px, minute, kind), (101.0, "15:55", "flat"))

    def test_cost_uses_the_printed_r_and_ten_shares_is_the_floor(self):
        self.assertEqual(bt.share_count(1.5), 66)
        self.assertEqual(bt.share_count(20), 0)  # $100 / $20 is 5 shares
        gross, cost, net = bt.net_of(100.10, 101.0, 1.5, 66, 0.20)
        self.assertAlmostEqual(gross, 0.6)
        self.assertAlmostEqual(cost, (0.70 + 66 * 0.20) / (1.5 * 66))
        self.assertAlmostEqual(net, gross - cost)

    def test_losing_streak_and_a_negative_book_is_no_go(self):
        self.assertEqual(bt.losing_streak([
            {"day": "2024-06-03", "minute": "10:00", "t": "A", "net_r": 1},
            {"day": "2024-06-03", "minute": "10:01", "t": "B", "net_r": -1},
            {"day": "2024-06-04", "minute": "10:00", "t": "C", "net_r": -1},
            {"day": "2024-06-05", "minute": "10:00", "t": "D", "net_r": 1},
            {"day": "2024-06-06", "minute": "10:00", "t": "E", "net_r": 0},
        ]), 2)
        bad = [{"day": f"2024-06-{(i % 20) + 1:02d}", "t": "A", "minute": "10:00", "net_r": -0.2,
                "net_r_short": 0.2, "filled": True, "grade": "B", "fill": 50.0, "R": 1.0, "risk": 0.005,
                "cost_r": 0.1, "kinds": "stop"} for i in range(30)]
        judged = bt.judge(bad, ["2024-06-03"])
        self.assertFalse(judged["go"])
        self.assertEqual(judged["stats"]["n"], 30)

    def test_grade_c_is_not_in_the_gate(self):
        trades = [{"day": "2024-06-03", "t": "C", "minute": "10:00", "net_r": 5, "net_r_short": -5,
                   "filled": True, "grade": "C", "fill": 50.0, "R": 1.0, "risk": 0.0, "cost_r": 0.0, "kinds": "flat"},
                  {"day": "2025-06-03", "t": "B", "minute": "10:00", "net_r": -0.2, "net_r_short": 0.2,
                   "filled": True, "grade": "B", "fill": 50.0, "R": 1.0, "risk": 0.005, "cost_r": 0.1, "kinds": "stop"}]
        self.assertEqual(bt.judge(trades, ["2024-06-03", "2025-06-03"])["stats"]["n"], 1)


class FakeFeed:
    def sessions(self, start, end):
        return [("2024-06-03", "16:00")]

    def daily(self, start, end):
        return {"AAA": bars()}

    def minutes(self, symbols, day):
        self.asked = list(symbols)
        return {"AAA": [("08:00", 99.5, 99.6, 99.4, 99.5, 40_000),
                        ("10:00", 99.8, 100.2, 99.7, 100.05, 1000),
                        ("15:55", 101.0, 101.2, 100.8, 101.0, 500)]}

    def trades(self, symbol, day, minute):
        return [99.9, 100.02, 100.10]

    def quote(self, symbol, day, minute):
        return (100.0, 100.2, 100, 100, 0.0)


class Pipeline(unittest.TestCase):
    def test_one_session_replays_the_printed_order_and_charges_the_spread(self):
        result = bt.replay(FakeFeed(), today="2024-06-04")
        self.assertTrue(result["complete"])
        (trade,) = result["judge"]["gated"]
        self.assertEqual((trade["t"], trade["grade"], trade["minute"], trade["kind"]), ("AAA", "A", "10:00", "flat"))
        self.assertAlmostEqual(trade["fill"], 100.10)
        self.assertAlmostEqual(trade["exit"], 101.0)
        self.assertEqual(trade["shares"], 66)
        gross, cost, net = bt.net_of(100.10, 101.0, 1.5, 66, 0.20)
        self.assertAlmostEqual(trade["net_r"], net)
        self.assertAlmostEqual(trade["cost_r"], cost)
        self.assertGreater(trade["net_r_short"], -10)  # the short is a number, not a crash
        self.assertIn("never an order", Path(bt.__file__).read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
