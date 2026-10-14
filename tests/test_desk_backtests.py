"""The locked penny and swing rules. No network."""
import datetime as dt
import unittest
from pathlib import Path

from bot import desk_gate as gate
from bot import penny_play as penny
from bot import penny_play_backtest as penny_bt
from bot import swing_pullback as swing
from bot import swing_pullback_backtest as swing_bt


def minute(hhmm, o, h, l, c, v=1_000, vw=None):
    return (hhmm, o, h, l, c, v, o if vw is None else vw)


class Lock(unittest.TestCase):
    def test_the_gate_and_the_windows_are_fixed(self):
        self.assertEqual((penny_bt.START, penny_bt.OOS), ("2016-01-01", "2023-01-01"))
        self.assertEqual((swing_bt.START, swing_bt.OOS), ("2016-01-01", "2023-01-01"))
        self.assertEqual((penny.PRICE_LO, penny.PRICE_HI, penny.GAP_MIN, penny.RVOL_MIN), (0.50, 5.0, 0.10, 5.0))
        self.assertEqual((swing.PRICE_MIN, swing.ADV_MIN, swing.HOLD), (5.0, 20_000_000.0, 15))
        self.assertEqual(gate.ACCOUNT, 460.0)
        self.assertEqual((gate.COMMISSION, gate.MIN_ORDER, gate.HAIRCUT), (0.0035, 0.35, 0.01))
        text = Path(gate.__file__).read_text(encoding="utf-8")
        self.assertIn("t > 2", text)
        self.assertIn("mean net R > 0 in 2023 and in 2024 and in 2025 and in 2026", text)
        self.assertIn("random-direction means", text)
        for mod in (gate, penny, swing, penny_bt, swing_bt):
            body = Path(mod.__file__).read_text(encoding="utf-8").lower()
            self.assertIn("does not place or transmit an order", body)
            self.assertNotIn("ib_insync", body)
            self.assertNotIn("submit", body)


class Walk(unittest.TestCase):
    def test_a_bar_that_reaches_both_exits_at_the_stop(self):
        bars = [("10:00", 1.05, 2.0, 0.5, 1.5)]
        self.assertEqual(gate.walk(bars, 0.9, 1.4, "long", True)[:2], (0.9, "stop"))

    def test_the_open_before_an_inside_fill_is_not_an_exit(self):
        # Filled at 1.20 inside a bar that opened at 1.10. The open is above the stop.
        bars = [("10:00", 1.10, 1.30, 1.05, 1.25), ("15:55", 1.25, 1.26, 1.24, 1.25)]
        got = gate.walk(bars, 0.99, 1.80, "long", False)
        self.assertEqual(got[:2], (1.25, "time"))


class Penny(unittest.TestCase):
    def test_gap_and_relative_volume(self):
        self.assertTrue(penny.gap_ok(1.0, 1.10))
        self.assertTrue(penny.gap_ok(4.99, 5.50))
        self.assertFalse(penny.gap_ok(5.0, 6.0))
        self.assertFalse(penny.gap_ok(0.40, 0.50))
        self.assertFalse(penny.rvol_ok(100, [10] * 9))
        self.assertTrue(penny.rvol_ok(50, [10] * 10))
        self.assertFalse(penny.rvol_ok(40, [10] * 10))

    def test_opening_range_fills_at_the_high_and_stops_a_cent_under(self):
        bars = [minute("09:30", 1.10, 1.20, 1.00, 1.12)]
        bars += [minute(f"09:{m:02d}", 1.12, 1.15, 1.10, 1.12) for m in range(31, 35)]
        bars.append(minute("09:35", 1.15, 1.25, 1.14, 1.22))
        bars.append(minute("15:55", 1.22, 1.23, 1.21, 1.22))
        got = penny.play(bars, "15:55", "orb")
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got["entry"], 1.20)
        self.assertAlmostEqual(got["stop"], 0.99)
        self.assertAlmostEqual(got["target"], 1.20 + 2 * (1.20 - 0.99))
        self.assertEqual(got["kind_exit"], "time")
        scored = penny.score(got, None)
        self.assertEqual(scored["shares"], int(460 / 1.20))

    def test_vwap_pullback_stops_under_the_pullback_low(self):
        bars = [minute("09:30", 1.00, 1.02, 0.98, 1.00, v=1_000, vw=1.00)]
        bars += [minute(f"09:{m:02d}", 1.00, 1.01, 0.99, 1.00, v=1_000, vw=1.00) for m in range(31, 35)]
        bars.append(minute("09:35", 0.95, 0.96, 0.88, 0.90, v=1_000, vw=0.90))
        bars.append(minute("09:36", 0.94, 1.08, 0.89, 1.06, v=100, vw=1.06))
        bars.append(minute("09:37", 1.04, 1.05, 1.03, 1.04, v=100, vw=1.04))
        bars.append(minute("15:55", 1.04, 1.05, 1.03, 1.04, v=100, vw=1.04))
        got = penny.play(bars, "15:55", "vwap")
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got["entry"], 1.04)
        self.assertAlmostEqual(got["stop"], 0.88 - 0.01)
        self.assertEqual(got["fill_minute"], "09:37")

    def test_the_largest_gap_is_the_day_trade(self):
        rows = [{"gap": 0.2, "t": "BBB"}, {"gap": 0.4, "t": "AAA"}, {"gap": 0.4, "t": "ZZZ"}]
        self.assertEqual(penny.pick(rows)["t"], "AAA")

    def test_missing_quote_is_a_one_percent_round_trip(self):
        pnl, r, mode = gate.net_r(1.0, 1.1, 0.9, 100, None, "long")
        self.assertEqual(mode, "haircut")
        slip = 0.005 * (1.0 + 1.1) * 100
        fee = 0.70
        self.assertAlmostEqual(pnl, 10 - slip - fee)
        self.assertAlmostEqual(r, pnl / 10)

    def test_a_quote_charges_the_full_spread(self):
        pnl, r, mode = gate.net_r(1.0, 1.1, 0.9, 100, 0.02, "long")
        self.assertEqual(mode, "quote")
        self.assertAlmostEqual(pnl, 10 - 2 - 0.70)


