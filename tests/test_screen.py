"""The owner's short-squeeze screen (2026-10-08): the criteria at their edges, Yahoo's batch quote and float, CBOE's
"has options", and the screen's funnel and order."""
import datetime as dt
import io
import json
import unittest
import urllib.error
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from bot import screen

NY = dt.timezone(dt.timedelta(hours=-4))
AT = int(dt.datetime(2026, 10, 8, 12, 30, tzinfo=NY).timestamp())  # Thursday 12:30 New York
PASS = {"float": 30e6, "short_float": 0.5, "short_ratio": 7.0, "avg_vol": 1e6, "price": 10.0, "rel_vol": 3.0,
        "options": True}


def yq(sym, price=10.0, chg=5.0, vol=3e6, avg=1e6, at=AT):
    """One row of Yahoo's v7 batch quote."""
    return {"symbol": sym, "regularMarketPrice": price, "regularMarketChangePercent": chg, "regularMarketVolume": vol,
            "averageDailyVolume3Month": avg, "regularMarketTime": at}


class FakeYahoo:
    """Yahoo's crumb-protected JSON, routed by URL; records the calls."""

    def __init__(self, quotes=(), floats=None, down=()):
        self.q, self.floats, self.down, self.calls = {x["symbol"]: x for x in quotes}, floats or {}, set(down), []

    def __call__(self, url):
        self.calls.append(url)
        u = urlsplit(url)
        if "/v7/finance/quote" in u.path:
            syms = parse_qs(u.query)["symbols"][0].split(",")
            if self.down & set(syms):
                raise OSError("timed out")
            return {"quoteResponse": {"result": [self.q[s] for s in syms if s in self.q]}}
        t = u.path.rsplit("/", 1)[1]
        f = self.floats.get(t)
        if f is OSError:
            raise OSError("timed out")
        stats = {} if f is None else {"floatShares": {"raw": f, "fmt": "x"}}
        return {"quoteSummary": {"result": [{"defaultKeyStatistics": stats}]}}


class Criteria(unittest.TestCase):
    def test_a_stock_meeting_every_criterion_passes(self):
        self.assertEqual(screen.fails(PASS), [])

    def test_each_bound_is_strict(self):
        for key, edge, inside in (("float", 50_000_000, 49_999_999), ("short_float", 0.20, 0.2001),
                                  ("short_ratio", 5.0, 5.01), ("avg_vol", 500_000, 500_001), ("price", 2.0, 2.01),
                                  ("rel_vol", 2.0, 2.01)):
            self.assertEqual(screen.fails({**PASS, key: edge}), [key], key)
            self.assertEqual(screen.fails({**PASS, key: inside}), [], key)

    def test_missing_or_no_options_fails(self):
        for key in PASS:
            self.assertEqual(screen.fails({**PASS, key: None}), [key], key)
        self.assertEqual(screen.fails({**PASS, "options": False}), ["options"])

    def test_only_the_asked_criteria(self):
        self.assertEqual(screen.fails({**PASS, "float": None, "price": 1.0}, screen.QUOTE_KEYS), ["price"])

    def test_metrics_like_finviz(self):
        m = screen.metrics(3_000_000, {"price": 4.0, "chg": 0.12, "vol": 2_000_000, "avg": 500_000}, 10_000_000, True)
        self.assertEqual(m, {"price": 4.0, "chg": 0.12, "avg_vol": 500_000, "rel_vol": 4.0, "short_ratio": 6.0,
                             "float": 10_000_000, "short_float": 0.3, "options": True})
        empty = screen.metrics(3_000_000, None)
        self.assertEqual({k for k, v in empty.items() if v is not None}, set())


class Yahoo(unittest.TestCase):
    def test_batch_quote_fifty_at_a_time(self):
        syms = [f"T{i:03}" for i in range(120)]
        y = FakeYahoo([yq(s) for s in syms if s != "T007"], down={"T110"})
        with mock.patch("builtins.print"):
            got = screen.quotes(y, syms)
        self.assertEqual(len(y.calls), 3)
        self.assertEqual(got["T000"], {"price": 10.0, "chg": 0.05, "vol": 3e6, "avg": 1e6, "date": "2026-10-08"})
        self.assertIsNone(got["T007"])  # Yahoo does not know it
        self.assertTrue(all(got[s] is False for s in syms[100:]))  # a failed batch: could not read

    def test_float_shares(self):
        y = FakeYahoo(floats={"AAA": 21_014_219, "DOWN": OSError})
        self.assertEqual(screen.float_shares(y, "AAA"), 21_014_219)
        self.assertIsNone(screen.float_shares(y, "NONE"))
        with mock.patch("builtins.print"):
            self.assertIs(screen.float_shares(y, "DOWN"), False)
        self.assertIn("/v10/finance/quoteSummary/AAA?modules=defaultKeyStatistics", y.calls[0])


