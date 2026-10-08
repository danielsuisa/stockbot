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
                        "previousTradeDate": "Sep 25, 2026", "nextTradeDate": "Sep 29, 2026"}}


def info(d, prev, nxt, business=True, close="16:00:00"):
    """Nasdaq market-info showing day d."""
    return json.dumps({"data": {"isBusinessDay": business, "pmOpenRaw": f"{d}T04:00:00", "openRaw": f"{d}T09:30:00",
                                "closeRaw": f"{d}T{close}", "ahCloseRaw": f"{d}T20:00:00",
                                "previousTradeDate": prev, "nextTradeDate": nxt}}).encode()


class Slot(unittest.TestCase):
    def test_each_slot_on_time_and_late(self):
        for t, want in (("07:30", "pre1"), ("08:59", "pre1"), ("09:15", "pre2"), ("16:30", "post1"),
                        ("17:59", "post1"), ("19:30", "post2")):
            self.assertEqual(sessions.slot(ny(f"2026-09-28T{t}"), day()), want, t)
        for t in ("04:15", "05:45"):  # right after the pre-market opens (11:15 Israel time)
            self.assertEqual(sessions.slot(ny(f"2026-09-28T{t}"), day()), "pre0", t)

    def test_hourly_slots_in_the_regular_session_each_run_their_own_hour(self):
        # 10:30-15:30 New York (17:30-22:30 Israel); 90-minute windows overlap, so the latest slot started wins
        for t, want in (("10:30", "reg1"), ("11:29", "reg1"), ("11:30", "reg2"), ("12:00", "reg2"), ("15:30", "reg6"),
                        ("16:29", "reg6"), ("16:30", "post1"), ("10:44", "reg1")):
            self.assertEqual(sessions.slot(ny(f"2026-09-28T{t}"), day()), want, t)
        self.assertEqual([n for n, (_, s) in sessions.SLOTS.items() if s == "regular"],
                         ["reg1", "reg2", "reg3", "reg4", "reg5", "reg6"])
        early = day("2026-11-27", close="13:00", ah="17:00")  # an early close: no slot after 13:00 is regular
        self.assertEqual(sessions.slot(ny("2026-11-27T13:30"), early), "reg3")
        self.assertIsNone(sessions.slot(ny("2026-11-27T14:31"), early))

    def test_too_early_too_late_or_outside_the_session(self):
        for t in ("04:14", "05:46", "07:29", "09:01", "18:01", "21:01"):
            self.assertIsNone(sessions.slot(ny(f"2026-09-28T{t}"), day()), t)

    def test_a_late_start_keeps_its_slot_after_the_session_ends(self):
        # GitHub starts schedules 15-60 minutes late at busy times: 09:15 and 19:30 must survive 09:30 and 20:00
        for t, want in (("09:40", "pre2"), ("10:29", "pre2"), ("20:10", "post2"), ("21:00", "post2")):
            self.assertEqual(sessions.slot(ny(f"2026-09-28T{t}"), day()), want, t)

    def test_a_run_serves_only_its_own_slots(self):
        # the insider scan has no hourly or 04:15 slots: at 12:00 and 04:15 it has nothing to run
        for t in ("12:00", "04:15"):
            self.assertIsNone(sessions.slot(ny(f"2026-09-28T{t}"), day(), sessions.INSIDER), t)
        self.assertEqual(sessions.slot(ny("2026-09-28T16:31"), day(), sessions.INSIDER), "post1")
        self.assertEqual(sessions.INSIDER, ["pre1", "pre2", "post1", "post2"])

    def test_daylight_saving_twins(self):
        summer, winter = day("2026-09-28"), day("2026-12-01", nxt="2026-12-02")
        utc = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).astimezone(NY)  # noqa: E731
        self.assertEqual(sessions.slot(utc("2026-09-28T11:30"), summer), "pre1")
        self.assertIsNone(sessions.slot(utc("2026-12-01T11:30"), winter))  # 06:30 EST
        self.assertEqual(sessions.slot(utc("2026-12-01T12:30"), winter), "pre1")
        self.assertEqual(sessions.slot(utc("2026-09-28T23:30"), summer), "post2")
        self.assertEqual(sessions.slot(utc("2026-09-29T00:30"), summer), "post2")  # 20:30 EDT: a retry; claim stops a repeat
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
            self.assertEqual(sessions.status(ny("2026-09-28T07:30")), day())
        self.assertEqual(f.call_args.kwargs["headers"]["Accept"], "application/json")

    def test_unreadable_is_none_and_the_fallback_uses_weekday_windows(self):
        with mock.patch.object(sessions.common, "fetch", side_effect=OSError("down")), mock.patch("builtins.print"):
            self.assertIsNone(sessions.status())
        self.assertEqual(sessions.fallback(ny("2026-09-28T07:30")), day())
        fri = sessions.fallback(ny("2026-10-02T20:30"))
        self.assertEqual((fri["business"], fri["next"]), (True, "2026-10-05"))
        self.assertFalse(sessions.fallback(ny("2026-10-03T07:30"))["business"])

    def test_another_days_answer_uses_todays_weekday_windows(self):
        # Nasdaq may already show the next day at the winter 19:30 run (00:30 UTC) - not measured, so guarded
        with mock.patch.object(sessions.common, "fetch", return_value=info("2026-12-02", "Dec 1, 2026", "Dec 3, 2026")):
            st = sessions.status(ny("2026-12-01T19:30"))
        self.assertEqual(st, day("2026-12-01", nxt="2026-12-02"))
        self.assertEqual(sessions.slot(ny("2026-12-01T19:30"), st), "post2")
        self.assertEqual(sessions.next_open(ny("2026-12-01T19:30"), st), "2026-12-02")
        # a holiday that shows the next trading day is still a holiday
        with mock.patch.object(sessions.common, "fetch",
                               return_value=info("2026-11-27", "Nov 25, 2026", "Nov 30, 2026", close="13:00:00")):
            self.assertFalse(sessions.status(ny("2026-11-26T07:30"))["business"])
        # yesterday's answer: today trades when it is yesterday's next trading day
        with mock.patch.object(sessions.common, "fetch", return_value=info("2026-09-28", "Sep 25, 2026", "Sep 29, 2026")):
            st = sessions.status(ny("2026-09-29T07:30"))
        self.assertEqual((st["business"], st["pre"][0]), (True, dt.datetime(2026, 9, 29, 4)))


class Now(unittest.TestCase):
    def test_session_now(self):
        for t, want in (("03:59", None), ("04:00", "pre"), ("09:30", "regular"), ("15:59", "regular"),
                        ("16:00", "post"), ("20:00", None)):
            self.assertEqual(sessions.session_now(ny(f"2026-09-28T{t}"), day()), want, t)
        self.assertIsNone(sessions.session_now(ny("2026-11-26T10:00"), day("2026-11-26", business=False)))

    def test_next_open_is_where_a_stock_seen_now_can_first_be_bought(self):
        self.assertEqual(sessions.next_open(ny("2026-09-28T07:30"), day()), "2026-09-28")  # before the open
        self.assertEqual(sessions.next_open(ny("2026-09-28T10:31"), day()), "2026-09-29")  # seen during the session
        self.assertEqual(sessions.next_open(ny("2026-10-02T16:35"), day("2026-10-02", nxt="2026-10-05")), "2026-10-05")
        self.assertEqual(sessions.next_open(ny("2026-10-03T12:00"), None), "2026-10-05")  # Saturday, no status


if __name__ == "__main__":
    unittest.main()
