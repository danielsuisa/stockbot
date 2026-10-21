"""T1 ingestion helpers (ADR 0016 section 8): the request guard, the grid times, the completeness cut-off. No
network."""
import datetime as dt
import unittest

from scalp_engine import t1_data as T

UTC = dt.timezone.utc


class Guard(unittest.TestCase):
    def test_range(self):
        T.guard(dt.datetime(2026, 10, 20, 8, tzinfo=UTC), dt.datetime(2026, 10, 21, 0, tzinfo=UTC))
        with self.assertRaises(ValueError):  # on or after 2026-11-02 ET
            T.guard(dt.datetime(2026, 11, 2, 14, tzinfo=UTC), dt.datetime(2026, 11, 2, 15, tzinfo=UTC))
        with self.assertRaises(ValueError):  # before 2022-01-03 ET
            T.guard(dt.datetime(2022, 1, 2, 14, tzinfo=UTC), dt.datetime(2022, 1, 3, 15, tzinfo=UTC))
        with self.assertRaises(Exception):  # the sealed legacy window
            T.guard(dt.datetime(2021, 12, 30, tzinfo=UTC), dt.datetime(2022, 1, 4, tzinfo=UTC))


class Grid(unittest.TestCase):
    def test_times_normal_and_early_close(self):
        g = T.grid_times(dt.date(2024, 6, 12), dt.time(16))
        self.assertEqual((len(g), g[0], g[22], g[-1]), (24, dt.time(9, 40), dt.time(11, 30), dt.time(15, 55)))
        self.assertEqual(T.grid_times(dt.date(2024, 11, 29), dt.time(13))[-1], dt.time(12, 55))


class TopupWindow(unittest.TestCase):
    def test_daily_request_ends_at_2000_et_of_the_last_session(self):
        """The free plan refuses an end within the last 15 minutes: the day's end (00:00 ET next day) can be in the
        future when the top-up runs at 21:42 ET; the extended session ends at 20:00 ET."""
        lo, hi = T.topup_daily_window(dt.date(2026, 10, 19), dt.date(2026, 10, 20))
        self.assertEqual(lo, dt.datetime(2026, 10, 19, 4, tzinfo=UTC))
        self.assertEqual(hi, dt.datetime(2026, 10, 21, 0, tzinfo=UTC))  # 20:00 ET (EDT)


class Cutoff(unittest.TestCase):
    def test_last_complete_session(self):
        et = T.ET
        self.assertEqual(T.last_complete_session(dt.datetime(2026, 10, 20, 14, tzinfo=et)), dt.date(2026, 10, 19))
        self.assertEqual(T.last_complete_session(dt.datetime(2026, 10, 20, 20, 31, tzinfo=et)), dt.date(2026, 10, 20))
        self.assertEqual(T.last_complete_session(dt.datetime(2026, 11, 5, 12, tzinfo=et)), dt.date(2026, 10, 30))

    def test_windows(self):
        self.assertEqual(T.CONF[1], dt.date(2026, 10, 16))
        self.assertEqual(T.DEV, (dt.date(2022, 2, 1), dt.date(2024, 12, 31)))


if __name__ == "__main__":
    unittest.main()
