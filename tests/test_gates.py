"""The verdict string comes from the gate file, not from a handwritten label."""
import json
import tempfile
import unittest
from pathlib import Path

from bot.gates import (
    CLOSED_COST, GO, NO_GO, MARKER_END, MARKER_START, effective_trials, load_gates,
    trend_verdict, verdict, write_trial_count,
)


class GateTests(unittest.TestCase):
    def test_closed_cost_needs_no_metrics(self):
        for trial_id in ("t3", "t3-mid", "t4", "t6", "t7", "t8", "t9"):
            self.assertEqual(verdict(trial_id), CLOSED_COST)

    def test_a_cagr_miss_is_no_go_even_when_the_bootstrap_passes(self):
        # Published T10 pre-tax figures: 8.01% against SPY 10.92%.
        self.assertEqual(trend_verdict("faber-spy", 0.0801, 0.1092, 1.0), NO_GO)
        flags_ok_drawdown = 0.0801 >= 0.1092 - 0.01
        self.assertFalse(flags_ok_drawdown)

    def test_both_legs_pass(self):
        self.assertEqual(trend_verdict("gem-agg", 0.10, 0.10, 0.80), GO)
        self.assertEqual(trend_verdict("gem-agg", 0.09, 0.10, 0.80), GO)
        self.assertEqual(trend_verdict("gem-agg", 0.089, 0.10, 0.80), NO_GO)
        self.assertEqual(trend_verdict("gem-agg", 0.10, 0.10, 0.799), NO_GO)

    def test_gem_bil_fails_when_the_early_window_fails(self):
        early = {"cagr": 0.0521, "spy_cagr": 0.1204, "boot_rate": 0.0}
        self.assertEqual(
            trend_verdict("gem-bil", 0.10, 0.10, 0.90, early=early), NO_GO,
        )
        self.assertEqual(trend_verdict("gem-bil", 0.10, 0.10, 0.90), NO_GO)
        good = {"cagr": 0.12, "spy_cagr": 0.12, "boot_rate": 0.80}
        self.assertEqual(trend_verdict("gem-bil", 0.12, 0.12, 0.80, early=good), GO)

    def test_the_counter_stays_put_until_gem(self):
        gates = load_gates()
        self.assertEqual(
            gates["counted_before_faber_gem"],
            ["breakout", "noise", "penny", "swing", "sector"],
        )
        self.assertNotIn("vix", gates["counted_before_faber_gem"])
        self.assertEqual(gates["increment_after"], "gem-agg")
        self.assertTrue(gates["faber_gem_is_one_trial"])
        before = len(gates["counted_before_faber_gem"])
        self.assertEqual(effective_trials(False), before)
        self.assertEqual(effective_trials(True), before + 1)
        self.assertEqual(before, 5)

    def test_the_count_file_is_written_from_the_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            trials = Path(tmp) / "TRIALS.md"
            trials.write_text(
                f"before\n{MARKER_START}\nnot yet\n{MARKER_END}\nafter\n",
                encoding="utf-8",
            )
            count_path = Path(tmp) / "trial-count.json"
            got = write_trial_count(NO_GO, trials_path=trials, count_path=count_path)
            self.assertEqual(got, 6)
            stored = json.loads(count_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["effective_trials"], 6)
            self.assertEqual(stored["gem_agg_verdict"], NO_GO)
            text = trials.read_text(encoding="utf-8")
            self.assertIn("**6**", text)
            self.assertIn(NO_GO, text)
            self.assertTrue(text.startswith("before\n"))
            self.assertTrue(text.endswith("after\n"))


if __name__ == "__main__":
    unittest.main()
