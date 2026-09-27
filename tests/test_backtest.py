"""Squeeze backtest engine: a synthetic market with one planted squeeze and two look-ahead traps."""
import datetime as dt
import gzip
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import backtest, shorts, squeeze


def weekdays(start, n):
    out, d = [], dt.date.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(1)
    return out


CAL = weekdays("2024-01-01", 60)
K0 = 35  # the squeeze session


def flat(vol=1_000_000.0, price=10.0):
    n = len(CAL)
    return {"d": list(CAL), "o": [price] * n, "h": [price] * n, "l": [price] * n, "c": [price] * n, "v": [vol] * n}


def market():
    aaa, bbb, ccc = flat(), flat(), flat()
    for b in (aaa, bbb):  # the day before: 5x volume and a breakout close; squeeze day: high 2x the open
        b["v"][K0 - 1], b["c"][K0 - 1], b["h"][K0 - 1] = 5_000_000.0, 11.0, 11.0
        b["h"][K0] = 20.0
    aaa["v"][K0] = 5_000_000.0  # still busy on the squeeze day -> qualifies again next day (repeat pick)
    ccc["v"][K0] = 5_000_000.0  # spike ON day K0: must not be visible to the list of K0
    shares = shorts.shares_index([{1: ("2023-06-30", 1_000_000), 2: ("2023-06-30", 1_000_000),
                                   3: ("2023-06-30", 1_000_000)}])
    row = lambda si: {"si": si, "dtc": 3.0, "chg": 0.0, "date": "x"}
    early = {"AAA": row(400_000), "BBB": row(10_000), "CCC": row(300_000)}
    late = {"AAA": row(400_000), "BBB": row(500_000), "CCC": row(300_000)}  # BBB's big SI settles 3 days pre-squeeze
    reports = [(shorts.usable_from(CAL[0], CAL), early), (shorts.usable_from(CAL[K0 - 3], CAL), late)]
    return reports, shares, {"AAA": 1, "BBB": 2, "CCC": 3}, {"AAA": aaa, "BBB": bbb, "CCC": ccc}


V = {"w": 0.5, "g_si": 0.10, "g_rv": 1.5}


class Replay(unittest.TestCase):
    def setUp(self):
        reports, shares, primary, bars = market()
        self.res = backtest.replay(CAL[25:45], CAL, reports, shares, primary, bars, {"v": V}, {"all": None,
                                   "gate": 0.10}, keep=("v",))

    def test_planted_squeeze_is_found_once_and_traps_hold(self):
        picks = [(p["date"], p["t"], p["hit"]) for p in self.res["picks"]["v"]]
        # AAA on the squeeze day (a hit, counted once despite qualifying again on K0+1);
        # BBB never (its big SI was not public yet); CCC only the day after its own spike (no hit)
        self.assertEqual(picks, [(CAL[K0], "AAA", True), (CAL[K0 + 1], "CCC", False)])
        a = self.res["lists"]["v"]
        self.assertEqual((a["n"], a["hits"], a["years"]), (2, 1, {"2024": [2, 1]}))

    def test_baselines(self):
        base = self.res["bases"]["all"]
        self.assertEqual((base["n"], base["hits"]), (60, 20))  # 20 days x 3 stocks; AAA, BBB hit for D in K0-9..K0
        self.assertEqual(self.res["days"], 20)
        self.assertNotIn("dd", base)

    def test_coverage(self):
        c = self.res["coverage"]["2024"]
        self.assertEqual(len(c["finra"]), 3)
        self.assertEqual(len(c["shares"]), 3)


def tally(n, hits, years):
    return {"n": n, "hits": hits, "years": years}


BASE = tally(30000, 3000, {"2024": [10000, 1000], "2025": [10000, 1000], "2026": [10000, 1000]})


