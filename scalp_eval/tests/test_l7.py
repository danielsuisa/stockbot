"""L7 evaluator tests: hand-computed golden set (12 trades), fill and account rules, costs.

Golden values were computed by hand from the ADR 0005 rules and the IBKR schedule (see each case's comment);
the evaluator must reproduce them (to 1e-9 dollars)."""
import datetime as dt
import unittest

from scalp_eval import costs
from scalp_eval.account import Signal, run_account
from scalp_eval.fills import signal_shares, simulate

UTC = dt.timezone.utc
DAY = dt.date(2025, 6, 2)
T = dt.datetime(2025, 6, 2, 14, 0, tzinfo=UTC)  # 10:00 ET
CLOSE = dt.datetime(2025, 6, 2, 20, 0, tzinfo=UTC)
HS, HSD = 0.02, 0.015  # half spread at t (real NBBO) and delayed half spread


def bars(rows, start=T + dt.timedelta(minutes=1)):
    """rows: (o, h, l, c, v) per minute from `start`."""
    opens = [start + dt.timedelta(minutes=i) for i in range(len(rows))]
    return (opens, [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows],
            [r[4] for r in rows])


FILL = (50.05, 50.10, 49.99, 50.02, 10000)  # low 49.99 < limit 50.00: fills at 50.00


def sim(b, limit=50.0, stop=49.5, target=50.75, shares=10, H=60, day=DAY):
    return simulate(T, limit, stop, target, 600, H, shares, b, CLOSE, HS, HSD, day)


class Golden(unittest.TestCase):
    def check(self, tr, status, reason=None, exit_price=None, gross=None, tiered=None, fixed=None):
        self.assertEqual(tr.status, status)
        if status != "filled":
            return
        self.assertEqual(tr.exit_reason, reason)
        self.assertAlmostEqual(tr.exit_price, exit_price, places=9)
        self.assertAlmostEqual(tr.pnl_gross, gross, places=9)
        self.assertAlmostEqual(tr.pnl["tiered"], tiered, places=9)
        if fixed is not None:
            self.assertAlmostEqual(tr.pnl["fixed"], fixed, places=9)

    def test_g01_target(self):
        # buy tiered 0.35 + 0.03 + 0.002 + 0.000259 + 0.00003 = 0.382289; sell @50.75 (507.5):
        # 0.35 + 0.032 + 0.000259 + 0.00003 + SEC 0.0104545 + TAF 0.00166 = 0.3944035 -> 7.5 - 0.7766925
        # fixed: 1.00003 + (1.00 + 0.00003 + 0.0104545 + 0.00166) -> 7.5 - 2.0121745; gross 10*(50.75-49.985)
        self.check(sim(bars([FILL, (50.10, 50.80, 50.05, 50.70, 10000)])), "filled", "target", 50.75, 7.65,
                   6.7233075, 5.4878255)

    def test_g02_stop(self):
        # sell @49.49 (494.9): SEC 0.01019494 -> sell 0.39414394; -5.1 - 0.77643294; fixed -5.1 - 2.01191494
        self.check(sim(bars([FILL, (49.90, 49.95, 49.40, 49.45, 10000)])), "filled", "stop", 49.49, -4.85,
                   -5.87643294, -7.11191494)

    def test_g03_gap_through_stop(self):
        # open 49.30 <= stop: exit 49.29 (492.9): SEC 0.01015374 -> sell 0.39410274; -7.1 - 0.77639174
        self.check(sim(bars([FILL, (49.30, 49.40, 49.20, 49.35, 10000)])), "filled", "gap", 49.29, -6.85, -7.87639174)

    def test_g04_same_bar_stop_wins(self):
        self.check(sim(bars([FILL, (50.10, 50.90, 49.40, 50.00, 10000)])), "filled", "stop", 49.49, -4.85, -5.87643294)

    def test_g05_time_exit(self):
        # H = 3: last bar opening before 14:04 is 14:03, close 50.25 - half spread 0.02 = 50.23 (502.3):
        # SEC 0.01034738 -> sell 0.39429638; 2.3 - 0.77658538; gross 10*(50.25-49.985)
        b = bars([FILL, (50.02, 50.20, 49.90, 50.10, 10000), (50.10, 50.30, 50.00, 50.25, 10000),
                  (50.25, 51.00, 50.20, 50.90, 10000)])
        self.check(sim(b, H=3), "filled", "time", 50.23, 2.65, 1.52341462)

    def test_g06_low_equal_to_limit_never_fills(self):
        self.check(sim(bars([(50.05, 50.10, 50.00, 50.02, 10000)] * 12)), "expired")

    def test_g07_ttl_boundary(self):
        rows = [(50.05, 50.10, 50.01, 50.02, 10000)] * 9 + [(50.0, 50.0, 49.0, 49.5, 10000)]  # 10th bar opens at t+10
        self.check(sim(bars(rows)), "expired")

    def test_g08_liquidity_reject(self):
        self.check(sim(bars([(50.05, 50.10, 49.99, 50.02, 900)])), "rejected")  # 10 shares > 1% of 900

    def test_g09_zero_shares_skipped(self):
        q = signal_shares(500, 600.0, 590.0)  # min(5/10, 500/600) < 1
        self.assertEqual(q, 0)
        self.check(sim(bars([FILL]), limit=600.0, stop=590.0, target=615.0, shares=q), "skipped")

    def test_g10_stop_inside_fill_bar(self):
        self.check(sim(bars([(50.05, 50.10, 49.40, 49.60, 10000)])), "filled", "stop", 49.49, -4.85, -5.87643294)

    def test_g11_commission_capped_at_one_percent(self):
        # 5 shares @6 (30.00): tiered buy min(0.35, 0.30) = 0.30 + 0.016 + 0.000222 + 0.000015 = 0.316237;
        # sell @7.50 (37.5): 0.35 + 0.016 + 0.000259 + 0.000015 + 0.0007725 + 0.00083 = 0.3678765 -> 7.5 - 0.6841135
        # fixed: 0.300015 + (0.375 + 0.000015 + 0.0007725 + 0.00083) = 0.6766325 -> 7.5 - 0.6766325; gross 5*(7.50-5.985)
        q = signal_shares(500, 6.0, 5.0)
        self.assertEqual(q, 5)
        b = bars([(6.05, 6.10, 5.99, 6.00, 10000), (6.00, 7.60, 5.95, 7.50, 10000)])
        self.check(sim(b, limit=6.0, stop=5.0, target=7.5, shares=q), "filled", "target", 7.5, 7.575, 6.8158865, 6.8233675)

    def test_g12_taf_fee_holiday(self):
        tr = sim(bars([FILL, (50.10, 50.80, 50.05, 50.70, 10000)]), day=dt.date(2026, 10, 5))
        self.assertAlmostEqual(tr.pnl["tiered"], 6.7249675, places=9)  # g01 without TAF 0.00166


