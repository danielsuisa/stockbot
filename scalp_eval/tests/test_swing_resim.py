"""The independent re-simulation (S2 Stage 2) agrees with the evaluator on synthetic sessions, and its fee schedule
reproduces costs.py. The check itself runs on real development tickets (python -m scalp_eval.swing_resim)."""
import datetime as dt
import unittest

from scalp_eval import costs
from scalp_eval import swing as W
from scalp_eval import swing_resim as R

DAYS = []
_d = dt.date(2024, 1, 2)
while len(DAYS) < 45:
    if _d.weekday() < 5:
        DAYS.append(_d)
    _d += dt.timedelta(days=1)


def market(spec, splits=None):
    spec = {0: (49.0, 49.9, 48.8, 49.8), **spec}
    ii = sorted(spec)
    bars = {"A": (ii, [spec[i][0] for i in ii], [spec[i][1] for i in ii], [spec[i][2] for i in ii], [spec[i][3] for i in ii])}
    return W.SwingMarket(DAYS, bars, splits=splits or {})


TK = {"ticket_id": "t", "symbol": "A", "signal_date": DAYS[0], "trigger": 50.0, "limit": 50.5, "stop": 48.0,
      "target": 54.0, "qty": 10}
OPEN = {("A", d): (49.99, 50.01) for d in DAYS}
CLOSE = {("A", d): (49.98, 50.02) for d in DAYS}
DV = {"A": {i: 100e6 for i in range(45)}}


def both(spec, mins=None, splits=None):
    m = market(spec, splits)
    q = W.Quotes(OPEN, CLOSE, 0.00025)
    mins = mins or {}
    window = set(DAYS[1:4])
    ev = W.run_ticket(TK, m, lambda s, d: mins.get((s, d)) if d in window else None, q)
    D = R.Data(m, {"T1": q, "T2": q}, mins, DV)
    return R.resim(TK, D), ev


class Agreement(unittest.TestCase):
    def check(self, spec, mins=None, splits=None):
        mine, ev = both(spec, mins, splits)
        self.assertIsNone(R.compare(mine, ev), (mine, ev))
        return mine

    def test_trigger_intraday_target(self):
        mine = self.check({1: (49.5, 50.6, 49.4, 50.3), 2: (50.5, 54.5, 50.4, 54.2)})
        self.assertEqual((mine["case"], mine["fill_px"]), ("target", 50.01))

    def test_gap_below_stop(self):
        mine = self.check({1: (49.5, 50.6, 49.4, 50.3), 3: (47.0, 47.5, 46.5, 47.2)})
        self.assertEqual(mine["case"], "gap_below_stop")

    def test_resting_limit_with_minutes(self):
        mins = {("A", DAYS[1]): [("09:30", 51.0, 51.2, 50.9, 51.1), ("09:31", 51.1, 51.1, 50.4, 50.6)]}
        mine = self.check({1: (51.0, 51.2, 50.4, 50.8), 2: (50.5, 54.5, 50.4, 54.2)}, mins)
        self.assertEqual((mine["fill_px"], mine["case"]), (50.5, "target"))

    def test_split_during_hold(self):
        mine = self.check({1: (49.5, 50.6, 49.4, 50.3), 2: (25.2, 25.4, 25.0, 25.3), 3: (25.3, 27.5, 25.2, 27.3)},
                          splits={"A": [(DAYS[2], 2.0)]})
        self.assertEqual(mine["case"], "target")

    def test_fee_schedule_matches_costs(self):
        for side in ("buy", "sell"):
            for q, px, d in ((10, 50.0, DAYS[0]), (300, 12.34, dt.date(2026, 10, 5)), (1, 900.0, DAYS[3])):
                self.assertAlmostEqual(R.fee(side, q, px, d), costs.order_cost("tiered", side, q, px, d), places=12)


if __name__ == "__main__":
    unittest.main()
