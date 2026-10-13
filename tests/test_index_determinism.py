"""The index program's verdict must reproduce the blind report in another process (E5): the synthetic blind and verdict
reports printed by tests.index_determinism_probe are identical under different PYTHONHASHSEED values."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDS = ("0", "1", "4242")


class HashSeed(unittest.TestCase):
    def test_reports_identical_under_any_hash_seed(self):
        outs = []
        for s in SEEDS:
            env = {**os.environ, "PYTHONHASHSEED": s, "PYTHONUTF8": "1", "SEC_UA": ""}
            p = subprocess.run([sys.executable, "-m", "tests.index_determinism_probe"], cwd=ROOT, env=env,
                               capture_output=True, timeout=600)
            self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace")[-2000:])
            outs.append(p.stdout)
        self.assertIn(b'"verdict"', outs[0])
        for s, o in zip(SEEDS[1:], outs[1:]):
            self.assertEqual(o, outs[0], f"PYTHONHASHSEED={s} printed other bytes")


if __name__ == "__main__":
    unittest.main()
