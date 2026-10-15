"""The locked $3–$20 premarket book. No network."""
import inspect
import unittest
from pathlib import Path

from bot import premarket_catalyst as pc
from bot import premarket_mid as mid
from bot.premarket_catalyst_backtest import _candidates, _news, _play, _screen


def _stub(**extra):
    base = {"run": "2026-10-15", "trades": [], "complete": True, "counts": {"rvol": 0},
            "end": "2026-10-14", "first_bar": "2016-01-04", "universe": 1, "inactive_names": 0,
            "inactive_with_bars": 0, "aapl": "ok", "split_days": 0, "probe": [], "reverses": {}}
    base.update(extra)
    return base


class Lock(unittest.TestCase):
    def test_the_band_is_three_through_twenty_and_the_sub_five_band_is_unchanged(self):
        self.assertEqual((mid.MID_LO, mid.MID_HI, mid.MID_LULD), (3.0, 20.0, 0.10))
        self.assertEqual((pc.PRICE_LO, pc.PRICE_HI), (0.50, 5.0))
        self.assertTrue(mid.mid_price_ok(3.0))
        self.assertTrue(mid.mid_price_ok(20.0))
        self.assertTrue(mid.mid_price_ok(5.0))
        self.assertFalse(mid.mid_price_ok(2.99))
        self.assertFalse(mid.mid_price_ok(20.01))
        self.assertFalse(mid.mid_price_ok(None))
        self.assertTrue(pc.price_ok(4.99))
        self.assertFalse(pc.price_ok(5.0))
        self.assertFalse(pc.price_ok(12.0))
        text = Path(mid.__file__).read_text(encoding="utf-8")
        back = Path(mid.__file__).with_name("premarket_mid_backtest.py").read_text(encoding="utf-8")
        self.assertIn("does not place or transmit an order", text)
        self.assertIn("does not place or transmit an order", back)
        self.assertNotIn("ib_insync", text)
        self.assertNotIn("ib_insync", back)
        self.assertEqual(pc.luld_band(3), 0.20)
        self.assertIs(inspect.signature(pc.desk_size).parameters["band"].default, None)
        self.assertIs(inspect.signature(_candidates).parameters["accept"].default, pc.price_ok)
        self.assertIs(inspect.signature(_play).parameters["scorer"].default, pc.score)
        self.assertEqual(inspect.signature(_news).parameters["cache_name"].default, "news")
        self.assertEqual(inspect.signature(_screen).parameters["news_cache"].default, "news")

    def test_the_sub_five_workflow_stays_dispatch_only(self):
        text = Path(".github/workflows/premarket-catalyst.yml").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?m)^  pull_request:")
        mid_yml = Path(".github/workflows/premarket-mid.yml").read_text(encoding="utf-8")
        self.assertIn("python -m bot.premarket_mid_backtest", mid_yml)
        self.assertIn("t3-mid-", mid_yml)
        self.assertIn("t3-alpaca-", mid_yml)
        self.assertIn("cancel-in-progress: false", mid_yml)


class Candidates(unittest.TestCase):
    def test_the_default_filter_stays_under_five_and_the_mid_filter_is_inclusive(self):
        sessions = ["2015-12-31", "2016-01-04"]

        def book(close):
            return {"ZZ": [("2015-12-31", 1, 1, 1, close, 1)]}

        self.assertEqual(len(_candidates(book(4.99), sessions, {})[0]), 1)
        self.assertEqual(_candidates(book(12.0), sessions, {})[0], [])
        self.assertEqual(_candidates(book(5.0), sessions, {})[0], [])
        self.assertEqual(len(_candidates(book(12.0), sessions, {}, mid.mid_price_ok)[0]), 1)
        self.assertEqual(len(_candidates(book(3.0), sessions, {}, mid.mid_price_ok)[0]), 1)
        self.assertEqual(len(_candidates(book(20.0), sessions, {}, mid.mid_price_ok)[0]), 1)
        self.assertEqual(len(_candidates(book(5.0), sessions, {}, mid.mid_price_ok)[0]), 1)
        self.assertEqual(_candidates(book(2.99), sessions, {}, mid.mid_price_ok)[0], [])
        self.assertEqual(_candidates(book(20.01), sessions, {}, mid.mid_price_ok)[0], [])
        empty, dropped = _candidates(book(12.0), sessions, {"ZZ": {"2016-01-04"}}, mid.mid_price_ok)
        self.assertEqual(empty, [])
        self.assertEqual(dropped, 1)


