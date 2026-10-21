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


class Coverage(unittest.TestCase):
    def test_gaps_per_range(self):
        """Every universe symbol must have been requested for every date range (T1 defect 2026-10-21: the daily
        store held only the symbol sets earlier phases asked for, so the prefilter silently dropped 2,184 universe
        symbols, SCO and UGL among them)."""
        d = dt.date
        sets = [(d(2022, 1, 3), d(2023, 7, 31), {"A", "B"}), (d(2023, 8, 1), d(2026, 10, 16), {"A"}),
                (d(2023, 8, 1), d(2026, 9, 30), {"C"})]
        gaps = T.coverage_gaps({"A", "B", "C"}, sets, d(2022, 1, 3), d(2026, 10, 16))
        self.assertEqual(gaps, [(d(2022, 1, 3), d(2023, 7, 31), ["C"]), (d(2023, 8, 1), d(2026, 9, 30), ["B"]),
                                (d(2026, 10, 1), d(2026, 10, 16), ["B", "C"])])
        self.assertEqual(T.coverage_gaps({"A"}, sets, d(2022, 1, 3), d(2026, 10, 16)), [])
        weekend = [(d(2026, 10, 1), d(2026, 10, 16), {"A"}), (d(2026, 10, 19), d(2026, 10, 20), {"A"})]
        self.assertEqual(len(T.coverage_gaps({"A"}, weekend, d(2026, 10, 1), d(2026, 10, 20))), 1)  # 10-17..18
        self.assertEqual(T.coverage_gaps({"A"}, weekend, d(2026, 10, 1), d(2026, 10, 20), [d(2026, 10, 16), d(2026, 10, 19)]), [])


class HeldKeys(unittest.TestCase):
    def test_rows_already_held_are_dropped_before_writing(self):
        """T1 defect 2026-10-21: the second stage-1 pass re-fetched FNGU 09:30-09:34 SIP on 13 sessions, which the
        denominators part already held (65 duplicate keys). A fetch window that overlaps any held part must drop
        the held keys before the partition is written."""
        import polars as pl
        t0 = dt.datetime(2023, 8, 4, 13, 30, tzinfo=UTC)
        df = pl.DataFrame({"symbol": ["FNGU", "FNGU", "ABC"], "event_at": [t0, t0 + dt.timedelta(minutes=5), t0]})
        held = {("FNGU", t0)}
        got = T.drop_held(df, held)
        self.assertEqual(sorted(zip(got["symbol"].to_list(), got["event_at"].to_list())),
                         [("ABC", t0), ("FNGU", t0 + dt.timedelta(minutes=5))])
        self.assertEqual(T.drop_held(df, set()).height, 3)


class Survivorship(unittest.TestCase):
    def test_delisted_and_renamed_names_with_their_validity(self):
        """T2 item 7: names Alpaca's asset list omits (acquired, worthless-removed, renamed away), P1's top500_v3
        method, each valid only before its delisting or rename; a fund reorganization is skipped."""
        d = dt.date
        listed = {"NEW", "ETFX", "KEEP"}
        uni = {"NEW", "KEEP"}
        dl = [("GONE", d(2023, 5, 1), None), ("GONE", d(2023, 6, 1), "KEEP"), ("INTOETF", d(2024, 1, 2), "ETFX"),
              ("KEEP", d(2024, 1, 2), None), ("BAD.W", d(2024, 1, 2), None), ("OLD", d(2025, 1, 2), None)]
        rn = [("OLD", "NEW", d(2024, 3, 1)), ("OTHER", "ETFX", d(2024, 3, 1)), ("NEW", "NEW", d(2024, 3, 1))]
        got = T.survivor_names(listed, uni, dl, rn)
        self.assertEqual(got, {"GONE": d(2023, 6, 1), "OLD": d(2024, 3, 1)})  # the rename date wins, as in P1

    def test_valid_on(self):
        d = dt.date
        spans = {"GONE": d(2023, 6, 1), "KEEP": None}
        self.assertTrue(T.valid_on(spans, "GONE", d(2023, 5, 31)))
        self.assertFalse(T.valid_on(spans, "GONE", d(2023, 6, 1)))
        self.assertTrue(T.valid_on(spans, "KEEP", d(2026, 10, 16)))


class Unrequested(unittest.TestCase):
    def test_only_requested_symbols_are_kept(self):
        """T2 defect 2026-10-21: Alpaca returned AHPAW's daily bars for a request that never named it (T1 fill), so
        a later fill that did name it stored them twice (210 keys). A fill keeps only the symbols it requested."""
        import polars as pl
        df = pl.DataFrame({"symbol": ["OABIW", "AHPAW"], "event_at": [dt.datetime(2022, 2, 7, 5, tzinfo=UTC)] * 2})
        self.assertEqual(T.keep_requested(df, ["OABIW"])["symbol"].to_list(), ["OABIW"])


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
