import datetime as dt
import json
import os
import socket
import unittest
from unittest import mock

from bot import intraday, news

TODAY = dt.date(2026, 10, 27)
RSS = """<rss><channel>
<item><title>Tempus AI signs $192.5M data agreement with Bristol Myers</title><pubDate>Mon, 26 Oct 2026 13:00:00 GMT</pubDate></item>
<item><title>3 stocks to watch this week</title><pubDate>Mon, 26 Oct 2026 14:00:00 GMT</pubDate></item>
<item><title>Tempus AI receives FDA clearance</title><pubDate>Mon, 05 Oct 2026 13:00:00 GMT</pubDate></item>
</channel></rss>"""
EMPTY = "<rss><channel></channel></rss>"


def fake(rss=EMPTY, earn=None, fail=False):
    def get(url, headers=None):
        if fail:
            raise socket.timeout("timed out")
        if "yahoo" in url:
            return rss.encode()
        if "nasdaq" in url:
            d = url.split("date=")[1]
            return json.dumps({"data": {"rows": [{"symbol": "TEM"}] if d == earn else []}}).encode()
        raise AssertionError(url)
    return get


class Tag(unittest.TestCase):
    def setUp(self):
        news._cache.clear(); news._cal.clear()
        self.env = mock.patch.dict(os.environ, {"NEWS_TAG": "1", "SEC_UA": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_positive_news(self):
        with mock.patch.object(news, "_get", side_effect=fake(RSS)):
            line = news.tag("TEM", TODAY)
        self.assertTrue(line.startswith("חדשות: Tempus AI signs $192.5M"), line)
        self.assertIn("(Yahoo Finance, 26/10)", line)  # the 3-week-old FDA item and the listicle are left out

    def test_earnings_soon(self):
        with mock.patch.object(news, "_get", side_effect=fake(earn="2026-11-02")):
            self.assertEqual(news.tag("TEM", TODAY), "קטליזטור: דוחות בעוד 6 ימים")

    def test_both(self):
        with mock.patch.object(news, "_get", side_effect=fake(RSS, earn="2026-10-27")):
            self.assertTrue(news.tag("TEM", TODAY).endswith(" · קטליזטור: דוחות היום"))

    def test_none(self):
        with mock.patch.object(news, "_get", side_effect=fake()):
            self.assertEqual(news.tag("TEM", TODAY), news.NONE)

    def test_timeout_is_safe_and_cached(self):
        with mock.patch.object(news, "_get", side_effect=fake(fail=True)) as g:
            self.assertEqual(news.tag("TEM", TODAY), news.NONE)
            n = g.call_count
            self.assertEqual(news.tag("TEM", TODAY), news.NONE)
            self.assertEqual(g.call_count, n)  # one lookup per symbol per day

    def test_budget_stops_the_calendar(self):
        with mock.patch.object(news, "_get", side_effect=fake(earn="2026-11-02")), mock.patch.object(news, "BUDGET", -1):
            self.assertEqual(news.tag("TEM", TODAY), news.NONE)

    def test_off_switch_makes_no_request(self):
        with mock.patch.dict(os.environ, {"NEWS_TAG": "0"}), mock.patch.object(news, "_get") as g:
            self.assertEqual(news.tag("TEM", TODAY), news.NONE)
        g.assert_not_called()

    def test_edgar_only_with_sec_ua(self):
        sub = {"filings": {"recent": {"form": ["8-K", "4"], "filingDate": ["2026-10-23", "2026-10-24"], "items": ["1.01", ""]}}}
        def get(url, headers=None):
            if "company_tickers" in url:
                return json.dumps({"0": {"cik_str": 1717115, "ticker": "TEM"}}).encode()
            if "submissions" in url:
                return json.dumps(sub).encode()
            return fake()(url)
        with mock.patch.dict(os.environ, {"SEC_UA": "bot a@b.c"}), mock.patch.object(news, "_get", side_effect=get):
            self.assertEqual(news.tag("TEM", TODAY), "חדשות: דיווח 8-K (סעיף 1.01) (SEC EDGAR, 23/10)")


class OnTicket(unittest.TestCase):
    def test_ticket_carries_the_line_and_survives_errors(self):
        with mock.patch.object(news, "tag", side_effect=RuntimeError("boom")):
            self.assertEqual(intraday._news("TEM", "2026-10-27"), news.NONE)
        from test_intraday_screener import _t
        text = intraday.ticket_text({**_t(), "news": "קטליזטור: דוחות בעוד 6 ימים"}, "2026-10-19")
        self.assertIn("קטליזטור: דוחות בעוד 6 ימים", text)
        self.assertIn(news.NONE, intraday.ticket_text(_t(), "2026-10-19"))


if __name__ == "__main__":
    unittest.main()
