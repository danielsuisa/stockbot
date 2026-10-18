"""Synthetic checks of the P2 measurement mechanics (no store needed): seeds, draws, outcome windows."""
import datetime as dt
import unittest

import polars as pl

from scalp_eval import measure_p2 as M

UTC = dt.timezone.utc


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


class Draws(unittest.TestCase):
    def test_seed_is_sha256_of_date_time_tag(self):
        import hashlib
        t = utc(2024, 4, 17, 14, 0)  # 10:00 ET
        self.assertEqual(M.seed(dt.date(2024, 4, 17), t, "p2-random-v1"),
                         int(hashlib.sha256(b"2024-04-17|10:00|p2-random-v1").hexdigest(), 16))

    def test_random_draw_reproducible_without_replacement(self):
        members = [f"S{i:03d}" for i in range(500)]
        a = M.random_draw(dt.date(2024, 4, 17), utc(2024, 4, 17, 14), list(reversed(members)), 20)
        b = M.random_draw(dt.date(2024, 4, 17), utc(2024, 4, 17, 14), members, 20)
        self.assertEqual(a, b)  # input order does not matter
        self.assertEqual(len(set(a)), 20)

    def test_atr_matched_same_decile(self):
        atr = {f"S{i:02d}": i / 100 for i in range(100)}
        atr["NOATR"] = None
        out = M.atr_matched_draw(dt.date(2024, 4, 17), utc(2024, 4, 17, 14), atr, ["S95", "S05", "NOATR"])
        self.assertIsNone(out[0])  # sorted candidates: NOATR first, no match
        self.assertTrue(0 <= int(out[1][1:]) <= 9)  # S05 -> decile 0
        self.assertTrue(90 <= int(out[2][1:]) <= 99)  # S95 -> decile 9


class Outcome(unittest.TestCase):
    def test_windows(self):
        t0 = utc(2024, 4, 17, 13, 30)
        rows = [{"symbol": "A", "event_at": t0 + dt.timedelta(minutes=i), "high": 10.0 + (i == 50) * 2 + (i == 120) * 5,
                 "low": 10.0, "close": 10.0} for i in range(0, 390)]
        db = M.DayBars(pl.DataFrame(rows), utc(2024, 4, 17, 20))
        t = t0 + dt.timedelta(minutes=10)
        r1, r2 = db.outcome("A", t)
        self.assertAlmostEqual(r1, 0.2)  # bar 50 inside [t+1, t+90); bar 120 outside
        self.assertAlmostEqual(r2, 0.5)  # to the close
        self.assertIsNone(db.outcome("A", t0))  # no bar with open <= t - 1 min
        self.assertIsNone(db.outcome("B", t))


if __name__ == "__main__":
    unittest.main()