class Costs(unittest.TestCase):
    def test_minimums_and_caps(self):
        self.assertAlmostEqual(costs.commission("tiered", 10, 50.0), 0.35)
        self.assertAlmostEqual(costs.commission("tiered", 1000, 50.0), 3.5)
        self.assertAlmostEqual(costs.commission("fixed", 10, 50.0), 1.00)
        self.assertAlmostEqual(costs.commission("fixed", 1, 6.0), 0.06)  # max 1% below the minimum
        self.assertAlmostEqual(costs.commission("tiered", 1, 6.0), 0.06)

    def test_taf_cap(self):
        big = costs.order_cost("fixed", "sell", 100000, 10.0, DAY)
        self.assertAlmostEqual(big, 500.0 + 0.3 + 0.0000206 * 1e6 + 8.30)


class Account(unittest.TestCase):
    def sig(self, t, symbol="A", p=0.6, b=None, day=DAY):
        return Signal(t, day, symbol, p, 50.0, 49.5, 50.75, 600, 60,
                      b or bars([FILL, (50.10, 50.80, 50.05, 50.70, 10000)], t + dt.timedelta(minutes=1)), CLOSE, HS, HSD)

    def test_unsettled_cash_blocks_until_next_session(self):
        d2 = dt.date(2025, 6, 3)
        t2 = dt.datetime(2025, 6, 3, 14, 0, tzinfo=UTC)
        sigs = [self.sig(T), self.sig(T + dt.timedelta(minutes=30), "B"),
                Signal(t2, d2, "C", 0.6, 50.0, 49.5, 50.75, 600, 60,
                       bars([FILL, (50.10, 50.80, 50.05, 50.70, 10000)], t2 + dt.timedelta(minutes=1)),
                       dt.datetime(2025, 6, 3, 20, tzinfo=UTC), HS, HSD)]
        trades, final, blocked = run_account(sigs, [DAY, d2], 500.0)
        self.assertEqual([s.symbol for s, tr in trades], ["A", "C"])
        self.assertEqual(trades[0][1].shares, 9)  # 10 shares + buy costs exceed $500 of settled cash
        self.assertEqual(blocked["cash"], 1)  # B: A's proceeds are unsettled until the next session

    def wide(self, t, symbol, p, b=None):
        """Stop 10% below the limit, so each position uses a fraction of a large account."""
        return Signal(t, DAY, symbol, p, 50.0, 45.0, 52.0, 600, 60,
                      b or bars([FILL, (50.10, 52.50, 50.05, 52.40, 10000)], t + dt.timedelta(minutes=1)), CLOSE, HS, HSD)

    def test_max_three_open(self):
        sigs = [self.wide(T, s, p) for s, p in (("A", 0.9), ("B", 0.8), ("C", 0.7), ("D", 0.95))]
        trades, _, blocked = run_account(sigs, [DAY], 25000.0)  # 50 shares x $50 per position
        self.assertEqual(sorted(s.symbol for s, _ in trades), ["A", "B", "D"])  # ranked by p
        self.assertEqual(blocked["concurrency"], 1)

    def test_daily_loss_limit(self):
        gap = bars([FILL, (40.0, 40.5, 39.5, 40.2, 10000)])  # 20 shares stopped at 39.99: about -$200.2 - costs
        sigs = [self.wide(T, "A", 0.6, gap), self.wide(T + dt.timedelta(minutes=30), "B", 0.6)]
        trades, _, blocked = run_account(sigs, [DAY], 10000.0)
        self.assertEqual([s.symbol for s, _ in trades], ["A"])
        self.assertEqual(blocked["daily_loss"], 1)  # realized loss >= 2% of the $10,000 start-of-session equity


if __name__ == "__main__":
    unittest.main()
