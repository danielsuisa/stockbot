"""T2 run rules (ADR 0017): the issued-basis gate (ii), the filled-basis report, the preflight refusals (authorize line,
uncommitted code, exactly one confirmation, the ORB 5 band), the set digest. Synthetic data only."""
import datetime as dt
import hashlib
import json
import unittest

import polars as pl

from scalp_eval import intraday_eval as E
from scalp_eval import intraday_t2 as T2

D1, D2 = dt.date(2025, 3, 3), dt.date(2025, 3, 4)


def res(status="filled", r=0.0):
    return {"status": status, "net_r": r} if status == "filled" else {"status": status}


class GateII(unittest.TestCase):
    H1 = [{"ticket_id": "a", "date": D1}, {"ticket_id": "b", "date": D1}, {"ticket_id": "c", "date": D2},
          {"ticket_id": "d", "date": D2}]
    CTRL = [{"ticket_id": "a|c0", "parent_id": "a"}, {"ticket_id": "a|c1", "parent_id": "a"},
            {"ticket_id": "b|c0", "parent_id": "b"}, {"ticket_id": "c|c0", "parent_id": "c"}]
    RES = {"a": res(r=1.5), "a|c0": res(r=-1.0), "a|c1": res("not_filled"),
           "b": res("not_filled"), "b|c0": res(r=2.0),
           "c": res(r=-1.0), "c|c0": res("rejected_liquidity"), "d": res(r=0.4)}

    def test_issued_basis(self):
        diffs, days, none = T2.issued_basis(self.H1, self.CTRL, self.RES)
        # a: 1.5 - (-1.0 + 0) / 2 = 2.0; b: 0 - 2.0 = -2.0; c: -1.0 - 0 = -1.0; d has no control
        self.assertEqual(diffs, [2.0, -2.0, -1.0])
        self.assertEqual((days, none), ([D1.isoformat(), D1.isoformat(), D2.isoformat()], 1))

    def test_filled_basis_reported_only(self):
        diffs, days, dropped = T2.filled_basis(self.H1, self.CTRL, self.RES)
        # a: 1.5 - (-1.0) = 2.5; b not filled; c: no filled control (dropped); d: no controls (dropped)
        self.assertEqual((diffs, dropped), ([2.5], 2))


class Preflight(unittest.TestCase):
    AUTH = {"phase": "T2", "kind": "authorize", "rules_sha256": "0" * 64, "sets": {}}
    DEV_OK = {"phase": "T2", "kind": "result", "window": "development", "orb5_in_band": True}

    @staticmethod
    def clean():
        return "abc", []

    def test_refusals(self):
        with self.assertRaisesRegex(E.Refused, "authorize"):
            T2.preflight("development", [], self.clean)
        with self.assertRaisesRegex(E.Refused, "uncommitted"):
            T2.preflight("development", [self.AUTH], lambda: ("abc", [" M scalp_eval/x.py"]))
        with self.assertRaisesRegex(E.Refused, "no logged development run"):
            T2.preflight("confirmation", [self.AUTH], self.clean)
        with self.assertRaisesRegex(E.Refused, "ORB 5"):
            T2.preflight("confirmation", [self.AUTH, {**self.DEV_OK, "orb5_in_band": False}], self.clean)
        self.assertEqual(T2.preflight("confirmation", [self.AUTH, self.DEV_OK], self.clean)[1], "abc")
        with self.assertRaisesRegex(E.Refused, "already started once"):
            T2.preflight("confirmation", [self.AUTH, self.DEV_OK, {"phase": "T2", "kind": "confirmation_start"}], self.clean)
        self.assertEqual(T2.preflight("development", [self.AUTH, self.DEV_OK], self.clean)[0], self.AUTH)

    def test_digest_is_order_free(self):
        df = pl.DataFrame({"b": [2, 1], "a": ["y", "x"], "d": [D2, D1]})
        lines = sorted(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in
                       [{"a": "y", "b": 2, "d": D2.isoformat()}, {"a": "x", "b": 1, "d": D1.isoformat()}])
        self.assertEqual(T2.canonical_sha256(df), hashlib.sha256("\n".join(lines).encode()).hexdigest())
        self.assertEqual(T2.canonical_sha256(df), T2.canonical_sha256(df.reverse().select(["d", "a", "b"])))


class TicketSetReader(unittest.TestCase):
    def test_refuses_a_sealed_date(self):
        import tempfile
        from pathlib import Path
        from scalp_eval.data_access import HoldoutSealedError, read_ticket_set
        with tempfile.TemporaryDirectory() as d:
            ok, bad = Path(d) / "ok.parquet", Path(d) / "bad.parquet"
            pl.DataFrame({"ticket_id": ["a"], "date": [D1]}).write_parquet(ok)
            pl.DataFrame({"ticket_id": ["a", "b"], "date": [D1, dt.date(2021, 6, 1)]}).write_parquet(bad)
            self.assertEqual(read_ticket_set(ok).height, 1)
            with self.assertRaises(HoldoutSealedError):
                read_ticket_set(bad)


if __name__ == "__main__":
    unittest.main()