class Swing(unittest.TestCase):
    def _hist(self):
        rows = []
        for i in range(70):
            close = 50 + i
            high = close + 0.5
            if i == 50:
                high = 140
            low = close - 0.2
            rows.append((f"2024-01-{i:02d}", close - 0.05, high, low, close, 1_000_000))
        # Signal day: tag the 20-day average and close in the top third.
        rows[-1] = ("2024-03-01", 118.0, 122.0, 100.0, 120.0, 1_000_000)
        return rows

    def test_a_rising_average_and_a_top_third_close_sets_the_trade(self):
        got = swing.signal(self._hist(), 121.0)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got["entry"], 121.0)
        self.assertAlmostEqual(got["stop"], 99.99)
        self.assertGreater(got["target"], 121.0)

    def test_a_close_in_the_bottom_third_is_refused(self):
        rows = self._hist()
        rows[-1] = ("2024-03-01", 118.0, 200.0, 100.0, 119.0, 1_000_000)
        self.assertIsNone(swing.signal(rows, 121.0))

    def test_the_proxy_is_the_second_monday_and_blocks_the_hold(self):
        self.assertEqual(swing.second_monday(2024, 1), dt.date(2024, 1, 8))
        banned = swing.blackout_days(2024, 2024)
        self.assertIn("2024-01-08", banned)
        self.assertIn("2024-01-17", banned)
        self.assertNotIn("2024-01-18", banned)
        cands = [
            {"entry_day": "2024-01-02", "exit_day": "2024-01-20", "adv": 50, "t": "AAA",
             "hold": ["2024-01-02", "2024-01-08"]},
            {"entry_day": "2024-01-05", "exit_day": "2024-01-09", "adv": 40, "t": "BBB", "hold": ["2024-01-05"]},
            {"entry_day": "2024-02-01", "exit_day": "2024-02-02", "adv": 10, "t": "CCC", "hold": ["2024-02-01"]},
        ]
        plain = swing.schedule(cands, banned, False)
        proxy = swing.schedule(cands, banned, True)
        self.assertEqual([c["t"] for c in plain], ["AAA", "CCC"])
        self.assertEqual([c["t"] for c in proxy], ["BBB", "CCC"])

    def test_same_day_stop_and_target_exits_at_the_stop(self):
        future = [("2024-01-02", 100.0, 120.0, 90.0, 110.0)] + [None] * 14
        self.assertEqual(swing.hold_exit(future, 100.0, 95.0, 115.0)[:2], (95.0, "stop"))


class Gate(unittest.TestCase):
    def _rows(self, value=0.2, short=-1.0, bad_year=None):
        rows = []
        for i in range(100):
            year = 2023 + (i % 4)
            if bad_year and year == bad_year:
                net = -0.5
            else:
                net = value + (i % 5) * 0.01
            rows.append({"day": f"{year}-06-{(i % 27) + 1:02d}", "t": f"N{i}", "variant": "x",
                         "net_r": net, "net_r_short": short})
        return rows

    def test_a_positive_book_passes_and_a_tied_random_mean_does_not(self):
        self.assertTrue(gate.judge(self._rows())["go"])
        tied = self._rows(value=0.2, short=0.2)
        for row, base in zip(tied, self._rows()):
            row["net_r_short"] = row["net_r"]
        self.assertFalse(gate.judge(tied)["go"])
        self.assertFalse(gate.judge(self._rows(bad_year=2025))["go"])
        weak = self._rows(value=-0.2, short=-0.3)
        self.assertFalse(gate.judge(weak)["go"])
