"""Swing evaluator golden set (ADR 0014 section 9): 21 hand-computed cases the evaluator must reproduce exactly.

Ticket unless stated: signal day = session 0; trigger 50.00, limit 50.50, stop 48.00, target 54.00 (R = 2), qty 10.
Quotes: every 09:35 sample (49.99, 50.01) -> half spread 0.01; every close - 5 min sample (49.98, 50.02) -> 0.02.
Costs are recomputed here by hand (IBKR Tiered with fees, 2024 dates: TAF applies), independent of costs.py."""
import datetime as dt
import unittest

from scalp_eval import swing as W

DAYS = []
_d = dt.date(2024, 1, 2)
while len(DAYS) < 45:
    if _d.weekday() < 5:
        DAYS.append(_d)
    _d += dt.timedelta(days=1)


def tiered(side, q, p):
    c = min(max(0.0035 * q, 0.35), 0.01 * q * p)
    t = c + 0.000003 * q + 0.003 * q + 0.0002 * q + c * (0.000175 + 0.000565)
    if side == "sell":
        t += 0.0000206 * q * p + min(0.000166 * q, 8.30)
    return t


FLAT = (50.2, 50.4, 49.9, 50.1)


def market(days, sym="A", **kw):
    """days: {session index: (o, h, l, c)} for symbol A (session 0 always has a bar)."""
    spec = {0: (49.0, 49.9, 48.8, 49.8), **days}
    ii = sorted(spec)
    bars = {sym: (ii, [spec[i][0] for i in ii], [spec[i][1] for i in ii], [spec[i][2] for i in ii], [spec[i][3] for i in ii])}
    bars.update(kw.pop("extra", {}))
    return W.SwingMarket(DAYS, bars, **kw)


QUOTES = W.Quotes({(s, d): (49.99, 50.01) for s in "ABCDEFGHI" for d in DAYS},
                  {(s, d): (49.98, 50.02) for s in "ABCDEFGHI" for d in DAYS})
TK = {"ticket_id": "t", "symbol": "A", "signal_date": DAYS[0], "trigger": 50.0, "limit": 50.5, "stop": 48.0,
      "target": 54.0, "qty": 10}


def no_min(sym, d):
    return None


def minutes_of(table):
    return lambda sym, d: table.get((sym, d))


def net(fill, exit_px, q=10, sell=True, shares=None, div=0.0):
    sh = q if shares is None else shares
    c = tiered("buy", q, fill) + (tiered("sell", sh, exit_px) if sell else 0.0)
    return (sh * exit_px + div - q * fill - c) / 25.0


TARGET_DAY2 = (50.5, 54.5, 50.4, 54.2)


class Entry(unittest.TestCase):
    def test_g01_trigger_touched_intraday(self):
        r = W.run_ticket(TK, market({1: (49.5, 50.6, 49.4, 50.3), 2: TARGET_DAY2}), no_min, QUOTES)
        self.assertEqual((r["fill_kind"], r["fill_px"], r["case"], r["exit_px"]), ("trigger", 50.01, "target", 54.0))
        self.assertAlmostEqual(r["net_r"], net(50.01, 54.0), places=10)

    def test_g02_open_above_trigger_within_limit(self):
        r = W.run_ticket(TK, market({1: (50.3, 50.6, 50.2, 50.5), 2: TARGET_DAY2}), no_min, QUOTES)
        self.assertEqual(r["fill_kind"], "open")
        self.assertAlmostEqual(r["fill_px"], 50.31, places=9)
        self.assertAlmostEqual(r["net_r"], net(50.31, 54.0), places=10)

    def test_g03_open_above_limit_then_pullback(self):  # A1: rests at the limit; fills on session 2
        r = W.run_ticket(TK, market({1: (51.0, 51.5, 50.7, 51.2), 2: (50.8, 51.0, 50.4, 50.9), 3: (51.0, 54.5, 50.9, 54.0)}),
                         no_min, QUOTES)
        self.assertEqual((r["fill_kind"], r["fill_px"], r["fill_i"], r["case"]), ("resting_limit", 50.5, 2, "target"))
        self.assertAlmostEqual(r["net_r"], net(50.5, 54.0), places=10)

    def test_g04_open_above_limit_never_back(self):
        r = W.run_ticket(TK, market({1: (51.0, 51.5, 50.7, 51.2), 2: (51.2, 51.6, 50.9, 51.3), 3: (51.5, 52.0, 51.1, 51.8)}),
                         no_min, QUOTES)
        self.assertEqual((r["filled"], r["reason"], r["net_r"]), (False, "gapped_above_limit", 0.0))

    def test_g05_expiry_without_touch(self):
        r = W.run_ticket(TK, market({1: (49.0, 49.8, 48.9, 49.5), 2: (49.5, 49.9, 49.1, 49.6), 3: (49.6, 49.99, 49.2, 49.7),
                                     4: (49.8, 51.0, 49.7, 50.9)}), no_min, QUOTES)
        self.assertEqual((r["filled"], r["reason"]), (False, "no_touch"))  # session 4 is past expiry