class Options(unittest.TestCase):
    def test_listed_options_from_cboe(self):
        chain = json.dumps({"data": {"options": [{"option": "MMED261016C00002500"}]}}).encode()
        denied = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))  # CBOE: no chain for it
        with mock.patch.object(screen.common, "fetch", return_value=chain) as f:
            self.assertIs(screen.optionable("BRK-B"), True)
        self.assertIn("/options/BRK.B.json", f.call_args[0][0])
        with mock.patch.object(screen.common, "fetch", side_effect=denied):
            self.assertIs(screen.optionable("XXXX"), False)
        with mock.patch.object(screen.common, "fetch", side_effect=OSError("down")), mock.patch("builtins.print"):
            self.assertIsNone(screen.optionable("MMED"))


class Screen(unittest.TestCase):
    def run_screen(self, short, quotes, floats, opts=None):
        y = FakeYahoo(quotes, floats)
        with mock.patch.object(screen, "optionable", side_effect=lambda t: (opts or {}).get(t, True)) as o, \
                mock.patch("builtins.print"):
            res = screen.screen(short, y)
        return res, y, o

    def test_funnel_and_order(self):
        short = {"LOW": 9e6, "MID": 12e6, "TOP": 18e6, "SMALL": 2_500_000, "BIGF": 10e6, "NOFL": 10e6, "QUIET": 10e6,
                 "NOOPT": 10e6}
        quotes = [yq(t) for t in short] + [yq("QUIET", vol=1.5e6)]
        floats = {"LOW": 40e6, "MID": 30e6, "TOP": 30e6, "BIGF": 60e6, "NOOPT": 20e6}  # NOFL: Yahoo has no float
        res, y, o = self.run_screen(short, quotes, floats, {"NOOPT": False})
        self.assertEqual([r["t"] for r in res["rows"]], ["TOP", "MID", "LOW"])
        self.assertEqual([round(r["short_float"], 3) for r in res["rows"]], [0.6, 0.4, 0.225])
        self.assertFalse(any("SMALL" in c for c in y.calls))  # short 2.5M: short ratio > 5 on > 500K is impossible
        self.assertFalse(any("quoteSummary/QUIET" in c for c in y.calls))  # float only after the quote criteria
        self.assertEqual(sorted(c.args[0] for c in o.call_args_list), ["LOW", "MID", "NOOPT", "TOP"])  # BIGF: float
        self.assertEqual((res["screened"], res["failed"], res["no_float"], res["no_options"], res["date"]),
                         (7, 0, 1, 0, "2026-10-08"))
        row = res["rows"][0]
        self.assertEqual({k: row[k] for k in ("t", "price", "chg", "rel_vol", "short_ratio", "float")},
                         {"t": "TOP", "price": 10.0, "chg": 0.05, "rel_vol": 3.0, "short_ratio": 18.0, "float": 30e6})

    def test_unknown_options_are_counted_not_listed(self):
        res = self.run_screen({"AAA": 10e6}, [yq("AAA")], {"AAA": 20e6}, {"AAA": None})[0]
        self.assertEqual((res["rows"], res["no_options"]), ([], 1))

    def test_yahoo_down_for_every_stock_raises(self):
        y = FakeYahoo(down={"AAA", "BBB"})
        with mock.patch("builtins.print"), self.assertRaises(RuntimeError):
            screen.screen({"AAA": 10e6, "BBB": 10e6}, y)


STATIC = {"AAA": {"float": 30e6, "short_float": 0.5, "short_ratio": 7.0, "avg_vol": 1e6},
          "BBB": {"float": 20e6, "short_float": 0.8, "short_ratio": 9.0, "avg_vol": 2e6},
          "CCC": {"float": 40e6, "short_float": 0.3, "short_ratio": 6.0, "avg_vol": 1e6}}
TODAY = "2026-10-08"


def pq(price=11.0, volume=150_000, date=TODAY, prev=10.0):
    """movers.quote(t, "pre")."""
    return {"price": price, "volume": volume, "date": date, "prev": prev}


