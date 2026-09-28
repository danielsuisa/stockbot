"""Session slots for the extended-hours runs: New York time, daylight-saving twins, holidays, early closes."""
import datetime as dt
import json
import unittest
from unittest import mock

from bot import sessions

NY = sessions.NY


def ny(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=NY)


def day(d="2026-09-28", close="16:00", ah="20:00", business=True, nxt="2026-09-29"):
    at = lambda t: dt.datetime.fromisoformat(f"{d}T{t}")  # noqa: E731
    return {"business": business, "pre": (at("04:00"), at("09:30")), "regular": (at("09:30"), at(close)),
            "post": (at(close), at(ah)), "next": nxt}


MARKET_INFO = {"data": {"isBusinessDay": True, "pmOpenRaw": "2026-09-28T04:00:00", "openRaw": "2026-09-28T09:30:00",
                        "closeRaw": "2026-09-28T16:00:00", "ahCloseRaw": "2026-09-28T20:00:00",
                        "nextTradeDate": "Sep 29, 2026"}}


class Slot(unittest.TestCase):
    def test_each_slot_on_time_and_late(self):
        for t, want in (("07:30", "pre1"), ("08:59", "pre1"), ("09:15", "pre2"), ("16:30", "post1"),
                        ("17:59", "post1"), ("19:30", "post2")):
            self.assertEqual(sessions.slot(ny(f"2026-09-28T{t}"), day()), want, t)

    def test_too_early_too_late_or_outside_the_session(self):
        for t in ("07:29", "09:01", "18:01", "20:00", "12:00"):
            self.assertIsNone(sessions.slot(ny(f"2026-09-28T{t}"), day()), t)

    def test_daylight_saving_twins(self):
        summer, winter = day("2026-09-28"), day("2026-12-01", nxt="2026-12-02")
        utc = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).astimezone(NY)  # noqa: E731
        self.assertEqual(sessions.slot(utc("2026-09-28T11:30"), summer), "pre1")
        self.assertIsNone(sessions.slot(utc("2026-12-01T11:30"), winter))  # 06:30 EST
        self.assertEqual(sessions.slot(utc("2026-12-01T12:30"), winter), "pre1")
        self.assertEqual(sessions.slot(utc("2026-09-28T23:30"), summer), "post2")
        self.assertIsNone(sessions.slot(utc("2026-09-29T00:30"), summer))  # 20:30 EDT: after-hours is over
        self.assertEqual(sessions.slot(utc("2026-12-02T00:30"), winter), "post2")  # 19:30 EST, the next UTC day

    def test_holiday_early_close_and_no_status(self):
        self.assertIsNone(sessions.slot(ny("2026-11-26T07:30"), day("2026-11-26", business=False)))
        early = day("2026-11-27", close="13:00", ah="17:00")
        self.assertEqual(sessions.slot(ny("2026-11-27T16:30"), early), "post1")
        self.assertIsNone(sessions.slot(ny("2026-11-27T19:30"), early))
        self.assertIsNone(sessions.slot(ny("2026-09-28T07:30"), None))


class Claim(unittest.TestCase):
    def test_a_slot_runs_once_per_day_and_old_days_are_dropped(self):
        state = {"slots": {"2026-09-01": ["pre1"]}}
        self.assertTrue(sessions.claim(state, "pre1", "2026-09-28"))
        self.assertFalse(sessions.claim(state, "pre1", "2026-09-28"))  # e.g. the other daylight-saving twin
        self.assertTrue(sessions.claim(state, "post1", "2026-09-28"))
        self.assertEqual(state["slots"], {"2026-09-28": ["pre1", "post1"]})

    def test_first_warning_per_key_and_day(self):
        state = {}
        self.assertTrue(sessions.first(state, "efts:post", "2026-09-28"))
        self.assertFalse(sessions.first(state, "efts:post", "2026-09-28"))
        self.assertTrue(sessions.first(state, "efts:pre", "2026-09-28"))


class Status(unittest.TestCase):
    def test_parses_nasdaq_market_info(self):
        with mock.patch.object(sessions.common, "fetch", return_value=json.dumps(MARKET_INFO).encode()) as f:
            self.assertEqual(sessions.status(), day())
        self.assertEqual(f.call_args.kwargs["headers"]["Accept"], "application/json")

    def test_unreadable_is_none_and_the_fallback_uses_weekday_windows(self):
        with mock.patch.object(sessions.common, "fetch", side_effect=OSError("down")), mock.patch("builtins.print"):
            self.assertIsNone(sessions.status())
        self.assertEqual(sessions.fallback(ny("2026-09-28T07:30")), day())
        fri = sessions.fallback(ny("2026-10-02T20:30"))
        self.assertEqual((fri["business"], fri["next"]), (True, "2026-10-05"))
        self.assertFalse(sessions.fallback(ny("2026-10-03T07:30"))["business"])


class Now(unittest.TestCase):
    def test_session_now(self):
        for t, want in (("03:59", None), ("04:00", "pre"), ("09:30", "regular"), ("15:59", "regular"),
                        ("16:00", "post"), ("20:00", None)):
            self.assertEqual(sessions.session_now(ny(f"2026-09-28T{t}"), day()), want, t)
        self.assertIsNone(sessions.session_now(ny("2026-11-26T10:00"), day("2026-11-26", business=False)))

    def test_target_session(self):
        self.assertEqual(sessions.target_session(ny("2026-09-28T07:30"), day()), "2026-09-28")
        self.assertEqual(sessions.target_session(ny("2026-09-28T16:30"), day()), "2026-09-29")
        fri = day("2026-10-02", nxt="2026-10-05")
        self.assertEqual(sessions.target_session(ny("2026-10-02T19:30"), fri), "2026-10-05")
        self.assertEqual(sessions.target_session(ny("2026-10-03T12:00"), None), "2026-10-05")  # Saturday, no status


if __name__ == "__main__":
    unittest.main()
