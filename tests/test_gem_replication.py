"""The standalone gem.py book must match the main month-end signals."""
import unittest

import gem
from bot.trend_backtest import load_symbol
from bot.trend_exec import gem_signals
from bot.gates import load_replication


def main_holdings(defensive):
    spy = load_symbol("SPY")["adj"]
    bil = load_symbol("BIL")["adj"]
    veu = load_symbol("VEU")["adj"]
    signals = gem_signals(spy, bil, veu, defensive, "SPY")
    held = {}
    for day, asset in signals.items():
        year, month = int(day[:4]), int(day[5:7])
        month += 1
        if month == 13:
            month = 1
            year += 1
        held[f"{year:04d}-{month:02d}"] = asset
    september = [asset for day, asset in signals.items() if day.startswith("2026-09")]
    return held, september


class GemReplicationTests(unittest.TestCase):
    def test_holdings_and_no_cost_cagr(self):
        policy_gap = 0.005
        for trial_id, defensive in (("gem-agg", "AGG"), ("gem-bil", "BIL")):
            independent = gem.book(defensive)
            main, september = main_holdings(defensive)
            months = gem.months_between("2014-01", "2025-12")
            mismatch = [
                month for month in months
                if independent["held"].get(month) != main.get(month)
            ]
            window = {month: main[month] for month in months if month in main}
            main_cagr, count = gem.no_cost_cagr(window, independent["prices"])
            self.assertEqual(count, 144)
            self.assertLessEqual(len(mismatch), 2, mismatch)
            self.assertLessEqual(abs(independent["cagr"] - main_cagr), policy_gap)
            self.assertEqual(independent["sep_2026"], "VEU")
            self.assertEqual(september, ["VEU"])
            recorded = load_replication()[trial_id]
            self.assertTrue(recorded["passed"])
            self.assertEqual(recorded["mismatch"], len(mismatch))
            self.assertAlmostEqual(recorded["independent_cagr"], independent["cagr"])
            self.assertAlmostEqual(recorded["main_cagr"], main_cagr)
            self.assertAlmostEqual(recorded["cagr_gap"], abs(independent["cagr"] - main_cagr))


if __name__ == "__main__":
    unittest.main()
