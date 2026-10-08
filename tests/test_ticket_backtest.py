"""Order-ticket backtest: fills, exits, costs and the random-direction mirror."""
import unittest

from bot import ticket, ticket_backtest as tb

CTX = {"day": "2024-03-01", "open": "09:30", "close": "16:00", "atr": 2.0, "open_rel_vol": 1.5, "from": "09:30"}
ORIG = {"ok": True, "rules": "ORIGINAL", "session": "REGULAR", "at": "10:25", "level": 10.00, "entry_type": "LMT",
        "entry": 10.05, "limit": 10.05, "stop": 9.75, "R": 0.30, "shares": 333,
        "legs": [{"qty": 166, "target": 10.65, "exit_at": None}, {"qty": 167, "target": None, "exit_at": None}],
        "valid_until": "16:00", "spread": 0.02}
IMPR = {"ok": True, "rules": "IMPROVED", "session": "REGULAR", "at": "09:40", "level": 10.00, "entry_type": "STP_LMT",
        "entry": 10.00, "limit": 10.20, "stop": 9.80, "R": 0.20, "shares": 454,
        "legs": [{"qty": 227, "target": 10.40, "exit_at": "15:55"}, {"qty": 227, "target": None, "exit_at": "15:55"}],
        "valid_until": "10:35", "spread": 0.02}


def mk(t, o, h, l, c, v=100):
    return (t, o, h, l, c, v, c)


def run(start, rows, v=100):
    """Consecutive minutes from `start`, rows (o, h, l, c)."""
    n = ticket.mins(start)
    return [mk(ticket.hhmm(n + i), *r, v) for i, r in enumerate(rows)]


def flat(start, end, p, v=100):
    return [mk(ticket.hhmm(n), p, p, p, p, v) for n in range(ticket.mins(start), ticket.mins(end))]


def fives(start, rows):
    """5-minute bars (h, l, c, v) as minutes: the first opens at the previous close (clipped) and holds the high and
    low, the other four sit at the close."""
    out, prev, n = [], None, ticket.mins(start)
    for h, l, c, v in rows:
        o = min(max(c if prev is None else prev, l), h)
        out += [mk(ticket.hhmm(n), o, h, l, c, v / 5)] + [mk(ticket.hhmm(n + j), c, c, c, c, v / 5) for j in range(1, 5)]
        prev, n = c, n + 5
    return out


PRE = flat("09:30", "10:25", 9.95)
FILLED = PRE + run("10:25", [(10.08, 10.10, 10.05, 10.08)])  # the 10:25 low reaches the 10.05 limit


def reflect(bars, p):
    return [(t, 2 * p - o, 2 * p - l, 2 * p - h, 2 * p - c, v, 2 * p - vw) for t, o, h, l, c, v, vw in bars]


# after the target: swing lows 10.40 (10:45) and 10.52 (11:00, higher, known when the 11:10 bar closes) -> the trail
# stop rises to 10.42 at 11:15; closes keep rising, so they stay above the VWAP
UP = [(10.70, 10.20, 10.60, 10000), (10.64, 10.55, 10.62, 10000), (10.65, 10.50, 10.63, 10000),
      (10.66, 10.40, 10.64, 10000), (10.68, 10.60, 10.66, 10000), (10.70, 10.62, 10.68, 10000),
      (10.71, 10.52, 10.69, 10000), (10.72, 10.58, 10.70, 10000), (10.74, 10.60, 10.72, 10000)]
TRAIL = fives("09:30", [(9.95, 9.95, 9.95, 100)] * 11 + [(10.10, 10.05, 10.08, 100)] + UP +
              [(10.72, 10.30, 10.35, 10000)])


