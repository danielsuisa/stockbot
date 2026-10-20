"""T1 stage 3 (ADR 0016): sizing, the replay clock, the leakage tests (future perturbation, available_at), the
control draw and geometry. Synthetic sessions only."""
import datetime as dt
import random
import unittest

from scalp_engine import intraday_rules as R
from scalp_engine import t1_replay as P

DAY = dt.date(2024, 6, 12)
ATR, PREV = 1.0, 20.0


def flat(start, end, px=20.4, v=50):
    out, t = [], R.mins(start)
    while t < R.mins(end):
        out.append((R.hhmm(t), px, px + 0.02, px - 0.02, px, v, px))
        t += 1
    return out


def candle(start, o, h, l, c, v):
    """One 5-minute candle as 5 minutes: the first carries the range."""
    t = R.mins(start)
    return [(start, o, h, l, c, v, (o + c) / 2)] + [(R.hhmm(t + k), c, c, c, c, 1, c) for k in range(1, 5)]


def session():
    """Rises above VWAP, pulls back to it, prints a hammer on the 09:45-09:50 candle (scan at 09:50)."""
    bars = (candle("09:30", 20.0, 20.5, 19.9, 20.4, 1000) + candle("09:35", 20.4, 20.6, 20.3, 20.5, 900)
            + candle("09:40", 20.5, 20.55, 20.3, 20.45, 800) + candle("09:45", 20.45, 20.5, 20.0, 20.47, 1200)
            + flat("09:50", "16:00", 20.47, 10))
    return {"ACME": bars}


WATCH = [{"symbol": "ACME", "gap": 0.03, "pre_rel": 0.2, "atr": ATR, "prev_close": PREV, "adv": 2e6, "open_relvol": 2.0}]


def rel(_):
    return {"ACME": 2.0}


class Sizing(unittest.TestCase):
    def test_shares(self):
        self.assertEqual(P.shares_for(20.91, 20.54), 67)                   # $25 / 0.37
        self.assertEqual(P.shares_for(20.91, 20.54, cap_share=0.2), 23)    # $500 / 20.91: the 20% variant binds
        self.assertEqual(P.shares_for(400.0, 370.0), 0)                   # R $30 > $25: a 0-share skip
        self.assertEqual(P.shares_for(400.0, 399.0), 6)                   # $2,500 / 400: the full-equity cap binds

    def test_windows(self):
        self.assertEqual(P.window_of(dt.date(2022, 2, 1)), "development")
        self.assertEqual(P.window_of(dt.date(2025, 1, 2)), "confirmation")
        self.assertEqual(P.window_of(dt.date(2026, 10, 20)), "topup")
        self.assertIsNone(P.window_of(dt.date(2022, 1, 31)))


class Clock(unittest.TestCase):
    def test_hammer_ticket_and_audit(self):
        audit = []
        st = P.replay_core(DAY, WATCH, session(), rel, audit=audit)
        h1 = [t for t in st["journal"] if t["setup"] == "vwap_hammer"]
        self.assertEqual([t["at"] for t in h1], ["09:50"])
        self.assertEqual((h1[0]["trigger_high"], h1[0]["stop_ref"]), (20.5, 20.0))
        self.assertTrue(all(last is None or last < now for now, last in audit))  # every input minute ends by the scan
        self.assertEqual(len(audit), 23)

    def test_future_perturbation_changes_nothing(self):
        bars = session()
        base = P.replay_core(DAY, WATCH, bars, rel)
        for cut in P.MORNING:
            alt = P.replay_core(DAY, WATCH, P.perturb(bars, cut), rel)
            self.assertEqual([t for t in base["journal"] if t["at"] <= cut], [t for t in alt["journal"] if t["at"] <= cut], cut)
            self.assertEqual([x for x in base["detected"] if x[0] <= cut], [x for x in alt["detected"] if x[0] <= cut], cut)
        deleted = {s: [b for b in bs if b[0] < "09:50"] for s, bs in bars.items()}
        self.assertEqual([t for t in base["journal"] if t["at"] <= "09:50"],
                         [t for t in P.replay_core(DAY, WATCH, deleted, rel)["journal"] if t["at"] <= "09:50"])


class Controls(unittest.TestCase):
    def test_draw_excludes_signal_candles_and_is_seeded(self):
        st = P.replay_core(DAY, WATCH, session(), rel)
        sig = P.signal_candles(st)["ACME"]
        self.assertIn("09:50", sig)
        t = next(t for t in st["journal"] if t["setup"] == "vwap_hammer")
        parent = P.ticket_row(DAY, t, "H1")
        ctrl, pool = P.control_rows(DAY, parent, session()["ACME"], sig)
        self.assertEqual(len(ctrl), 3)
        self.assertEqual(pool, len(P.MORNING) - len(sig))
        self.assertFalse({c["issued_at"] for c in ctrl} & sig)
        again, _ = P.control_rows(DAY, parent, session()["ACME"], sig)
        self.assertEqual(ctrl, again)
        pool_list = sorted(c for c in P.MORNING if c not in sig)
        self.assertEqual([c["issued_at"] for c in ctrl],
                         sorted(random.Random(P.control_seed(parent["ticket_id"])).sample(pool_list, 3)))

    def test_control_geometry_is_the_parents(self):
        st = P.replay_core(DAY, WATCH, session(), rel)
        t = next(t for t in st["journal"] if t["setup"] == "vwap_hammer")
        parent = P.ticket_row(DAY, t, "H1")
        r = round(parent["trigger"] - parent["stop"], 2)
        for c in P.control_rows(DAY, parent, session()["ACME"], P.signal_candles(st)["ACME"])[0]:
            self.assertAlmostEqual(c["trigger"] - c["stop"], r, places=6)
            self.assertAlmostEqual(c["target"] - c["trigger"], 2 * r, places=6)
            self.assertEqual(c["parent_id"], parent["ticket_id"])

    def test_fewer_eligible_closes(self):
        self.assertEqual(P.draw_controls("x", ["10:00", "10:05"]), ["10:00", "10:05"])
        self.assertEqual(P.draw_controls("x", []), [])


class Build(unittest.TestCase):
    def test_session_sets_and_counts(self):
        rows, c = P.build_session(DAY, WATCH, session(), session(), rel)
        sets = sorted({r["set"] for r in rows})
        self.assertEqual(sets, ["CONTROLS", "H1", "H1_SIPSIG"])
        self.assertEqual(c["tickets:H1:vwap_hammer"], 1)
        self.assertEqual(c["controls"], 3)
        self.assertEqual(len(P.frame(rows)), len(rows))
        self.assertEqual(P.canonical_sha256(P.frame(rows)), P.canonical_sha256(P.frame(list(reversed(rows)))))


if __name__ == "__main__":
    unittest.main()
