"""Locks for the pre-FOMC windows and the CPI/PPI gap book."""
import math
import unittest

from bot.macro_exec import (
    GAP, bls_releases, fomc_events, gap_signals, meeting_dates, order_fee,
    run_close_book, run_open_fade, shares_for_cash,
)


HEADINGS = """
<h5>January 21 Conference Call - 2008</h5>
<h5>January 29-30 Meeting - 2008</h5>
<h5>March 17-18 (cancelled) Meeting - 2020</h5>
<h5>March 2 (unscheduled) Meeting - 2020</h5>
<h5>March 15 (unscheduled) Meeting - 2020</h5>
<h5>March 19 (notation vote) - 2020</h5>
"""

CALENDAR = """
<h4><a id="1">2026 FOMC Meetings</a></h4>
<div class="fomc-meeting__month"><strong>September</strong></div>
<div class="fomc-meeting__date">15-16*</div>
<div class="fomc-meeting__month"><strong>October</strong></div>
<div class="fomc-meeting__date">27-28</div>
"""

BLS = """
<tr><td class="date-cell"><p>Friday, January 15, 2010</p></td>
<td class="time-cell"><p>08:30 AM</p></td>
<td class="desc-cell"><p><strong>Consumer Price Index</strong> for December 2009</p></td></tr>
<tr><td class="date-cell"><p>Thursday, January 21, 2010</p></td>
<td class="time-cell"><p>08:30 AM</p></td>
<td class="desc-cell"><p><strong>Producer Price Index</strong> for December 2009</p></td></tr>
<tr><td class="date-cell"><p>Friday, January 15, 2010</p></td>
<td class="time-cell"><p>08:30 AM</p></td>
<td class="desc-cell"><p><strong>Real Earnings</strong> for December 2009</p></td></tr>
"""


class CalendarTests(unittest.TestCase):
    def test_meetings_skip_calls_cancellations_and_notation_votes(self):
        self.assertEqual(meeting_dates(HEADINGS), [
            "2008-01-30", "2020-03-02", "2020-03-15",
        ])

    def test_calendar_page_uses_the_last_day(self):
        self.assertEqual(meeting_dates(CALENDAR), ["2026-09-16", "2026-10-28"])

    def test_month_crossing_headings(self):
        html = """
        <h5>Jan/Feb 31-1 Meeting - 2017</h5>
        <h5>April/May 30-1 Meeting - 2013</h5>
        <h5>June 30-1 Meeting - 2024</h5>
        """
        self.assertEqual(meeting_dates(html), ["2013-05-01", "2017-02-01", "2024-07-01"])

    def test_calendar_page_month_crossing(self):
        html = """
        <h4><a id="1">2024 FOMC Meetings</a></h4>
        <div class="fomc-meeting__month"><strong>June</strong></div>
        <div class="fomc-meeting__date">30-1</div>
        <div class="fomc-meeting__month"><strong>Jan/Feb</strong></div>
        <div class="fomc-meeting__date">31-1</div>
        """
        self.assertEqual(meeting_dates(html), ["2024-02-01", "2024-07-01"])

    def test_sunday_announcement_uses_the_next_session(self):
        sessions = ["2020-03-12", "2020-03-13", "2020-03-16", "2020-03-17"]
        events = fomc_events(sessions, ["2020-03-15"], "A")
        self.assertEqual(events[0]["session"], "2020-03-16")
        self.assertEqual(events[0]["entry"], "2020-03-12")
        self.assertEqual(events[0]["exit"], "2020-03-13")
        self.assertEqual(events[0]["spy_days"], ("2020-03-13",))

    def test_variant_c_holds_the_announcement_session(self):
        sessions = ["2020-03-12", "2020-03-13", "2020-03-16", "2020-03-17"]
        events = fomc_events(sessions, ["2020-03-15"], "C")
        self.assertEqual(events[0]["spy_days"], ("2020-03-13", "2020-03-16"))

    def test_bls_keeps_cpi_and_ppi_only(self):
        self.assertEqual(bls_releases(BLS), [
            ("2010-01-15", "CPI"), ("2010-01-21", "PPI"),
        ])