class FillSession(unittest.TestCase):
    def test_g06_fill_session_stop_with_minutes(self):
        """The 47.50 low comes before the fill minute and is ignored; the 47.90 low after it stops the ticket."""
        mins = {("A", DAYS[1]): [(0, 49.5, 49.6, 47.5, 49.5), (1, 49.6, 50.2, 49.6, 50.1), (2, 50.1, 50.2, 47.9, 48.0)]}
        r = W.run_ticket(TK, market({1: (49.5, 50.2, 47.5, 48.0)}), minutes_of(mins), QUOTES)
        self.assertEqual((r["fill_px"], r["case"], r["exit_px"]), (50.01, "stop_fill_session", 47.98))
        self.assertAlmostEqual(r["net_r"], net(50.01, 47.98), places=10)
        mins2 = {("A", DAYS[1]): [(0, 49.5, 49.6, 47.5, 49.5), (1, 49.6, 50.2, 49.6, 50.1)]}
        r2 = W.run_ticket(TK, market({1: (49.5, 50.2, 47.5, 50.1), 2: TARGET_DAY2}), minutes_of(mins2), QUOTES)
        self.assertEqual(r2["case"], "target")  # the pre-fill low did not stop it

    def test_g07_fill_session_stop_without_minutes(self):
        r = W.run_ticket(TK, market({1: (49.5, 50.2, 47.5, 49.8)}), no_min, QUOTES)
        self.assertEqual((r["case"], r["exit_px"], r["exit_i"]), ("stop_fill_session", 47.98, 1))  # stop first


class Holding(unittest.TestCase):
    FILL = {1: (49.5, 50.6, 49.4, 50.3)}

    def test_g08_conflict_resolved_by_minutes(self):
        mins = {("A", DAYS[2]): [(0, 50.5, 54.2, 50.4, 54.1), (1, 54.1, 54.1, 47.5, 47.6)]}
        r = W.run_ticket(TK, market({**self.FILL, 2: (50.5, 54.5, 47.5, 50.0)}), minutes_of(mins), QUOTES)
        self.assertEqual((r["case"], r["exit_px"]), ("target", 54.0))

    def test_g09_conflict_stop_first(self):
        r = W.run_ticket(TK, market({**self.FILL, 2: (50.5, 54.5, 47.5, 50.0)}), no_min, QUOTES)
        self.assertEqual((r["case"], r["exit_px"]), ("stop", 47.98))
        self.assertAlmostEqual(r["net_r"], net(50.01, 47.98), places=10)

    def test_g10_target_touched_not_traded_through(self):
        r = W.run_ticket(TK, market({**self.FILL, 2: (50.5, 54.0, 50.4, 53.9), 3: (53.9, 54.01, 53.5, 54.0)}), no_min, QUOTES)
        self.assertEqual((r["case"], r["exit_i"], r["exit_px"]), ("target", 3, 54.0))

    def test_g11_gap_below_stop(self):
        r = W.run_ticket(TK, market({**self.FILL, 2: (47.0, 47.5, 46.0, 46.5)}), no_min, QUOTES)
        self.assertEqual((r["case"], r["exit_px"]), ("gap_below_stop", 46.98))
        self.assertAlmostEqual(r["net_r"], net(50.01, 46.98), places=10)

    def test_g12_gap_above_target(self):
        r = W.run_ticket(TK, market({**self.FILL, 2: (55.0, 56.0, 54.8, 55.5)}), no_min, QUOTES)
        self.assertEqual((r["case"], r["exit_px"]), ("gap_above_target", 55.0))
        self.assertAlmostEqual(r["net_r"], net(50.01, 55.0), places=10)

    def test_g13_time_exit(self):
        days = {i: FLAT for i in range(1, 25)}
        r = W.run_ticket(TK, market(days), no_min, QUOTES)
        self.assertEqual((r["fill_kind"], r["fill_px"], r["case"], r["exit_i"], r["exit_px"]), ("open", 50.21, "time", 20, 50.08))
        self.assertAlmostEqual(r["net_r"], net(50.21, 50.08), places=10)