class Verdict(unittest.TestCase):
    def test_exact_edges_pass(self):
        a = tally(100, 30, {"2024": [40, 12], "2025": [40, 12], "2026": [20, 6]})
        checks, go = backtest.verdict(a, BASE, ["2024", "2025", "2026"])
        self.assertTrue(go, checks)

    def test_too_few_hits(self):
        a = tally(50, 29, {"2024": [25, 15], "2025": [25, 14]})
        self.assertFalse(backtest.verdict(a, BASE, ["2024", "2025", "2026"])[1])

    def test_one_good_year_is_not_enough(self):
        a = tally(100, 40, {"2024": [40, 30], "2025": [40, 8], "2026": [20, 2]})
        checks, go = backtest.verdict(a, BASE, ["2024", "2025", "2026"])
        self.assertTrue(checks[0][1])
        self.assertFalse(go)

    def test_zero_hit_baseline_is_no_go_not_a_crash(self):
        a = tally(100, 40, {"2024": [100, 40]})
        self.assertFalse(backtest.verdict(a, tally(10, 0, {"2024": [10, 0]}), ["2024"])[1])


class Select(unittest.TestCase):
    def test_highest_lift_with_enough_hits(self):
        res = {"bases": {"all": tally(1000, 100, {})},
               "lists": {"best-but-thin": tally(50, 29, {}), "good": tally(100, 40, {}), "ok": tally(200, 60, {}),
                         "tie-more-hits": tally(200, 80, {})}}
        self.assertEqual(backtest.select(res, list(res["lists"])), "tie-more-hits")
        self.assertIsNone(backtest.select({"bases": res["bases"], "lists": {"x": tally(10, 5, {})}}, ["x"]))


class Loaders(unittest.TestCase):
    def test_to_bars(self):
        r = {"timestamp": [1704205800, 1704292200, 1704292300, 1704378600],
             "indicators": {"quote": [{"open": [None, 10.0, 10.5, 11.0], "high": [None, 12.0, 12.5, None],
                                       "low": [9.0, 9.5, 9.6, None], "close": [9.5, 11.0, 11.5, None],
                                       "volume": [100, None, 300, 400]}]}}
        b = backtest.to_bars(r)
        self.assertEqual(b["d"], ["2024-01-02", "2024-01-03"])  # duplicate session kept once (last), no-close dropped
        self.assertEqual((b["o"], b["h"], b["c"], b["v"]), ([None, 10.5], [9.5, 12.5], [9.5, 11.5], [100, 300]))
        self.assertIsNone(backtest.to_bars(None))
        p = backtest.pack(b)
        self.assertTrue(math.isnan(p["o"][0]))
        self.assertEqual(list(p["c"]), [9.5, 11.5])

    def test_cache_saves_only_real_values(self):
        with tempfile.TemporaryDirectory() as d:
            path, calls = Path(d, "x.json.gz"), []
            make = lambda: calls.append(1) or {"a": 1}
            self.assertEqual(backtest._cached(path, make), {"a": 1})
            self.assertEqual(backtest._cached(path, make), {"a": 1})
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads(gzip.decompress(path.read_bytes())), {"a": 1})
            empty = Path(d, "none.json.gz")
            self.assertIsNone(backtest._cached(empty, lambda: None))
            self.assertFalse(empty.exists())


class Render(unittest.TestCase):
    def test_report_shows_verdict_tables_and_choice(self):
        reports, shares, primary, bars = market()
        grid = {backtest.vname(V): V}
        r = backtest.replay(CAL[25:45], CAL, reports, shares, primary, bars, {**grid, "trigger-only":
                            {"w": 0.0, "g_si": -1.0, "g_rv": 0.0}}, {"all": None})
        checks, go = backtest.verdict(r["lists"][backtest.vname(V)], r["bases"]["all"], ["2024"])
        md = backtest.render({"run": "2026-09-27", "lines": ["x"]}, r, grid, backtest.vname(V),
                             {**r, "lists": {"selected": r["lists"][backtest.vname(V)], **r["lists"]}}, checks, go)
        self.assertIn("NO-GO", md)
        self.assertIn("w0.5/si0.1/rv1.5", md)
        self.assertIn("| 2024 |", md)
        none = backtest.render({"run": "d", "lines": []}, r, grid, None, None, [], False)
        self.assertIn("No variant reached", none)


if __name__ == "__main__":
    unittest.main()
