"""Phase C, ruling C22: the ticket journal's format and writer (spec section 8, `data/ticket_journal.json`), tested on
synthetic tickets only. The blind run writes no journal; the live journal is phase E."""
import json
import os
import tempfile
import unittest

from bot import ticket_journal as tj

TICKET = {"day": "2017-02-27", "signal": "S3", "symbol": "AAA", "mode": "breakout", "horizon": 21, "entry_stop": 50.51,
          "limit": 50.76, "planned_entry": 50.51, "stop_loss": 48.51, "target": 53.51, "trail": 2.0, "shares": 50,
          "legs": (25, 25), "R": 2.0, "guard_spread": 0.02}


class Journal(unittest.TestCase):
    def test_record_holds_the_ticket_fields_only(self):
        rec = tj.record(TICKET)
        self.assertEqual(tuple(rec), tj.FIELDS)
        self.assertEqual(rec["legs"], [25, 25])
        self.assertIsNone(rec["outcome"])  # filled in by the forward check (phase E), never in phase C

    def test_append_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ticket_journal.json")
            tj.append(path, tj.record(TICKET))
            tj.append(path, tj.record({**TICKET, "symbol": "BBB"}))
            got = tj.load(path)
            self.assertEqual([r["symbol"] for r in got["tickets"]], ["AAA", "BBB"])
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["version"], tj.VERSION)

    def test_load_missing_is_empty(self):
        self.assertEqual(tj.load(os.path.join(tempfile.gettempdir(), "no-such-journal.json")),
                         {"version": tj.VERSION, "tickets": []})


if __name__ == "__main__":
    unittest.main()
