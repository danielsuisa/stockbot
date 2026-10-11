"""Phase C task 11 (spec 6.4 enforcement points 2-3, rulings C17, C24, C25): the driver. A synthetic end-to-end blind
run writes nothing but the report and prints no result; the prefetch is outcome-free; the network is closed in blind
mode; --verdict refuses without the blind run's commit and the owner's go-ahead. SYNTHETIC BARS ONLY."""
import contextlib
import io
import os
import pathlib
import tempfile
import unittest
import urllib.request
from unittest import mock

from bot import ensemble as en, ensemble_backtest as bt, ensemble_counts as ec, ensemble_data as ed, levels, signals
from tests.test_ensemble_backtest import GOOD_QUOTE, TRIGGER, flat
from tests.test_ensemble_signals import inputs
from tests.test_equity_privacy import Leaks
from tests.test_probe_ensemble import Bars

S = Bars.S
D = S[300]
SYMS = [f"P{k:02d}" for k in range(25)]


def scenario():
    after = [TRIGGER] + [(50.6 + 0.01 * k, 50.7 + 0.01 * k, 50.5, 50.6 + 0.01 * k) for k in range(60)]
    inp = inputs({s: flat(after=after) for s in SYMS}, {s: [S[200]] for s in SYMS})
    got = {"firings": [(D, "S3", "P00"), (D, "S2", "P01"), (S[301], "S4", "P02")],
           "members": {d: frozenset(SYMS) for d in (D, S[301])}, "renamed": {},
           "deciles": {d: (tuple(SYMS), bytes([3] * 25)) for d in (D, S[301])}, "warm_up": {}, "diagnostics": {}}
    return inp, got


def patched():
    inp, got = scenario()
    return contextlib.ExitStack(), inp, got


@contextlib.contextmanager
def synthetic_run():
    inp, got = scenario()
    with mock.patch.object(en, "prepare", return_value=(inp, got)), \
            mock.patch.object(en, "check_r_s"), \
            mock.patch.object(en.alpaca, "quote_full", return_value=GOOD_QUOTE), \
            mock.patch.object(ed, "SIGNAL_START", S[260]), mock.patch.object(ed, "SIGNAL_END", S[330]):
        yield inp, got


class Blind(unittest.TestCase):
    def test_blind_writes_only_the_report(self):
        leaks = Leaks()
        with tempfile.TemporaryDirectory() as d, synthetic_run():
            old = os.getcwd()
            os.chdir(d)
            try:
                with leaks.watch():
                    self.assertEqual(en.main(["--blind"]), 0)
            finally:
                os.chdir(old)
        files = [w for w in leaks.writes if w.startswith("[file ")]
        self.assertEqual(files, [f"[file {en.REPORT_PATH}]"])  # one file: the report
        self.assertEqual(leaks.logs, [])
        self.assertEqual(leaks.summaries, [])
        printed = leaks.out.getvalue().strip().splitlines()
        self.assertEqual(len(printed), 1)  # no result on stdout or stderr: the path and the input fingerprint only
        self.assertRegex(printed[0], rf"^blind report written: {en.REPORT_PATH}; inputs [0-9a-f]{{16}}$")

    def test_blind_report_content_is_the_whitelist(self):
        with tempfile.TemporaryDirectory() as d, synthetic_run():
            rep = en.blind_report()
            path = pathlib.Path(d, "r.json")
            en.blind.write(rep, path)
            self.assertEqual(tuple(rep), en.blind.BLIND_KEYS)
            self.assertTrue(path.exists())

    def test_blind_is_offline(self):
        with en.offline():
            with self.assertRaises(en.MissingInput):
                urllib.request.urlopen("https://example.com")
        self.assertIsNot(urllib.request.urlopen, en._refuse)  # restored afterwards

    def test_missing_input_aborts_without_a_result(self):
        buf = io.StringIO()
        with synthetic_run(), mock.patch.object(en, "blind_report", side_effect=en.MissingInput("x")), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            self.assertEqual(en.main(["--blind"]), 3)
        self.assertIn("missing", buf.getvalue())

    def test_crash_prints_no_value(self):
        buf = io.StringIO()
        with mock.patch.object(en, "blind_report", side_effect=ValueError("x-bar 0.0123456")), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf), \
                mock.patch("sys.excepthook"), mock.patch("threading.excepthook"):
            self.assertEqual(en.main(["--blind"]), 1)
        self.assertIn("ValueError", buf.getvalue())
        self.assertNotIn("0.0123456", buf.getvalue())


