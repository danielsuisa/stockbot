"""Phase C item B-D: the verdict run (phase D) decided now, because 6.4 point 4 freezes the commit at the blind run.
SYNTHETIC DATA ONLY: a hand-checked small case of every reported number, end-to-end runs of `--verdict` on synthetic
bars (a planted effect -> GO; no effect -> NO-GO; check 1 or comparability failing -> their NO-GO with no return
statistic), the files the verdict writes, and the dispatch-only workflow."""
import contextlib
import csv
import io
import json
import math
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from bot import blind, ensemble as en, ensemble_data as ed, ensemble_stats as st, ensemble_verdict as ev
from tests.test_ensemble_backtest import GOOD_QUOTE, flat
from tests.test_ensemble_signals import inputs
from tests.test_equity_privacy import Leaks
from tests.test_probe_ensemble import Bars

S = Bars.S


def tk(signal, day, block, g, m, c, cs, filled, exits):
    return {"signal": signal, "day": day, "symbol": f"X{day[:4]}", "bars_of": f"X{day[:4]}", "block": block, "g": g,
            "m": m, "c": c, "cost_r": c, "cost_r_stress": cs, "filled": filled, "exits": exits}


HAND = [tk("S1", "2017-03-01", 0, 1.0, 0.2, 0.05, 0.07, True, ["target", "trail"]),
        tk("S2", "2018-05-02", 2, -0.5, 0.1, 0.04, 0.06, True, ["stop", "stop"]),
        tk("S3", "2019-07-03", 4, 0.0, -0.1, 0.0, 0.0, False, None),
        tk("S4", "2020-09-04", 6, 2.0, 0.3, 0.06, 0.09, True, ["target", "time"]),
        tk("S3", "2021-11-05", 8, 0.4, 0.0, 0.03, 0.045, True, ["time", "time"]),
        tk("S4", "2022-12-06", 9, -0.2, 0.1, 0.02, 0.03, True, ["stop", "trail"])]
A_K = [(k + 0.5) / 1000 for k in range(1000)]  # no A_k ties A = 0.35


class HandCase(unittest.TestCase):
    """Every reported number of the verdict, checked by hand on six tickets (b = 0.0041 with K = 1)."""

    def setUp(self):
        self.rep = ev.numbers(HAND, A_K, b=0.0041, blocks10=[t["block"] for t in HAND], refusals={"S1": {"x": 1}})

    def test_core_numbers(self):
        r = self.rep
        x = [0.75, -0.64, 0.1, 1.64, 0.37, -0.32]
        xbar = 1.9 / 6
        self.assertEqual(r["n"], 6)
        self.assertAlmostEqual(r["x_bar"], xbar)
        self.assertAlmostEqual(r["x_bar_minus_b"], xbar - 0.0041)
        d = [v - xbar for v in x]
        se10 = math.sqrt(10 / 9 * sum(v * v for v in d)) / 6  # one ticket per block: u_g = its deviation
        u5 = [d[0], d[1], d[2], d[3], d[4] + d[5]]  # long blocks 0, 1, 2, 3, 4 (blocks 8 and 9 share 4)
        se5 = math.sqrt(5 / 4 * sum(v * v for v in u5)) / 6
        self.assertAlmostEqual(r["se_t10"], se10)
        self.assertAlmostEqual(r["se_t5"], se5)
        self.assertAlmostEqual(r["A"], 2.1 / 6)
        self.assertAlmostEqual(r["A_950th"], 0.9495)
        self.assertAlmostEqual(r["p"], 651 / 1001)  # A_k >= 0.35: k = 350 .. 999 (650 of them)

    def test_gates(self):
        g = self.rep["gates"]
        xbar_b = 1.9 / 6 - 0.0041
        self.assertAlmostEqual(g["1"]["t10"], xbar_b / self.rep["se_t10"])
        self.assertAlmostEqual(g["1"]["t5"], xbar_b / self.rep["se_t5"])
        self.assertFalse(g["1"]["pass"])
        self.assertFalse(g["2"]["pass"])  # 0.35 is not above 0.949
        means = g["3"]["block_means"]
        self.assertEqual([None if v is None else round(v, 6) for v in means],
                         [0.75, None, -0.64, None, 0.1, None, 1.64, None, 0.37, -0.32])
        self.assertAlmostEqual(g["3"]["half_means"][0], 0.21 / 3)
        self.assertAlmostEqual(g["3"]["half_means"][1], 1.69 / 3)
        self.assertEqual((g["3"]["blocks_positive"], g["3"]["pass"]), (4, False))
        self.assertAlmostEqual(g["4"]["mean_without_top_1pct"], 0.26 / 5)  # ceil(6 / 100) = 1: 1.64 removed
        removed = g["4"]["mean_each_signal_removed"]
        for s, v in {"S1": 1.15 / 5, "S2": 2.54 / 5, "S3": 1.43 / 4, "S4": 0.58 / 4}.items():
            self.assertAlmostEqual(removed[s], v, msg=s)
        self.assertAlmostEqual(g["5"]["x_bar_minus_b"], 1.805 / 6 - 0.0041)  # stress costs: sum 0.295

    def test_reported_never_gated(self):
        r = self.rep["reported"]
        alone = r["each_signal_alone"]
        self.assertEqual((alone["S1"]["n"], alone["S3"]["n"]), (1, 2))
        self.assertAlmostEqual(alone["S3"]["x_bar"], 0.47 / 2)
        self.assertAlmostEqual(alone["S4"]["x_bar"], 1.32 / 2)
        self.assertEqual(set(r["calendar_years"]), {"2017", "2018", "2019", "2020", "2021", "2022"})
        self.assertAlmostEqual(r["calendar_years"]["2018"]["x_bar"], -0.64)
        self.assertEqual(r["filled_only"]["n"], 5)
        self.assertAlmostEqual(r["filled_only"]["x_bar"], 1.8 / 5)
        self.assertEqual(r["exit_types"], {"leg_a": {"target": 2, "stop": 2, "time": 1},
                                           "leg_b": {"trail": 2, "stop": 1, "time": 2}})
        cost = r["cost_distribution"]["all"]
        self.assertEqual((cost["p50"], cost["p75"], cost["p90"]), (0.04, 0.05, 0.06))
        self.assertAlmostEqual(cost["mean"], 0.04)
        self.assertEqual(r["refusals"], {"S1": {"x": 1}})


