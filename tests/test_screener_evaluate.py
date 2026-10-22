"""Screener evaluation on synthetic stores (offline, hand-computed): CLOSED-mode filters, the cap boundary at exactly
$2B, strict o20, float fallback with its flag, relative volume at 10:30 from a synthetic profile, a 52-week high
including today, signals (admit, rank, cap, then o=), unknown/unavailable codes listed, stage-2 truncation, earnings
and IPO windows."""
import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from bot.screener import evaluate, parser
from bot.screener.stores import bars as BARS

NY = ZoneInfo("America/New_York")


def sessions(n, end="2026-10-20"):
    out, d = [], dt.date.fromisoformat(end)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(1)
    return out[::-1]


DAYS = sessions(300)


def series(closes, highs=None, lows=None, opens=None, vols=None, days=DAYS):
    s = BARS.Series()
    n = len(closes)
    rows = [(days[-n + i], (opens or closes)[i], (highs or closes)[i], (lows or closes)[i], closes[i],
             (vols or [1e6] * n)[i], 100) for i in range(n)]
    s.extend(rows)
    return s


def env(bars, universe, session="CLOSED", now=None, **kw):
    now = now or dt.datetime(2026, 10, 20, 21, 0, tzinfo=NY)
    return evaluate.Env(now=now, session=session, day=now.date().isoformat(), sessions_done=DAYS, universe=universe,
                        bars=bars, calendar=DAYS + ["2026-10-21", "2026-10-22"], **kw)


def uni(*syms, cik0=100, **extra):
    return {s: {"exch": "nasd", "etf": False, "cik": cik0 + i, "optionable": True, "shortable": True, **extra}
            for i, s in enumerate(syms)}


def shares(cik, n):
    """A fundamentals store with one shares-outstanding fact (bot/shorts index rows: usable from, fact date, shares)."""
    return {"facts": {}, "shares": {str(cik): [["2026-06-30", "2026-06-30", n]]}}