class Costs(unittest.TestCase):
    def _row(self, entry):
        return {"entry": entry, "exit": entry + 0.5, "stop": entry - 0.5, "short_exit": entry - 0.4,
                "day": "2024-06-03", "t": "AAA"}

    def test_under_five_follows_the_cheap_scorer_and_five_and_above_does_not(self):
        cheap = self._row(4.0)
        self.assertAlmostEqual(mid.score_priced(cheap, None, None, 0.01)["net_r"],
                               pc.score(cheap, None, None, 0.01)["net_r"])
        self.assertGreater(mid.score_priced(cheap, None, None, 0.005)["net_r"],
                           mid.score_priced(cheap, None, None, 0.02)["net_r"])
        priced = self._row(5.0)
        hair = mid.score_priced(priced, None, None, 0.005)
        again = mid.score_priced(priced, None, None, 0.02)
        self.assertEqual(hair["cost_mode"], "haircut")
        self.assertEqual(hair["shares"], 92)
        self.assertAlmostEqual(hair["fill_entry"], 5.0)
        self.assertAlmostEqual(hair["net_r"], again["net_r"])
        self.assertAlmostEqual(hair["net_r"], 39.17 / 46)
        self.assertNotAlmostEqual(hair["net_r"], pc.score(priced, None, None, 0.01)["net_r"])
        quoted = mid.score_priced(priced, (4.95, 5.05), (5.40, 5.60), 0.02)
        wide_exit = mid.score_priced(priced, (4.95, 5.05), (1.0, 9.0), 0.01)
        self.assertEqual(quoted["cost_mode"], "quote")
        self.assertAlmostEqual(quoted["fill_entry"], 5.0)
        self.assertAlmostEqual(quoted["net_r"], 34.80 / 46)
        self.assertAlmostEqual(quoted["net_r"], wide_exit["net_r"])
        slipped = pc.score(priced, None, None, 0.01)
        self.assertEqual(slipped["cost_mode"], "slip")
        self.assertAlmostEqual(slipped["fill_entry"], 5.0 * 1.01)


class Desk(unittest.TestCase):
    def test_three_dollars_is_twenty_percent_unless_this_book_forces_ten(self):
        default = pc.desk_size(3.0, 2.90)
        self.assertEqual(default["luld"], 0.20)
        self.assertAlmostEqual(default["position"], 46.0)
        self.assertFalse(default["kept"])
        forced = pc.desk_size(3.0, 2.90, band=mid.MID_LULD)
        self.assertEqual(forced["luld"], 0.10)
        self.assertAlmostEqual(forced["position"], 92.0)
        self.assertTrue(forced["kept"])
        self.assertAlmostEqual(pc.desk_size(20.0, 19.90, band=0.10)["position"], 92.0)
        edge = pc.desk_size(10.0, 9.08, band=0.10)
        self.assertAlmostEqual(edge["position"], 50.0)
        self.assertTrue(edge["kept"])
        wide = pc.desk_size(10.0, 9.07, band=0.10)
        self.assertLess(wide["position"], 50.0)
        self.assertFalse(wide["kept"])


class Report(unittest.TestCase):
    def test_the_empty_book_is_a_separate_no_go(self):
        from bot.premarket_mid_backtest import render
        text = render(_stub())
        self.assertIn("Premarket $3–$20: UNKNOWN (insufficient sample)", text)
        self.assertIn("Separate from the sub-$5 book", text)
        self.assertIn("both ends inclusive", text)
        self.assertIn("Excluded 0 of 0 trades", text)
        self.assertIn("Not tested", text)
        self.assertIn("the tax is zero", text)
        self.assertIn("Trades affected by a halt: 0 of 0", text)
        self.assertIn("Signals that survive: 0 of 0", text)
        self.assertIn("$92", text)
        self.assertIn("9.2%", text)
        self.assertNotIn("Premarket catalyst: UNKNOWN (insufficient sample)", text)

    def test_a_recorded_trade_keeps_the_halt_the_tax_and_the_ten_percent_count(self):
        from bot.premarket_mid_backtest import render
        trade = {"day": "2024-06-03", "t": "AAA", "entry": 10.0, "stop": 9.50, "exit": 11.0,
                 "net_r": 0.5, "net_r_short": -0.4, "net_r_no_commission": 0.55, "pnl": 20.0,
                 "shares": 46, "cost_mode": "haircut", "net_r_0_5": 0.5, "net_r_2": 0.5,
                 "halt_affected": True, "planned_loss": 0.50, "realized_loss": 0.80}
        text = render(_stub(trades=[trade], counts={"rvol": 3, "news": 1},
                            reverses={"AAA": ["2024-01-01"]}))
        self.assertIn("Premarket $3–$20: UNKNOWN (insufficient sample)", text)
        self.assertIn("Excluded 1 of 1 trades", text)
        self.assertIn("Tax $5.00", text)
        self.assertIn("After tax $15.00", text)
        self.assertIn("Trades affected by a halt: 1 of 1", text)
        self.assertIn("Signals that survive: 1 of 1 (100.0%)", text)
        self.assertIn("The locked submissions screen", text)
        self.assertIn("Round-trip haircut: 1", text)
