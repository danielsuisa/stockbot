"""Index program phase 2, task 6 (spec 8.2, 8.3, B5, E2, E4, E5): the driver and its reports on SYNTHETIC paths. The
windows are shortened in these tests (the engine is the same); no real price is read."""
import contextlib
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import index_run as ir
from tests import index_synth as syn
from tests.test_equity_privacy import Leaks

SHORT = {"P": ("2007-01-03", "2008-12-31"), "FIT_P": ("2006-01-03", "2006-12-29"), "H": ("2006-01-03", "2008-12-31")}


@contextlib.contextmanager
def short_windows():
    with mock.patch.multiple(ir, **SHORT):
        yield


def path(kind):
    if kind == "flat":
        return syn.rows("2005-02-25", "2008-12-31", syn.flat())
    if kind == "crash":  # a rise, then a 45% fall over 80 days in 2008 (inside P), then flat
        return syn.rows("2005-02-25", "2008-12-31", syn.crash(at=740, length=80, depth=0.45, g=0.0006))
    return syn.rows("2005-02-25", "2008-12-31", syn.rising())


class Blind(unittest.TestCase):
    def test_blind_whitelist(self):
        rows = path("rise")
        with short_windows():
            rep = ir.blind_report(rows, "f" * 16)
        self.assertEqual(tuple(rep), ir.BLIND_KEYS)
        self.assertEqual(set(rep["pi"]), {"TREND", "TOM", "D"})
        self.assertEqual(set(rep["pi"]["TOM"]), {"P", "FIT_P", "H"})
        self.assertEqual(rep["trend_order_tickets_per_year"], {"2006": 2})  # a steady rise: one entry at H's first open

    def test_blind_computes_no_equity(self):
        rows = path("rise")
        with short_windows(), mock.patch.object(ir.ie, "run_window", side_effect=AssertionError("equity run")):
            ir.blind_report(rows, "f" * 16)  # 8.2 is counts only: the engine is never run on the prices


class Verdict(unittest.TestCase):
    def test_planted_crash_gives_timing(self):
        rows = path("crash")
        with short_windows():
            rep = ir.verdict_report(rows, syn.cash(rows), "f" * 16)
        self.assertEqual(rep["outcome"], "TIMING", json.dumps(rep["eligibility"]))
        self.assertEqual(rep["selected"], "TREND")
        self.assertIn("information, no decision", rep)

    def test_steady_rise_gives_static(self):
        rows = path("rise")  # TREND stays ON like STATIC: it cannot be 1 point a year above it
        with short_windows():
            rep = ir.verdict_report(rows, syn.cash(rows), "f" * 16)
        self.assertEqual(rep["outcome"], "STATIC")

    def test_report_shape(self):
        rows = path("crash")
        with short_windows():
            rep = ir.verdict_report(rows, syn.cash(rows), "f" * 16)
        pol = rep["policies"]["STATIC"]
        self.assertEqual(set(pol["windows"]), {"P", "FIT_P", "H"})
        self.assertEqual(set(pol["windows"]["P"]["costs"]), {"optimistic", "base", "stress"})
        self.assertEqual(set(pol["windows"]["P"]["accounts_base"]), {"5000", "25000"})
        for key in ("growth", "final_equity", "max_drawdown", "worst_year", "positive_years", "order_tickets",
                    "mean_spy_weight", "calendar_year_returns"):
            self.assertIn(key, pol["windows"]["P"]["costs"]["base"])
        self.assertEqual(set(rep["overlays"]), {"TOM", "D"})
        self.assertIn("round_trips", rep["overlays"]["TOM"])
        self.assertEqual(set(rep["references"]), {"spy_s1", "cash_leg"})
        info = rep["information, no decision"]
        self.assertEqual(set(info), {"no_crash_cap", "budget_15", "budget_17", "t_8_months", "t_12_months",
                                     "d_rsi_5", "d_rsi_15", "t_timing_statistic"})

    def test_no_daily_series_in_report(self):
        rows = path("crash")
        with short_windows():
            rep = ir.verdict_report(rows, syn.cash(rows), "f" * 16)

        def lists(v):
            if isinstance(v, dict):
                for x in v.values():
                    yield from lists(x)
            elif isinstance(v, list):
                yield v
                for x in v:
                    yield from lists(x)
        self.assertLessEqual(max(len(x) for x in lists(rep)), 10)  # E2, B5: no daily or monthly series

    def test_hebrew_summary_states_outcome(self):
        for kind, word in (("rise", "STATIC"), ("crash", "TIMING")):
            rows = path(kind)
            with short_windows():
                rep = ir.verdict_report(rows, syn.cash(rows), "f" * 16)
            md = ir.summary_md(rep, "2026-10-13")
            self.assertIn(word, md)
            self.assertIn("תוצאה", md)