class Fills(unittest.TestCase):
    def test_limit_fills_only_when_low_reaches_it(self):
        bars = flat("09:30", "10:24", 10.30) + run("10:24", [(10.0, 10.0, 10.0, 10.0), (10.15, 10.20, 10.06, 10.10),
                                                            (10.10, 10.12, 10.05, 10.08)]) + flat("10:27", "16:00", 10.08)
        r = tb.simulate(ORIG, bars, CTX)
        self.assertEqual((r["filled"], r["fill_at"], r["fill"]), (True, "10:26", 10.05))  # not 10:24, before the order
        never = flat("09:30", "10:25", 10.30) + flat("10:25", "16:00", 10.06)
        self.assertEqual(tb.simulate(ORIG, never, CTX)["filled"], False)
        self.assertEqual(tb.simulate(ORIG, never, CTX)["net_r"], 0)

    def test_stop_limit_triggers_then_fills(self):
        pre = flat("09:30", "09:40", 9.95)
        at_level = tb.simulate(IMPR, pre + run("09:40", [(9.98, 10.10, 9.97, 10.05)]) + flat("09:41", "16:00", 10.05), CTX)
        self.assertEqual((at_level["fill_at"], at_level["fill"]), ("09:40", 10.00))
        gap = tb.simulate(IMPR, pre + run("09:40", [(10.05, 10.10, 10.04, 10.08), (10.08, 10.45, 10.08, 10.40)]) +
                          flat("09:42", "16:00", 10.40), CTX)
        self.assertEqual(gap["fill"], 10.05)
        self.assertEqual(gap["exits"][0], {"qty": 227, "price": 10.45, "at": "09:41", "kind": "target"})  # 10.05 + 2D
        above = pre + run("09:40", [(10.30, 10.35, 10.25, 10.30), (10.25, 10.26, 10.15, 10.20)]) + \
            flat("09:42", "16:00", 10.20)
        r = tb.simulate(IMPR, above, CTX)
        self.assertEqual((r["fill_at"], r["fill"]), ("09:41", 10.20))
        gone = pre + run("09:40", [(10.30, 10.35, 10.25, 10.30)]) + flat("09:41", "16:00", 10.30)
        self.assertFalse(tb.simulate(IMPR, gone, CTX)["filled"])
        late = pre + run("09:40", [(10.30, 10.35, 10.25, 10.30)]) + flat("09:41", "10:35", 10.30) + \
            flat("10:35", "16:00", 10.10)  # back under the limit only after the order expired
        self.assertFalse(tb.simulate(IMPR, late, CTX)["filled"])


