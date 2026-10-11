"""ZBA v2 runner (spec section 14): the lists, the filters outside _zba, the selection, the holdout gates, a mocked run."""
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import common, zba_v2 as z
from test_ticket_backtest import flat, run, sessions_before, zba_bars


class Variants(unittest.TestCase):
    def test_the_22_preregistered_variants(self):
        self.assertEqual(len(z.VARIANTS), 22)
        self.assertEqual(z.VARIANTS["X4"], {"rank": "qr", "tod_min": 2.0, "vstop": 0.25, "limit_d": 0.25, "dq": True})
        self.assertEqual(z.VARIANTS["K10s"], {})
        self.assertEqual(z.BASE, {"stop_atr": 0.10, "spread_d": 0.25})


class Lists(unittest.TestCase):
    def test_dollar_volume_window_matches_the_prefilter(self):
        days = sessions_before("2024-03-01", 20) + ["2024-03-01"]
        bars = [(d, 1, 1, 1, 10.0, 1000 * (i + 1)) for i, d in enumerate(days[:-1])]
        prev = {"2024-03-01": days[-2]}
        got = z.dollar_days(["2024-03-01"], prev, bars)
        self.assertAlmostEqual(got["2024-03-01"], 10.0 * sum(1000 * (i + 1) for i in range(6, 20)) / 14)

    def test_quality_rank_equal_weight_z_scores(self):
        cands = [("AAA", 3.0, (10, 11, 9, 10.1)), ("BBB", 2.0, (10, 11, 9, 11.0)), ("CCC", 1.0, (10, 11, 9, 9.0))]
        top = z.qr_top(cands, {"AAA": 1e6, "BBB": 9e6, "CCC": 5e6}, {"CCC"}, n=2)
        self.assertEqual([t for t, _, _ in top], ["BBB", "CCC"])  # AAA leads on rel vol only

    def test_time_of_day_relative_volume(self):
        bars = run("09:30", [(1, 1, 1, 1)] * 3, v=300)  # 300 a minute today
        f = z.tod_fn(bars, [{"09:30": 100, "09:31": 100, "09:32": 100}, {"09:30": 100}])
        self.assertAlmostEqual(f("09:32"), 600 / ((200 + 100) / 2))  # through 09:31: the trigger minute is not counted
        self.assertAlmostEqual(f("09:31"), 300 / ((100 + 100) / 2))
        self.assertIsNone(z.tod_fn(bars, [{}])("09:31"))

    def test_data_quality_without_look_ahead(self):
        good = run("09:30", [(10, 10.1, 9.9, 10)] * 12)
        self.assertIsNone(z.dq_reason(good, "09:40"))
        gap = [b for b in good if b[0] != "09:37"]
        self.assertEqual(z.dq_reason(gap, "09:40"), "dq: missing bars")
        self.assertIsNone(z.dq_reason(gap, "09:31"))  # the gap is after the entry: not looked at
        vw = good[:3] + [("09:33", 10, 10.1, 9.9, 10, 100, 11.0)] + good[4:]
        self.assertEqual(z.dq_reason(vw, "09:40"), "dq: vwap outside the bar")
        zero = good[:3] + [("09:33", 10, 10.1, 9.9, 10, 0, None)] + good[4:]
        self.assertEqual(z.dq_reason(zero, "09:40"), "dq: price moved on zero volume")
        self.assertIsNone(z.dq_reason(vw, "09:33"))  # the entry minute itself is not looked at (it is still forming)


def st(n, mean, t, years):
    return {"n": n, "mean": mean, "t": t, "year_means": years}


