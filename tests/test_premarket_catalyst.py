"""The locked premarket-catalyst rule. No network."""
import datetime as dt
import unittest
from pathlib import Path

from bot import premarket_catalyst as pc


def minute(hhmm, o, h, l, c, v=1_000):
    return (hhmm, o, h, l, c, v)


class Lock(unittest.TestCase):
    def test_the_window_the_band_and_the_wires_are_fixed(self):
        self.assertEqual((pc.PRICE_LO, pc.PRICE_HI, pc.RVOL_MIN, pc.FLOAT_MAX), (0.50, 5.0, 5.0, 20_000_000.0))
        self.assertEqual((pc.ENTRY_FROM, pc.ENTRY_TO, pc.TARGET_R), ("09:35", "10:30", 2.0))
        text = Path(pc.__file__).read_text(encoding="utf-8")
        self.assertIn("first 1-minute close", text)
        self.assertIn("does not place or transmit an order", text)
        self.assertIn("adjustment=split", text)
        self.assertNotIn("ib_insync", text)
        for name in ("LNKD", "YHOO", "WFM", "MON", "TWX", "SHLD", "SPLS", "PNRA", "CAB"):
            self.assertIn(name, text)
        self.assertTrue(any("never stored" in gap for gap in pc.GAPS))
        self.assertTrue(any("not float" in gap for gap in pc.GAPS))


class Screen(unittest.TestCase):
    def test_relative_volume_needs_ten_prints_and_five_times(self):
        self.assertTrue(pc.price_ok(0.50))
        self.assertTrue(pc.price_ok(4.99))
        self.assertFalse(pc.price_ok(5.0))
        self.assertFalse(pc.price_ok(0.49))
        prior = [100] * 10 + [None] * 10
        self.assertTrue(pc.rvol_ok(500, prior))
        self.assertFalse(pc.rvol_ok(499, prior))
        self.assertFalse(pc.rvol_ok(500, [100] * 9))

    def test_premarket_stops_at_09_25_and_the_open_is_the_09_30_bar(self):
        bars = [minute("09:25", 1, 1.4, 0.9, 1.1, 10), minute("09:30", 1.2, 1.5, 1.0, 1.3, 50),
                minute("04:00", 1, 1.2, 0.8, 1.0, 5)]
        self.assertEqual(pc.premarket(bars), (15, 1.4))
        self.assertEqual(pc.opening_low(bars), 1.0)

    def test_a_reverse_split_is_not_a_penny_gap(self):
        # Raw 1.00 to 10.00 is a 1-for-10 reverse split. Split-adjusted, both closes are 10.
        self.assertGreater(10.0 / 1.0 - 1, 1)
        self.assertEqual(10.0 / 10.0 - 1, 0)
        self.assertFalse(pc.price_ok(10.0))
        banned = pc.ex_dates([{"symbol": "AAA", "ex_date": "2024-06-03", "old_rate": 1, "new_rate": 10}])
        self.assertEqual(banned["AAA"], {"2024-06-03"})
        closes = {"2020-08-28": 100.0, "2020-08-31": 102.0}
        ok, _ = pc.split_sane(closes, ["2020-08-28", "2020-08-31"])
        self.assertTrue(ok)
        bad, _ = pc.split_sane({"2020-08-28": 100.0, "2020-08-31": 400.0}, ["2020-08-28", "2020-08-31"])
        self.assertFalse(bad)

    def test_wires_pass_and_a_recap_does_not(self):
        self.assertTrue(pc.primary_source("Business Wire"))
        self.assertTrue(pc.primary_source("GlobeNewswire Inc."))
        self.assertFalse(pc.primary_source("Benzinga"))
        self.assertFalse(pc.primary_source(""))
        self.assertFalse(pc.primary_source(None))

    def test_s1_424b_and_the_shelf_block_thirty_days(self):
        day = "2024-06-20"
        self.assertTrue(pc.filing_blocks([("2024-06-01T16:00", "S-1")], day))
        self.assertTrue(pc.filing_blocks([("2024-06-19T09:29", "424B5")], day))
        self.assertTrue(pc.filing_blocks([("2024-06-10T12:00", "S-3ASR")], day))
        self.assertFalse(pc.filing_blocks([("2024-06-10T12:00", "8-K")], day))
        self.assertFalse(pc.filing_blocks([("2024-05-20T09:30", "S-1")], day))
        self.assertFalse(pc.filing_blocks([("2024-06-20T09:30", "S-1")], day))

    def test_shares_outstanding_under_twenty_million_is_the_proxy(self):
        day = "2024-06-20"
        self.assertEqual(pc.shares_ok([("2024-03-01", "2024-02-28", 19_000_000)], day)[0], True)
        self.assertEqual(pc.shares_ok([("2024-03-01", "2024-02-28", 20_000_000)], day)[1], "large")
        self.assertEqual(pc.shares_ok([("2024-03-01", "2024-02-28", 100)], day)[1], "scale")
        self.assertEqual(pc.shares_ok([("2022-01-01", "2022-01-01", 1_000_000)], day)[1], "stale")
        self.assertEqual(pc.shares_ok([("2024-06-20", "2024-03-31", 1_000_000)], day)[1], "none")


