"""The locked post-earnings rule. No network."""
import unittest
from pathlib import Path

from bot import pead


def bar(day, o, h, l, c, v):
    return (day, o, h, l, c, v)


class Lock(unittest.TestCase):
    def test_the_hold_the_keywords_and_the_fees_are_fixed(self):
        self.assertEqual(pead.GATE_HOLD, 20)
        self.assertEqual(pead.HOLDS, (10, 20, 40))
        self.assertEqual((pead.GAP_MIN, pead.RVOL_MIN, pead.PRICE_MIN, pead.ADV_MIN), (0.05, 3.0, 5.0, 20_000_000.0))
        text = Path(pead.__file__).read_text(encoding="utf-8")
        self.assertIn("does not place or transmit an order", text)
        self.assertIn("IBKR Lite", text)
        self.assertIn("buy-and-hold SPY", text)
        self.assertNotIn("ib_insync", text)
        for word in ("earnings", "results", "quarter", "EPS", "revenue"):
            self.assertTrue(pead.headline_matches(f"the {word} today"))


class Event(unittest.TestCase):
    def _priors(self, n=20, volume=100, price=20):
        return [volume] * n, [price * volume] * n

    def test_a_five_percent_gap_and_the_top_half_qualify(self):
        vols, dollars = [100_000] * 20, [21_000_000] * 20
        good = bar("2024-06-03", 21.0, 22.0, 20.5, 21.8, 400_000)
        self.assertTrue(pead.event_ok(20.0, good, vols, dollars))
        shy = bar("2024-06-03", 20.9, 22.0, 20.5, 21.8, 400_000)
        self.assertFalse(pead.event_ok(20.0, shy, vols, dollars))
        mid_low = bar("2024-06-03", 21.0, 23.0, 20.0, 21.0, 400_000)
        self.assertFalse(pead.event_ok(20.0, mid_low, vols, dollars))
        cheap = bar("2024-06-03", 4.2, 4.4, 4.1, 4.3, 400_000)
        self.assertFalse(pead.event_ok(4.0, cheap, vols, dollars))

    def test_the_headline_window_is_twenty_four_hours(self):
        self.assertTrue(pead.headline_matches("Acme earnings beat"))
        self.assertTrue(pead.headline_matches("Q2 revenue"))
        self.assertFalse(pead.headline_matches("analyst upgrade"))
        self.assertTrue(pead.in_prior_24h("2024-06-02T13:30:00Z", "2024-06-03"))
        self.assertFalse(pead.in_prior_24h("2024-06-03T13:30:00Z", "2024-06-03"))
        self.assertFalse(pead.in_prior_24h("2024-06-01T13:00:00Z", "2024-06-03"))


class Hold(unittest.TestCase):
    def test_the_disaster_stop_and_the_twentieth_close(self):
        future = [("2024-06-04", 21.0, 21.5, 20.8, 21.2)]
        for i in range(1, 20):
            future.append((f"2024-07-{i:02d}", 21.2, 21.6, 20.9, 21.4))
        got = pead.mark(future, 21.0, 20.0, 20, "long")
        self.assertEqual(got[1], "time")
        self.assertAlmostEqual(got[0], 21.4)
        stopped = [("2024-06-04", 21.0, 21.2, 19.5, 20.2)] + future[1:]
        hit = pead.mark(stopped, 21.0, 20.0, 20, "long")
        self.assertEqual(hit[:2], (20.0, "stop"))
        self.assertIsNone(pead.mark(future, 19.0, 20.0, 20, "long"))

    def test_ten_sessions_exit_sooner_than_forty(self):
        future = [(f"2024-06-{i:02d}", 10, 11, 9, 10.5) for i in range(1, 29)]
        future += [(f"2024-07-{i:02d}", 10, 11, 9, 12.0) for i in range(1, 21)]
        ten = pead.mark(future, 10.0, 8.0, 10, "long")
        forty = pead.mark(future, 10.0, 8.0, 40, "long")
        self.assertEqual(ten[2], future[9][0])
        self.assertEqual(forty[2], future[39][0])
        self.assertAlmostEqual(ten[0], 10.5)
        self.assertAlmostEqual(forty[0], 12.0)
        halted = future[:5] + [None] + future[6:10]
        self.assertEqual(pead.mark(halted, 10.0, 8.0, 10, "long")[1], "time")
        delisted = future[:5] + [None] * 5
        gone = pead.mark(delisted, 10.0, 8.0, 10, "long")
        self.assertEqual(gone[1], "last")
        self.assertEqual(gone[2], future[4][0])

    def test_one_position_waits_for_settlement(self):
        rows = [
            {"entry_day": "2024-01-02", "exit_day": "2024-02-01", "adv": 50, "t": "AAA"},
            {"entry_day": "2024-01-03", "exit_day": "2024-01-10", "adv": 40, "t": "BBB"},
            {"entry_day": "2024-02-02", "exit_day": "2024-02-20", "adv": 10, "t": "CCC"},
        ]
        self.assertEqual([c["t"] for c in pead.schedule(rows)], ["AAA", "CCC"])


class Costs(unittest.TestCase):
    def test_lite_drops_the_fee_and_spy_has_to_be_beaten(self):
        pro = pead.score(10.0, 11.0, 9.0, 9.0, None)
        self.assertGreater(pro["lite_r"], pro["net_r"])
        self.assertGreater(pro["lite_return"], pro["net_return"])
        trades = [{"net_return": 0.02, "spy": 0.01, "net_r": 0.2, "net_r_short": -0.2, "day": "2024-06-03",
                   "t": "AAA"}]
        self.assertTrue(pead.beats_spy(trades))
        tied = [{"net_return": 0.01, "spy": 0.01}]
        self.assertFalse(pead.beats_spy(tied))
        self.assertFalse(pead.beats_spy([{"net_return": 0.02, "spy": None}]))
        self.assertIsNone(pead.spy_return(0, 10))
        self.assertAlmostEqual(pead.spy_return(100, 110), 0.10)