# ---------- end to end on synthetic bars ----------
def winner(k, rise):
    """Flat through S[k], then a steady move of `rise` a session (o, h, l, c)."""
    after = []
    for j in range(399 - k):
        o = 50.3 + rise * j
        c = o + 0.9 * rise
        after.append((o, max(o, c) + 0.2, min(o, c) - 0.2, c))
    return flat(n=k + 1, after=after)


def scenario(kind):
    """20 tickets, two a block over S[260] .. S[359]; S3 (breakout) and S4 (open) alternate. Pools: 25 flat symbols
    of the same decile (their tickets never move: gross 0)."""
    pools = {f"P{j:02d}": flat(n=400) for j in range(25)}
    ser, fired, deciles = dict(pools), [], {}
    for k in range(10):
        for slot, (off, sig) in enumerate(((2, "S3"), (6, "S4"))):
            i = 260 + 10 * k + off
            sym = f"W{2 * k + slot:02d}"
            if kind == "go":
                rise = 1.0 + 0.003 * k
            elif kind == "none":
                rise = 0.0
            else:  # "unconfirmable": up in the first five blocks, down in the last five
                rise = 1.0 if k < 5 else -1.0
            ser[sym] = winner(i, rise) if rise else flat(n=400)
            fired.append((S[i], sig, sym))
            members = sorted(list(pools) + [sym])
            deciles[S[i]] = (tuple(members), bytes([3] * len(members)))
    inp = inputs(ser, {s: [S[200]] for s in ser})
    got = {"firings": sorted(fired), "members": {}, "renamed": {}, "deciles": deciles, "warm_up": {},
           "diagnostics": {}}
    return inp, got


@contextlib.contextmanager
def verdict_env(kind, quote=None, K=None):
    inp, got = scenario(kind)
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(en, "prepare", return_value=(inp, got)))
        stack.enter_context(mock.patch.object(en, "check_r_s"))
        stack.enter_context(mock.patch.object(en.ec, "ended", return_value={}))
        stack.enter_context(mock.patch.object(en.alpaca, "quote_full",
                                              side_effect=quote or (lambda s, at: GOOD_QUOTE)))
        stack.enter_context(mock.patch.object(ed, "SIGNAL_START", S[260]))
        stack.enter_context(mock.patch.object(ed, "SIGNAL_END", S[359]))
        stack.enter_context(mock.patch.object(en, "head", return_value="c0ffee"))
        if K is not None:
            stack.enter_context(mock.patch.object(en.ec, "k_value", return_value=(K, None)))
        yield inp, got


