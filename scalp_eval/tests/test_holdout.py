"""Holdout lock v2 (ADR 0002 section 2): two windows, range checks, timezone edges, partition pruning,
post-read assertion, per-window ledger.

All rows here are synthetic and written to a temp directory; no market data is read."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scalp_eval import data_access as da

UTC = dt.timezone.utc
LEGACY, FORWARD = "legacy_2016_2021", "forward_2026q4"


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.h = da.load_holdout()
        self.w = {w.name: w for w in self.h.windows}

    def test_two_windows_with_roles(self):
        self.assertEqual([w.name for w in self.h.windows], [LEGACY, FORWARD])
        self.assertEqual(self.w[LEGACY].role, "veto_only")
        self.assertEqual(self.w[FORWARD].role, "confirmation")

    def test_et_trading_dates_converted_to_utc(self):
        legacy, forward = self.w[LEGACY], self.w[FORWARD]
        self.assertEqual((legacy.first_date, legacy.last_date), (dt.date(2016, 1, 4), dt.date(2021, 12, 31)))
        self.assertEqual((legacy.start, legacy.end), (utc(2016, 1, 4, 5), utc(2022, 1, 1, 5)))  # EST both ends
        self.assertEqual((forward.first_date, forward.last_date), (dt.date(2026, 11, 2), dt.date(2027, 1, 29)))
        self.assertEqual((forward.start, forward.end), (utc(2026, 11, 2, 5), utc(2027, 1, 30, 5)))  # DST ended 11-01

    def test_window_sessions_sealed_at_the_open(self):
        for t in (utc(2016, 1, 4, 14, 30), utc(2021, 12, 31, 20, 59), utc(2026, 11, 2, 14, 30),
                  utc(2027, 1, 29, 20, 59)):
            with self.subTest(t=t):
                self.assertTrue(self.h.contains(t))


class RangeCheck(unittest.TestCase):
    def test_overlapping_either_window_raises(self):
        for start, end in [
            (utc(2015, 6, 1), utc(2016, 6, 1)),  # straddles legacy start
            (utc(2021, 6, 1), utc(2022, 6, 1)),  # straddles legacy end
            (utc(2018, 3, 1), utc(2018, 3, 2)),  # inside legacy
            (utc(2026, 10, 1), utc(2026, 11, 15)),  # straddles forward start
            (utc(2027, 1, 15), utc(2027, 3, 1)),  # straddles forward end
            (utc(2026, 12, 1), utc(2026, 12, 2)),  # inside forward
        ]:
            with self.subTest(start=start, end=end), self.assertRaises(da.HoldoutSealedError):
                da.check_range(start, end)

    def test_range_spanning_both_windows_raises(self):
        with self.assertRaises(da.HoldoutSealedError) as cm:
            da.check_range(utc(2015, 1, 1), utc(2027, 6, 1))
        self.assertIn(LEGACY, str(cm.exception))
        self.assertIn(FORWARD, str(cm.exception))
        # a range between the windows that touches neither passes
        da.check_range(utc(2022, 1, 1, 5), utc(2026, 11, 2, 5))

    def test_unbounded_ranges_raise(self):
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(None, utc(2023, 1, 1))
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2023, 1, 1), None)

    def test_adjacent_trading_days_pass(self):
        da.check_range(utc(2015, 12, 31, 5), utc(2016, 1, 1, 5))  # Thu 2015-12-31, full ET day
        da.check_range(utc(2022, 1, 3, 5), utc(2022, 1, 4, 5))  # Mon 2022-01-03
        da.check_range(utc(2026, 10, 30, 4), utc(2026, 10, 31, 4))  # Fri 2026-10-30 (EDT)
        da.check_range(utc(2027, 2, 1, 5), utc(2027, 2, 2, 5))  # Mon 2027-02-01

    def test_legacy_start_midnight_utc(self):
        da.check_range(utc(2015, 12, 31), utc(2016, 1, 4, 5))  # through Sun 2016-01-03 ET
        da.check_range(utc(2016, 1, 3), utc(2016, 1, 4))  # 2016-01-04T00:00Z is still Jan 3 ET
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2016, 1, 3), utc(2016, 1, 5))  # UTC Jan 4 reaches ET Jan 4
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2016, 1, 3), utc(2016, 1, 4, 5, 0, 0, 1))

    def test_legacy_end_midnight_utc(self):
        with self.assertRaises(da.HoldoutSealedError):  # 2022-01-01T00:00Z = 2021-12-31 19:00 ET
            da.check_range(utc(2022, 1, 1), utc(2022, 1, 4))
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2022, 1, 1, 4, 59, 59), utc(2022, 1, 4))
        da.check_range(utc(2022, 1, 1, 5), utc(2022, 1, 4))

    def test_forward_start_midnight_utc(self):
        da.check_range(utc(2026, 10, 30, 4), utc(2026, 11, 2, 5))  # Fri 10-30 ET through Sun 11-01 ET
        da.check_range(utc(2026, 11, 1), utc(2026, 11, 2))  # 2026-11-02T00:00Z is Nov 1 19:00 EST
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2026, 11, 1), utc(2026, 11, 3))  # UTC Nov 2 reaches ET Nov 2
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2026, 10, 30, 4), utc(2026, 11, 2, 5, 0, 0, 1))

    def test_forward_end_midnight_utc(self):
        with self.assertRaises(da.HoldoutSealedError):  # 2027-01-30T00:00Z = 2027-01-29 19:00 ET
            da.check_range(utc(2027, 1, 30), utc(2027, 2, 2))
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2027, 1, 30, 4, 59, 59), utc(2027, 2, 2))
        da.check_range(utc(2027, 1, 30, 5), utc(2027, 2, 2, 5))

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
        self._part("2026-10-30", "a.csv", ["2026-10-30T13:30:00Z"])
        self._part("2026-11-02", "a.csv", ["2026-11-02T14:30:00Z"])  # sealed partition

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

    def test_read_before_legacy(self):
        rows = da.read_table(self.root, utc(2015, 12, 31, 5), utc(2016, 1, 1, 5))
        self.assertEqual([r["event_at"] for r in rows], [utc(2015, 12, 31, 14, 30), utc(2016, 1, 1, 0, 59)])

    def test_read_between_windows_filters_rows_and_skips_mislabelled_rows(self):
        rows = da.read_table(self.root, utc(2022, 1, 1, 5), utc(2026, 11, 2, 5))
        self.assertEqual([r["event_at"] for r in rows],
                         [utc(2022, 1, 3, 14, 30), utc(2022, 1, 3, 14, 31), utc(2022, 1, 4, 14, 30),
                          utc(2026, 10, 30, 13, 30)])

    def test_read_overlapping_a_window_raises_before_opening_files(self):
        for start, end in [(utc(2021, 12, 1), utc(2022, 1, 5)), (utc(2026, 10, 30), utc(2026, 11, 3))]:
            with self.subTest(start=start), self.assertRaises(da.HoldoutSealedError):
                da.read_table(self.root / "does-not-exist", start, end)

    def test_post_read_assertion(self):
        h = da.load_holdout()
        for t in (utc(2019, 5, 1, 15), utc(2026, 12, 1, 15)):
            with self.subTest(t=t), self.assertRaises(da.HoldoutSealedError):
                da.assert_outside_holdout([{"event_at": t}], h)
        da.assert_outside_holdout([{"event_at": utc(2022, 1, 3, 15)}, {"event_at": utc(2027, 2, 1, 15)}], h)


class Ledger(unittest.TestCase):
    def setUp(self):
        self.h = da.load_holdout()

    def test_committed_ledger_is_empty(self):
        self.assertTrue(da.LEDGER_FILE.exists())
        self.assertEqual(da.LEDGER_FILE.read_bytes(), b"")

    def test_unseal_raises_without_record(self):
        for window in (LEGACY, FORWARD):
            with self.subTest(window=window), self.assertRaises(da.HoldoutSealedError):
                da.unseal_record("p7-confirmation", window)
        with self.assertRaises(da.HoldoutSealedError):
            da.check_range(utc(2018, 1, 1), utc(2018, 2, 1), unseal_run_id="p7-confirmation")
        with self.assertRaises(ValueError):
            da.unseal_record("p7-confirmation", "no_such_window")

    def test_holdout_hash_ignores_checkout_line_endings(self):
        text = da.HOLDOUT_FILE.read_bytes().replace(b"\r\n", b"\n")
        with tempfile.TemporaryDirectory() as tmp:
            lf, crlf = Path(tmp) / "lf.yaml", Path(tmp) / "crlf.yaml"
            lf.write_bytes(text)
            crlf.write_bytes(text.replace(b"\n", b"\r\n"))
            self.assertEqual(da.load_holdout(lf).sha256, da.load_holdout(crlf).sha256)
            self.assertEqual(da.load_holdout(lf).sha256, self.h.sha256)

    def _rec(self, run, window, sha=None):
        return {"run_id": run, "window": window, "approved_by": "owner", "approved_at": "2027-02-01T00:00:00Z",
                "purpose": "test", "holdout_sha256": sha or self.h.sha256}

    def test_unseal_is_per_run_and_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            ledger.write_text(json.dumps(self._rec("r1", FORWARD)) + "\n", encoding="utf-8")
            self.assertEqual(da.unseal_record("r1", FORWARD, self.h, ledger)["run_id"], "r1")
            with self.assertRaises(da.HoldoutSealedError):
                da.unseal_record("r1", LEGACY, self.h, ledger)  # other window
            with self.assertRaises(da.HoldoutSealedError):
                da.unseal_record("r2", FORWARD, self.h, ledger)  # other run
            da.check_range(utc(2026, 12, 1), utc(2026, 12, 2), unseal_run_id="r1", ledger=ledger)
            with self.assertRaises(da.HoldoutSealedError):  # spans both: needs both records
                da.check_range(utc(2015, 1, 1), utc(2027, 6, 1), unseal_run_id="r1", ledger=ledger)
            with open(ledger, "a", encoding="utf-8") as f:
                f.write(json.dumps(self._rec("r1", LEGACY)) + "\n")
            da.check_range(utc(2015, 1, 1), utc(2027, 6, 1), unseal_run_id="r1", ledger=ledger)

    def test_unseal_record_binds_holdout_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            ledger.write_text(json.dumps(self._rec("r1", FORWARD, "0" * 64)) + "\n", encoding="utf-8")
            with self.assertRaises(da.HoldoutSealedError):  # approval was for different boundaries
                da.unseal_record("r1", FORWARD, self.h, ledger)
            ledger.write_text(json.dumps({"run_id": "r1", "window": FORWARD}) + "\n", encoding="utf-8")
            with self.assertRaises(da.HoldoutSealedError):  # malformed record
                da.unseal_record("r1", FORWARD, self.h, ledger)


class FileValidation(unittest.TestCase):
    def _load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "h.yaml"
            p.write_text(text, encoding="utf-8")
            return da.load_holdout(p)

    def test_rejects_malformed_holdout_files(self):
        base = "schema_version: 2\ntimezone: America/New_York\n"
        w = ("window.a.first_date: 2020-01-02\nwindow.a.last_date: 2020-01-31\n"
             "window.a.role: veto_only\nwindow.a.rationale: x\n")
        self.assertEqual(len(self._load(base + w).windows), 1)
        for bad in (
            "schema_version: 1\ntimezone: America/New_York\n" + w,  # old schema
            base,  # no windows
            base + w.replace("veto_only", "tuning"),  # unknown role
            base + w.replace("window.a.rationale: x\n", ""),  # missing field
            base + w.replace("2020-01-31", "2019-12-31"),  # reversed
            base + w + w.replace("window.a.", "window.b.").replace("2020-01-02", "2020-01-15"),  # overlap
            base + w + "extra: 1\n",  # unknown key
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self._load(bad)


if __name__ == "__main__":
    unittest.main()
