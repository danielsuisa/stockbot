"""S2 run rules (ADR 0020): the preflight refusals (authorize line, uncommitted code, a logged development run, a
passed re-simulation check, exactly one confirmation), the issued-basis gate (ii) and the filled-basis report, the
A5 fallback minute window, the liquidity tier and the SPY total return. Synthetic data only."""
import datetime as dt
import unittest

from scalp_eval import swing as W
from scalp_eval import swing_s2 as S

DAYS = []
_d = dt.date(2025, 3, 3)
while len(DAYS) < 30:
    if _d.weekday() < 5:
        DAYS.append(_d)
    _d += dt.timedelta(days=1)


def clean():
    return "abc", []


AUTH = {"phase": "S2", "kind": "authorize", "rules_sha256": W.RULES_SHA256, "sets": {}}
DEV = {"phase": "S2", "kind": "result", "window": "development"}
RESIM = {"phase": "S2", "kind": "resim_check", "pass": True}


class Preflight(unittest.TestCase):
    def test_refusals_and_order(self):
        with self.assertRaisesRegex(S.Refused, "authorize"):
            S.preflight("development", [], clean)
        with self.assertRaisesRegex(S.Refused, "uncommitted"):
            S.preflight("development", [AUTH], lambda: ("abc", [" M scalp_eval/x.py"]))
        with self.assertRaisesRegex(S.Refused, "no logged development run"):
            S.preflight("confirmation", [AUTH], clean)
        with self.assertRaisesRegex(S.Refused, "re-simulation"):
            S.preflight("confirmation", [AUTH, DEV], clean)
        with self.assertRaisesRegex(S.Refused, "re-simulation"):  # a check before the last development run does not count
            S.preflight("confirmation", [AUTH, DEV, RESIM, DEV], clean)
        self.assertEqual(S.preflight("confirmation", [AUTH, DEV, RESIM], clean)[1], "abc")
        with self.assertRaisesRegex(S.Refused, "already started once"):
            S.preflight("confirmation", [AUTH, DEV, RESIM, {"phase": "S2", "kind": "confirmation_start"}], clean)
        self.assertEqual(S.preflight("development", [AUTH], clean)[0], AUTH)

    def test_repository_ledger_has_no_s2_authorize_yet_or_one(self):
        n = sum(1 for r in S.ledger_lines() if r.get("phase") == "S2" and r.get("kind") == "authorize")
        self.assertIn(n, (0, 1))


def mkt():
    bars = {"A": (list(range(30)), [10.0] * 30, [10.5] * 30, [9.5] * 30, [10.0] * 30),
            "SPY": (list(range(30)), [500.0] * 30, [501.0] * 30, [499.0] * 30, [500.0 + i for i in range(30)])}
    return W.SwingMarket(DAYS, bars, divs={"SPY": [(DAYS[5], 2.0)]})


class Gate(unittest.TestCase):
    H1 = [{"ticket_id": "a", "signal_date": DAYS[0]}, {"ticket_id": "b", "signal_date": DAYS[0]},
          {"ticket_id": "c", "signal_date": DAYS[10]}, {"ticket_id": "d", "signal_date": DAYS[10]}]
    CTRL = {"a": [{"ticket_id": "a|c0"}, {"ticket_id": "a|c1"}], "b": [{"ticket_id": "b|c0"}], "c": [{"ticket_id": "c|c0"}]}
    RES = {"a": {"filled": True, "net_r": 1.5}, "a|c0": {"filled": True, "net_r": -1.0}, "a|c1": {"filled": False, "net_r": 0.0},
           "b": {"filled": False, "net_r": 0.0}, "b|c0": {"filled": True, "net_r": 2.0},
           "c": {"filled": True, "net_r": -1.0}, "c|c0": {"filled": False, "net_r": 0.0},
           "d": {"filled": True, "net_r": 0.4}}

    def test_issued_basis(self):
        s, none = S.issued_difference(self.H1, self.CTRL, self.RES, mkt())
        # a: 1.5 - (-1.0 + 0) / 2 = 2.0; b: 0 - 2.0 = -2.0; c: -1.0 - 0 = -1.0; d: no controls
        self.assertEqual((s["n"], none), (3, 1))
        self.assertAlmostEqual(s["mean"], (2.0 - 2.0 - 1.0) / 3)

    def test_filled_basis(self):
        f = S.filled_difference(self.H1, self.CTRL, self.RES)
        self.assertEqual((f["n"], f["dropped_no_filled_control"]), (1, 2))  # a only: 1.5 - (-1.0); c and d dropped
        self.assertIsNone(f["t"])


class Inputs(unittest.TestCase):
    def test_minute_window_is_the_tickets_expiry(self):
        m = mkt()
        mins = {("A", DAYS[i]): [("09:30", 10, 10, 10, 10)] for i in range(10)}
        f = S.minutes_for({"signal_date": DAYS[2]}, m, mins)
        self.assertIsNone(f("A", DAYS[2]))
        self.assertIsNotNone(f("A", DAYS[3]))
        self.assertIsNotNone(f("A", DAYS[5]))
        self.assertIsNone(f("A", DAYS[6]))  # a holding session after expiry: stop first (A5 fallback)

    def test_tier(self):
        dv = {"A": {i: 60e6 for i in range(30)}, "B": {i: (60e6 if i % 2 else 0.0) for i in range(30)}}
        self.assertEqual(S.tier(dv, "A", 25), "T1")
        self.assertEqual(S.dv20(dv, "B", 25), 30e6)  # half the sessions without a bar count 0: median 30M
        self.assertEqual(S.tier(dv, "B", 25), "T2")
        self.assertEqual(S.TIER_HALF, {"T1": 0.00025, "T2": 0.000475})

    def test_spy_total_return(self):
        self.assertAlmostEqual(S.spy_total_return(mkt(), 0, 10), (510.0 + 2.0) / 500.0 - 1.0)


if __name__ == "__main__":
    unittest.main()