def run_verdict(kind, quote=None, extra=(), fingerprint=None, blind_edit=None, K=None):
    """The blind run first (its report file, as the owner has it), then --verdict on the same inputs."""
    leaks = Leaks()
    with tempfile.TemporaryDirectory() as d, verdict_env(kind, quote, K) as (inp, _):
        blind_path = os.path.join(d, "blind", "ensemble-blind-report.json")
        os.makedirs(os.path.dirname(blind_path))
        with en.offline():
            rep = json.loads(blind.serialize(en.blind_report()))
        if blind_edit:
            blind_edit(rep)
        pathlib.Path(blind_path).write_text(json.dumps(rep), "utf-8")
        fp = ed.fingerprint(inp) if fingerprint is None else fingerprint
        old = os.getcwd()
        os.chdir(d)
        try:
            with leaks.watch():
                code = en.main(["--verdict", "--blind-commit", "c0ffee", "--owner-go-ahead", "--fingerprint", fp,
                                "--blind-report", blind_path, *extra])
        finally:
            os.chdir(old)
    files, content, cur = [], {}, None
    for w in leaks.writes:
        if w.startswith("[file "):
            cur = w[6:-1].replace("\\", "/")
            files.append(cur)
        elif cur:
            content[cur] = content.get(cur, "") + w
    return code, files, content, leaks


class EndToEnd(unittest.TestCase):
    def report(self, content):
        return json.loads(content[en.VERDICT_PATH])

    def test_planted_effect_is_go(self):
        code, files, content, _ = run_verdict("go")
        self.assertEqual(code, 0)
        rep = self.report(content)
        self.assertEqual(rep["outcome"], "GO", rep.get("gates"))
        self.assertEqual(rep["n"], 20)
        self.assertGreater(rep["x_bar"], 0)
        self.assertTrue(all(g["pass"] for g in rep["gates"].values()))
        rows = list(csv.DictReader(io.StringIO(content[en.TICKETS_PATH])))
        self.assertEqual(len(rows), 20)
        self.assertEqual(set(rows[0]), set(ev.CSV_FIELDS))
        md = next(v for k, v in content.items() if k.endswith(".md"))
        self.assertIn("עבר מבחן היסטורי, במעקב קדימה", md)
        self.assertIn("שער", md)

    def test_no_effect_is_no_go(self):
        code, _, content, _ = run_verdict("none")
        rep = self.report(content)
        self.assertEqual(rep["outcome"], "NO-GO")
        self.assertFalse(rep["gates"]["1"]["pass"])

    def test_failing_check_1_has_no_return_statistic(self):
        code, files, content, _ = run_verdict("unconfirmable")
        rep = self.report(content)
        self.assertEqual(rep["outcome"], "NO-GO (not confirmable)")
        self.assert_no_statistic(rep, files, content)

    def test_failing_comparability_has_no_return_statistic(self):
        bad = {S[262], S[266]}  # no usable quote on both firings of block 0: 10% > 5%, check 1 still passes
        quote = lambda s, at: None if at[:10] in bad else GOOD_QUOTE  # noqa: E731
        code, files, content, _ = run_verdict("go", quote=quote)
        rep = self.report(content)
        self.assertEqual(rep["outcome"], "NO-GO (not comparable)")
        self.assert_no_statistic(rep, files, content)

    def assert_no_statistic(self, rep, files, content):
        for key in ("x_bar", "x_bar_minus_b", "A", "A_950th", "p", "gates", "reported"):
            self.assertNotIn(key, rep)
        self.assertNotIn(en.TICKETS_PATH, files)  # no per-ticket results
        md = next(v for k, v in content.items() if k.endswith(".md"))
        self.assertIn("ללא יתרון מוכח", md)

    def test_verdict_writes_only_its_report_files(self):
        code, files, _, leaks = run_verdict("go")
        self.assertEqual(sorted(files), sorted([en.VERDICT_PATH, en.TICKETS_PATH, ev.summary_path(en.TODAY())]))
        self.assertEqual(leaks.logs, [])
        printed = leaks.out.getvalue().strip().splitlines()
        self.assertEqual(len(printed), 1)
        self.assertRegex(printed[0], r"^verdict written: ")

    def test_fingerprint_must_match(self):
        code, files, _, leaks = run_verdict("go", fingerprint="0000000000000000")
        self.assertEqual(code, 2)
        self.assertEqual(files, [])
        self.assertIn("fingerprint", leaks.out.getvalue())

    def test_fingerprint_is_required(self):
        code, files, _, leaks = run_verdict("go", fingerprint="")
        self.assertEqual(code, 2)
        self.assertEqual(files, [])
        self.assertIn("fingerprint", leaks.out.getvalue())

    def test_blind_report_must_match(self):
        """Inputs the fingerprint misses (the cached quotes) still change the blind report: the verdict recomputes it
        and refuses unless it equals the file the owner reviewed."""
        def tamper(rep):
            rep["pools_dropped_small"] += 1
        code, files, _, leaks = run_verdict("go", blind_edit=tamper)
        self.assertEqual(code, 2)
        self.assertEqual(files, [])
        self.assertIn("blind report", leaks.out.getvalue())
        out = io.StringIO()
        with mock.patch.object(en, "verdict_report") as run, mock.patch.object(en, "head", return_value="c0ffee"), \
                contextlib.redirect_stdout(out):  # --blind-report is required
            code = en.main(["--verdict", "--blind-commit", "c0ffee", "--owner-go-ahead", "--fingerprint", "f"])
        self.assertEqual(code, 2)
        self.assertIn("--blind-report", out.getvalue())
        run.assert_not_called()

    def test_b_above_data_limit_has_no_return_statistic(self):
        code, files, content, _ = run_verdict("go", K=8.0)  # b = 8 x 0.0041 = 0.0328 > 0.03
        rep = self.report(content)
        self.assertEqual(rep["outcome"], "NO-GO (data)")
        self.assert_no_statistic(rep, files, content)

    def test_degenerate_numbers_still_summarize(self):
        rep = {"outcome": "NO-GO", "inputs": "f", "commit": "c", "comparability": {"quote_share": 0.0,
               "small_pool_share": 0.0, "comparable": True},
               "check_1": {"pass": True, "mu_min": 0.0, "b_R": 0.0041, "se_t10": 0.0, "se_t5": 0.0}}
        rep.update(ev.numbers(HAND[:1], A_K, 0.0041, [0], {}))
        md = ev.summary_md(rep, "2026-10-11")  # n = 1: no mean without the top 1%; SE 0: t = inf
        self.assertIn("שער 4", md)
        self.assertIn("—", md)


