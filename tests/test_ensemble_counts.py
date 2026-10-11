"""Phase C measurements 1a (K, spec 6.4 and 4.2) and 1b (quote coverage): counts only. No fill, exit or outcome is
computed: the simulation functions are patched to fail in these tests."""
import json
import re
import unittest
from unittest import mock

from bot import ensemble_counts as ec, levels
from tests.test_ensemble_signals import inputs, liquid
from tests.test_probe_ensemble import Bars, series

S = Bars.S


def no_simulation():
    boom = mock.Mock(side_effect=AssertionError("no simulation in a measurement"))
    return mock.patch.multiple(levels, fill=boom, exits=boom, simulate=boom, gross_r=boom)


class Ended(unittest.TestCase):
    def test_ended_and_rename(self):
        end = S[350]
        ser = {"GONE": liquid(n=351), "OLD": liquid(n=351), "NEW": series(S[:400], close=10.0, volume=2_000_000),
               "FAR": liquid(n=351), "NEW2": series(S[:400], close=25.0, volume=2_000_000)}
        rows = [(S[300], "1", "GONE"), (S[300], "2", "OLD"), (S[0], "2", "NEW"), (S[300], "3", "FAR"),
                (S[0], "3", "NEW2")]
        filed = {}
        for d, _, s in rows:
            filed.setdefault(s, []).append(d)
        prices = {"2": [(S[320], S[319], "a", 12.0)], "3": [(S[320], S[319], "a", 12.0)]}
        inp = inputs(ser, filed, rows=rows, prices=prices)
        got = ec.ended(inp, start=S[0], end=S[399], before="2025-04-01")
        self.assertEqual(got.get("GONE"), end)  # its last bar, no rename: ended
        self.assertNotIn("OLD", got)  # NEW continues within a factor of 2 of the CIK's price: renamed, not ended
        self.assertEqual(got.get("FAR"), end)  # NEW2 at 25 is outside 12 x 2: not a rename
        self.assertNotIn("NEW", got)  # bars through the last session: not ended

    def test_end_on_or_after_the_cut_is_not_counted(self):
        inp = inputs({"A": liquid(n=351)}, {"A": [S[300]]})
        self.assertEqual(ec.ended(inp, start=S[0], end=S[399], before=S[350]), {})
        self.assertEqual(ec.ended(inp, start=S[0], end=S[399], before=S[351]), {"A": S[350]})


class KCounts(unittest.TestCase):
    def test_window_edges(self):
        sessions = S
        w = ec.window(S[300], sessions, start=S[100], end=S[399])
        import datetime as dt
        lo = (dt.date.fromisoformat(S[300]) - dt.timedelta(365)).isoformat()
        self.assertEqual(w, [d for d in S if lo < d <= S[300] and d >= S[100]])
        self.assertEqual(ec.window(S[50], sessions, start=S[100], end=S[399]), [])  # before the period

    def test_r_s_by_hand(self):
        days = S[100:140]  # 40 sessions, two calendar months or more
        members = {d: frozenset({"A", "B"}) for d in days}
        fired = [(days[5], "S2", "A"), (days[30], "S2", "A"), (days[6], "S2", "B"), (days[7], "S3", "B")]
        ended = {"A": days[39]}  # window: all 40 sessions of A
        got = ec.k_counts(fired, members, ended, S, start=days[0], end=days[-1])
        months_a = len({d[:7] for d in days})
        s2 = got["S2"]
        self.assertEqual((s2["firings_end"], s2["stock_sessions_end"], s2["stock_months_end"]), (2, 40, months_a))
        self.assertEqual((s2["firings_all"], s2["stock_sessions_all"], s2["stock_months_all"]), (3, 80, 2 * months_a))
        self.assertAlmostEqual(s2["r_session"], (2 / 40) / (3 / 80))
        self.assertAlmostEqual(s2["r_month"], (2 / months_a) / (3 / (2 * months_a)))
        self.assertEqual(s2["r_s"], max(s2["r_session"], s2["r_month"]))
        self.assertEqual(got["S3"]["firings_end"], 0)
        self.assertEqual(got["S3"]["r_s"], 0.0)
        self.assertEqual(got["S1"]["r_s"], 0.0)  # no firings at all: rate 0, never a division by zero

    def test_k_and_b(self):
        r = {"S1": 0.5, "S2": 1.5, "S3": 1.0, "S4": 3.0}
        w = ec.W_PROVISIONAL
        K, K_max = ec.k_value(r, w)
        self.assertAlmostEqual(K, w["S1"] * 1 + w["S2"] * 1.5 + w["S3"] * 1 + w["S4"] * 3.0)
        self.assertEqual(K_max, 3.0)
        self.assertAlmostEqual(sum(w.values()), 1.0)
        self.assertEqual(ec.F, 0.0041)
        self.assertTrue(ec.within(0.03))  # b at the limit continues
        self.assertFalse(ec.within(0.0300001))

    def test_phase_a_totals(self):
        self.assertEqual(ec.PHASE_A_FIRINGS, {"S1": 1511, "S2": 7979, "S3": 13355, "S4": 14679})