class CorporateActions(unittest.TestCase):
    FILL = {1: (49.5, 50.6, 49.4, 50.3)}

    def test_g14_split(self):  # 2:1 ex session 2: 20 shares; stop 24, target 27; target traded through on session 3
        m = market({**self.FILL, 2: (25.1, 25.2, 24.9, 25.0), 3: (25.0, 27.5, 24.95, 27.2)}, splits={"A": [(DAYS[2], 2.0)]})
        r = W.run_ticket(TK, m, no_min, QUOTES)
        self.assertEqual((r["case"], r["exit_px"], r["shares_exit"]), ("target", 27.0, 20.0))
        self.assertAlmostEqual(r["net_r"], net(50.01, 27.0, shares=20.0), places=10)

    def test_g15_dividend(self):
        m = market({**self.FILL, 2: (50.2, 50.5, 50.0, 50.3), 3: TARGET_DAY2}, divs={"A": [(DAYS[2], 0.5)]})
        r = W.run_ticket(TK, m, no_min, QUOTES)
        self.assertEqual((r["case"], r["dividends"]), ("target", 5.0))
        self.assertAlmostEqual(r["net_r"], net(50.01, 54.0, div=5.0), places=10)

    def test_g16_cash_merger(self):  # no bar after session 2; cash merger 53 effective session 3: no sell cost
        m = market({**self.FILL, 2: (50.2, 50.5, 50.0, 50.3)}, mergers={"A": [(DAYS[3], "cash_mergers", 0.0, 53.0, None)]})
        r = W.run_ticket(TK, m, no_min, QUOTES)
        self.assertEqual((r["case"], r["exit_px"]), ("merger_cash", 53.0))
        self.assertAlmostEqual(r["net_r"], net(50.01, 53.0, sell=False), places=10)

    def test_g17_other_delisting(self):  # last bar session 2 (close 49.0): 49.0 x 0.70 = 34.30, no sell cost
        r = W.run_ticket(TK, market({**self.FILL, 2: (49.5, 49.8, 48.5, 49.0)}), no_min, QUOTES)
        self.assertEqual(r["case"], "delisting")
        self.assertAlmostEqual(r["exit_px"], 34.3, places=10)
        self.assertAlmostEqual(r["net_r"], net(50.01, 34.3, sell=False), places=10)


def tk(tid, sym, day0, qty, trigger=50.0):
    return {"ticket_id": tid, "symbol": sym, "signal_date": DAYS[day0], "trigger": trigger, "limit": trigger + 0.5,
            "stop": trigger - 2.0, "target": trigger + 4.0, "qty": qty}


def flat_from(start, n=40, base=50.0):
    """A symbol that opens at base + 0.2 (an open fill) from `start` and drifts flat."""
    return {i: (base + 0.2, base + 0.4, base - 0.1, base + 0.1) for i in range(start, n)}


def bars_of(spec):
    ii = sorted(spec)
    return (ii, [spec[i][0] for i in ii], [spec[i][1] for i in ii], [spec[i][2] for i in ii], [spec[i][3] for i in ii])


