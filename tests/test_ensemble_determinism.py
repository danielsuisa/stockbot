"""The verdict must reproduce the blind report in another process, possibly on another runner image (6.4 point 4).
SYNTHETIC DATA ONLY: tests.determinism_probe (the universe with the rename rule, the detectors and the blind report)
run in fresh processes under different PYTHONHASHSEED values must print identical bytes. bot/signals.py iterates sets
(the rename pool, the symbols of a CIK), so this is tested, not assumed."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDS = ("0", "1", "4242")


def run(code_args, seed):
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONUTF8": "1", "SEC_UA": ""}
    return subprocess.run([sys.executable, *code_args], cwd=ROOT, env=env, capture_output=True, timeout=600)


class HashSeed(unittest.TestCase):
    def test_the_seeds_take_effect(self):
        got = {run(["-c", "print(hash('ensemble'))"], s).stdout for s in SEEDS}
        self.assertEqual(len(got), len(SEEDS))  # string hashes differ: set order can differ

    def test_blind_report_is_identical_under_any_hash_seed(self):
        outs = []
        for s in SEEDS:
            p = run(["-m", "tests.determinism_probe"], s)
            self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace")[-2000:])
            outs.append(p.stdout)
        self.assertIn(b"blind_report", outs[0])
        for s, o in zip(SEEDS[1:], outs[1:]):
            self.assertEqual(o, outs[0], f"PYTHONHASHSEED={s} printed other bytes than PYTHONHASHSEED={SEEDS[0]}")


class Workflows(unittest.TestCase):
    def test_blind_and_verdict_jobs_pin_the_hash_seed_and_the_image(self):
        for name in ("ensemble.yml", "ensemble-verdict.yml"):
            with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as fh:
                wf = fh.read()
            env = wf[wf.index("\nenv:"):wf.index("\njobs:")]
            self.assertIn('PYTHONHASHSEED: "0"', env, name)
            self.assertNotIn("ubuntu-latest", wf, name)
            self.assertEqual(wf.count("runs-on:"), wf.count("runs-on: ubuntu-24.04"), name)


if __name__ == "__main__":
    unittest.main()
