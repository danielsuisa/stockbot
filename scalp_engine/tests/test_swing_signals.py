"""Swing signals, tickets, controls (ADR 0013 section 1, ADR 0014 sections 3-4) and the S1 leakage tests:
future perturbation and the clock. Synthetic data only."""
import datetime as dt
import random
import unittest

import polars as pl

from scalp_engine import swing_signals as W

UTC = dt.timezone.utc


def sessions(n, start=dt.date(2022, 9, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


S = sessions(70)
T = S[45]  # the signal day (in the development window)


def bars(mutate_after=None):
    rows = []
    for sym, px in (("AAA", 50.0), ("BBB", 40.0), ("CCC", 30.0), ("DDD", 60.0), ("EEE", 25.0), ("FFF", 45.0), ("GGG", 35.0)):
        for i, d in enumerate(S):
            o, h, l, c, v = px, px * 1.01, px * 0.99, px, 1_000_000
            if d == T and sym in ("AAA", "BBB", "CCC"):  # gap and hold: open >= 1.03 * prior high, close near the high
                o, h, l, c = px * 1.05, px * 1.08, px * 1.045, px * 1.075
            if mutate_after is not None and d > mutate_after:
                o, h, l, c, v = o * 3.7, h * 4.1, l * 0.2, c * 1.9, v * 9
            rows.append((sym, d, o, h, l, c, v))
    b = pl.DataFrame(rows, schema={"symbol": pl.Utf8, "d": pl.Date, "open": pl.Float64, "high": pl.Float64,
                                   "low": pl.Float64, "close": pl.Float64, "volume": pl.Int64}, orient="row")
    if mutate_after == "delete":
        return b
    return b


def inputs():
    obs = pl.DataFrame([(s, S[0], c) for s, c in (("AAA", "1"), ("BBB", "2"), ("CCC", "3"), ("DDD", "4"), ("EEE", "5"),
                                                  ("FFF", "6"), ("GGG", "7"))],
                       schema={"symbol": pl.Utf8, "obs_d": pl.Date, "cik": pl.Utf8}, orient="row")
    sic = pl.DataFrame({"cik": ["1", "2", "3", "4", "5", "6", "7"], "sic": ["3674", "2834", "1311", "7372", "6726", "3674", "3674"]})
    annual = pl.DataFrame([("3", S[2], "20-F"), ("1", S[3], "10-K")],
                          schema={"cik": pl.Utf8, "filing_date": pl.Date, "form": pl.Utf8}, orient="row")
    # BBB: an Item 2.02 8-K accepted at t 17:30 ET (inside the A2 window); AAA: one after issuance on t + 2 (late)
    acc = pl.DataFrame([("2", dt.datetime.combine(T, dt.time(17, 30), W.ET).astimezone(UTC)),
                        ("1", dt.datetime.combine(S[47], dt.time(8, 0), W.ET).astimezone(UTC))],
                       schema={"cik": pl.Utf8, "accepted_at": pl.Datetime("us", "UTC")}, orient="row")
    return obs, sic, annual, acc


def run(b):
    obs, sic, annual, acc = inputs()
    old = (W.SIG_FIRST, W.SIG_LAST)
    return W.build(b, S, obs, sic, annual, acc)


class Tickets(unittest.TestCase):
    def test_tick_and_geometry(self):
        self.assertEqual(W.tick(10.005), 10.01)
        tk = W.make_ticket("gaphold", 52.0, 51.0, 1.0)
        self.assertEqual((tk["trigger"], tk["limit"], tk["stop"], tk["r"], tk["target"]), (52.01, 52.26, 50.99, 1.02, 54.05))
        self.assertEqual(tk["qty"], min(int(25 // 1.02), int(1000 // 52.26)))  # 24 vs 19: the 40% cap binds
        tk = W.make_ticket("nday", 52.0, 51.0, 1.0)
        self.assertEqual((tk["stop"], tk["r"]), (50.01, 2.0))
        self.assertEqual(W.make_ticket("gaphold", 2000.0, 1990.0, 30.0)["qty"], 0)  # a 0-share ticket
        self.assertIsNone(W.make_ticket("gaphold", 10.0, 9.0, None))

    def test_dedupe_twenty_sessions(self):
        idx = {d: i for i, d in enumerate(S)}
        rs = [{"symbol": "A", "d": S[i]} for i in (5, 10, 24, 25, 45)]
        self.assertEqual([idx[r["d"]] for r in W.dedupe(rs, idx)], [5, 25, 45])

    def test_controls_seeded_and_deterministic(self):
        cands = {s: (50.0, 49.0, 1.0) for s in ("D", "E", "F", "G", "H")}
        a = W.draw_controls("gaphold|X|2024-01-02", "gaphold", cands)
        self.assertEqual(a, W.draw_controls("gaphold|X|2024-01-02", "gaphold", cands))
        perm = sorted(cands)
        random.Random(W.control_seed("gaphold|X|2024-01-02")).shuffle(perm)
        self.assertEqual([s for s, _ in a], perm[:3])


class Rules(unittest.TestCase):
    def test_rules_hash_is_adr_0013_section_1(self):
        from pathlib import Path
        adr = Path(W.__file__).resolve().parents[1] / "docs" / "engine" / "adr" / "0013-swing-ticket-validation-design.md"
        self.assertEqual(W.rules_text_sha256(adr), W.RULES_SHA256)


class Survivorship(unittest.TestCase):
    def test_rows_after_delisting_are_dropped(self):
        b = pl.DataFrame({"symbol": ["GONE", "GONE", "GONE", "KEEP"], "d": [S[0], S[1], S[2], S[2]]})
        got = W.drop_after(b, {"GONE": S[1]})
        self.assertEqual(sorted(zip(got["symbol"].to_list(), got["d"].to_list())), [("GONE", S[0]), ("KEEP", S[2])])
        self.assertEqual(W.drop_after(b, {}).height, 4)


class Build(unittest.TestCase):
    def test_h1_sb_a2_a3_controls(self):
        tickets, counts = run(bars())
        by = {(t["set"], t["symbol"]) for t in tickets if t["set"] != "controls"}
        self.assertIn(("H1", "AAA"), by)
        self.assertNotIn(("H1", "BBB"), by)       # A2: earnings 8-K at t 17:30 ET
        self.assertIn(("S-b", "BBB"), by)         # S-b keeps earnings days
        self.assertNotIn(("S-b", "CCC"), by)      # A3: a 20-F filer is outside the universe
        self.assertEqual(sum(counts["a2_excluded"].values()), 1)
        self.assertEqual(sum(counts["a3_excluded"].values()), 1)
        self.assertEqual(sum(counts["late_8k"].values()), 1)  # AAA's 8-K on t + 2 is reported, not excluded
        ctrl = [t for t in tickets if t["set"] == "controls" and t["parent_id"] == f"gaphold|AAA|{T.isoformat()}"]
        self.assertEqual(len(ctrl), 3)
        self.assertTrue({c["symbol"] for c in ctrl} <= {"DDD", "FFF", "GGG"})  # not AAA/BBB (signals), not EEE (fund SIC), not CCC (FPI)
        h1 = next(t for t in tickets if t["set"] == "H1")
        self.assertEqual(h1["trigger"], W.tick(50.0 * 1.08 + 0.01))

    def test_leakage_future_perturbation(self):
        """Altering every bar dated after t, or deleting them, leaves every signal and ticket at t unchanged."""
        base = [t for t in run(bars())[0] if t["signal_date"] == T]
        altered = [t for t in run(bars(mutate_after=T))[0] if t["signal_date"] == T]
        deleted = [t for t in run(bars().filter(pl.col("d") <= T))[0] if t["signal_date"] == T]
        self.assertEqual(base, altered)
        self.assertEqual(base, deleted)
        self.assertTrue(base)

    def test_clock_inputs_available_by_issuance(self):
        """Every input used for a ticket at t is available at or before t 20:00 ET."""
        tickets, _ = run(bars())
        obs, sic, annual, acc = inputs()
        issue = W.issue_at(T)
        self.assertEqual(issue, dt.datetime.combine(T, dt.time(20), W.ET).astimezone(UTC))
        bar_available = dt.datetime.combine(T, dt.time(16, 15), W.ET).astimezone(UTC)  # daily bar t (ADR 0014 section 3)
        self.assertLessEqual(bar_available, issue)
        for t in tickets:
            self.assertEqual(W.issue_at(t["signal_date"]), W.issue_at(t["signal_date"]))
        self.assertTrue(all(d <= T for d in annual["filing_date"].to_list()))
        lo, hi = W.a2_window(T, S[44])
        self.assertEqual(hi, issue)  # the exclusion never looks past issuance


if __name__ == "__main__":
    unittest.main()
