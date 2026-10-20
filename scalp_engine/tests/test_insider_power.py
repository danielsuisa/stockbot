"""I1 MDE inputs: the I2 overlap rule and per-window week counts (no returns involved)."""
import datetime as dt
import unittest

from scalp_engine import insider_power as W


def sessions(n, start=dt.date(2024, 7, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


class Multiplier(unittest.TestCase):
    def test_mde_multiplier_matches_gate(self):
        # gate (i): clustered t >= 2.0; 80% power -> MDE = (2.0 + z_0.80) SE (orchestrator correction 2026-10-20)
        self.assertEqual((W.T_GATE, W.Z_POWER), (2.0, 0.8416))
        self.assertAlmostEqual(W.Z, 2.8416, places=10)
        self.assertNotAlmostEqual(W.Z, 1.645 + 0.842, places=3)

    def test_excluded_days_cover_event_reach(self):
        s = sessions(200)
        ex = W.excluded_days([{"symbol": "A", "entry_date": s[100].isoformat()},
                              {"symbol": None, "entry_date": s[10].isoformat()}], s)
        self.assertEqual(ex["A"], set(range(80, 161)))  # [entry - 20, entry + 60] contains [entry, entry + 60]
        self.assertEqual(set(ex), {"A"})


class Overlap(unittest.TestCase):
    def test_first_kept_within_h(self):
        s = sessions(60)
        ev = [{"cik": "1", "trigger": "cluster", "entry_date": s[i].isoformat(), "accepted_at": str(i)} for i in (0, 5, 19, 20, 45)]
        ev.append({"cik": "1", "trigger": "big", "entry_date": s[5].isoformat(), "accepted_at": "x"})
        kept = W.overlap_first(ev, s)
        self.assertEqual([(e["trigger"], e["entry_date"]) for e in kept],
                         [("cluster", s[0].isoformat()), ("big", s[5].isoformat()), ("cluster", s[20].isoformat()),
                          ("cluster", s[45].isoformat())])

    def test_window_counts(self):
        s = sessions(30)
        ev = [{"cik": str(i), "trigger": "cluster", "entry_date": s[i % 3].isoformat(), "accepted_at": "a",
               "universe_b": True, "fpi": False} for i in range(6)]
        ev.append({"cik": "9", "trigger": "cluster", "entry_date": s[0].isoformat(), "accepted_at": "a",
                   "universe_b": False, "fpi": False})
        c = W.window_counts(ev, s)["confirmation"]
        self.assertEqual((c["n"], c["weeks"], c["m_eff"]), (6, 1, 6.0))


if __name__ == "__main__":
    unittest.main()
