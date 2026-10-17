"""scalp_eval's own Parquet read path: pruning, predicate pushdown and the post-read assertion over
both holdout windows (synthetic files written here with polars; no engine code)."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

import polars as pl

from scalp_eval import data_access as da

UTC = dt.timezone.utc


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


class ParquetGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "raw_bars_1m"
        for day, times in [("2021-12-31", [utc(2021, 12, 31, 20)]), ("2022-01-03", [utc(2022, 1, 3, 15)]),
                           ("2022-01-04", [utc(2021, 12, 31, 15), utc(2022, 1, 4, 15)]),  # mislabelled row
                           ("2026-10-30", [utc(2026, 10, 30, 15)]), ("2026-11-02", [utc(2026, 11, 2, 15)])]:
            d = self.root / "source=synthetic" / "feed=sip" / f"date={day}"
            d.mkdir(parents=True)
            pl.DataFrame({"event_at": times, "symbol": ["T"] * len(times), "close": [1.0] * len(times)},
                         schema={"event_at": pl.Datetime("us", "UTC"), "symbol": pl.Utf8, "close": pl.Float64}
                         ).write_parquet(d / "part-x.parquet")

    def tearDown(self):
        self.tmp.cleanup()

    def test_overlaps_raise(self):
        for start, end in [(utc(2021, 12, 30), utc(2022, 1, 5)), (utc(2026, 10, 30), utc(2026, 11, 3)),
                           (utc(2015, 1, 1), utc(2027, 6, 1))]:
            with self.subTest(start=start), self.assertRaises(da.HoldoutSealedError):
                da.read_frame(self.root, start, end)

    def test_between_windows(self):
        df = da.read_frame(self.root, utc(2022, 1, 1, 5), utc(2026, 11, 2, 5))
        self.assertEqual(sorted(df["event_at"].to_list()), [utc(2022, 1, 3, 15), utc(2022, 1, 4, 15), utc(2026, 10, 30, 15)])

    def test_missing_partition_raises(self):
        with self.assertRaises(da.MissingPartitionError):
            da.read_frame(self.root, utc(2022, 1, 1, 5), utc(2022, 1, 6, 5), require_dates=[dt.date(2022, 1, 5)])


if __name__ == "__main__":
    unittest.main()