class Closed(unittest.TestCase):
    def test_price_and_strict_over(self):
        b = {"A": series([10.0] * 299 + [20.0]), "B": series([10.0] * 299 + [20.01]), "C": series([5.0] * 300)}
        r = evaluate.run(parser.parse("sh_price_o20"), env(b, uni("A", "B", "C")))
        self.assertEqual([x["t"] for x in r.rows], ["B"])  # exactly 20 is not "over 20"
        r = evaluate.run(parser.parse("sh_price_5to20"), env(b, uni("A", "B", "C")))
        self.assertEqual([x["t"] for x in r.rows], ["A", "C"])  # ranges inclusive

    def test_cap_bucket_boundary_at_exactly_2_billion(self):
        b = {"A": series([20.0] * 300), "B": series([20.0] * 300)}
        fund = {"facts": {}, "shares": {"100": [["2026-06-30", "2026-06-30", 100_000_000]],  # 20 x 100M = $2.0B
                                        "101": [["2026-06-30", "2026-06-30", 100_000_001]]}}
        e = env(b, uni("A", "B"), fund=fund)
        self.assertEqual([x["t"] for x in evaluate.run(parser.parse("cap_mid"), e).rows], ["A", "B"])
        self.assertEqual([x["t"] for x in evaluate.run(parser.parse("cap_small"), e).rows], ["A"])
        self.assertEqual([x["t"] for x in evaluate.run(parser.parse("cap_midover"), e).rows], ["B"])

    def test_float_falls_back_to_outstanding_with_a_flag(self):
        b = {"A": series([10.0] * 300), "B": series([10.0] * 300)}
        fund = {"facts": {}, "shares": {"100": [["2026-06-30", "2026-06-30", 40_000_000]],
                                        "101": [["2026-06-30", "2026-06-30", 60_000_000]]}}
        own = {"issuers": {"101": {"9": {"role": "do", "h": [["2026-09-01", 15_000_000]]}}}}
        e = env(b, uni("A", "B"), fund=fund, own=own)
        r = evaluate.run(parser.parse("sh_float_u50"), e)
        self.assertEqual([x["t"] for x in r.rows], ["A", "B"])  # A: 40M (proxy); B: 60M - 15M insiders = 45M
        a, bb = r.rows[0]["ctx"], r.rows[1]["ctx"]
        self.assertEqual((a.get("float"), a.get("float_method")), (40e6, "outstanding_proxy"))
        self.assertEqual((bb.get("float"), bb.get("float_method")), (45e6, "insiders"))
        self.assertTrue(a.value("float").approx)
        self.assertAlmostEqual(bb.get("insider_own"), 0.25)

    def test_short_float_and_ratio(self):
        b = {"A": series([10.0] * 300, vols=[500_000.0] * 300)}
        e = env(b, uni("A"), fund=shares(100, 10_000_000), short={"rows": {"A": {"si": 2_500_000}}})
        c = evaluate.run(parser.parse("sh_short_o20"), e).rows[0]["ctx"]
        self.assertAlmostEqual(c.get("short_float"), 0.25)
        self.assertAlmostEqual(c.get("short_ratio"), 5.0)
        self.assertEqual(evaluate.run(parser.parse("sh_short_o25"), e).rows, [])  # 25% exactly is not over 25

    def test_52_week_high_including_today(self):
        highs = [100.0] * 299 + [100.0]
        b = {"A": series([95.0] * 299 + [99.0], highs=highs), "B": series([95.0] * 300, highs=[100.0] * 299 + [99.0])}
        r = evaluate.run(parser.parse("ta_highlow52w_nh"), env(b, uni("A", "B")))
        self.assertEqual([x["t"] for x in r.rows], ["A"])  # today's high equals the prior max: a new high
        c = evaluate.run(parser.parse("ta_highlow52w_b0to10h"), env(b, uni("A", "B"))).rows
        self.assertEqual([x["t"] for x in c], ["A", "B"])  # 99/100 - 1 = -1%; 95/100 - 1 = -5%

    def test_unknown_and_unavailable_codes_are_reported(self):
        b = {"A": series([10.0] * 300)}
        r = evaluate.run(parser.parse("sh_price_o5,an_recom_buy,zz_x"), env(b, uni("A")))
        self.assertEqual([x["t"] for x in r.rows], ["A"])
        self.assertEqual([c for c, _ in r.ignored], ["an_recom_buy", "zz_x"])

    def test_etfs_are_left_out_unless_asked(self):  # D-036, confirmed in parity
        b = {"A": series([10.0] * 300), "E": series([10.0] * 300)}
        u = {**uni("A"), "E": {"exch": "arca", "etf": True, "cik": None}}
        self.assertEqual([x["t"] for x in evaluate.run(parser.parse("sh_price_o5"), env(b, u)).rows], ["A"])
        self.assertEqual([x["t"] for x in evaluate.run(parser.parse("ind_exchangetradedfund"), env(b, u)).rows], ["E"])
        self.assertEqual([x["t"] for x in evaluate.run(parser.parse("ind_stocksonly"), env(b, u)).rows], ["A"])

    def test_average_volume_leaves_out_the_measured_session(self):  # D-043
        vols = [1e6] * 299 + [4e6]
        b = {"A": series([10.0] * 300, vols=vols)}
        c = evaluate.run(parser.parse("sh_price_o5"), env(b, uni("A"))).rows[0]["ctx"]  # CLOSED on 2026-10-20
        self.assertEqual(c.get("avg_volume"), 1e6)
        self.assertEqual(c.get("rel_volume"), 4.0)


class Signals(unittest.TestCase):
    def test_top_gainers_rank_cap_then_sort(self):
        closes = {s: [10.0] * 299 + [10.0 * (1 + k / 100)] for k, s in enumerate(["A", "B", "C", "D"], start=-1)}
        b = {s: series(c) for s, c in closes.items()}
        r = evaluate.run(parser.parse("s=ta_topgainers"), env(b, uni(*b)))
        self.assertEqual([x["t"] for x in r.rows], ["D", "C"])  # +2%, +1%; B is flat, A down
        r = evaluate.run(parser.parse("s=ta_topgainers o=ticker"), env(b, uni(*b)))
        self.assertEqual([x["t"] for x in r.rows], ["C", "D"])

    def test_oversold(self):
        down = [100.0 - i for i in range(300)]
        b = {"A": series(down), "B": series([100.0 + (i % 2) for i in range(300)])}
        r = evaluate.run(parser.parse("s=ta_oversold"), env(b, uni("A", "B")))
        self.assertEqual([x["t"] for x in r.rows], ["A"])