class Quotes(unittest.TestCase):
    def fired(self):
        out = []
        for sig in ("S2", "S3"):
            for y in ("2017", "2018"):
                out += [(f"{y}-0{1 + k % 9}-1{k % 9}", sig, f"X{k:03d}") for k in range(60 if sig == "S2" else 7)]
        return sorted(out)

    def test_sample_stratified_and_seeded(self):
        a, b = ec.sample(self.fired(), seed=20261011, per=50), ec.sample(self.fired(), seed=20261011, per=50)
        self.assertEqual(a, b)
        strata = {}
        for d, sig, _ in a:
            strata[(sig, d[:4])] = strata.get((sig, d[:4]), 0) + 1
        self.assertEqual(strata, {("S2", "2017"): 50, ("S2", "2018"): 50, ("S3", "2017"): 7, ("S3", "2018"): 7})
        self.assertNotEqual(a, ec.sample(self.fired(), seed=7, per=50))

    def test_plan_rows_rescale_to_the_raw_close_of_d(self):
        dates = S[:40]
        raw = [20.0] * 30 + [10.0] * 10  # a 2:1 split after 30 sessions: split-adjusted 10 throughout
        s = series(dates, close=[10.0] * 40, volume=1_000, raw_close=raw, raw_volume=[1_000] * 30 + [2_000] * 10)
        rows = ec.plan_rows(s, dates[-1])
        self.assertTrue(all(abs(r[4] - 10.0) < 1e-9 for r in rows))  # the whole history in D's raw money
        self.assertTrue(all(abs(r[4] * r[5] - 20_000) < 1e-6 for r in rows))  # raw dollar volume kept
        self.assertEqual(rows[-1][0], dates[-1])

    def test_quote_counts_by_hand(self):
        ser = {f"Q{k}": liquid(n=301) for k in range(4)}
        inp = inputs(ser, {s: [S[200]] for s in ser})
        D, nxt = S[299], S[300]
        sampled = [(D, "S3", "Q0"), (D, "S3", "Q1"), (D, "S3", "Q2"), (D, "S3", "Q3")]
        guard_at, entry_at = levels.guard_time(D, inp.cal), levels.entry_time(nxt, inp.cal)
        quotes = {("Q0", guard_at): (10.0, 10.01, 1, 1, 3), ("Q0", entry_at): (10.0, 10.01, 1, 1, 3),
                  ("Q1", guard_at): None, ("Q1", entry_at): (10.0, 10.01, 1, 1, 70),  # no guard; stale entry
                  ("Q2", guard_at): (10.0, 10.0, 1, 1, 3), ("Q2", entry_at): None,  # locked: usable
                  ("Q3", guard_at): (10.0, 11.0, 1, 1, 3), ("Q3", entry_at): (10.0, 10.02, 1, 1, 3)}  # cost guard
        with no_simulation():
            got = ec.quote_counts(sampled, inp, {}, quote=lambda sym, at: quotes[(sym, at)])
        c = got[("S3", D[:4])]
        self.assertEqual((c["sampled"], c["passing"], c["no_guard_quote"], c["cost_guard"], c["no_entry_quote"]),
                         (4, 4, 1, 1, 2))

    def test_weighted_share_and_se(self):
        counts = {("S2", "2017"): {"sampled": 50, "passing": 50, "no_guard_quote": 1, "cost_guard": 1},
                  ("S3", "2017"): {"sampled": 10, "passing": 10, "no_guard_quote": 0, "cost_guard": 0}}
        population = {("S2", "2017"): 100, ("S3", "2017"): 10}
        share, se = ec.weighted_share(counts, population)
        self.assertAlmostEqual(share, (100 * 2 / 50) / (100 * 50 / 50 + 10 * 10 / 10))
        self.assertGreater(se, 0)
        exact = {("S2", "2017"): {"sampled": 100, "passing": 100, "no_guard_quote": 5, "cost_guard": 0}}
        share, se = ec.weighted_share(exact, {("S2", "2017"): 100})
        self.assertAlmostEqual(share, 0.05)
        self.assertEqual(se, 0.0)  # the whole stratum sampled: no sampling error
        self.assertFalse(ec.quote_stop(0.05))  # exactly 5% does not stop
        self.assertTrue(ec.quote_stop(0.0500001))


class Run(unittest.TestCase):
    def test_run_counts_only(self):
        inp = inputs({f"A{k}": liquid(n=400, volume=2_000_000 + k) for k in range(12)}, {f"A{k}": [S[200]] for k in range(12)})
        with no_simulation(), \
                mock.patch.object(ec.ed, "load", return_value=inp), \
                mock.patch.object(ec.ed, "SIGNAL_START", S[260]), mock.patch.object(ec.ed, "SIGNAL_END", S[399]), \
                mock.patch.object(ec.alpaca, "quote_full", return_value=(10.0, 10.01, 1, 1, 3)):
            out = ec.run()
        self.assertEqual(set(out), {"k", "quotes", "requests_and_runtime"})
        self.assertEqual(tuple(out["k"]), ec.K_REPORT_KEYS)
        if out["k"]["continue_to_quotes"]:
            self.assertEqual(tuple(out["quotes"]), ec.QUOTE_REPORT_KEYS)

    def test_crash_prints_no_value(self):
        import contextlib
        import io
        buf = io.StringIO()
        with mock.patch.object(ec, "run", side_effect=ValueError("sentinel 31337.17")), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf), \
                mock.patch("sys.excepthook"), mock.patch("threading.excepthook"):
            self.assertEqual(ec.main([]), 1)
        self.assertIn("ValueError", buf.getvalue())
        self.assertNotIn("31337.17", buf.getvalue())


class Report(unittest.TestCase):
    def test_reports_whitelisted_and_outcome_free(self):
        for keys in (ec.K_REPORT_KEYS, ec.QUOTE_REPORT_KEYS):
            text = json.dumps(keys)
            self.assertIsNone(re.search(r"(?i)return|mean|median|\bwin|gross|fill|exit", text), keys)


if __name__ == "__main__":
    unittest.main()