class Flat(unittest.TestCase):
    def test_flat_market_favours_the_cash_leg(self):
        """In a flat market TREND is OFF (C is not above SMA10) and earns the bill rate, above STATIC's 40% of it."""
        rows = path("flat")
        with short_windows():
            d = ir.decide(rows, syn.cash(rows))
        self.assertEqual(d["p_numbers"]["TREND"]["mdd_p"], 0.0)
        self.assertGreater(d["p_numbers"]["TREND"]["growth_p"], d["p_numbers"]["STATIC"]["growth_p"])


class Main(unittest.TestCase):
    def files(self, d, rows):
        body = b"Date;Open;High;Low;Close;Volume\n" + b"".join(
            f"{r[0]};{r[1]!r};{r[2]!r};{r[3]!r};{r[4]!r};1\n".encode() for r in rows)
        h15 = b"# source: Board\ndate,value\n1993-12-01,2.0\n"
        paths = {"spy_us_d.csv": body, "h15-3m-tbill.csv": h15}
        for name, b in paths.items():
            with open(os.path.join(d, name), "wb") as fh:
                fh.write(b)
        man = {n: {"sha256": hashlib.sha256(b).hexdigest()} for n, b in paths.items()}
        with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(man, fh)
        return [os.path.join(d, n) for n in ("spy_us_d.csv", "h15-3m-tbill.csv", "manifest.json")]

    def test_blind_writes_only_its_report(self):
        rows = path("rise")
        leaks = Leaks()
        with tempfile.TemporaryDirectory() as d, short_windows():
            stooq, h15, man = self.files(d, rows)
            old = os.getcwd()
            os.chdir(d)
            try:
                with leaks.watch():
                    code = ir.main(["--blind", "--stooq", stooq, "--h15", h15, "--manifest", man])
            finally:
                os.chdir(old)
        self.assertEqual(code, 0)
        files = [w[6:-1] for w in leaks.writes if w.startswith("[file ")]
        self.assertEqual(files, [ir.BLIND_PATH])
        self.assertEqual(len(leaks.out.getvalue().strip().splitlines()), 1)

    def test_verdict_refusals(self):
        rows = path("rise")
        with tempfile.TemporaryDirectory() as d, short_windows(), mock.patch.object(ir, "head", return_value="c0ffee"), \
                contextlib.redirect_stdout(open(os.devnull, "w")):
            stooq, h15, man = self.files(d, rows)
            base = ["--stooq", stooq, "--h15", h15, "--manifest", man]
            self.assertEqual(ir.main(["--verdict", *base]), 2)  # no commit, no go-ahead
            self.assertEqual(ir.main(["--verdict", "--commit", "c0ffee", "--owner-go-ahead", *base]), 2)  # E5
            blind = os.path.join(d, "blind.json")
            with open(blind, "w", encoding="utf-8") as fh:
                json.dump({"fingerprint": "other"}, fh)
            self.assertEqual(ir.main(["--verdict", "--commit", "c0ffee", "--owner-go-ahead", "--blind-report", blind,
                                      *base]), 2)  # E5: the recomputed blind report differs

    def test_sha256_mismatch_stops(self):
        rows = path("rise")
        with tempfile.TemporaryDirectory() as d, short_windows(), contextlib.redirect_stdout(open(os.devnull, "w")):
            stooq, h15, man = self.files(d, rows)
            with open(stooq, "ab") as fh:
                fh.write(b"\n")
            self.assertEqual(ir.main(["--blind", "--stooq", stooq, "--h15", h15, "--manifest", man]), 2)


if __name__ == "__main__":
    unittest.main()
