"""Stage 2 replay: isolation (no clock, no network), total order, cross-process determinism, sensitivity,
live/historical equivalence. Synthetic data in a temp root; tests/test_replay_days.py covers real days."""
import ast
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import polars as pl

from scalp_engine import replay, storage
from scalp_engine.live_source import LiveSource

PACKAGE = Path(__file__).resolve().parent.parent
REPO = PACKAGE.parent
UTC = dt.timezone.utc
DAY = dt.date(2024, 6, 12)

# Runs one replay in a fresh interpreter with every socket entry point disabled.
RUNNER = (
    "import socket, sys\n"
    "def _blocked(*a, **k):\n"
    "    raise OSError('network disabled in replay test')\n"
    "socket.socket = _blocked; socket.create_connection = _blocked; socket.getaddrinfo = _blocked\n"
    "from scalp_engine import replay\n"
    "sys.exit(replay.main(sys.argv[1:]))\n")


def replay_in_subprocess(root, day, hashseed):
    env = dict(os.environ, PYTHONHASHSEED=str(hashseed), SCALP_DATA_ROOT=str(root))
    out = subprocess.run([sys.executable, "-c", RUNNER, day.isoformat(), "--root", str(root)], cwd=REPO, env=env,
                         check=True, capture_output=True, text=True, timeout=1800)
    return json.loads(out.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- static isolation

BANNED_IMPORTS = {"socket", "ssl", "http", "urllib", "requests", "asyncio", "websocket", "websockets", "aiohttp",
                  "ftplib", "smtplib", "time", "random", "secrets", "uuid"}
CLOCK_CALLS = {"now", "utcnow", "today"}


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def violations(source):
    """[(line, what)] for clock reads and network / clock / randomness imports in source."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out += [(node.lineno, f"import {a.name}") for a in node.names if a.name.split(".")[0] in BANNED_IMPORTS]
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in BANNED_IMPORTS:
            out.append((node.lineno, f"from {node.module}"))
        elif isinstance(node, ast.ImportFrom) and node.module == "datetime":
            pass
        elif isinstance(node, ast.Call):
            name = _dotted(node.func) or ""
            if name.rsplit(".", 1)[-1] in CLOCK_CALLS:
                out.append((node.lineno, f"clock call {name}"))
    return out


def engine_imports(source):
    mods = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("scalp_engine"):
            mods.add(node.module if node.module != "scalp_engine" else None)
            if node.module == "scalp_engine":
                mods |= {f"scalp_engine.{a.name}" for a in node.names}
        elif isinstance(node, ast.Import):
            mods |= {a.name for a in node.names if a.name.startswith("scalp_engine.")}
    return {m for m in mods if m}


def closure(roots):
    seen, todo = set(), list(roots)
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        path = REPO / (mod.replace(".", "/") + ".py")
        todo += engine_imports(path.read_text(encoding="utf-8"))
    return sorted(seen)


class Isolation(unittest.TestCase):
    def test_checker_catches_clock_and_network(self):
        bad = ["import datetime as dt\ndt.datetime.now()", "from datetime import datetime\ndatetime.utcnow()",
               "import datetime\ndatetime.date.today()", "import time\ntime.time()", "import socket",
               "from urllib.request import urlopen", "import random"]
        for src in bad:
            with self.subTest(src=src):
                self.assertTrue(violations(src))
        self.assertEqual(violations("import datetime as dt\ndt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)"), [])

    def test_replay_closure_reads_no_clock_and_no_network(self):
        mods = closure(["scalp_engine.replay", "scalp_engine.live_source"])
        self.assertIn("scalp_engine.data_access", mods)
        for forbidden in ("scalp_engine.ingest", "scalp_engine.alpaca_client", "scalp_engine.storage"):
            self.assertNotIn(forbidden, mods)
        found = []
        for mod in mods:
            path = REPO / (mod.replace(".", "/") + ".py")
            found += [f"{mod}:{line} {what}" for line, what in violations(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [])


# ---------------------------------------------------------------- synthetic store

def build_day(root, close_shift=0.0, extra_part=None):
    """Synthetic calendar, actions, bars (SIP + IEX) and quote samples for DAY under root."""
    with mock.patch.dict(os.environ, {"SCALP_DATA_ROOT": str(root)}):
        ing = dt.datetime(2026, 10, 17, tzinfo=UTC)
        open_at = dt.datetime(2024, 6, 12, 13, 30, tzinfo=UTC)
        storage.write_partition("calendar", storage.frame("calendar", [{
            "event_at": dt.datetime(2024, 6, 12, 4, tzinfo=UTC), "trading_date": DAY,
            "session_open_at": dt.datetime(2024, 6, 12, 8, tzinfo=UTC), "open_at": open_at,
            "close_at": dt.datetime(2024, 6, 12, 20, tzinfo=UTC), "session_close_at": dt.datetime(2024, 6, 13, tzinfo=UTC),
            "early_close": False, "settlement_date": None, "feed": "none", "request_id": "r", "ingested_at": ing,
            "source": "synthetic", "schema_version": 1}]), part="c", source="synthetic", date=DAY)
        storage.write_partition("corporate_actions", storage.frame("corporate_actions", [{
            "event_at": dt.datetime(2024, 6, 12, 4, tzinfo=UTC), "action_type": "forward_splits", "symbol": "BBB",
            "new_symbol": None, "ratio_new": 2, "ratio_old": 1, "cash_amount": None, "declared_at": None,
            "vendor_id": "v1", "process_date": DAY, "raw": "{}", "feed": "none", "request_id": "r", "ingested_at": ing,
            "source": "synthetic", "schema_version": 2}]), part="a", source="synthetic", date=DAY)
        rows = []
        for feed in ("sip", "iex"):
            for sym in ("BBB", "AAA"):  # deliberately not in sorted order
                for i in (2, 0, 1):
                    t = open_at + dt.timedelta(minutes=i)
                    c = 10.0 + i + (close_shift if (feed, sym, i) == ("sip", "AAA", 1) else 0.0)
                    rows.append({"event_at": t, "symbol": sym, "open": c, "high": c + 0.1, "low": c - 0.1, "close": c,
                                 "volume": 100 * (i + 1), "vwap": c, "trade_count": i + 1, "feed": feed,
                                 "adjustment": "raw", "asof": "-", "request_id": "r", "ingested_at": ing,
                                 "source": "synthetic", "schema_version": 2})
        df = storage.frame("raw_bars_1m", rows)
        for feed in ("sip", "iex"):
            storage.write_partition("raw_bars_1m", df.filter(pl.col("feed") == feed), part=feed, source="synthetic",
                                    feed=feed, date=DAY)
        if extra_part is not None:
            storage.write_partition("raw_bars_1m", extra_part, part="sip-reingest", source="synthetic", feed="sip", date=DAY)
        storage.write_partition("raw_quote_samples", storage.frame("raw_quote_samples", [{
            "event_at": open_at + dt.timedelta(minutes=5), "symbol": s, "status": "ok",
            "quote_at": open_at + dt.timedelta(minutes=5, seconds=-1), "bid_price": 10.0, "bid_size": 3,
            "ask_price": 10.02, "ask_size": 4, "bid_exchange": "Q", "ask_exchange": "N", "conditions": ["R"],
            "tape": "C", "size_unit": "round_lot", "lookback_ms": 5000, "feed": "sip", "request_id": "r",
            "ingested_at": ing, "source": "synthetic", "schema_version": 1} for s in ("BBB", "AAA")]),
            part="q", source="synthetic", feed="sip", date=DAY)


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build_day(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_total_order_and_available_at(self):
        ev = replay.HistoricalSource(self.root).events(DAY)
        keys = [(e.available_at, e.priority, e.symbol, e.feed, e.seq) for e in ev]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual([e.seq for e in ev], list(range(len(ev))))
        bar = next(e for e in ev if e.kind == "bar")
        self.assertEqual(bar.available_at - json.loads(bar.payload)["event_at"], 62_000_000)  # +60 s +2 s
        self.assertEqual(ev[0].kind, "corporate_action")  # 00:00 ET precedes the 04:00 session open
        self.assertEqual(len(ev), 4 + 1 + 12 + 2)

    def test_hash_identical_across_processes_and_hash_seeds(self):
        a = replay_in_subprocess(self.root, DAY, 0)
        b = replay_in_subprocess(self.root, DAY, 4242)
        self.assertEqual(a, b)
        self.assertEqual(a["sha256"], replay.day_hash(self.root, DAY)[0])

    def test_one_changed_row_changes_the_hash(self):
        with tempfile.TemporaryDirectory() as other:
            build_day(Path(other), close_shift=0.01)
            self.assertNotEqual(replay.day_hash(self.root, DAY)[0], replay.day_hash(Path(other), DAY)[0])

    def test_as_of_pins_a_replay_against_later_reingestion(self):
        with tempfile.TemporaryDirectory() as other:
            later = dt.datetime(2026, 12, 1, tzinfo=UTC)
            with mock.patch.dict(os.environ, {"SCALP_DATA_ROOT": other}):
                fix = storage.frame("raw_bars_1m", [{
                    "event_at": dt.datetime(2024, 6, 12, 13, 31, tzinfo=UTC), "symbol": "AAA", "open": 99.0,
                    "high": 99.0, "low": 99.0, "close": 99.0, "volume": 1, "vwap": 99.0, "trade_count": 1, "feed": "sip",
                    "adjustment": "raw", "asof": "-", "request_id": "r2", "ingested_at": later, "source": "synthetic",
                    "schema_version": 2}])
            build_day(Path(other), extra_part=fix)
            pinned = replay.day_hash(Path(other), DAY, as_of=dt.datetime(2026, 11, 1, tzinfo=UTC))[0]
            self.assertEqual(pinned, replay.day_hash(self.root, DAY)[0])
            self.assertNotEqual(replay.day_hash(Path(other), DAY)[0], pinned)  # latest ingestion wins without as_of

    def test_live_source_matches_historical_for_the_same_bars(self):
        msgs = []
        for i in (0, 1, 2):
            t = dt.datetime(2024, 6, 12, 13, 30 + i, tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            for sym in ("AAA", "BBB"):
                c = 10.0 + i
                msgs.append({"T": "b", "S": sym, "o": c, "h": c + 0.1, "l": c - 0.1, "c": c, "v": 100 * (i + 1),
                             "t": t, "n": i + 1, "vw": c})
        live = LiveSource([[{"T": "success", "msg": "authenticated"}], list(reversed(msgs))]).events(DAY)
        hist = [e for e in replay.HistoricalSource(self.root, feeds=("iex",)).events(DAY) if e.kind == "bar"]
        strip = lambda evs: [(e.available_at, e.symbol, e.feed, e.payload) for e in evs]
        self.assertEqual(strip(live), strip(hist))


if __name__ == "__main__":
    unittest.main()
