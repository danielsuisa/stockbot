"""Phase C tasks 6-7 (spec sections 3, 6.2, 4.2): issuing tickets and the random comparison. SYNTHETIC BARS ONLY --
the blind discipline: no outcome is computed on real bars before the blind run."""
import unittest

from bot import ensemble_backtest as bt, levels
from tests.test_ensemble_signals import inputs
from tests.test_probe_ensemble import Bars, series

S = Bars.S
D = S[300]


def flat(n=301, close=50.0, rng=1.0, volume=1_000_000, dates=None, after=()):
    """A synthetic series: n flat sessions (every true range `rng`), then the (o, h, l, c) rows of `after`."""
    dates = dates or S[:n + len(after)]
    closes = [close] * n + [c for _, _, _, c in after]
    s = series(dates, close=closes, volume=volume)
    s["raw_open"] = [close] * n + [o for o, _, _, _ in after]
    s["raw_high"] = [close + rng / 2] * n + [h for _, h, _, _ in after]
    s["raw_low"] = [close - rng / 2] * n + [lo for _, _, lo, _ in after]
    return s


TRIGGER = (50.0, 50.6, 50.4, 50.5)  # fills a breakout at 50.51 (stop = high of D 50.5 + 0.01)
QUIET = (50.0, 50.4, 49.9, 50.0)
GOOD_QUOTE = (50.0, 50.02, 1, 1, 3)


def quote_ok(sym, at):
    return GOOD_QUOTE


class Rows(unittest.TestCase):
    def test_rows_after_rescaled_and_same_segment(self):
        s = flat(after=[TRIGGER, QUIET])
        rows = bt.rows_after(s, D)
        self.assertEqual([r[0] for r in rows], [S[301], S[302]])
        self.assertEqual(rows[0][1:5], TRIGGER)  # no split: raw money unchanged
        s["segment"] = list(s["segment"][:302]) + [1]  # a bad-price break after S[301]
        self.assertEqual([r[0] for r in bt.rows_after(s, D)], [S[301]])

    def test_beyond_the_data_is_a_rule_on_dates(self):
        sessions = S[:330]
        k = sessions.index(D)
        last_ok = len(sessions) - 1 - 21 - 3 - k  # breakout: 3 valid sessions + H 21 must fit
        self.assertFalse(bt.beyond_data(sessions[k + last_ok], "breakout", 21, sessions))
        self.assertTrue(bt.beyond_data(sessions[k + last_ok + 1], "breakout", 21, sessions))
        self.assertTrue(bt.beyond_data(sessions[-5], "open", 5, sessions))
        self.assertFalse(bt.beyond_data(sessions[-7], "open", 5, sessions))


class RunTicket(unittest.TestCase):
    def ticket(self, mode="breakout", horizon=21):
        s = flat()
        return levels.plan("A", bt.plan_rows(s, D), D, mode=mode, horizon=horizon, risk=100.0)

    def test_unfilled_counts_zero_with_no_cost(self):
        got = bt.run_ticket(self.ticket(), [(S[301], *QUIET, 0), (S[302], *QUIET, 0), (S[303], *QUIET, 0)], 0.02)
        self.assertEqual((got["filled"], got["gross_r"], got["cost_r"]), (False, 0.0, 0.0))
        self.assertEqual(got["end"], S[303])  # pending through its last valid session

    def test_no_session_of_the_segment_in_the_validity(self):
        got = bt.run_ticket(self.ticket(), [], 0.02)  # a break right after D (ruling C15): issued, unfilled
        self.assertEqual((got["filled"], got["gross_r"], got["cost_r"], got["end"]), (False, 0.0, 0.0, D))

    def test_break_inside_an_open_ticket_exits_at_the_last_close(self):
        rows = [(S[301], *TRIGGER, 0), (S[302], 50.6, 51.0, 50.5, 50.9, 0)]  # the series ends after S[302]
        got = bt.run_ticket(self.ticket(), rows, 0.02)
        self.assertTrue(got["filled"])
        self.assertAlmostEqual(got["gross_r"], (50.9 - 50.51) / 2.0)
        self.assertEqual(got["end"], S[302])

    def test_entry_spread_falls_back_to_the_guard(self):
        rows = [(S[301], *TRIGGER, 0), (S[302], 54.0, 54.5, 53.8, 54.2, 0), (S[303], 53.0, 53.0, 52.0, 52.2, 0)]
        t = self.ticket()
        with_entry = bt.run_ticket(t, rows, 0.02, entry_spread=lambda day: 0.04)
        fallback = bt.run_ticket(t, rows, 0.02, entry_spread=lambda day: None)
        self.assertAlmostEqual(with_entry["cost_r"], levels.cost_r(t, 0.04))
        self.assertAlmostEqual(fallback["cost_r"], levels.cost_r(t, 0.02))
        self.assertAlmostEqual(with_entry["cost_r_stress"], levels.cost_r(t, 0.04, stress=True))
        self.assertEqual(with_entry["exits"], ["target", "trail"])  # kept in memory for the verdict run
        self.assertIsNone(bt.run_ticket(t, [], 0.02)["exits"])


