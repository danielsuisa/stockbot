"""Determinism probe (SYNTHETIC prices only): the index program's blind and verdict reports on a planted path, in a
fresh process, as canonical JSON. tests.test_index_determinism runs it under different PYTHONHASHSEED values.

    python -m tests.index_determinism_probe"""
import json
import os
import sys
from unittest import mock

if os.environ.get("ENSEMBLE_PROBE_PRELUDE"):  # the local runner's time-zone stub (CI has the real tz database)
    exec(open(os.environ["ENSEMBLE_PROBE_PRELUDE"], encoding="utf-8").read())

from bot import index_run as ir  # noqa: E402
from tests import index_synth as syn  # noqa: E402
from tests.test_index_run import SHORT  # noqa: E402


def main():
    rows = syn.rows("2005-02-25", "2008-12-31", syn.crash(at=740, length=80, depth=0.45, g=0.0006))
    with mock.patch.multiple(ir, **SHORT):
        out = {"blind": ir.blind_report(rows, "f" * 16), "verdict": ir.verdict_report(rows, syn.cash(rows), "f" * 16)}
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
