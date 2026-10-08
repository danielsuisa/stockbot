"""Alpaca market data: pagination, multi-symbol split, New York minutes, the disk cache, keys in headers only."""
import json
import os
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from bot import alpaca

KEYS = {"ALPACA_KEY_ID": "KID123", "ALPACA_SECRET_KEY": "SECRET456"}


def bar(t, c=1.0, v=100):
    return {"t": t, "o": c, "h": c, "l": c, "c": c, "v": v, "n": 1, "vw": c}


def answer(js):
    return json.dumps(js).encode()


def params(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = [mock.patch.object(alpaca, "CACHE", Path(d.name)), mock.patch.dict(os.environ, KEYS),
             mock.patch.object(alpaca, "GAP", 0)]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def fetch(self, *bodies, error=None):
        f = mock.patch.object(alpaca.common, "fetch", side_effect=error or [answer(b) for b in bodies])
        self.addCleanup(f.stop)
        return f.start()


class MinuteBars(Base):
    def test_minute_bars_split_pages_and_map_to_new_york(self):
        f = self.fetch({"bars": {"AAA": [bar("2026-10-08T08:00:00Z", 2.0)]}, "next_page_token": "P2"},
                       {"bars": {"AAA": [bar("2026-10-08T13:30:00Z", 2.5)], "BBB": [bar("2026-10-08T08:01:00Z")]},
                        "next_page_token": None})
        got = alpaca.minute_bars(["AAA", "BBB", "CCC"], "2026-10-08")
        self.assertEqual(got["AAA"], [("04:00", 2.0, 2.0, 2.0, 2.0, 100, 2.0), ("09:30", 2.5, 2.5, 2.5, 2.5, 100, 2.5)])
        self.assertEqual([b[0] for b in got["BBB"]], ["04:01"])
        self.assertEqual(got["CCC"], [])
        first, second = (params(c.args[0]) for c in f.call_args_list)
        self.assertEqual(first["symbols"], "AAA,BBB,CCC")
        self.assertEqual((first["timeframe"], first["feed"], first["limit"]), ("1Min", "sip", "10000"))
        self.assertEqual((first["start"], first["end"]), ("2026-10-08T08:00:00Z", "2026-10-09T00:00:00Z"))
        self.assertNotIn("page_token", first)
        self.assertEqual(second["page_token"], "P2")
        # winter: 14:30 UTC is 09:30 New York
        self.fetch({"bars": {"AAA": [bar("2026-12-01T14:30:00Z")]}})
        self.assertEqual(alpaca.minute_bars(["AAA"], "2026-12-01")["AAA"][0][0], "09:30")

    def test_empty_day_is_cached_as_no_trades(self):
        f = self.fetch({"bars": {}, "next_page_token": None})
        self.assertEqual(alpaca.minute_bars(["AAA"], "2024-01-02"), {"AAA": []})
        self.assertEqual(alpaca.minute_bars(["AAA"], "2024-01-02"), {"AAA": []})
        self.assertEqual(f.call_count, 1)

    def test_cached_symbols_are_not_requested_again(self):
        self.fetch({"bars": {"AAA": [bar("2024-01-02T14:30:00Z")]}})
        alpaca.minute_bars(["AAA"], "2024-01-02")
        f = self.fetch({"bars": {"BBB": [bar("2024-01-02T14:31:00Z")]}})
        got = alpaca.minute_bars(["AAA", "BBB"], "2024-01-02")
        self.assertEqual(params(f.call_args.args[0])["symbols"], "BBB")
        self.assertEqual((got["AAA"][0][0], got["BBB"][0][0]), ("09:30", "09:31"))

    def test_failed_download_is_not_cached(self):
        self.fetch(error=OSError("reset"))
        with self.assertRaises(OSError):
            alpaca.minute_bars(["AAA"], "2024-01-02")
        f = self.fetch({"bars": {"AAA": [bar("2024-01-02T14:30:00Z")]}})
        self.assertEqual(len(alpaca.minute_bars(["AAA"], "2024-01-02")["AAA"]), 1)
        self.assertEqual(f.call_count, 1)

    def test_a_failed_second_page_caches_nothing(self):
        self.fetch(error=[answer({"bars": {"AAA": [bar("2024-01-02T14:30:00Z")]}, "next_page_token": "P2"}),
                          OSError("reset")])
        with self.assertRaises(OSError):
            alpaca.minute_bars(["AAA"], "2024-01-02")
        f = self.fetch({"bars": {}})
        alpaca.minute_bars(["AAA"], "2024-01-02")
        self.assertEqual(f.call_count, 1)

    def test_keys_in_headers_not_urls(self):
        f = self.fetch({"bars": {}})
        alpaca.minute_bars(["AAA"], "2024-01-02")
        url, kw = f.call_args.args[0], f.call_args.kwargs
        self.assertEqual(kw["headers"], {"APCA-API-KEY-ID": "KID123", "APCA-API-SECRET-KEY": "SECRET456"})
        self.assertNotIn("KID123", url)
        self.assertNotIn("SECRET456", url)

    def test_missing_keys_stop_before_any_request(self):
        f = self.fetch({"bars": {}})
        with mock.patch.dict(os.environ, {"ALPACA_KEY_ID": "", "ALPACA_SECRET_KEY": ""}):
            with self.assertRaises(RuntimeError):
                alpaca.minute_bars(["AAA"], "2024-01-02")
        f.assert_not_called()


class Other(Base):
    def test_bars_any_timeframe_with_dated_minutes(self):
        f = self.fetch({"bars": {"AAA": [bar("2024-01-02T14:30:00Z", 3.0, 500)]}})
        got = alpaca.bars("AAA", "2023-12-01", "2024-01-02", "5Min")
        self.assertEqual(got, [("2024-01-02 09:30", 3.0, 3.0, 3.0, 3.0, 500, 3.0)])
        self.assertEqual(params(f.call_args.args[0])["timeframe"], "5Min")
        alpaca.bars("AAA", "2023-12-01", "2024-01-02", "5Min")
        self.assertEqual(f.call_count, 1)

    def test_daily_raw_bars_by_new_york_date(self):
        f = self.fetch({"bars": {"AAA": [bar("2024-01-02T05:00:00Z", 4.0, 900)], "BBB": []}})
        got = alpaca.daily(["AAA", "BBB"], "2023-10-01", "2024-01-02")
        self.assertEqual(got, {"AAA": [("2024-01-02", 4.0, 4.0, 4.0, 4.0, 900)], "BBB": []})
        p = params(f.call_args.args[0])
        self.assertEqual((p["timeframe"], p["adjustment"]), ("1Day", "raw"))

    def test_quote_at_takes_the_last_quote(self):
        f = self.fetch({"quotes": [{"t": "2024-01-02T14:34:59Z", "bp": 2.01, "ap": 2.04}]}, {"quotes": []})
        self.assertEqual(alpaca.quote_at("AAA", "2024-01-02T14:35:00Z"), (2.01, 2.04))
        p = params(f.call_args.args[0])
        self.assertEqual((p["sort"], p["limit"], p["feed"]), ("desc", "1", "sip"))
        self.assertEqual((p["start"], p["end"]), ("2024-01-02T14:34:00Z", "2024-01-02T14:35:00Z"))
        self.assertIsNone(alpaca.quote_at("AAA", "2024-01-03T14:35:00Z"))

    def test_quote_at_is_cached_and_an_unusable_quote_is_none(self):
        f = self.fetch({"quotes": [{"bp": 0, "ap": 2.04}]})
        self.assertIsNone(alpaca.quote_at("AAA", "2024-01-02T14:35:00Z"))
        self.assertIsNone(alpaca.quote_at("AAA", "2024-01-02T14:35:00Z"))
        self.assertEqual(f.call_count, 1)

    def test_calendar_early_close(self):
        f = self.fetch([{"date": "2026-11-25", "open": "09:30", "close": "16:00"},
                        {"date": "2026-11-27", "open": "09:30", "close": "13:00"}])
        got = alpaca.calendar("2026-11-25", "2026-11-27")
        self.assertEqual(got, {"2026-11-25": ("09:30", "16:00"), "2026-11-27": ("09:30", "13:00")})
        self.assertTrue(f.call_args.args[0].startswith("https://paper-api.alpaca.markets/v2/calendar?"))
        alpaca.calendar("2026-11-25", "2026-11-27")
        self.assertEqual(f.call_count, 1)

    def test_utc_in_summer_and_winter(self):
        self.assertEqual(alpaca.utc("2026-12-01", "09:30"), "2026-12-01T14:30:00Z")
        self.assertEqual(alpaca.utc("2026-10-08", "09:30"), "2026-10-08T13:30:00Z")
        self.assertEqual(alpaca.utc("2026-10-08", "15:59:30"), "2026-10-08T19:59:30Z")


if __name__ == "__main__":
    unittest.main()