class Issue(unittest.TestCase):
    def setUp(self):
        after = [TRIGGER] + [(50.6, 50.7, 50.5, 50.6)] * 40
        self.inp = inputs({"A": flat(after=after), "B": flat(after=after)}, {"A": [S[200]], "B": [S[200]]})

    def test_signal_modes_and_horizons(self):
        tickets, _ = bt.issue([(D, "S2", "A"), (D, "S4", "B")], self.inp, {}, quote_ok)
        got = {t["signal"]: (t["mode"], t["horizon"]) for t in tickets}
        self.assertEqual(got, {"S2": ("breakout", 42), "S4": ("open", 5)})

    def test_one_open_ticket_per_symbol_and_signal(self):
        fired = [(D, "S3", "A"), (S[310], "S3", "A"), (S[310], "S4", "A")]
        tickets, refusals = bt.issue(fired, self.inp, {}, quote_ok)
        self.assertEqual([(t["day"], t["signal"]) for t in tickets], [(D, "S3"), (S[310], "S4")])
        self.assertEqual(refusals[("S3", "already open")], 1)  # a different signal on the symbol is issued

    def test_a_pending_breakout_expires(self):
        quiet = [QUIET] * 40
        inp = inputs({"A": flat(after=quiet)}, {"A": [S[200]]})
        fired = [(D, "S3", "A"), (S[303], "S3", "A"), (S[304], "S3", "A")]
        tickets, refusals = bt.issue(fired, inp, {}, quote_ok)
        self.assertEqual([t["day"] for t in tickets], [D, S[304]])  # pending through S[303], free from S[304]
        self.assertEqual(refusals[("S3", "already open")], 1)

    def test_refusals_counted_by_signal_and_reason(self):
        tickets, refusals = bt.issue([(D, "S3", "A"), (D, "S2", "B")], self.inp, {}, lambda sym, at: None)
        self.assertEqual(tickets, [])
        self.assertEqual(refusals[("S3", "no usable quote")], 1)
        self.assertEqual(refusals[("S2", "no usable quote")], 1)

    def test_beyond_the_data_is_dropped_and_counted(self):
        inp = inputs({"A": flat(n=380, after=[QUIET] * 20)}, {"A": [S[200]]})  # bars end at S[399]
        tickets, refusals = bt.issue([(S[379], "S3", "A")], inp, {}, quote_ok)
        self.assertEqual(tickets, [])
        self.assertEqual(refusals[("S3", "beyond the data")], 1)

    def test_renamed_symbol_uses_the_other_bars(self):
        tickets, _ = bt.issue([(D, "S3", "OLD")], self.inp, {(D, "OLD"): "A"}, quote_ok)
        self.assertEqual((tickets[0]["symbol"], tickets[0]["bars_of"]), ("OLD", "A"))