class Exits(unittest.TestCase):
    def test_stop_gap_fills_at_open(self):
        gap = tb.simulate(ORIG, FILLED + run("10:26", [(9.70, 9.72, 9.60, 9.65)]) + flat("10:27", "16:00", 9.65), CTX)
        self.assertEqual(gap["exits"], [{"qty": 333, "price": 9.70, "at": "10:26", "kind": "stop"}])
        touch = tb.simulate(ORIG, FILLED + run("10:26", [(9.90, 9.92, 9.74, 9.80)]) + flat("10:27", "16:00", 9.80), CTX)
        self.assertEqual(touch["exits"], [{"qty": 333, "price": 9.75, "at": "10:26", "kind": "stop"}])
        self.assertAlmostEqual(touch["gross_r"], -1)

    def test_stop_first_when_both_touched(self):
        r = tb.simulate(ORIG, FILLED + run("10:26", [(10.00, 10.70, 9.70, 10.00)]) + flat("10:27", "16:00", 10.0), CTX)
        self.assertEqual(r["exits"], [{"qty": 333, "price": 9.75, "at": "10:26", "kind": "stop"}])

    def test_stop_on_the_fill_minute(self):
        r = tb.simulate(ORIG, PRE + run("10:25", [(10.08, 10.70, 9.70, 10.0)]) + flat("10:26", "16:00", 10.0), CTX)
        self.assertEqual(r["exits"], [{"qty": 333, "price": 9.75, "at": "10:25", "kind": "stop"}])

    def test_target_then_breakeven_original(self):
        r = tb.simulate(ORIG, FILLED + run("10:26", [(10.30, 10.66, 10.30, 10.50), (10.50, 10.52, 10.04, 10.10)]) +
                        flat("10:28", "16:00", 10.10), CTX)
        self.assertEqual(r["exits"], [{"qty": 166, "price": 10.65, "at": "10:26", "kind": "target"},
                                      {"qty": 167, "price": 10.05, "at": "10:27", "kind": "breakeven"}])
        self.assertAlmostEqual(r["gross_r"], 166 * 0.60 / (0.30 * 333))

    def test_time_stop_60_minutes(self):
        drift = FILLED + flat("10:26", "11:25", 10.20) + flat("11:26", "16:00", 10.10)  # 11:25 missing
        r = tb.simulate(ORIG, drift, CTX)
        self.assertEqual(r["exits"], [{"qty": 333, "price": 10.10, "at": "11:26", "kind": "time"}])
        r = tb.simulate(ORIG, FILLED + flat("10:26", "16:00", 10.20), CTX)
        self.assertEqual(r["exits"][0]["at"], "11:25")  # 60 minutes after the 10:25 fill

    def test_trail_and_vwap_exit_after_target(self):
        r = tb.simulate(ORIG, TRAIL, CTX)
        self.assertEqual(r["exits"], [{"qty": 166, "price": 10.65, "at": "10:30", "kind": "target"},
                                      {"qty": 167, "price": 10.42, "at": "11:15", "kind": "trail"}])
        self.assertAlmostEqual(r["gross_r"], (166 * 0.60 + 167 * 0.37) / (0.30 * 333))
        dip = fives("09:30", [(9.95, 9.95, 9.95, 100)] * 11 + [(10.10, 10.05, 10.08, 100)] + UP +
                    [(10.70, 10.45, 10.46, 10000), (10.47, 10.40, 10.45, 10000)])
        r = tb.simulate(ORIG, dip, CTX)  # the 11:15 bar closes at 10.46, under the VWAP (~10.63)
        self.assertEqual(r["exits"][1], {"qty": 167, "price": 10.46, "at": "11:20", "kind": "vwap"})

    def test_end_of_day_exit_on_early_close(self):
        ctx = {**CTX, "close": "13:00"}
        bars = FILLED + run("10:26", [(10.30, 10.70, 10.30, 10.70)]) + flat("10:27", "13:00", 10.70, 1000) + \
            flat("13:00", "16:00", 9.00)  # after-hours minutes are not the session
        r = tb.simulate(ORIG, bars, ctx)
        self.assertEqual(r["exits"][1], {"qty": 167, "price": 10.70, "at": "12:59", "kind": "eod"})
        t = {**IMPR, "legs": [{**leg, "exit_at": "12:55"} for leg in IMPR["legs"]]}
        bars = flat("09:30", "09:40", 9.95) + run("09:40", [(9.98, 10.10, 9.97, 10.05)]) + flat("09:41", "12:55", 10.10) + \
            flat("12:55", "16:00", 10.15)
        r = tb.simulate(t, bars, ctx)
        self.assertEqual(r["exits"], [{"qty": 227, "price": 10.15, "at": "12:55", "kind": "eod"},
                                      {"qty": 227, "price": 10.15, "at": "12:55", "kind": "eod"}])

    def test_costs(self):
        r = tb.simulate(ORIG, FILLED + run("10:26", [(9.90, 9.92, 9.74, 9.80)]) + flat("10:27", "16:00", 9.80), CTX)
        risk = 0.30 * 333
        self.assertAlmostEqual(r["cost_r"], (2 * 333 * 0.0035 + 333 * 0.02) / risk)
        self.assertAlmostEqual(r["net_r"], -1 - r["cost_r"])
        small = {**IMPR, "shares": 20, "legs": [{**IMPR["legs"][0], "qty": 10}, {**IMPR["legs"][1], "qty": 10}]}
        bars = flat("09:30", "09:40", 9.95) + run("09:40", [(9.98, 10.10, 9.97, 10.05), (9.90, 9.90, 9.70, 9.75)]) + \
            flat("09:42", "16:00", 9.75)
        r = tb.simulate(small, bars, CTX)  # 2 entry and 2 exit orders at the $0.35 minimum
        self.assertAlmostEqual(r["cost_r"], (4 * 0.35 + 20 * 0.02) / (0.20 * 20))

    def test_mirror_short_is_symmetric_on_symmetric_bars(self):
        long_ = tb.simulate(ORIG, TRAIL, CTX)
        short = tb.simulate(ORIG, TRAIL, CTX, direction=-1)
        self.assertEqual((short["fill_at"], short["fill"]), (long_["fill_at"], long_["fill"]))
        mirror = tb.simulate(ORIG, reflect(TRAIL, 10.05), CTX)
        self.assertEqual([e["kind"] for e in short["exits"]], [e["kind"] for e in mirror["exits"]])
        for k in ("gross_r", "cost_r", "net_r"):
            self.assertAlmostEqual(short[k], mirror[k])
        self.assertAlmostEqual(short["exits"][0]["price"], 2 * 10.05 - mirror["exits"][0]["price"])
        self.assertEqual(short["exits"][0]["kind"], "stop")  # TRAIL rises: the short is stopped above the fill
        self.assertAlmostEqual(short["exits"][0]["price"], 10.35)