class Live(unittest.TestCase):
    def test_relative_volume_at_1030_from_a_synthetic_profile(self):
        b = {"A": series([10.0] * 300, vols=[1e6] * 300)}
        # profile: 20 sessions whose cumulative volume at each hour mark is 0 before 09:00, 200K at 10:00, 300K at 11:00
        cum = [0.0] * 5 + [100e3, 200e3, 300e3] + [400e3] * 8 + [1e6]
        prof = {"days": {"A": {d: cum for d in DAYS[-20:]}}}
        now = dt.datetime(2026, 10, 21, 10, 30, tzinfo=NY)
        today = {"A": {"o": 10.0, "h": 10.5, "l": 9.9, "c": 10.2, "last": 10.2, "v": 500e3, "n": 10, "pre": 10.1,
                       "post": None, "at": "10:10"}}
        e = env(b, uni("A"), session="RTH", now=now, profile=prof, fetch_today=lambda syms: today)
        e.sessions_done = DAYS  # 2026-10-20 completed; 10-21 trading
        r = evaluate.run(parser.parse("sh_relvol_o1.5"), e)
        c = r.rows[0]["ctx"]
        # expected at 10:30 = 200K + (300K - 200K) * 30/60 = 250K; 500K / 250K = 2.0
        self.assertAlmostEqual(c.get("rel_volume"), 2.0)
        self.assertAlmostEqual(c.get("change"), 0.02)
        self.assertAlmostEqual(c.get("change_open"), 0.02)
        self.assertEqual(r.stage2, 1)

    def test_relative_volume_without_any_profile_uses_the_static_curve(self):
        b = {"A": series([10.0] * 300, vols=[1e6] * 300)}
        today = {"A": {"o": 10.0, "h": 10.5, "l": 9.9, "c": 10.2, "last": 10.2, "v": 500e3, "n": 10, "pre": None,
                       "post": None, "at": "10:10"}}
        now = dt.datetime(2026, 10, 21, 10, 30, tzinfo=NY)
        e = env(b, uni("A"), session="RTH", now=now, profile={}, fetch_today=lambda syms: today)
        c = evaluate.run(parser.parse("sh_relvol_o1.5"), e).rows[0]["ctx"]
        # expected at 10:30 = 1M x (0.0739 + (0.1886 - 0.0739) x 30/60) = 131,250; 500K / 131,250 = 3.8095
        self.assertAlmostEqual(c.get("rel_volume"), 500e3 / 131_250)
        self.assertEqual(c.get("rel_volume_basis"), "static")
        from bot.screener import format as fmt
        self.assertIn("≈", fmt.cell(c, "rel_volume"))
        pre = dt.datetime(2026, 10, 21, 8, 30, tzinfo=NY)  # before the open the static curve is not used
        e = env(b, uni("A"), session="PRE", now=pre, profile={}, fetch_today=lambda syms: today)
        self.assertIsNone(evaluate.run(parser.parse("sh_price_o5"), e).rows[0]["ctx"].get("rel_volume"))

    def test_premarket_price_and_gap(self):
        b = {"A": series([10.0] * 300)}
        now = dt.datetime(2026, 10, 21, 8, 0, tzinfo=NY)
        today = {"A": {"o": None, "h": None, "l": None, "c": None, "last": 11.0, "v": 50e3, "n": 5, "pre": 11.0,
                       "post": None, "at": "07:40"}}
        e = env(b, uni("A"), session="PRE", now=now, fetch_today=lambda syms: today)
        c = evaluate.run(parser.parse("ta_gap_u5"), e).rows[0]["ctx"]
        self.assertAlmostEqual(c.get("gap"), 0.10)
        self.assertAlmostEqual(c.get("price"), 11.0)
        self.assertIsNone(c.get("change_open"))
        self.assertAlmostEqual(c.get("rsi14"), c.get("rsi14"))  # technicals use completed bars before the open

    def test_stage2_cap_truncates_and_reports(self):
        b = {f"S{i:03}": series([10.0] * 300, vols=[1e6 + i] * 300) for i in range(30)}
        now = dt.datetime(2026, 10, 21, 11, 0, tzinfo=NY)
        fetched = []

        def fetch(syms):
            fetched.extend(syms)
            return {s: {"o": 10.0, "h": 10.0, "l": 10.0, "c": 10.0, "last": 10.0, "v": 1.0, "n": 1, "pre": None,
                        "post": None, "at": "10:40"} for s in syms}
        bars = evaluate.bars_so_far(BARS.end_time(now))  # 10:44 -> 14 bars since 04:00
        budget = evaluate.stage2_calls(10, bars)  # 1 request + ceil(140 / PAGE_POINTS) pages = 2 calls
        self.assertEqual(budget, 2)
        self.assertEqual(evaluate.stage2_fit(30, bars, budget), 30)  # 30 x 14 = 420 points: still 2 calls
        r = evaluate.run(parser.parse("sh_price_o5"), env(b, uni(*b), session="RTH", now=now, fetch_today=fetch,
                                                          budget_left=lambda: 1))
        self.assertEqual(len(fetched), 0)  # one call cannot hold a request and its pages
        self.assertEqual(r.truncated, 30)
        fetched.clear()
        many = {f"S{i:04}": series([10.0] * 300, vols=[1e6 + i] * 300) for i in range(1000)}
        fit = evaluate.stage2_fit(1000, bars, 10)  # 1 request + ceil(k x 14 / 580) pages <= 10 -> k = 372
        self.assertEqual(fit, 372)
        r = evaluate.run(parser.parse("sh_price_o5"), env(many, uni(*many), session="RTH", now=now, fetch_today=fetch,
                                                          budget_left=lambda: 10))
        self.assertEqual(len(fetched), 372)
        self.assertEqual(r.truncated, 1000 - 372)
        self.assertEqual(fetched[0], "S0999")  # no sort: the most liquid first (avg volume, daily data)
        self.assertTrue(any(n.startswith("truncated") for n in r.notes))

    def test_stage2_fetches_every_survivor_when_the_budget_allows(self):
        b = {f"S{i:03}": series([10.0] * 300, vols=[1e6 + i] * 300) for i in range(30)}
        now = dt.datetime(2026, 10, 21, 11, 0, tzinfo=NY)
        fetched = []

        def fetch(syms):
            fetched.extend(syms)
            return {}
        r = evaluate.run(parser.parse("sh_price_o5"), env(b, uni(*b), session="RTH", now=now, fetch_today=fetch,
                                                          budget_left=lambda: 140))
        self.assertEqual((len(fetched), r.truncated), (30, 0))

    def test_planner_estimate_follows_the_paging_rule(self):
        self.assertEqual(evaluate.bars_so_far("04:00"), 0)
        self.assertEqual(evaluate.bars_so_far("04:01"), 1)
        self.assertEqual(evaluate.bars_so_far("10:44"), 14)
        self.assertEqual(evaluate.bars_so_far("20:00"), 32)
        self.assertEqual(evaluate.stage2_calls(0, 14), 0)
        # 695 symbols (the 2026-10-22 stage 1 of unusual_volume_liquid) at 15:14: 1 request + ceil(15985 / 580)
        self.assertEqual(evaluate.stage2_calls(695, 23), 1 + 28)
        # 10,000 symbols at 10:44: 13 requests + ceil(140,000 / 580) = 13 + 242
        self.assertEqual(evaluate.stage2_calls(10_000, 14), 13 + 242)
        self.assertEqual(evaluate.stage2_fit(10_000, 14, None), 10_000)
        for budget in (5, 40, 139):
            k = evaluate.stage2_fit(10_000, 14, budget)
            self.assertLessEqual(evaluate.stage2_calls(k, 14), budget)
            self.assertGreater(evaluate.stage2_calls(k + 1, 14), budget)


