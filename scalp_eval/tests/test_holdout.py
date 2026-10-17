"""B3 holdout lock: range checks, timezone edges, partition pruning, post-read assertion, ledger.

All rows here are synthetic and written to a temp directory; no market data is read."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scalp_eval import data_access as da

UTC = dt.timezone.utc


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.h = da.load_holdout()

    def test_boundaries_are_et_dates_converted_to_utc(self):
        self.assertEqual(self.h.first_date, dt.date(2016, 1, 1))
        self.assertEqual(self.h.last_date, dt.date(2021, 12, 31))
        self.assertEqual(self.h.start, utc(2016, 1, 1, 5))  # 00:00 EST
        self.assertEqual(self.h.end, utc(2022, 1, 1, 5))  # exclusive, 00:00 EST on 2022-01-01

    def test_trading_days_match_zba_holdout(self):
        # bot/zba_v2.py:24 HOLDOUT = ("2016-01-04", "2021-12-31"): both sessions sealed at the open
        self.assertTrue(self.h.contains(utc(2016, 1, 4, 14, 30)))
        self.assertTrue(self.h.contains(utc(2021, 12, 31, 20, 59)))


class RangeCheck(unittest.TestCase):
    def test_overlapping_ranges_raise(self):
        for start, end in [
            (utc(2015, 6, 1), utc(2016, 6, 1)),  # straddles the start
            (utc(2021, 6, 1), utc(2022, 6, 1)),  # straddles the end
            (utc(2018, 3, 1), utc(2018, 3, 2)),  # inside
            (utc(2010, 1, 1), utc(2026, 1, 1)),  # contains it
        ]:
            with self.subTest(start=start, end=end), self.assertRaises(da.HoldoutSealedError):
                da.check_range(start, end)

    def test_unbounded_ranges_raise(self):
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(None, utc(2023, 1, 1))
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2010, 1, 1), None)

    def test_last_trading_day_before_passes(self):
        # 2015-12-31 (Thu) is the last NYSE session before the holdout; its full ET day incl. after-hours
        da.check_range(utc(2015, 12, 31, 5), utc(2016, 1, 1, 5))

    def test_first_trading_day_after_passes(self):
        # 2022-01-03 (Mon) is the first NYSE session after the holdout
        da.check_range(utc(2022, 1, 3, 5), utc(2022, 1, 4, 5))
        da.check_range(utc(2022, 1, 1, 5), utc(2026, 10, 17))

    def test_utc_midnight_is_not_et_midnight_at_the_start(self):
        # [2015-12-31Z, 2016-01-01Z) is still 2015-12-31 ET everywhere: passes
        da.check_range(utc(2015, 12, 31), utc(2016, 1, 1))
        # one more UTC day reaches 2016-01-01 00:00-19:00 ET: sealed
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2015, 12, 31), utc(2016, 1, 2))
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2015, 12, 31), utc(2016, 1, 1, 5, 0, 0, 1))

    def test_utc_midnight_is_not_et_midnight_at_the_end(self):
        # 2022-01-01T00:00Z is 2021-12-31 19:00 ET (inside the holdout's last day)
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2022, 1, 1), utc(2022, 1, 4))
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2022, 1, 1, 4, 59, 59), utc(2022, 1, 4))
        da.check_range(utc(2022, 1, 1, 5), utc(2022, 1, 4))

    def test_other_offsets_are_normalized(self):
        jerusalem = dt.timezone(dt.timedelta(hours=2))
        with self.assertRaises(da.HoldoutSealedError):  # 06:00+02:00 = 04:00Z on 2022-01-01
            da.check_range(dt.datetime(2022, 1, 1, 6, tzinfo=jerusalem), utc(2022, 1, 4))
        da.check_range(dt.datetime(2022, 1, 1, 7, tzinfo=jerusalem), utc(2022, 1, 4))

    def test_naive_and_empty_ranges_are_rejected(self):
        with self.assertRaises(TypeError):
            da.check_range(dt.datetime(2023, 1, 1), utc(2023, 2, 1))
        with self.assertRaises(ValueError):
            da.check_range(utc(2023, 2, 1), utc(2023, 1, 1))


class Reads(unittest.TestCase):
    """Synthetic table: one partition per ET date, CSV and JSONL parts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "raw_bars_1m"
        self._part("2015-12-31", "a.csv", ["2015-12-31T14:30:00Z", "2016-01-01T00:59:00Z"])
        self._part("2016-01-04", "a.csv", ["2016-01-04T14:30:00Z"])  # sealed partition
        self._part("2021-12-31", "a.jsonl", ["2021-12-31T20:59:00Z"])  # sealed partition
        self._part("2022-01-03", "a.jsonl", ["2022-01-03T14:30:00Z", "2022-01-03T14:31:00Z"])
        # a mislabelled partition: the date dir says 2022 but a row is inside the holdout
        self._part("2022-01-04", "bad.csv", ["2021-12-31T15:00:00Z", "2022-01-04T14:30:00Z"])

    def tearDown(self):
        self.tmp.cleanup()

    def _part(self, day, name, times):
        d = self.root / "source=synthetic" / "feed=sip" / f"date={day}"
        d.mkdir(parents=True, exist_ok=True)
        if name.endswith(".csv"):
            lines = ["event_at,symbol,close"] + [f"{t},TEST,1.00" for t in times]
        else:
            lines = [json.dumps({"event_at": t, "symbol": "TEST", "close": "1.00"}) for t in times]
        (d / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_read_before_holdout(self):
        rows = da.read_table(self.root, utc(2015, 12, 31, 5), utc(2016, 1, 1, 5))
        self.assertEqual([r["event_at"] for r in rows], [utc(2015, 12, 31, 14, 30), utc(2016, 1, 1, 0, 59)])

    def test_read_after_holdout_filters_rows_and_skips_mislabelled_rows(self):
        rows = da.read_table(self.root, utc(2022, 1, 1, 5), utc(2022, 1, 5, 5))
        self.assertEqual([r["event_at"] for r in rows],
                         [utc(2022, 1, 3, 14, 30), utc(2022, 1, 3, 14, 31), utc(2022, 1, 4, 14, 30)])

    def test_read_overlapping_holdout_raises_before_opening_files(self):
        with self.assertRaises(da.HoldoutSealedError):
            da.read_table(self.root / "does-not-exist", utc(2021, 12, 1), utc(2022, 1, 5))

    def test_post_read_assertion(self):
        h = da.load_holdout()
        with self.assertRaises(da.HoldoutSealedError):
            da.assert_outside_holdout([{"event_at": utc(2019, 5, 1, 15)}], h)
        da.assert_outside_holdout([{"event_at": utc(2022, 1, 3, 15)}], h)

    def test_parquet_backend_not_available_in_p0(self):
        d = self.root / "source=synthetic" / "feed=sip" / "date=2023-01-03"
        d.mkdir(parents=True)
        (d / "a.parquet").write_bytes(b"")
        with self.assertRaises(NotImplementedError):
            da.read_table(self.root, utc(2023, 1, 3, 5), utc(2023, 1, 4, 5))


class Ledger(unittest.TestCase):
    def test_committed_ledger_is_empty(self):
        self.assertTrue(da.LEDGER_FILE.exists())
        self.assertEqual(da.LEDGER_FILE.read_bytes(), b"")

    def test_unseal_raises_without_record(self):
        with self.assertRaises(da.HoldoutSealedError):
            da.unseal_record("p7-confirmation")
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2018, 1, 1), utc(2018, 2, 1), unseal_run_id="p7-confirmation")

    def test_holdout_hash_ignores_checkout_line_endings(self):
        text = da.HOLDOUT_FILE.read_bytes().replace(b"\r\n", b"\n")
        with tempfile.TemporaryDirectory() as tmp:
            lf, crlf = Path(tmp) / "lf.yaml", Path(tmp) / "crlf.yaml"
            lf.write_bytes(text)
            crlf.write_bytes(text.replace(b"\n", b"\r\n"))
            self.assertEqual(da.load_holdout(lf).sha256, da.load_holdout(crlf).sha256)

    def test_unseal_record_must_match_run_and_holdout_hash(self):
        h = da.load_holdout()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            rec = {"run_id": "r1", "approved_by": "owner", "approved_at": "2027-01-01T00:00:00Z",
                   "purpose": "test", "holdout_sha256": h.sha256}
            ledger.write_text(json.dumps(rec) + "\n", encoding="utf-8")
            self.assertEqual(da.unseal_record("r1", h, ledger)["run_id"], "r1")
            with self.assertRaises(da.HoldoutSealedError):
                da.unseal_record("r2", h, ledger)
            ledger.write_text(json.dumps(dict(rec, holdout_sha256="0" * 64)) + "\n", encoding="utf-8")
            with self.assertRaises(da.HoldoutSealedError):  # approval was for different boundaries
                da.unseal_record("r1", h, ledger)


if __name__ == "__main__":
    unittest.main()