# ---------- candidate universes ----------
import datetime as dt  # noqa: E402
import os  # noqa: E402
import tempfile  # noqa: E402
from unittest import mock  # noqa: E402

DAY = "2024-06-03"


def sessions_before(day, n):
    """n weekdays before `day`, ascending (a stand-in calendar)."""
    out, d = [], dt.date.fromisoformat(day)
    while len(out) < n:
        d -= dt.timedelta(1)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return out[::-1]


def daily(vol=1_000_000, close=5.0, n=63, jump=None):
    rows = [(d, close, close + 0.5, close - 0.5, close, vol) for d in sessions_before(DAY, n)]
    if jump:
        k, c = jump
        rows[k] = rows[k][:4] + (c, vol)
    return rows


def b_case(short=6_000_000, shares=20_000_000, **kw):
    """One ticker AAA (cik 1): FINRA short shares, SEC shares outstanding, Alpaca daily bars."""
    reports = [("2024-05-01", {"AAA": {"si": 1}}), ("2024-05-20", {"AAA": {"si": short}}),
               ("2024-06-04", {"AAA": {"si": 1}})]  # the last is usable only after DAY
    idx = {1: [("2024-04-01", "2024-03-31", shares)]}
    return tb.fixed_b(DAY, reports, idx, {"AAA": 1}, {"AAA": daily(**kw)})