class Windows(unittest.TestCase):
    def test_earnings_windows(self):
        e = env({}, {}, now=dt.datetime(2026, 10, 21, 8, 0, tzinfo=NY))
        e.day = "2026-10-21"
        ev = [dt.datetime(2026, 10, 21, 7, 0), dt.datetime(2025, 10, 23, 16, 5)]
        self.assertTrue(evaluate.earnings_window(e, "todaybefore", ev))
        self.assertFalse(evaluate.earnings_window(e, "todayafter", ev))
        self.assertTrue(evaluate.earnings_window(e, "nextdays5", ev))  # 2025-10-23 + 364 days = 2026-10-22 (est.)
        self.assertTrue(evaluate.earnings_window(e, "tomorrowafter", ev))
        self.assertTrue(evaluate.earnings_window(e, "thisweek", ev))
        self.assertFalse(evaluate.earnings_window(e, "prevweek", ev))
        self.assertFalse(evaluate.earnings_window(e, "today", None))

    def test_ipo_windows(self):
        e = env({}, {}, now=dt.datetime(2026, 10, 21, 8, 0, tzinfo=NY))
        e.day = "2026-10-21"
        self.assertTrue(evaluate.ipo_window(e, "prevmonth", "2026-10-01"))
        self.assertFalse(evaluate.ipo_window(e, "more1", "2026-10-01"))
        self.assertTrue(evaluate.ipo_window(e, "more10", None))  # listed before 2016-01: over 10.8 years
        self.assertFalse(evaluate.ipo_window(e, "more15", None))  # unknown beyond the history start


if __name__ == "__main__":
    unittest.main()
