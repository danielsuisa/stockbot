"""P1 gate: replay three ingested trading days in separate processes under different PYTHONHASHSEED values,
with sockets disabled; hashes must match. Changing one input row must change the hash.

Days (override with SCALP_REPLAY_DAYS=YYYY-MM-DD,YYYY-MM-DD,YYYY-MM-DD):
  2024-04-17 a normal full session; 2024-11-29 an early close (13:00 ET);
  2024-02-26 WMT's 3:1 forward split, WMT a top500 member that month.
Skipped when the data root does not hold those days (e.g. CI); the P1 report records the run.
"""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import polars as pl

from scalp_engine import replay, storage
from scalp_engine.data_access import day_bounds, partition_dates, read_frame
from scalp_engine.tests.test_replay import replay_in_subprocess

DAYS = [dt.date.fromisoformat(d) for d in
        os.environ.get("SCALP_REPLAY_DAYS", "2024-04-17,2024-11-29,2024-02-26").split(",")]
ROOT = Path(os.environ.get("SCALP_DATA_ROOT") or r"D:\scalp-data")
TABLES = ["calendar", "corporate_actions", "raw_bars_1m", "raw_quote_samples"]


def have(day):
    return all(day in partition_dates(ROOT / t) for t in ("calendar", "raw_bars_1m", "raw_quote_samples"))


@unittest.skipUnless(all(have(d) for d in DAYS), f"ingested days {DAYS} not under {ROOT}")
class RealDays(unittest.TestCase):
    hashes = {}

    def test_1_identical_hash_in_two_processes(self):
        for day in DAYS:
            with self.subTest(day=day):
                a = replay_in_subprocess(ROOT, day, 0)
                b = replay_in_subprocess(ROOT, day, 987654321)
                self.assertEqual(a, b)
                self.assertGreater(a["events"], 100_000)
                RealDays.hashes[day] = a
        print("\nreplay hashes:", {str(k): v for k, v in RealDays.hashes.items()})

    def test_2_one_changed_row_changes_the_hash(self):
        day = DAYS[0]
        original = replay.day_hash(ROOT, day)[0]
        for shift, should_match in ((0.0, True), (0.01, False)):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(shift=shift):
                with mock.patch.dict(os.environ, {"SCALP_DATA_ROOT": tmp}):
                    lo, hi = day_bounds(day)
                    for table in TABLES:
                        df = read_frame(ROOT / table, lo, hi)
                        if df.height == 0:
                            continue
                        if table == "raw_bars_1m" and shift:
                            first = df.sort(["feed", "symbol", "event_at"]).row(0, named=True)
                            df = df.with_columns(pl.when((pl.col("feed") == first["feed"]) &
                                                         (pl.col("symbol") == first["symbol"]) &
                                                         (pl.col("event_at") == first["event_at"]))
                                                 .then(pl.col("close") + shift).otherwise(pl.col("close")).alias("close"))
                        segs = {"source": df["source"][0], "date": day}
                        if "feed" in [s.split("=")[0] for s in storage.contract(table)["partitioning"]]:
                            for feed in sorted(set(df["feed"].to_list())):
                                storage.write_partition(table, df.filter(pl.col("feed") == feed), part="copy",
                                                        **dict(segs, feed=feed))
                        else:
                            storage.write_partition(table, df, part="copy", **segs)
                copy = replay.day_hash(Path(tmp), day)[0]
                self.assertEqual(copy == original, should_match)


if __name__ == "__main__":
    unittest.main()