class Universes(unittest.TestCase):
    def test_universe_a_reads_the_picks(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "picks.csv")
            with open(p, "w") as f:
                f.write("date,t,score\n2024-01-02,AAOI,0.4\n2024-01-03,AVXL,0.5\n")
            self.assertEqual(tb.universe_a(p), [("2024-01-02", "AAOI"), ("2024-01-03", "AVXL")])

    def test_fixed_b_each_gate_at_its_edge(self):
        row = b_case()["AAA"]
        self.assertEqual((row["short"], row["shares"], row["avg_vol"], row["prev_close"]),
                         (6_000_000, 20_000_000, 1_000_000, 5.0))
        self.assertAlmostEqual(row["atr"], 1.0)
        self.assertIn("AAA", b_case(short=10_000_000, shares=49_999_999))
        self.assertNotIn("AAA", b_case(short=10_000_000, shares=50_000_000))  # shares under 50M
        self.assertNotIn("AAA", b_case(short=4_000_000, shares=20_000_000, vol=700_000))  # short/shares 0.20
        self.assertIn("AAA", b_case(short=4_000_001, shares=20_000_000, vol=700_000))
        self.assertNotIn("AAA", b_case(short=6_000_000, vol=500_000))  # average volume over 500K
        self.assertNotIn("AAA", b_case(short=6_000_000, vol=1_200_000))  # short/avg 5
        self.assertIn("AAA", b_case(short=6_000_001, vol=1_200_000))
        self.assertNotIn("AAA", b_case(close=2.0))  # previous close over $2
        self.assertIn("AAA", b_case(close=2.01))
        self.assertNotIn("AAA", b_case(n=62))  # fewer than 63 sessions of history

    def test_fixed_b_uses_only_reports_usable_that_day(self):
        self.assertEqual(b_case(short=6_000_000)["AAA"]["short"], 6_000_000)  # not the 2024-06-04 report
        reports = [("2024-06-04", {"AAA": {"si": 6_000_000}})]
        self.assertEqual(tb.fixed_b(DAY, reports, {1: [("2024-04-01", "2024-03-31", 20_000_000)]}, {"AAA": 1},
                                    {"AAA": daily()}), {})
        late = {1: [("2024-06-04", "2024-03-31", 20_000_000)]}  # shares filed after DAY
        self.assertEqual(tb.fixed_b(DAY, [("2024-05-20", {"AAA": {"si": 6_000_000}})], late, {"AAA": 1},
                                    {"AAA": daily()}), {})

    def test_split_in_window_skips_and_counts(self):
        counts = {}
        reports = [("2024-05-20", {"AAA": {"si": 6_000_000}})]
        idx = {1: [("2024-04-01", "2024-03-31", 20_000_000)]}
        for jump, out in (((30, 2.4), True), ((30, 10.1), True), ((30, 2.6), False)):
            with self.subTest(jump):
                got = tb.fixed_b(DAY, reports, idx, {"AAA": 1}, {"AAA": daily(jump=jump)}, counts)
                self.assertEqual("AAA" not in got, out)
        self.assertEqual(counts["skipped_split"], 2)

    def test_eligible_from_is_the_first_qualifying_minute(self):
        bars = [("09:29", 3, 3, 3, 3, 5_000_000, 3), ("09:30", 3, 3, 3, 3, 1_000_000, 3),
                ("09:31", 3, 3, 3, 3, 1_000_000, 3), ("09:32", 3, 3, 3, 3, 1, 3)]
        self.assertEqual(tb.eligible_from("REGULAR", bars, 1_000_000, "09:30"), "09:32")  # 2.0 is not over 2
        self.assertIsNone(tb.eligible_from("REGULAR", bars[:3], 1_000_000, "09:30"))
        pre = [("04:00", 1.9, 1.9, 1.9, 1.9, 60_000, 1.9), ("04:01", 1.9, 1.9, 1.9, 1.9, 40_000, 1.9),
               ("04:02", 2.1, 2.1, 2.1, 2.1, 1, 2.1), ("09:30", 5, 5, 5, 5, 9_000_000, 5)]
        self.assertEqual(tb.eligible_from("PREMARKET", pre, 1_000_000, "09:30"), "04:02")  # 10% at 04:01, close 1.90
        self.assertIsNone(tb.eligible_from("PREMARKET", pre[:2] + pre[3:], 1_000_000, "09:30"))

    def test_open_rel_vol_same_window_of_14_sessions(self):
        prior = sessions_before(DAY, 14)
        rows = [(f"{d} 09:30", 1, 1, 1, 1, 1000, 1) for d in prior] + [(f"{d} 09:35", 1, 1, 1, 1, 9e9, 1) for d in prior]
        rows += [(f"{DAY} 04:00", 1, 1, 1, 1, 9e9, 1), (f"{DAY} 09:30", 1, 1, 1, 1, 2000, 1)]
        with mock.patch.object(tb.alpaca, "bars", return_value=sorted(rows)) as f:
            self.assertEqual(tb.open_rel_vol("AAA", DAY, prior + [DAY]), 2.0)
        self.assertEqual(f.call_args.args, ("AAA", tb.alpaca.utc(prior[0], "09:30"), tb.alpaca.utc(DAY, "09:35"), "5Min"))
        with mock.patch.object(tb.alpaca, "bars", return_value=sorted(rows[1:])):
            self.assertIsNone(tb.open_rel_vol("AAA", DAY, prior + [DAY]))  # 13 prior opening bars
        self.assertIsNone(tb.open_rel_vol("AAA", DAY, prior[1:] + [DAY]))  # 13 prior sessions in the calendar


if __name__ == "__main__":
    unittest.main()