class Book(unittest.TestCase):
    def test_g18_forty_percent_cap(self):  # qty 30 at 50.21 -> cap floor(0.4 x 2500 / 50.21) = 19
        m = W.SwingMarket(DAYS, {"A": bars_of({0: (49, 49.9, 48.8, 49.8), **flat_from(1)})})
        b = W.simulate_book([tk("a", "A", 0, 30)], m, no_min, QUOTES)
        self.assertEqual(b["trades"], 1)
        self.assertEqual(int(0.4 * 2500 // 50.21), 19)

    def test_g19_zero_share_skip(self):  # a $3,000 stock: floor(1000 / 3000.21) = 0
        m = W.SwingMarket(DAYS, {"A": bars_of({0: (2990, 2999, 2980, 2995), **flat_from(1, base=3000.0)})})
        b = W.simulate_book([tk("a", "A", 0, 5, trigger=3000.0)], m, no_min,
                            W.Quotes({}, {("A", d): (2999.9, 3000.1) for d in DAYS}))
        self.assertEqual((b["trades"], b["skips"]["zero_qty"]), (0, 1))

    def test_g20_t1_blocks(self):
        """A and B fill on session 1 (19 shares each, about $955 each); A gaps below its stop on session 2 and
        exits; C fills on session 2 and needs about $955 while about $590 is settled: A's proceeds settle only from
        session 3, so C is skipped. D fills on session 3 and is taken."""
        a = {0: (49, 49.9, 48.8, 49.8), 1: (50.2, 50.4, 49.9, 50.1), 2: (47.0, 47.5, 46.0, 46.5), 3: (46.5, 46.8, 46.0, 46.2)}
        bars = {"A": bars_of(a), "B": bars_of({0: (49, 49.9, 48.8, 49.8), **flat_from(1)}),
                "C": bars_of({0: (49, 49.9, 48.8, 49.8), 1: (49.0, 49.8, 48.9, 49.5), **flat_from(2)}),
                "D": bars_of({0: (49, 49.9, 48.8, 49.8), 1: (49.0, 49.8, 48.9, 49.5), 2: (49.0, 49.8, 48.9, 49.5), **flat_from(3)})}
        m = W.SwingMarket(DAYS, bars)
        tks = [tk("a", "A", 0, 19), tk("b", "B", 0, 19), tk("c", "C", 1, 19), tk("d", "D", 2, 19)]
        b = W.simulate_book(tks, m, no_min, QUOTES)
        self.assertEqual((b["trades"], b["skips"]["t1_cash"]), (3, 1))

    def test_g21_four_position_cap(self):
        bars = {s: bars_of({0: (49, 49.9, 48.8, 49.8), **flat_from(1)}) for s in "ABCDE"}
        m = W.SwingMarket(DAYS, bars)
        b = W.simulate_book([tk(s.lower(), s, 0, 2) for s in "ABCDE"], m, no_min, QUOTES)
        self.assertEqual((b["trades"], b["skips"]["max_positions"]), (4, 1))


class Guard(unittest.TestCase):
    def test_cli_refuses_without_s2_ledger(self):
        with self.assertRaises(SystemExit) as cm:  # the repository ledger is empty in S1
            W.main(["--window", "development", "--pubkey", "x"])
        self.assertIn("refused", str(cm.exception))

    def test_only_an_s2_authorize_record_opens(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            led = Path(d) / "s_trials.jsonl"
            self.assertFalse(W.s2_open(led))
            led.write_text(json.dumps({"phase": "S1", "kind": "authorize"}) + "\n", encoding="utf-8")
            self.assertFalse(W.s2_open(led))
            led.write_text(json.dumps({"kind": "s2_open"}) + "\n", encoding="utf-8")  # the ADR 0014 record no longer opens
            self.assertFalse(W.s2_open(led))
            led.write_text(json.dumps({"phase": "S2", "kind": "authorize"}) + "\n", encoding="utf-8")
            self.assertTrue(W.s2_open(led))

    def test_control_difference_counts_unfilled_as_zero(self):
        res = {"t1": {"net_r": 0.5}, "t2": {"net_r": 0.0}}
        ctl = {"t1": [{"net_r": 0.2}, {"net_r": 0.0}, {"net_r": -0.2}], "t2": [{"net_r": 0.3}]}
        self.assertEqual(dict(W.control_differences(res, ctl)), {"t1": 0.5, "t2": -0.3})


if __name__ == "__main__":
    unittest.main()