class Candidates(unittest.TestCase):
    def test_every_criterion_but_price_and_volume_once_a_day(self):
        short = {"OK": 10e6, "CHEAP": 10e6, "QUIET": 10e6, "SMALL": 2_500_000, "BIGF": 10e6, "NOOPT": 10e6, "UNK": 10e6}
        quotes = [yq(t) for t in short] + [yq("CHEAP", price=1.5, vol=0), yq("QUIET", vol=0)]
        y = FakeYahoo(quotes, {"OK": 30e6, "CHEAP": 30e6, "QUIET": 30e6, "BIGF": 60e6, "NOOPT": 30e6, "UNK": 30e6})
        opts = {"NOOPT": False, "UNK": None}
        with mock.patch.object(screen, "optionable", side_effect=lambda t: opts.get(t, True)), \
                mock.patch("builtins.print"):
            c = screen.candidates(short, y)
        # price and today's volume are the pre-market's to judge: CHEAP ($1.50 close) and QUIET (no volume) stay in;
        # UNK (CBOE did not answer) stays in too, to be asked again if it moves - the set is kept all morning
        self.assertEqual(sorted(c["rows"]), ["CHEAP", "OK", "QUIET", "UNK"])
        self.assertEqual(c["rows"]["OK"], {"float": 30e6, "short_float": 10e6 / 30e6, "short_ratio": 10.0,
                                           "avg_vol": 1e6, "options": True})
        self.assertIsNone(c["rows"]["UNK"]["options"])
        self.assertEqual((c["screened"], c["failed"], c["no_float"], c["no_options"]), (6, 0, 0, 1))
        self.assertFalse(any("SMALL" in u for u in y.calls))


class Premarket(unittest.TestCase):
    def run_pre(self, quotes, static=STATIC):
        with mock.patch("builtins.print"):
            return screen.premarket({"rows": static}, lambda t: quotes.get(t), TODAY)

    def test_pre_market_volume_and_price_decide_sorted_by_short_float(self):
        res = self.run_pre({"AAA": pq(), "BBB": pq(price=5.0, volume=300_000, prev=4.0), "CCC": pq(volume=50_000)})
        self.assertEqual([r["t"] for r in res["rows"]], ["BBB", "AAA"])  # CCC: 5% of a normal day
        bbb = res["rows"][0]
        self.assertEqual({k: bbb[k] for k in ("price", "chg", "pre_vol", "options")},
                         {"price": 5.0, "chg": 0.25, "pre_vol": 0.15, "options": True})
        self.assertEqual((res["kind"], res["date"], res["checked"], res["failed"]), ("pre", TODAY, 3, 0))

    def test_edges(self):
        one = {"AAA": STATIC["AAA"]}
        self.assertEqual(len(self.run_pre({"AAA": pq(volume=100_000)}, one)["rows"]), 1)  # exactly 10%: at least
        for q in (pq(volume=99_999), pq(price=2.0), pq(date="2026-10-07"), pq(price=None), None):
            self.assertEqual(self.run_pre({"AAA": q}, one)["rows"], [], q)  # yesterday's data never counts
        self.assertIsNone(self.run_pre({"AAA": pq(prev=None)}, one)["rows"][0]["chg"])

    def test_unknown_options_are_asked_again_only_for_stocks_that_move(self):
        static = {**STATIC, "AAA": {**STATIC["AAA"], "options": None}, "BBB": {**STATIC["BBB"], "options": None},
                  "CCC": {**STATIC["CCC"], "options": None}}
        quotes = {"AAA": pq(), "BBB": pq(volume=300_000), "CCC": pq(volume=1)}  # CCC does not move: no CBOE call
        with mock.patch.object(screen, "optionable", side_effect=lambda t: {"AAA": True, "BBB": None}[t]) as o:
            res = self.run_pre(quotes, static)
        self.assertEqual(sorted(c.args[0] for c in o.call_args_list), ["AAA", "BBB"])
        self.assertEqual(([r["t"] for r in res["rows"]], res["no_options"]), (["AAA"], 1))
        self.assertIs(res["rows"][0]["options"], True)

    def test_nasdaq_down_for_every_stock_raises_and_some_failing_are_counted(self):
        with self.assertRaises(RuntimeError):
            self.run_pre({t: False for t in STATIC})
        self.assertEqual(self.run_pre({"AAA": False, "BBB": pq(), "CCC": None})["failed"], 1)

    def test_an_empty_fixed_set_is_an_empty_list(self):
        self.assertEqual(self.run_pre({}, {})["rows"], [])


if __name__ == "__main__":
    unittest.main()
