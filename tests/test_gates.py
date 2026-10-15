"""The verdict string comes from the gate file, not from a handwritten label."""
import json
import tempfile
import unittest
from pathlib import Path

from bot.gates import (
    CLOSED_COST, GO, NO_GO, PENDING, MARKER_END, MARKER_START, completed_tests,
    dsr_trials_next, load_gates, replication_ok, trend_verdict, verdict, write_trial_count,
)


class GateTests(unittest.TestCase):
    def test_closed_cost_needs_no_metrics(self):
        for trial_id in ("t3", "t3-mid", "t4", "t6", "t7", "t8", "t9"):
            self.assertEqual(verdict(trial_id), CLOSED_COST)

    def test_a_cagr_miss_is_no_go_once_replication_is_waived(self):
        # Published T10 pre-tax figures: 8.01% against SPY 10.92%.
        self.assertEqual(
            trend_verdict("faber-spy", 0.0801, 0.1092, 1.0, replicated=True), NO_GO,
        )
        self.assertEqual(trend_verdict("faber-spy", 0.0801, 0.1092, 1.0), PENDING)
        flags_ok_drawdown = 0.0801 >= 0.1092 - 0.01
        self.assertFalse(flags_ok_drawdown)

    def test_both_legs_pass_only_after_replication(self):
        self.assertEqual(trend_verdict("gem-agg", 0.10, 0.10, 0.80, replicated=True), GO)
        self.assertEqual(trend_verdict("gem-agg", 0.09, 0.10, 0.80, replicated=True), GO)
        self.assertEqual(trend_verdict("gem-agg", 0.089, 0.10, 0.80, replicated=True), NO_GO)
        self.assertEqual(trend_verdict("gem-agg", 0.10, 0.10, 0.80, replicated=True), GO)
        self.assertEqual(trend_verdict("gem-agg", 0.10, 0.10, 0.799, replicated=True), NO_GO)
        self.assertEqual(trend_verdict("gem-agg", 0.10, 0.10, 0.80, replicated=False), PENDING)

    def test_gem_bil_fails_when_the_early_window_fails(self):
        early = {"cagr": 0.0521, "spy_cagr": 0.1204, "boot_rate": 0.0}
        self.assertEqual(
            trend_verdict("gem-bil", 0.10, 0.10, 0.90, early=early, replicated=True), NO_GO,
        )
        self.assertEqual(trend_verdict("gem-bil", 0.10, 0.10, 0.90, replicated=True), NO_GO)
        good = {"cagr": 0.12, "spy_cagr": 0.12, "boot_rate": 0.80}
        self.assertEqual(
            trend_verdict("gem-bil", 0.12, 0.12, 0.80, early=good, replicated=True), GO,
        )

    def test_recorded_gem_replication_lets_the_real_verdict_through(self):
        self.assertTrue(replication_ok("gem-agg"))
        self.assertTrue(replication_ok("gem-bil"))
        self.assertFalse(replication_ok("faber-spy"))
        self.assertEqual(trend_verdict("gem-agg", 0.0648, 0.1124, 0.001), NO_GO)

    def test_the_counter_is_nine_and_the_next_sharpe_uses_eighteen(self):
        gates = load_gates()
        self.assertEqual(
            gates["completed_prior"],
            ["breakout", "noise", "penny", "swing", "sector", "vix"],
        )
        self.assertEqual(gates["completed_added"], ["faber", "gem", "gem-bil"])
        self.assertEqual(gates["go_count"], 0)
        self.assertEqual(completed_tests(), 9)
        self.assertEqual(dsr_trials_next(), 18)
        self.assertGreaterEqual(dsr_trials_next(), 18)

    def test_a_pending_verdict_still_increments_the_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            trials = Path(tmp) / "TRIALS.md"
            trials.write_text(
                f"before\n{MARKER_START}\nnot yet\n{MARKER_END}\nafter\n",
                encoding="utf-8",
            )
            count_path = Path(tmp) / "trial-count.json"
            got = write_trial_count(PENDING, trials_path=trials, count_path=count_path)
            self.assertEqual(got, 9)
            stored = json.loads(count_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["completed_tests"], 9)
            self.assertEqual(stored["effective_trials"], 9)
            self.assertEqual(stored["dsr_n_next"], 18)
            self.assertEqual(stored["go"], 0)
            self.assertEqual(stored["gem_agg_verdict"], PENDING)
            text = trials.read_text(encoding="utf-8")
            self.assertIn("**9**", text)
            self.assertIn("**18**", text)
            self.assertIn(PENDING, text)
            self.assertTrue(text.startswith("before\n"))
            self.assertTrue(text.endswith("after\n"))


if __name__ == "__main__":
    unittest.main()