class CacheMissInCI(unittest.TestCase):
    def test_a_cache_miss_without_keys_is_a_missing_input(self):
        # review M3: in the blind job there are no keys; a quote missing from the cache must surface as MissingInput
        with tempfile.TemporaryDirectory() as d, mock.patch.object(en.alpaca, "CACHE", pathlib.Path(d)), \
                mock.patch.dict(os.environ, {"ALPACA_KEY_ID": "", "ALPACA_SECRET_KEY": "", "SEC_UA": ""}), \
                mock.patch.object(en.alpaca, "GAP", 0):
            with en.offline():
                with self.assertRaises(en.MissingInput):
                    en.alpaca.quote_full("AAA", "2026-01-05T20:55:00Z")


class Fingerprint(unittest.TestCase):
    def test_same_inputs_same_fingerprint(self):
        a, _ = scenario()
        b, _ = scenario()
        self.assertEqual(ed.fingerprint(a), ed.fingerprint(b))
        b.series["P00"]["raw_close"][5] += 0.01
        self.assertNotEqual(ed.fingerprint(a), ed.fingerprint(b))

    def test_insider_quarters_pinned(self):
        import datetime as dt
        asked = []
        with mock.patch.object(ed.pi, "quarter_zip", side_effect=lambda y, q: asked.append((y, q))), \
                mock.patch.object(ed.alpaca, "calendar", side_effect=RuntimeError("stop here")):
            with self.assertRaises(RuntimeError):
                ed.load(dt.date(2027, 6, 1))
        self.assertEqual(max(asked), ed.LAST_QUARTER)  # 2026Q1: the insider data sets end there (6.1)


class Prefetch(unittest.TestCase):
    def test_prefetch_is_outcome_free(self):
        inp, got = scenario()
        boom = mock.Mock(side_effect=AssertionError("no simulation in the prefetch"))
        with mock.patch.multiple(levels, fill=boom, exits=boom, simulate=boom, gross_r=boom):
            wanted = en.candidates(got["firings"], inp, {})
        g = levels.guard_time
        e = levels.entry_time
        self.assertIn(("P00", g(D, inp.cal)), wanted)
        for k in (301, 302, 303):  # a breakout: every session it could fill on, whatever happens
            self.assertIn(("P00", e(S[k], inp.cal)), wanted)
        self.assertIn(("P02", e(S[302], inp.cal)), wanted)  # an open order: the next session only
        self.assertNotIn(("P02", e(S[303], inp.cal)), wanted)

    def test_prefetch_prints_counts_only(self):
        inp, got = scenario()
        buf = io.StringIO()
        with mock.patch.object(en, "prepare", return_value=(inp, got)), \
                mock.patch.object(en.alpaca, "quote_full", return_value=GOOD_QUOTE), contextlib.redirect_stdout(buf):
            self.assertEqual(en.main(["--prefetch", "--deadline-min", "1"]), 0)
        self.assertRegex(buf.getvalue().strip(), r"^prefetch: \d+ quotes wanted, \d+ read, 0 index pages missing; "
                                                  r"inputs [0-9a-f]{16}$")

    def test_require_complete(self):
        inp, got = scenario()
        with mock.patch.object(en, "prepare", return_value=(inp, got)), \
                mock.patch.object(en.alpaca, "quote_full", return_value=GOOD_QUOTE), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(en.main(["--prefetch", "--deadline-min", "0", "--require-complete"]), 4)
            self.assertEqual(en.main(["--prefetch", "--deadline-min", "60", "--require-complete"]), 0)


class Verdict(unittest.TestCase):
    def test_verdict_refuses_without_its_flags(self):
        with mock.patch.object(en, "head", return_value="abc123"), mock.patch.object(en, "verdict_report") as run:
            self.assertEqual(en.main(["--verdict"]), 2)
            self.assertEqual(en.main(["--verdict", "--blind-commit", "abc123"]), 2)
            self.assertEqual(en.main(["--verdict", "--blind-commit", "zzz", "--owner-go-ahead"]), 2)
        run.assert_not_called()


class Workflow(unittest.TestCase):
    def test_workflow_uploads_the_report_only(self):
        wf = (pathlib.Path(__file__).resolve().parent.parent / ".github/workflows/ensemble.yml").read_text("utf-8")
        on = wf[wf.index("on:"):wf.index("permissions:")]
        self.assertIn("branches: [ensemble-run-blind]", on)
        for trigger in ("schedule", "workflow_dispatch", "pull_request"):
            self.assertNotIn(trigger, on)
        self.assertEqual(wf.count("actions/upload-artifact"), 1)
        self.assertIn(f"path: {en.REPORT_PATH}", wf)
        self.assertNotIn("--verdict", wf)
        self.assertIn("--prefetch", wf)
        self.assertIn("--blind", wf)
        self.assertIn("--require-complete", wf)  # the last prefetch fails unless every input is cached
        self.assertGreaterEqual(wf.count("fail-on-cache-miss: true"), 4)  # prefetch 2-4 and the blind job


if __name__ == "__main__":
    unittest.main()
