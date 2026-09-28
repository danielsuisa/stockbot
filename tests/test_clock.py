"""The listener's clock: GitHub starts this repo's scheduled runs hours late, so the always-on listener starts the
timed runs itself by dispatch, at their New York (sessions) or UTC (morning scan) times."""
import datetime as dt
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from bot import clock, common, sessions
from test_sessions import day

UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parents[1]
ACTIONS = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t"}


def utc(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


def ticks(start, end):
    """A listener ticking every minute from start to end (UTC) -> [(UTC 'MM-DD HH:MM', key, workflow, mode)]."""
    done, out, t = set(), [], utc(start)
    while t < utc(end):
        for key, wf, inputs in clock.due(t, done):
            done.add(key)
            out.append((f"{t:%m-%d %H:%M}", key, wf, inputs["mode"]))
        t += dt.timedelta(minutes=1)
    return out


class Due(unittest.TestCase):
    def test_a_summer_monday(self):
        got = {(t, k) for t, k, _, _ in ticks("2026-09-28T00:00", "2026-09-29T06:00")}
        want = {("09-28 08:15", "squeeze:pre0:2026-09-28")}
        for utc_t, slot in (("11:30", "pre1"), ("13:15", "pre2"), ("20:30", "post1"), ("23:30", "post2")):
            want |= {(f"09-28 {utc_t}", f"squeeze:{slot}:2026-09-28"), (f"09-28 {utc_t}", f"insider:{slot}:2026-09-28")}
        want.add(("09-29 05:30", "scan:morning:2026-09-29"))  # Tuesday's morning scan of Monday's filings
        self.assertEqual(got, want)

    def test_a_winter_tuesday_and_the_after_midnight_1930_run(self):
        got = {(t, k) for t, k, _, _ in ticks("2026-12-01T00:00", "2026-12-02T01:00")}
        want = {("12-01 00:30", "squeeze:post2:2026-11-30"), ("12-01 00:30", "insider:post2:2026-11-30"),
                ("12-01 05:30", "scan:morning:2026-12-01"), ("12-01 09:15", "squeeze:pre0:2026-12-01")}
        for utc_t, slot in (("12-01 12:30", "pre1"), ("12-01 14:15", "pre2"), ("12-01 21:30", "post1"),
                            ("12-02 00:30", "post2")):
            want |= {(utc_t, f"squeeze:{slot}:2026-12-01"), (utc_t, f"insider:{slot}:2026-12-01")}
        self.assertEqual(got, want)

    def test_every_started_slot_is_the_slot_its_run_serves(self):
        for start, end, d in (("2026-09-28T00:00", "2026-09-29T00:00", "2026-09-28"),
                              ("2026-12-01T06:00", "2026-12-02T06:00", "2026-12-01")):
            st = day(d, nxt="x")
            for t, key, wf, mode in ticks(start, end):
                if key.startswith("scan:"):
                    self.assertEqual((wf, mode), ("daily-scan.yml", "morning"))
                    continue
                name, slot, _ = key.split(":")
                now = utc(f"{end[:4]}-{t[:5]}T{t[6:]}").astimezone(sessions.NY)
                self.assertEqual(sessions.slot(now, st), slot, key)
                self.assertEqual((wf, mode), {"squeeze": ("squeeze.yml", "slot"),
                                              "insider": ("daily-scan.yml", "intraday")}[name])

    def test_weekends_only_the_saturday_morning_scan(self):
        # from Saturday 01:00 UTC: Friday's 19:30 New York run (23:30 UTC) is over an hour old by then
        self.assertEqual([k for _, k, _, _ in ticks("2026-10-03T01:00", "2026-10-05T00:00")],
                         ["scan:morning:2026-10-03"])

    def test_a_late_listener_catches_up_within_the_hour_and_never_twice(self):
        self.assertEqual([k for k, _, _ in clock.due(utc("2026-09-28T09:15"), set())], ["squeeze:pre0:2026-09-28"])
        self.assertEqual(clock.due(utc("2026-09-28T09:16"), set()), [])
        self.assertEqual(clock.due(utc("2026-09-28T08:20"), {"squeeze:pre0:2026-09-28"}), [])


class Tick(unittest.TestCase):
    def test_starts_each_job_once(self):
        done = set()
        with mock.patch.dict(os.environ, ACTIONS), mock.patch.object(common, "dispatch", return_value="") as d, \
                mock.patch("builtins.print"):
            self.assertEqual(clock.tick(done, utc("2026-09-28T08:16")), ["squeeze:pre0:2026-09-28"])
            self.assertEqual(clock.tick(done, utc("2026-09-28T08:17")), [])
        d.assert_called_once_with("squeeze.yml", {"mode": "slot"})

    def test_a_failed_start_is_retried_every_tick_and_reported_once(self):
        done, results = set(), iter(["HTTP 500", "HTTP 500", "HTTP 500", "HTTP 500", "", ""])
        with mock.patch.dict(os.environ, ACTIONS), \
                mock.patch.object(common, "dispatch", side_effect=lambda wf, i: next(results)) as d, \
                mock.patch.object(common, "send") as send, mock.patch("builtins.print"):
            self.assertEqual(clock.tick(done, utc("2026-09-28T11:31")), [])
            self.assertEqual(clock.tick(done, utc("2026-09-28T11:32")), [])
            self.assertEqual(len(clock.tick(done, utc("2026-09-28T11:33"))), 2)
        self.assertEqual(d.call_count, 6)
        send.assert_called_once()
        text = send.call_args[0][0]
        for part in ("squeeze:pre1:2026-09-28", "insider:pre1:2026-09-28", "HTTP 500"):
            self.assertIn(part, text)
        self.assertEqual(common.rtl_bad_lines(text), [])

    def test_does_nothing_off_actions(self):
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "", "GITHUB_TOKEN": ""}), \
                mock.patch.object(common, "dispatch") as d:
            self.assertEqual(clock.tick(set(), utc("2026-09-28T08:16")), [])
        d.assert_not_called()


class Workflows(unittest.TestCase):
    """GitHub's schedules start this repo's runs 3.5-5.5 h late (measured 2026-09-25..28): timed runs are
    dispatched by the clock, and every mode it sends is one the workflow accepts."""
    def text(self, name):
        return (ROOT / ".github" / "workflows" / name).read_text()

    def test_timed_runs_are_dispatched_not_scheduled(self):
        self.assertIsNone(re.search(r"^\s+schedule:", self.text("squeeze.yml"), re.M))
        self.assertEqual(re.findall(r'cron: "([^"]+)"', self.text("daily-scan.yml")), ["0 7 * * 0"])  # weekly only
        for _, _, _, _, wf, inputs in clock.JOBS:
            options = re.search(r"mode:.*?options: \[([^\]]*)\]", self.text(wf), re.S).group(1)
            self.assertIn(inputs["mode"], [o.strip() for o in options.split(",")], wf)


if __name__ == "__main__":
    unittest.main()
