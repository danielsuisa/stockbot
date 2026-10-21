"""The intraday ticket evaluator's golden set (ADR 0016 section 10): hand-computed cases on synthetic sessions, plus
inference, the gate and the CLI guard. The control draw that excludes signal candles is golden case 15, in
scalp_engine/tests/test_t1_replay.py (Controls), since the draw is part of the signed set."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scalp_eval import costs
from scalp_eval import intraday_eval as E

DAY = dt.date(2024, 6, 12)
TK = {"trigger": 20.91, "limit": 20.96, "stop": 20.54, "target": 21.65, "shares": 67, "issued_at": "09:50"}
Q = {"09:50": (20.90, 20.92), "15:55": (21.00, 21.04)}  # spreads $0.02 at the candle close, $0.04 at the exit
BIG = 1_000_000


def m(t, o, h, l, c, v=BIG):
    return (t, o, h, l, c, v)


def quiet(first, last, px=20.80):
    out, n = [], E.hm(first)
    while n <= E.hm(last):
        out.append(m(E.hhmm(n), px, px + 0.01, px - 0.01, px))
        n += 1
    return out


class Golden(unittest.TestCase):
    def test_01_trigger_after_live(self):
        bars = [m("09:50", 20.80, 20.85, 20.75, 20.80), m("09:51", 20.85, 20.95, 20.84, 20.93),
                m("09:52", 20.93, 21.70, 20.90, 21.60)]
        r = E.simulate(TK, bars, Q, day=DAY)
        self.assertEqual((r["status"], r["exit_kind"], r["fill_minute"], r["exit_minute"]), ("filled", "target", "09:51", "09:52"))
        self.assertAlmostEqual(r["entry"], 20.92)  # the trigger (open below it) + half of $0.02
        self.assertAlmostEqual(r["exit"], 21.65)   # the target, no spread
        # by hand: buy 0.35 + 0.000201 + 0.201 + 0.0134 + 0.000259 = 0.56486; sell adds SEC 0.02988133 + TAF 0.011122
        self.assertAlmostEqual(r["costs"], 1.17072333, places=7)
        self.assertAlmostEqual(r["net"], 48.91 - 1.17072333, places=6)
        self.assertAlmostEqual(r["net_r"], 1.92574734, places=6)  # net / (67 x 0.37)
        self.assertAlmostEqual(r["gross_r"], 2.0)  # 67 x (21.65 - 20.91) / (67 x 0.37): before spread and fees

    def test_02_traded_before_live_filled_at_open(self):
        bars = [m("09:50", 20.80, 20.95, 20.75, 20.90), m("09:51", 20.94, 20.98, 20.90, 20.95)] + quiet("09:52", "15:59", 20.95)
        r = E.simulate(TK, bars, Q, day=DAY)
        self.assertEqual((r["status"], r["fill_minute"]), ("filled", "09:51"))
        self.assertAlmostEqual(r["entry"], 20.95)  # the open 20.94 (<= limit 20.96) + 0.01

    def test_03_traded_before_live_resting_then_filled(self):
        bars = [m("09:50", 20.80, 20.95, 20.75, 20.90), m("09:51", 21.00, 21.05, 20.98, 21.02),
                m("09:52", 20.99, 21.00, 20.95, 20.97)] + quiet("09:53", "15:59", 20.97)
        r = E.simulate(TK, bars, Q, day=DAY)
        self.assertEqual((r["status"], r["fill_minute"]), ("filled", "09:52"))
        self.assertAlmostEqual(r["entry"], 20.97)  # the limit 20.96 (open 20.99 above it, low 20.95 below) + 0.01

    def test_04_traded_before_live_expires_at_time_exit(self):
        bars = [m("09:50", 20.80, 20.95, 20.75, 20.90)] + quiet("09:51", "15:59", 21.10)
        r = E.simulate(TK, bars, Q, day=DAY)
        self.assertEqual((r["status"], r["resting"]), ("not_filled", True))

    def test_05_stop_in_fill_minute(self):
        bars = [m("09:50", 20.80, 20.85, 20.75, 20.80), m("09:51", 20.85, 20.95, 20.50, 20.60)]
        r = E.simulate(TK, bars, Q, day=DAY)
        self.assertEqual((r["exit_kind"], r["exit_minute"]), ("stop", "09:51"))
        self.assertAlmostEqual(r["exit"], 20.52)  # min(20.54, 20.85) - 0.01 - 0.01

    def test_06_same_minute_stop_and_target(self):
        bars = [m("09:50", 20.80, 20.85, 20.75, 20.80), m("09:51", 20.85, 20.95, 20.84, 20.93),
                m("09:52", 20.93, 21.80, 20.50, 21.00)]
        r = E.simulate(TK, bars, Q, day=DAY)
        self.assertEqual(r["exit_kind"], "stop")
        self.assertAlmostEqual(r["exit"], 20.52)

    def test_07_target_touched_not_traded_through(self):
        bars = ([m("09:50", 20.80, 20.85, 20.75, 20.80), m("09:51", 20.85, 20.95, 20.84, 20.93),
                 m("09:52", 21.00, 21.65, 20.99, 21.30)] + quiet("09:53", "15:54", 21.30))
        r = E.simulate(TK, bars, Q, day=DAY)
        self.assertEqual(r["exit_kind"], "time")

    def test_08_gap_through_stop(self):
        bars = [m("09:50", 20.80, 20.85, 20.75, 20.80), m("09:51", 20.85, 20.95, 20.84, 20.93),
                m("09:52", 20.80, 20.85, 20.70, 20.75), m("09:53", 20.30, 20.35, 20.20, 20.25)]
        r = E.simulate(TK, bars, Q, day=DAY)
        self.assertEqual((r["exit_kind"], r["exit_minute"]), ("stop", "09:53"))
        self.assertAlmostEqual(r["exit"], 20.28)  # the open 20.30 below the stop - 0.01 - 0.01

    def test_09_time_exit_1555(self):
        bars = ([m("09:50", 20.80, 20.85, 20.75, 20.80), m("09:51", 20.85, 20.95, 20.84, 20.93)]
                + quiet("09:52", "15:53", 21.00) + [m("15:54", 21.05, 21.12, 21.04, 21.10)] + quiet("15:55", "15:59", 25.0))
        r = E.simulate(TK, bars, Q, day=DAY)
        self.assertEqual((r["exit_kind"], r["exit_minute"]), ("time", "15:54"))
        self.assertAlmostEqual(r["exit"], 21.08)  # the 15:54 close 21.10 - half of the 15:55 spread $0.04
        early = E.simulate(TK, bars[:200], Q, time_exit="12:55", day=DAY)  # early close: close - 5 minutes
        self.assertEqual(early["exit_minute"], "12:54")

    def test_10_liquidity_rejection(self):
        bars = [m("09:50", 20.80, 20.85, 20.75, 20.80), m("09:51", 20.85, 20.95, 20.84, 20.93, 5_000)]
        r = E.simulate(TK, bars, Q, day=DAY)
        self.assertEqual(r["status"], "rejected_liquidity")  # 67 shares > 1% of 5,000

    def test_11_zero_share_skip(self):
        self.assertEqual(E.size(400.0, 370.0), 0)  # R $30 > $25
        self.assertEqual(E.simulate({**TK, "shares": 0}, [], Q)["status"], "zero_share")

    def test_12_full_equity_cap_binds(self):
        self.assertEqual(E.size(400.0, 399.0), 6)  # min(25 / 1, 2500 / 400 = 6.25)

    def test_13_twenty_percent_cap_variant(self):
        self.assertEqual(E.size(20.91, 20.54), 67)
        self.assertEqual(E.size(20.91, 20.54, cap_share=0.20), 23)  # 500 / 20.91

    def test_14_t_plus_1_blocks_a_second_trade(self):
        s = [DAY, dt.date(2024, 6, 13)]
        t1 = {"ticket_id": "a", "date": DAY, "fill_minute": "09:51", "exit_minute": "10:30", "entry": 20.92, "exit": 21.65, "shares": 67}
        t2 = {"ticket_id": "b", "date": DAY, "fill_minute": "11:00", "exit_minute": "12:00", "entry": 20.0, "exit": 20.5, "shares": 70}
        t3 = {**t2, "ticket_id": "c", "date": s[1]}
        b = E.book([t1, t2, t3], s)
        self.assertEqual(b["taken"], ["a", "c"])  # b needs $1,400 of settled cash; only ~$1,098 is settled that day
        self.assertEqual(b["blocked_settlement"], 1)
        t4 = {**t2, "ticket_id": "d", "fill_minute": "10:00"}  # fills while a is open
        self.assertEqual(E.book([t1, t4], s)["blocked_position"], 1)

    def test_16_commission_minimum_vs_per_share(self):
        self.assertAlmostEqual(costs.commission("tiered", 67, 20.92), 0.35)   # 0.0035 x 67 = 0.2345 < 0.35
        self.assertAlmostEqual(costs.commission("tiered", 200, 20.92), 0.70)  # 0.0035 x 200


class Helpers(unittest.TestCase):
    def test_spread_fallback(self):
        self.assertAlmostEqual(E.spread_at({"10:00": (10.0, 10.05)}, "10:00"), 0.05)
        self.assertAlmostEqual(E.spread_at({"10:00": (10.0, 9.9), "10:10": (10.0, 10.03), "09:50": (10, 10.02)}, "10:00"), 0.02)
        self.assertIsNone(E.spread_at({"10:00": None}, "10:00"))
        self.assertEqual(E.simulate(TK, [], {}), {"status": "no_quote"})

    def test_control_difference(self):
        self.assertAlmostEqual(E.control_difference(1.0, [0.5, None, -0.5]), 1.0)
        self.assertIsNone(E.control_difference(1.0, [None, None]))

    def test_inference_and_gate(self):
        vals = [1.0, -0.5, 0.8, 0.2, 0.4, -0.1]
        days = ["a", "a", "b", "c", "c", "d"]
        out = E.inference(vals, days, n=2000)
        self.assertAlmostEqual(out["mean"], sum(vals) / 6)
        self.assertEqual(out["G"], 4)
        self.assertEqual(out["boot_ci"], E.inference(vals, days, n=2000)["boot_ci"])  # seeded
        g = E.gate(vals, days, [0.1, None, 0.2])
        self.assertEqual((g["ii_n"], g["ii_dropped"], g["pass_ii"]), (2, 1, True))
        self.assertAlmostEqual(E.mde(1340), 2.8416 * 1.47 / 1340 ** 0.5)


class Guard(unittest.TestCase):
    def test_refuses_without_t2_entry(self):
        with tempfile.TemporaryDirectory() as d:
            led = Path(d) / "t_trials.jsonl"
            with self.assertRaises(E.Refused):
                E.run_real("x.signed.json", ledger=led)
            led.write_text(json.dumps({"phase": "T1", "kind": "authorize"}) + "\n", encoding="utf-8")
            with self.assertRaises(E.Refused):
                E.run_real("x.signed.json", ledger=led)
            led.write_text(json.dumps({"phase": "T2", "kind": "authorize"}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(E.Refused, "intraday_t2"):  # real sets run by window, with every check
                E.run_real("x.signed.json", ledger=led)

    def test_repository_ledger_state(self):
        """T2: the ledger holds exactly one authorize line; a single-set run is still refused (exit 2), and the
        confirmation is refused while the last development run's ORB 5 mean is outside its band."""
        from scalp_eval import intraday_t2 as T2
        lines = T2.ledger_lines()
        self.assertEqual(sum(1 for r in lines if r.get("phase") == "T2" and r.get("kind") == "authorize"), 1)
        self.assertEqual(E.main(["run", "--set", "derived/t1/sets/confirmation/H1.signed.json"]), 2)
        if any(r.get("kind") == "stop_before_confirmation" for r in lines):
            with self.assertRaises(E.Refused):
                T2.preflight("confirmation", lines, lambda: ("x", []))


if __name__ == "__main__":
    unittest.main()