class Selection(unittest.TestCase):
    def test_rerun_the_top_three_then_select_on_tick_level(self):
        screen = {"A": st(600, 0.3, 3, {}), "B": st(700, 0.2, 3, {}), "C": st(499, 0.9, 3, {}), "D": st(800, 0.1, 3, {}),
                  "E": st(900, 0.05, 3, {})}
        self.assertEqual(z.rerun_pick(screen), ["A", "B", "D"])  # C has too few trades
        good = {y: 0.1 for y in z.DEV_YEARS}
        real = {"A": st(600, 0.20, 2.5, good), "B": st(650, 0.25, 2.1, {**good, "2023": -0.01}),
                "D": st(700, 0.10, 2.0, good)}
        self.assertEqual(z.select(screen, real), ["A", "D"])  # B has a negative year
        self.assertEqual(z.select(screen, {"A": st(600, 0.2, 1.9, good)}), [])  # NO-GO

    def test_holdout_gates(self):
        days = [f"{y}-03-01" for y in z.HOLD_YEARS]
        xs = [{"day": days[i % 6], "t": f"T{i}", "net_r": 0.5 if i % 4 else -0.4, "net_r_short": -0.5, "R": 1.0,
               "fill": 10.0, "kinds": "eod"} for i in range(360)]
        s = z.summary(xs, days, z.HOLD_YEARS)
        checks, go = z.holdout_checks(xs, s, 1)
        self.assertTrue(go, checks)
        checks2, _ = z.holdout_checks(xs, s, 2)
        self.assertIn("t >= 2.24", checks2[1][0])
        few, go = z.holdout_checks(xs[:200], z.summary(xs[:200], days, z.HOLD_YEARS), 1)
        self.assertFalse(go)
        self.assertFalse(few[0][1])


class MockedRun(unittest.TestCase):
    """The whole dev run and the holdout's guards on tiny mocked data (no network)."""

    def patches(self, day, sessions):
        dly = [(d, 9.9, 10.4, 9.4, 9.9, 3_000_000) for d in sessions[:-1]]
        opening = lambda syms, d: {s: (10.0, 10.5, 9.9, 10.4, 3000 if d == day else 1000) for s in syms}  # noqa: E731
        bars = zba_bars()
        a = z.alpaca
        return [mock.patch.object(a, "calendar", return_value={x: ("09:30", "16:00") for x in sessions}),
                mock.patch.object(a, "assets", return_value=[{"symbol": "AAA", "exchange": "NYSE", "name": "A Corp"},
                                                             {"symbol": "BBB", "exchange": "NASDAQ", "name": "B Inc"}]),
                mock.patch.object(a, "daily", side_effect=lambda syms, s, e: {x: dly for x in syms}),
                mock.patch.object(a, "opening_bars", side_effect=opening),
                mock.patch.object(a, "news_symbols", return_value={"AAA"}),
                mock.patch.object(a, "minute_bars", side_effect=lambda syms, _: {x: bars for x in syms}),
                mock.patch.object(a, "minute_volumes", side_effect=lambda syms, _: {x: {"09:30": 100} for x in syms}),
                mock.patch.object(a, "quote_full", return_value=(10.00, 10.02, 800, 900, 1.0)),
                mock.patch.object(a, "trades", return_value=[("a", 10.5), ("b", 10.52)]),
                mock.patch("builtins.print")]

    def test_dev_run_reports_every_variant_and_no_go_without_trades(self):
        day = "2024-03-01"
        sessions = sessions_before(day, 40) + [day]
        with tempfile.TemporaryDirectory() as d:
            ps = self.patches(day, sessions)
            for p in ps:
                p.start()
            try:
                self.assertEqual(z.main(["--mode", "dev", "--start", day, "--end", day, "--out", d]), 0)
            finally:
                for p in ps:
                    p.stop()
            md = open(os.path.join(d, [f for f in os.listdir(d) if f.endswith(".md")][0]), encoding="utf-8").read()
            self.assertFalse(os.path.exists(os.path.join(d, z.FROZEN)))  # 2 trades: nobody reaches 500
        for v in z.VARIANTS:
            self.assertIn(f"| {v} |", md)
        self.assertIn("NO-GO", md)
        summary = md.split("## סיכום", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(common.rtl_bad_lines(summary), [])

    def test_holdout_refuses_without_a_freeze_runs_once_and_checks_the_parameters(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                z.main(["--mode", "holdout", "--out", d])
            with open(os.path.join(d, z.FROZEN), "w") as f:
                json.dump({"frozen": "x", "finalists": ["T2"], "params": {"T2": {"tod_min": 9.0}}}, f)
            with self.assertRaises(SystemExit) as e:
                z.main(["--mode", "holdout", "--out", d])
            self.assertIn("changed since the freeze", str(e.exception))
            open(os.path.join(d, "ticket-zba-v2-holdout-2026-10-10.md"), "w").close()
            with self.assertRaises(SystemExit) as e:
                z.main(["--mode", "holdout", "--out", d])
            self.assertIn("already run once", str(e.exception))


if __name__ == "__main__":
    unittest.main()
