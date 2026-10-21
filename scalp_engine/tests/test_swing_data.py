"""S1 ingestion helpers (ADR 0014 section 2): submissions parsing (forms, range drop) and the request guard."""
import datetime as dt
import unittest

from scalp_engine import swing_data as S

UTC = dt.timezone.utc
GOT = dt.datetime(2026, 10, 20, tzinfo=UTC)


class Submissions(unittest.TestCase):
    def test_parse_block_forms_and_range(self):
        block = {"form": ["8-K", "4", "10-K", "8-K", "20-F", "8-K"],
                 "filingDate": ["2024-02-01", "2024-02-01", "2024-03-01", "2021-12-30", "2025-04-01", "2026-10-21"],
                 "accessionNumber": ["a1", "a2", "a3", "a4", "a5", "a6"],
                 "items": ["2.02,9.01", "", "", "2.02", "", "2.02"],
                 "acceptanceDateTime": ["2024-02-01T16:05:00.000Z"] * 6}
        rows = S.parse_block(block, "123", "rid", GOT)
        self.assertEqual([r["accession"] for r in rows], ["a1", "a3", "a5"])  # Form 4 skipped; 2021 and 10-21 dropped
        self.assertEqual(rows[0]["items"], "2.02,9.01")
        self.assertEqual(rows[0]["event_at"], dt.datetime(2024, 2, 1, 5, tzinfo=UTC))
        S.storage.frame("issuer_filings", rows)

    def test_guard_refuses_sealed_and_after_nov_2(self):
        S.guard(dt.datetime(2026, 10, 19, 13, tzinfo=UTC), dt.datetime(2026, 10, 19, 20, tzinfo=UTC))
        with self.assertRaises(ValueError):
            S.guard(dt.datetime(2026, 11, 2, 14, tzinfo=UTC), dt.datetime(2026, 11, 2, 15, tzinfo=UTC))
        with self.assertRaises(Exception):
            S.guard(dt.datetime(2021, 12, 30, tzinfo=UTC), dt.datetime(2022, 1, 4, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
