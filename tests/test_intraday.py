"""Same-day insider runs at the session slots (extended-hours spec, section 4)."""
import datetime as dt
import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from bot import common, market, scan, sessions
from test_bot import NO_ENRICH, buy
from test_sessions import day, ny

TODAY = dt.date(2026, 9, 28)


def cluster():
    return [buy(f"a{k}", 1, who, 50_000, last="2026-09-28") for k, who in enumerate(("x", "y", "z"), 1)]


class ScanDayUrls(unittest.TestCase):
    def test_only_unread_filings_are_fetched_and_the_day_is_not_marked(self):
        state = scan.prune({"seen": {"20260928": ["a1"]}}, TODAY)
        with mock.patch.object(scan, "_get", return_value=None) as get, \
                mock.patch.object(common, "cik_tickers", return_value={}), mock.patch("builtins.print"):
            scan.scan_day("20260928", state, TODAY, rescan=True, urls={"a1": "u1", "a2": "u2"})
        get.assert_called_once_with("u2")
        self.assertEqual(state["days"], {})
        self.assertEqual(state["seen"]["20260928"], ["a1", "a2"])

    def test_prune_keeps_todays_read_filings(self):
        state = scan.prune({"seen": {"20260928": ["a1"], "20260801": ["old"]}}, TODAY)
        self.assertEqual(state["seen"], {"20260928": ["a1"]})


class Intraday(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "state.json")
        self.write({"buys": cluster(), "regime": {"tag": "normal"}})
        mocks = {}
        for name, p in (("env", mock.patch.dict(os.environ, {"STATE_FILE": self.path})),
                        ("status", mock.patch.object(sessions, "status", return_value=day())),
                        ("efts", mock.patch.object(scan, "efts_urls", return_value={})),
                        ("scan_day", mock.patch.object(scan, "scan_day", return_value="ok")),
                        ("foreign", mock.patch.object(scan, "foreign", return_value=False)),
                        ("links", mock.patch.object(scan, "links", return_value={})),
                        ("regime", mock.patch.object(market, "regime", return_value={"tag": "normal"})),
                        ("enrich", NO_ENRICH), ("print", mock.patch("builtins.print")),
                        ("send", mock.patch.object(common, "send"))):
            mocks[name] = p.start()
            self.addCleanup(p.stop)
        self.send, self.efts = mocks["send"], mocks["efts"]

    def write(self, st):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(st, fh)

    def state(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_alerts_same_day_once_and_the_morning_scan_does_not_repeat(self):
        scan.intraday(ny("2026-09-28T16:35"))
        text = self.send.call_args[0][0]
        self.assertIn("מהיום", text)
        self.assertEqual(common.rtl_bad_lines(text), [])
        st = self.state()
        self.assertEqual((st["days"], st["slots"], st["intraday"]["slot"]), ({}, {"2026-09-28": ["post1"]}, "post1"))
        self.assertEqual(sorted(st["alerted"]["1"]), ["a1", "a2", "a3"])
        scan.intraday(ny("2026-09-28T17:30"))  # the other daylight-saving twin of the same slot
        self.assertEqual((self.send.call_count, self.efts.call_count), (1, 1))
        self.assertEqual(scan.alerts(scan.load(Path(self.path)), TODAY), [])  # the morning scan: nothing to repeat

    def test_nothing_new_sends_nothing(self):
        self.write({"buys": cluster(), "alerted": {"1": ["a1", "a2", "a3"]}})
        scan.intraday(ny("2026-09-28T19:40"))
        self.send.assert_not_called()
        self.assertEqual(self.state()["slots"], {"2026-09-28": ["post2"]})

    def test_outside_a_slot_does_nothing(self):
        scan.intraday(ny("2026-09-28T12:00"))
        self.efts.assert_not_called()
        self.assertNotIn("slots", self.state())

    def test_sec_search_down_warns_once_per_session(self):
        self.efts.side_effect = OSError("down")
        scan.intraday(ny("2026-09-28T16:35"))
        scan.intraday(ny("2026-09-28T19:35"))
        self.assertEqual(self.send.call_count, 1)
        self.assertIn("SEC", self.send.call_args[0][0])
        self.assertEqual(common.rtl_bad_lines(self.send.call_args[0][0]), [])


class Mode(unittest.TestCase):
    def test_from_flags_and_schedule(self):
        a = Namespace(followup=False, intraday=False)
        for env, want in (({}, "scan"), ({"SCHEDULE": "30 5 * * 2-6"}, "scan"), ({"SCHEDULE": "0 7 * * 0"}, "followup"),
                          ({"SCHEDULE": "30 20 * * 1-5"}, "intraday"), ({"SCAN_MODE": "followup"}, "followup")):
            with mock.patch.dict(os.environ, {"SCHEDULE": "", "SCAN_MODE": "", **env}):
                self.assertEqual(scan.mode(a), want, env)
        self.assertEqual(scan.mode(Namespace(followup=False, intraday=True)), "intraday")


if __name__ == "__main__":
    unittest.main()