class CostTests(unittest.TestCase):
    def test_minimum_and_cap(self):
        self.assertEqual(order_fee(10, 100), 1.0)
        self.assertAlmostEqual(order_fee(1000, 100), 5.0)
        self.assertAlmostEqual(order_fee(10, 2), 0.20)

    def test_buy_does_not_spend_more_than_the_cash(self):
        shares, fee, left = shares_for_cash(460, 100)
        self.assertGreater(shares, 0)
        self.assertGreaterEqual(left, -1e-9)
        self.assertAlmostEqual(fee, 1.0)


class PathTests(unittest.TestCase):
    def test_variant_a_earns_one_session_and_c_earns_two(self):
        days = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
        spy = {
            "2020-01-02": 100.0, "2020-01-03": 100.0,
            "2020-01-06": 110.0, "2020-01-07": 121.0,
        }
        prices = {"SPY": spy, "BIL": {}, "SGOV": {}}
        # Announcement is the last session. A holds the middle jump. C holds both jumps.
        events_a = fomc_events(days, ["2020-01-07"], "A")
        events_c = fomc_events(days, ["2020-01-07"], "C")
        book_a = run_close_book(days, events_a[0]["spy_days"], prices, {}, None, None)
        book_c = run_close_book(days, events_c[0]["spy_days"], prices, {}, None, None)
        self.assertEqual(len(book_a["trades"]), 1)
        self.assertEqual(len(book_c["trades"]), 1)
        self.assertAlmostEqual(book_a["trades"][0]["exit_price"], 110.0)
        self.assertAlmostEqual(book_c["trades"][0]["exit_price"], 121.0)
        self.assertGreater(book_c["terminal"], book_a["terminal"])
        shares, _fee, left = shares_for_cash(460, 100)
        proceeds, _sell = __import__("bot.macro_exec", fromlist=["sell_proceeds"]).sell_proceeds(shares, 110)
        self.assertAlmostEqual(book_a["terminal"], left + proceeds, places=6)

    def test_the_path_terminal_is_the_last_mark(self):
        days = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
        spy = {
            "2020-01-02": 100.0, "2020-01-03": 100.0,
            "2020-01-06": 110.0, "2020-01-07": 110.0,
        }
        events = fomc_events(days, ["2020-01-07"], "A")
        book = run_close_book(days, events[0]["spy_days"], {"SPY": spy}, {}, None, None)
        self.assertAlmostEqual(book["curve"][-1][1], book["terminal"])

    def test_gap_filter_and_vix_filter(self):
        days = ["2024-01-02", "2024-01-03", "2024-01-04"]
        opens = {"2024-01-03": 99.0, "2024-01-04": 99.6}
        closes = {"2024-01-02": 100.0, "2024-01-03": 100.0, "2024-01-04": 100.0}
        releases = ["2024-01-03", "2024-01-04"]
        base = gap_signals(days, opens, closes, releases)
        self.assertEqual([row["day"] for row in base], ["2024-01-03"])
        self.assertLessEqual(base[0]["gap"], GAP)
        # A 20 vol point VIX1D is about 1.26% a day, so the 1% gap fails the variant.
        vix = {"2024-01-02": 20.0}
        strict = gap_signals(days, opens, closes, releases, vix=vix, vix_filter=True)
        self.assertEqual(strict, [])
        wide = gap_signals(
            days, {"2024-01-03": 98.0}, closes, releases,
            vix={"2024-01-02": 10.0}, vix_filter=True,
        )
        self.assertEqual(len(wide), 1)
        self.assertAlmostEqual(wide[0]["implied"], (10 / 100) / math.sqrt(252))

    def test_open_fade_earns_the_intraday_move(self):
        days = ["2024-01-02", "2024-01-03", "2024-01-04"]
        opens = {"2024-01-03": 99.0, "2024-01-04": 100.0}
        closes = {"2024-01-02": 100.0, "2024-01-03": 101.0, "2024-01-04": 101.0}
        signals = gap_signals(days, opens, closes, ["2024-01-03"])
        book = run_open_fade(days, signals, {"SPY": closes}, opens, closes, {}, None, None)
        self.assertEqual(len(book["trades"]), 1)
        self.assertAlmostEqual(book["trades"][0]["gross"], 101 / 99 - 1)
        self.assertGreater(book["trades"][0]["pnl"], 0)


if __name__ == "__main__":
    unittest.main()
