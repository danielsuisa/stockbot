"""Parity with the live bot on every live-journal day committed in git from 2026-10-20 to the start of T2 (ADR 0017
item 6): the replay in parity mode issues exactly the journaled tickets, with identical prices, R and shares, and
the same re-ranked watchlist with identical fields (floating-point ATR differences below 1e-9 relative are the
only tolerated difference). Needs the T1/T2 store; skipped without it."""
import json
import os
import unittest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
JOURNALS = {"2026-10-20": "intraday_journal_2026-10-20_a83748e.json"}  # data/intraday_state.json at a83748e
STORE = Path(os.environ.get("SCALP_DATA_ROOT") or r"D:\scalp-data")


def close(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a))
    return a == b


@unittest.skipUnless((STORE / "derived" / "t1" / "watch").exists(), "needs the T1/T2 store")
class LiveParity(unittest.TestCase):
    def test_every_journal_day(self):
        import datetime as dt
        from scalp_engine import t1_replay as P
        for day, name in JOURNALS.items():
            out = P.parity(str(FIXTURES / name), dt.date.fromisoformat(day))
            self.assertEqual(out["matched"], out["journaled"], day)
            self.assertEqual(out["replay_only"], [], day)
            for w in out["watch"]:
                self.assertTrue(w["live"] and w["replay"], (day, w["symbol"]))
                for f, (a, b) in w["fields"].items():
                    self.assertTrue(close(a, b), (day, w["symbol"], f, a, b))

    def test_fixture_is_the_committed_journal(self):
        js = json.loads((FIXTURES / JOURNALS["2026-10-20"]).read_text(encoding="utf-8"))
        self.assertEqual((js["day"], len(js["journal"]), len(js["watch"])), ("2026-10-20", 4, 11))


if __name__ == "__main__":
    unittest.main()
