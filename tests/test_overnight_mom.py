"""The locked overnight book, on made-up bars. No network and no orders."""
import datetime as dt
import math
import random
import unittest
from pathlib import Path

from bot import overnight_mom as om


def days(n, start=dt.date(2022, 1, 3)):
    return [(start + dt.timedelta(days=i)).isoformat() for i in range(n)]


def series(n, overnight=1.0, price=20.0, volume=2_000_000, raw_price=None, jump_at=None, jump=1.0):
    """n sessions. `overnight` is the open / previous close. `jump_at` multiplies that day's open only."""
    sess = days(n)
    raw, adj = {}, {}
    px = price
    for i, d in enumerate(sess):
        op = px * overnight * (jump if i == jump_at else 1.0)
        cl = op
        raw[d] = (op if raw_price is None else raw_price, raw_price if raw_price is not None else cl, volume)
        adj[d] = (op, cl, volume)
        px = cl
    if raw_price is not None:
        for d in sess:
            raw[d] = (raw_price, raw_price, volume)
    return sess, raw, adj


class Rules(unittest.TestCase):
    def test_constants_match_the_pre_registration(self):
        self.assertEqual((om.START, om.OOS, om.TOP_N, om.SIGNAL_DAYS, om.ADV_DAYS), ("2016-01-01", "2023-01-01", 5, 21, 20))
        self.assertEqual(om.COSTS, (0.0005, 0.0010, 0.0020))
        self.assertEqual((om.GATE_COST, om.DRAWS, om.PRICE_MIN, om.ADV_MIN), (0.0010, 1000, 10.0, 20_000_000.0))
        text = Path(om.PREREG).read_text(encoding="utf-8")
        self.assertIn("t > 2", text)
        self.assertIn("95th percentile", text)
        self.assertIn("70%", text)
        self.assertNotIn("submit_order", Path(om.__file__).read_text(encoding="utf-8"))

    def test_common_stock_filter(self):
        good = {"symbol": "AAPL", "exchange": "NASDAQ", "name": "Apple Inc.", "status": "inactive"}
        self.assertTrue(om.is_common(good))
        self.assertTrue(om.is_common({"symbol": "BRK.B", "exchange": "NYSE", "name": "Berkshire Hathaway Inc."}))
        self.assertFalse(om.is_common({"symbol": "SPY", "exchange": "ARCA", "name": "SPDR S&P 500 ETF Trust"}))
        self.assertFalse(om.is_common({"symbol": "QQQ", "exchange": "NASDAQ", "name": "Invesco QQQ Trust"}))
        self.assertFalse(om.is_common({"symbol": "ABC.WS", "exchange": "NYSE", "name": "Something"}))
        self.assertFalse(om.is_common({"symbol": "WARR", "exchange": "NYSE", "name": "Foo Warrant"}))
        self.assertEqual(om.index_assets([
            {"symbol": "A", "status": "active"}, {"symbol": "A", "status": "inactive"}])["A"]["status"], "inactive")

    def test_the_signal_does_not_see_tomorrow(self):
        sess, raw, adj = series(30, overnight=1.01)
        # tomorrow's open is a crash; the signal on the day before must ignore it
        last, prev = sess[-1], sess[-2]
        adj[last] = (1.0, 1.0, 1)
        rows = {r["date"]: r for r in om.build_rows("BBB", sess, raw, adj, {}, {})}
        self.assertIn(prev, rows)
        self.assertAlmostEqual(rows[prev]["signal"], 21 * math.log(1.01), places=9)
        self.assertAlmostEqual(rows[prev]["ret"], 1.0 / adj[prev][1] - 1, places=9)

    def test_price_filter_uses_the_raw_close(self):
        sess, raw, adj = series(30, raw_price=9.0, volume=3_000_000)
        self.assertEqual(om.build_rows("CHEAP", sess, raw, adj, {}, {}), [])
        sess, raw, adj = series(30, price=11.0, volume=2_000_000)
        got = om.build_rows("OK", sess, raw, adj, {}, {})
        self.assertTrue(got)
        self.assertTrue(all(r["raw_close"] > 10 for r in got))

    def test_a_hole_breaks_the_chain(self):
        sess, raw, adj = series(30)
        hole = sess[25]
        del raw[hole]
        del adj[hole]
        dates = {r["date"] for r in om.build_rows("H", sess, raw, adj, {}, {})}
        self.assertNotIn(sess[26], dates)
        self.assertNotIn(hole, dates)

    def test_top_5_and_the_tie_break(self):
        rows = [{"symbol": s, "signal": sig, "adv": adv} for s, sig, adv in
                (("A", 1, 10), ("B", 3, 1), ("C", 3, 5), ("D", 2, 1), ("E", 0, 9), ("F", 2, 1))]
        self.assertEqual([r["symbol"] for r in om.select(rows)], ["C", "B", "D", "F", "A"])

    def test_cash_night_has_no_cost_and_vwap_can_drop_a_night(self):
        self.assertEqual(om.portfolio_return([], 0.001), 0.0)
        self.assertAlmostEqual(om.portfolio_return([0.01, 0.03], 0.001), 0.019)
        picks = [{"ret": 0.02, "exit_kind": "open", "vwap_ret": None}]
        self.assertIsNone(om.book_return(picks, "vwap", 0.001))
        self.assertAlmostEqual(om.book_return(picks, "open", 0.001), 0.019)
        picks[0]["vwap_ret"] = 0.05
        self.assertAlmostEqual(om.book_return(picks, "vwap", 0.001), 0.049)

    def test_exit_paths(self):
        sess = days(20)
        adj = {d: (10.0, 10.0, 1) for d in sess}
        # halt: no print tomorrow, a print the day after
        held = dict(adj)
        del held[sess[11]]
        ret, kind, exit_day = om.exit_return("AAA", sess, 10, held, 10.0, {}, {})
        self.assertEqual((kind, exit_day), ("halt", sess[12]))
        self.assertAlmostEqual(ret, 0.0)
        # gone, with a cash merger inside the window
        thin = {sess[10]: (10.0, 10.0, 1)}
        ret, kind, _ = om.exit_return("AAA", sess, 10, thin, 10.0, {"AAA": [(sess[12], 12.0)]}, {})
        self.assertEqual(kind, "merger")
        self.assertAlmostEqual(ret, 0.2)
        # gone, no cash, not at the edge of the file
        ret, kind, _ = om.exit_return("AAA", sess, 10, thin, 10.0, {}, {})
        self.assertEqual((ret, kind), (-1.0, "delist"))
        # gone inside the last five sessions: censored, not a fake bankruptcy
        ret, kind, _ = om.exit_return("AAA", sess, 18, {sess[18]: (10.0, 10.0, 1)}, 10.0, {}, {})
        self.assertEqual((ret, kind), (None, "censored"))

    def test_rename_does_not_double_count(self):
        raw = {"OLD": {"2020-01-01": (1, 1, 1), "2020-06-01": (1, 1, 1)},
               "NEW": {"2019-06-01": (1, 1, 1)}}
        adj = {k: dict(v) for k, v in raw.items()}
        skip = om.apply_rename(raw, adj, [("OLD", "NEW", "2020-03-01")])
        self.assertIn("OLD", skip)
        raw = {"OLD": {"2020-01-01": (1, 1, 1), "2020-06-01": (1, 1, 1)},
               "NEW": {"2020-06-01": (1, 1, 1)}}
        adj = {k: dict(v) for k, v in raw.items()}
        skip = om.apply_rename(raw, adj, [("OLD", "NEW", "2020-03-01")])
        self.assertEqual(skip, set())
        self.assertEqual(list(raw["OLD"]), ["2020-01-01"])

    def test_earnings_filter_changes_the_pick_not_the_past(self):
        sess, raw, adj = series(30, overnight=1.0)
        hi, raw_b, adj_b = series(30, overnight=1.02)
        rows = om.build_rows("LOW", sess, raw, adj, {}, {}) + om.build_rows("HIGH", hi, raw_b, adj_b, {}, {})
        day = rows[-1]["date"]
        plain = om.nights_from(sess, rows, {}, {}, False)
        flagged = om.nights_from(sess, rows, {day: {"HIGH"}}, {}, True)
        last_plain = plain[-1]["picks"][0]["symbol"]
        last_flag = flagged[-1]["picks"][0]["symbol"]
        self.assertEqual(last_plain, "HIGH")
        self.assertEqual(last_flag, "LOW")

    def test_percentile_gate_and_a_negative_year(self):
        self.assertEqual(om.percentile(list(range(100)), 0.95), 94)
        self.assertEqual(om.DRAWS, 1000)
        self.assertEqual(om.percentile(list(range(1000)), 0.95), 949)
        nights = []
        rng = random.Random(1)
        for i in range(80):
            day = (dt.date(2023, 1, 2) + dt.timedelta(days=i)).isoformat()
            r = 0.01 + rng.random() * 0.001
            nights.append({"date": day, "oos": True, "picks": [{"ret": r, "exit_kind": "open", "vwap_ret": r,
                                                                "raw_close": 20.0}],
                           "eligible_rets": [r], "spy": 0.0})
        # one later year that loses
        for i in range(40):
            day = (dt.date(2024, 1, 2) + dt.timedelta(days=i)).isoformat()
            nights.append({"date": day, "oos": True, "picks": [{"ret": -0.02, "exit_kind": "open", "vwap_ret": -0.02,
                                                                "raw_close": 20.0}],
                           "eligible_rets": [-0.02], "spy": 0.0})
        ev = om.evaluate_book(nights, om.GATE_COST)
        checks = om.gate(ev["block"]["oos"], ev["draws"], ev["spy"], ev["strategy_on_spy"],
                         ev["block"]["oos"]["years"], ev["block"]["oos"]["years"])
        by = {label: ok for label, ok, _ in checks}
        self.assertFalse(by["every OOS year mean net > 0"])
        # a book that only wins does clear t and SPY and the single year
        only = [n for n in nights if n["date"].startswith("2023")]
        ev = om.evaluate_book(only, om.GATE_COST)
        checks = om.gate(ev["block"]["oos"], ev["draws"], ev["spy"], ev["strategy_on_spy"],
                         ev["block"]["oos"]["years"], ev["block"]["oos"]["years"])
        by = {label: ok for label, ok, _ in checks}
        self.assertTrue(by["OOS mean net > 0 and t > 2"])
        self.assertTrue(by["OOS mean above SPY close-to-open on the same nights"])
        self.assertTrue(by["every OOS year mean net > 0"])

    def test_split_check_and_random_draw_is_reproducible(self):
        sess = days(5, dt.date(2020, 8, 27))
        adj = {sess[0]: (100, 100, 1), sess[-1]: (25, 25, 1)}
        ok, _ = om.split_sane(adj, sess)
        self.assertFalse(ok)
        adj[sess[-1]] = (101, 101, 1)
        ok, _ = om.split_sane(adj, sess)
        self.assertTrue(ok)
        nights = [{"date": "2023-01-03", "eligible_rets": [0.01, 0.02, 0.03, -0.01, -0.02, 0.0], "oos": True,
                   "picks": [], "spy": 0.0}]
        a = om.random_means(nights, 0.001, draws=5, seed=om.SEED)
        b = om.random_means(nights, 0.001, draws=5, seed=om.SEED)
        self.assertEqual(a, b)

    def test_last_session_is_before_today(self):
        self.assertEqual(om.last_completed("2026-10-14", ["2026-10-12", "2026-10-13", "2026-10-14"]), "2026-10-13")


if __name__ == "__main__":
    unittest.main()