class Comparison(unittest.TestCase):
    def setUp(self):
        after = [TRIGGER] + [(50.6, 50.7, 50.5, 50.6)] * 40
        self.syms = [f"P{k:02d}" for k in range(25)]
        self.inp = inputs({s: flat(after=after) for s in self.syms}, {s: [S[200]] for s in self.syms})
        self.day_members = {D: (tuple(self.syms), bytes([3] * 25))}  # all in decile 3

    def test_pool_membership(self):
        excl = {"P00", "P01"}  # a firing or an open ticket on D
        pool = bt.pool(D, 3, "breakout", 21, self.inp, self.day_members, {}, excl)
        self.assertEqual(pool, sorted(set(self.syms) - excl))
        self.assertEqual(bt.pool(D, 4, "breakout", 21, self.inp, self.day_members, {}, set()), [])  # other decile

    def test_pool_excludes_quote_free_refusals(self):
        inp = inputs({**{s: flat() for s in self.syms}, "THIN": flat(volume=10)},
                     {s: [S[200]] for s in self.syms + ["THIN"]})
        members = {D: (tuple(self.syms + ["THIN"]), bytes([3] * 26))}
        self.assertNotIn("THIN", bt.pool(D, 3, "breakout", 21, inp, members, {}, set()))  # 1% of dollar volume

    def test_exclusions(self):
        tickets = [{"day": S[290], "end": D, "symbol": "P05"}, {"day": S[290], "end": S[299], "symbol": "P06"}]
        fired_by_day = {D: {"P07"}}
        self.assertEqual(bt.exclusions(D, fired_by_day, tickets), {"P05", "P07"})

    def test_open_by_day_equals_exclusions(self):
        tickets = [{"day": S[290], "end": D, "symbol": "P05"}, {"day": S[290], "end": S[299], "symbol": "P06"},
                   {"day": D, "end": S[305], "symbol": "P08"}]
        fired_by_day = {D: {"P07"}, S[302]: {"P09"}}
        open_map = bt.open_by_day(tickets, S)
        for day in S[285:310]:
            self.assertEqual(open_map.get(day, set()) | fired_by_day.get(day, set()),
                             bt.exclusions(day, fired_by_day, tickets), day)

    def test_small_pool_dropped(self):
        for n, kept in ((19, False), (20, True)):
            got = bt.pool_ok(list(range(n)))
            self.assertEqual(got, kept, n)

    def test_pool_results_and_m(self):
        res = bt.pool_results(D, 3, "breakout", 21, self.syms[:20], self.inp, {})
        self.assertEqual(len(res), 20)
        self.assertAlmostEqual(bt.m(res), sum(res) / 20)

    def test_draws_reproducible_and_same_function(self):
        pools = {("k1",): [0.1, 0.2, 0.3], ("k2",): [1.0, -1.0]}
        kept = [{"pool": ("k1",), "m": 0.2, "g": 0.5}, {"pool": ("k2",), "m": 0.0, "g": -0.4}]
        a = bt.draws(kept, pools, n=50)
        self.assertEqual(a, bt.draws(kept, pools, n=50))
        import random
        rng = random.Random(20261010 + 7)
        picked = [pools[("k1",)][rng.randrange(3)], pools[("k2",)][rng.randrange(2)]]
        self.assertAlmostEqual(a[7], bt.excess_mean(picked, [0.2, 0.0]))  # A_k: the same function as A
        self.assertAlmostEqual(bt.A(kept), bt.excess_mean([0.5, -0.4], [0.2, 0.0]))

    def test_compare_shares_pools_and_drops_small_ones(self):
        tickets, _ = bt.issue([(D, "S3", "P00"), (D, "S3", "P01")], self.inp, {}, quote_ok)
        fired_by_day = {D: {"P00", "P01"}}
        kept, pools, small = bt.compare(tickets, self.inp, {}, self.day_members, fired_by_day)
        self.assertEqual((len(kept), small, len(pools)), (2, 0, 1))  # one key (D, 3, breakout, 21): shared
        key = kept[0]["pool"]
        self.assertEqual(len(pools[key]), 23)  # 25 minus the two firing symbols
        self.assertAlmostEqual(kept[0]["m"], sum(pools[key]) / 23)
        few = {D: (tuple(self.syms[:21]), bytes([3] * 21))}  # 21 - 2 firing = 19 members: dropped
        kept, pools, small = bt.compare(tickets, self.inp, {}, few, fired_by_day)
        self.assertEqual((len(kept), small, pools), (0, 2, {}))

    def test_kept_in_c25_order(self):
        # review M1: draws go over the tickets in (signal day, symbol, signal) order
        fired = [(D, "S4", "P03"), (D, "S2", "P09"), (D, "S3", "P01")]
        tickets, _ = bt.issue(fired, self.inp, {}, quote_ok)
        kept, _, _ = bt.compare(tickets, self.inp, {}, self.day_members, {D: {"P01", "P03", "P09"}})
        self.assertEqual([(t["day"], t["symbol"], t["signal"]) for t in kept],
                         [(D, "P01", "S3"), (D, "P03", "S4"), (D, "P09", "S2")])

    def test_rows_after_capped(self):
        s = flat(after=[QUIET] * 40)
        self.assertEqual(len(bt.rows_after(s, D, limit=7)), 7)
        self.assertEqual(len(bt.rows_after(s, D)), 40)

    def test_comparability_shares(self):
        self.assertEqual(bt.comparability(quote_refused=5, passing=100, small_pool=2, issued=100),
                         {"quote_share": 0.05, "small_pool_share": 0.02, "comparable": True})
        self.assertFalse(bt.comparability(6, 100, 2, 100)["comparable"])
        self.assertFalse(bt.comparability(5, 100, 3, 100)["comparable"])


if __name__ == "__main__":
    unittest.main()
