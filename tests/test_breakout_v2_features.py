"""Daily feature definitions for the v2 post-mortem. No network."""
import unittest

from bot.breakout_v2_features import failed_probes, row_features


def bars():
    """40 flat sessions, then one probe, then the trade day. Closes stay at 100 except the probe."""
    out = []
    for i in range(40):
        d = f"2024-01-{i + 1:02d}" if i < 31 else f"2024-02-{i - 30:02d}"
        out.append((d, 100.0, 101.0, 99.0, 100.0, 1_000_000))
    # A day that trades through the prior high (101) and closes back under it.
    out.append(("2024-02-11", 100.0, 102.0, 99.0, 100.5, 1_000_000))
    out.append(("2024-02-12", 101.0, 101.5, 100.0, 101.2, 1_000_000))
    return out


class Features(unittest.TestCase):
    def test_a_failed_probe_is_a_high_that_closed_back_under(self):
        prior = bars()[:-1]
        self.assertEqual(failed_probes(prior), 1)

    def test_the_gap_uses_today_open_and_the_high_excludes_today(self):
        got = row_features(bars(), "2024-02-12")
        self.assertAlmostEqual(got["gap"], 101.0 / 100.5 - 1)
        self.assertAlmostEqual(got["prev_close"], 100.5)
        self.assertGreaterEqual(got["high20"], 102.0)
        self.assertEqual(got["probes"], 1)
        self.assertIsNotNone(got["atr"])
        self.assertIsNotNone(got["ret5"])

    def test_a_short_history_is_skipped(self):
        self.assertIsNone(row_features(bars()[:10], "2024-01-10"))


if __name__ == "__main__":
    unittest.main()
