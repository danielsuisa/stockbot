"""I2 Stage 0: the I1 duplicate-key remedy on a synthetic store (temporary SCALP_DATA_ROOT)."""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path

import polars as pl

from scalp_engine import i1_remedy as M
from scalp_engine import storage
from scalp_engine.data_access import partition_dates, read_frame

UTC = dt.timezone.utc
DAY = dt.date(2026, 10, 2)
T = dt.datetime(2026, 10, 2, 4, tzinfo=UTC)


def bar(sym, close, got):
    return {"event_at": T, "symbol": sym, "open": close, "high": close, "low": close, "close": close, "volume": 100,
            "vwap": close, "trade_count": 3, "feed": "sip", "adjustment": "raw", "asof": "-", "request_id": "r",
            "ingested_at": got, "source": "alpaca", "schema_version": 1}


class Remedy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("SCALP_DATA_ROOT")
        os.environ["SCALP_DATA_ROOT"] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop("SCALP_DATA_ROOT", None)
        else:
            os.environ["SCALP_DATA_ROOT"] = self.old
        self.tmp.cleanup()

    def write(self, part, rows):
        storage.write_partition("raw_bars_1d", storage.frame("raw_bars_1d", rows), part=part, source="alpaca",
                                feed="sip", date=DAY)

    def test_duplicates_set_aside_and_new_keys_kept(self):
        g1, g2 = dt.datetime(2026, 10, 17, tzinfo=UTC), dt.datetime(2026, 10, 19, tzinfo=UTC)
        self.write("sip-1d-v1", [bar("A", 10.0, g1), bar("B", 20.0, g1)])
        self.write("sip-1d-i1c", [bar("A", 10.0, g2), bar("B", 20.0, g2), bar("C", 30.0, g2)])
        r = M.remedy_one(M.BARS, DAY)
        self.assertEqual(r, {"date": DAY.isoformat(), "rows": 3, "identical_in_p1": 2, "kept": 1})
        names = sorted(Path(p).name for p in partition_dates(storage.data_root() / "raw_bars_1d")[DAY])
        self.assertEqual(names, ["part-sip-1d-i1c2.parquet", "part-sip-1d-v1.parquet"])  # the orphan is never listed
        self.assertTrue(any(f.name.startswith("part-sip-1d-i1c.parquet.orphan-") for f in
                            (storage.data_root() / "raw_bars_1d").rglob("*")))
        df = read_frame(storage.data_root() / "raw_bars_1d", T, T + dt.timedelta(days=1))
        self.assertEqual(sorted(df["symbol"].to_list()), ["A", "B", "C"])  # 0 duplicate keys, no value lost
        rec = [x for x in storage.manifest("raw_bars_1d") if x.get("kind") == "set_aside"]
        self.assertEqual((len(rec), rec[0]["kept_rows"], rec[0]["identical_in_p1"]), (1, 1, 2))

    def test_differing_duplicate_stops(self):
        g = dt.datetime(2026, 10, 17, tzinfo=UTC)
        self.write("sip-1d-v1", [bar("A", 10.0, g)])
        self.write("sip-1d-i1c", [bar("A", 10.5, g)])
        with self.assertRaises(AssertionError):
            M.remedy_one(M.BARS, DAY)
        names = sorted(Path(p).name for p in partition_dates(storage.data_root() / "raw_bars_1d")[DAY])
        self.assertIn("part-sip-1d-i1c.parquet", names)  # nothing moved


if __name__ == "__main__":
    unittest.main()