class Entry(unittest.TestCase):
    def _minutes(self):
        rows = [minute("09:30", 1.10, 1.15, 1.00, 1.12),
                minute("09:34", 1.12, 1.40, 1.11, 1.35),
                minute("09:35", 1.16, 1.18, 1.14, 1.17),
                minute("09:36", 1.18, 1.30, 1.16, 1.25),
                minute("09:37", 1.25, 1.28, 1.22, 1.26)]
        return rows

    def test_the_first_close_above_the_premarket_high_from_09_35(self):
        got = pc.setup(self._minutes(), 1.20, 1.00, "15:55")
        self.assertEqual(got["fill_minute"], "09:36")
        self.assertAlmostEqual(got["entry"], 1.25)
        self.assertAlmostEqual(got["stop"], 0.99)
        self.assertAlmostEqual(got["target"], 1.25 + 2 * (1.25 - 0.99))
        self.assertEqual(got["kind_exit"], "last")

    def test_a_close_at_10_31_does_not_enter(self):
        rows = [minute("09:30", 1.1, 1.2, 1.0, 1.1), minute("10:31", 1.2, 1.5, 1.15, 1.40)]
        self.assertIsNone(pc.setup(rows, 1.20, 1.00, "15:55"))

    def test_the_next_bar_can_stop_the_trade_out(self):
        rows = [minute("09:35", 1.10, 1.30, 1.05, 1.25), minute("09:36", 1.20, 1.22, 0.80, 0.90)]
        got = pc.setup(rows, 1.20, 1.00, "15:55")
        self.assertEqual(got["kind_exit"], "stop")
        self.assertAlmostEqual(got["exit"], 0.99)

    def test_the_highest_relative_volume_is_the_day_trade(self):
        chosen = pc.pick([{"t": "BBB", "rvol": 8}, {"t": "AAA", "rvol": 8}, {"t": "CCC", "rvol": 7}])
        self.assertEqual(chosen["t"], "AAA")

    def test_the_quote_is_scaled_into_split_adjusted_dollars(self):
        self.assertAlmostEqual(pc.quote_spread(0.20, 10.0, 1.0), 0.02)
        self.assertIsNone(pc.quote_spread(None, 10.0, 1.0))


class Universe(unittest.TestCase):
    def test_inactive_names_stay_and_funds_do_not(self):
        assets = [
            {"symbol": "AAA", "exchange": "NASDAQ", "name": "Acme Corp", "status": "active"},
            {"symbol": "AAA", "exchange": "NASDAQ", "name": "Acme Corp", "status": "inactive"},
            {"symbol": "QQQ", "exchange": "NASDAQ", "name": "Invesco QQQ Trust", "status": "active"},
            {"symbol": "OLD", "exchange": "NYSE", "name": "Old Co", "status": "inactive"},
            {"symbol": "WRNT", "exchange": "NASDAQ", "name": "Old Co Warrant", "status": "active"},
        ]
        symbols, indexed = pc.universe(assets)
        self.assertEqual(symbols, ["AAA", "OLD"])
        self.assertEqual(indexed["AAA"]["status"], "inactive")
        self.assertEqual(indexed["OLD"]["status"], "inactive")


class Dates(unittest.TestCase):
    def test_thirty_calendar_days_not_sessions(self):
        # 2024-05-21 09:30 is exactly 30 days before 2024-06-20 09:30, so it still blocks.
        self.assertTrue(pc.filing_blocks([("2024-05-21T09:30", "S-1/A")], "2024-06-20"))
        self.assertFalse(pc.filing_blocks([("2024-05-21T09:29", "S-1/A")], "2024-06-20"))
        self.assertEqual(pc.flat_minute("13:00"), "12:59")
        _ = dt.date(2024, 5, 21)
