"""Index program tasks 7 and 8 (Amendment 6, F3): the blind and verdict workflows run on a push to their own branch
only, never on pull_request; the verdict waits in the owner's environment, refuses a second run and needs the blind
report; secrets reach the shell only through env; the artifacts hold the reports only."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as fh:
        return fh.read()


def run_blocks(wf):
    lines, out = wf.splitlines(), []
    for i, line in enumerate(lines):
        if line.strip().startswith("run:"):
            ind = len(line) - len(line.lstrip())
            block = [line]
            for nxt in lines[i + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= ind:
                    break
                block.append(nxt)
            out.append("\n".join(block))
    return out


class Common(unittest.TestCase):
    def check(self, name, branch):
        wf = read(name)
        on = wf[wf.index("\non:"):wf.index("\npermissions:")]
        self.assertIn(f"branches: [{branch}]", on)
        for trigger in ("pull_request", "schedule", "workflow_dispatch", "workflow_run"):
            self.assertNotIn(trigger, on)
        self.assertIn('PYTHONHASHSEED: "0"', wf)
        self.assertNotIn("ubuntu-latest", wf)
        self.assertIn("persist-credentials: false", wf)
        for block in run_blocks(wf):
            self.assertNotIn("${{", block)  # secrets and contexts reach the shell only through env
        self.assertIn("DATA_REPO_KEY: ${{ secrets.DATA_REPO_KEY }}", wf)
        self.assertIn('rm -f "$RUNNER_TEMP/data_key"', wf)
        self.assertNotIn("TIINGO", wf)
        return wf

    def test_blind(self):
        wf = self.check("index-blind.yml", "index-run-blind")
        self.assertIn("--blind", wf)
        self.assertEqual(wf.count("actions/upload-artifact"), 1)
        self.assertIn("path: index-blind-report.json", wf)
        self.assertNotIn("environment:", wf)

    def test_verdict(self):
        wf = self.check("index-verdict.yml", "index-run-verdict")
        self.assertIn("environment: index-verdict", wf)  # F3: the owner's approval of the waiting run
        self.assertIn("concurrency:", wf)
        self.assertIn("ls docs/backtest/index-verdict-*.json", wf)  # F3: no second verdict
        self.assertIn("--blind-report", wf)
        self.assertIn("gh run download", wf)
        self.assertIn("--commit \"$SHA\" --owner-go-ahead", wf)
        upload = wf[wf.index("actions/upload-artifact"):wf.index("Remove the private checkout")]
        self.assertIn("docs/backtest/index-verdict-*.json", upload)
        self.assertIn("docs/backtest/index-2*.md", upload)
        self.assertNotRegex(upload, re.compile(r"spy_us_d|private"))


if __name__ == "__main__":
    unittest.main()