class Workflow(unittest.TestCase):
    def test_verdict_workflow_is_dispatch_only_and_guarded(self):
        wf = (pathlib.Path(__file__).resolve().parent.parent / ".github/workflows/ensemble-verdict.yml").read_text("utf-8")
        on = wf[wf.index("on:"):wf.index("permissions:")]
        self.assertIn("workflow_dispatch:", on)
        for trigger in ("push", "schedule", "pull_request", "workflow_run"):
            self.assertNotIn(trigger, on)
        for name in ("blind_run_id", "blind_commit", "fingerprint", "confirm"):
            self.assertIn(f"{name}:", on)
        self.assertIn("refs/heads/ensemble-run-blind", wf)
        self.assertIn("fail-on-cache-miss: true", wf)
        self.assertIn("ensemble-inputs-${{ inputs.blind_run_id }}-4", wf)
        self.assertIn("--verdict", wf)
        self.assertIn("--fingerprint", wf)
        self.assertIn("environment: ensemble-verdict", wf)
        self.assertIn("concurrency:", wf)
        self.assertIn('test -n "$FINGERPRINT"', wf)
        self.assertIn("actions/download-artifact", wf)  # the blind report the owner reviewed
        self.assertIn("run-id: ${{ inputs.blind_run_id }}", wf)
        self.assertIn("--blind-report", wf)
        upload = wf[wf.index("uses: actions/upload-artifact"):]
        self.assertNotIn("ensemble-*.md", upload)  # the committed phase reports are not verdict files
        self.assertIn("docs/backtest/ensemble-2*.md", upload)
        lines, scripts = wf.splitlines(), []
        for i, line in enumerate(lines):  # every run: script with its indented continuation
            if line.strip().startswith("run:"):
                ind = len(line) - len(line.lstrip())
                block = [line]
                for nxt in lines[i + 1:]:
                    if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= ind:
                        break
                    block.append(nxt)
                scripts.append("\n".join(block))
        self.assertGreaterEqual(len(scripts), 2)
        for sc in scripts:
            self.assertNotIn("${{", sc)  # inputs reach shell steps only via env


if __name__ == "__main__":
    unittest.main()
