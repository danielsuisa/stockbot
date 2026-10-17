"""The locked Noise Area rule. No network."""
import unittest
from pathlib import Path

from bot import noise_area as na
from bot import noise_area_backtest as bt


def bar(minute, o, h, l, c, v=1_000, vw=None):
    return (minute, o, h, l, c, v, o if vw is None else vw)


def session(spike_at="09:59", spike=102.0, last=103.0):
    """A flat 100 session with one signal bar and a 10:00 open, plus the closing bar."""
    bars = [bar("09:30", 100, 100.2, 99.8, 100, vw=100)]
    bars.append(bar(spike_at, spike, spike, spike, spike, vw=spike))
    bars.append(bar("10:00", 102.2, 102.3, 102.1, 102.2, vw=102.2))
    bars.append(bar("15:59", last, last + 0.2, last - 0.1, last, vw=last))
    return bars


class Lock(unittest.TestCase):
    def test_the_multiplier_window_and_gate_are_fixed(self):
        self.assertEqual(na.MULT, 1.0)
        self.assertEqual(na.LOOKBACK, 14)
        self.assertEqual((na.COMMISSION, na.MIN_ORDER, na.SLIP), (0.0035, 0.35, 0.01))
        self.assertEqual((bt.START, bt.OOS), ("2016-01-01", "2023-01-01"))
        self.assertEqual(na.DECISIONS[0], "10:00")
        self.assertEqual(na.DECISIONS[-1], "15:30")
        self.assertEqual(na.minute_before("10:00"), "09:59")
        text = Path(bt.__file__).read_text(encoding="utf-8")
        self.assertIn("at least 300", text)
        self.assertIn("t > 2", text)
        self.assertIn("Sharpe > 0.8", text)
        self.assertIn("70%", text)
        self.assertIn("long-only", text)
        self.assertNotIn("ib_insync", text)
        self.assertNotIn("submit", text.lower())
        self.assertIn("does not place or transmit an order", Path(na.__file__).read_text(encoding="utf-8").lower())

    def test_bands_use_the_open_and_the_previous_close(self):
        upper, lower = na.bands(110, 100, 0.02)
        self.assertAlmostEqual(upper, 110 * 1.02)
        self.assertAlmostEqual(lower, 100 * 0.98)

    def test_sigma_uses_the_last_fourteen_and_not_a_shorter_window(self):
        self.assertIsNone(na.trailing_sigma([0.01] * 13))
        self.assertAlmostEqual(na.trailing_sigma([0.5] + [0.01] * 14), 0.01)

    def test_a_cross_of_the_upper_band_enters_once_and_exits_at_the_close(self):
        got = na.simulate_day(session(), 100, {"10:00": 0.01, "10:30": 0.01})
        self.assertEqual(got["status"], "ok")
        self.assertEqual(len(got["trades"]), 1)
        trade = got["trades"][0]
        self.assertEqual((trade["side"], trade["entry_minute"], trade["kind"]), ("long", "10:00", "close"))
        self.assertAlmostEqual(trade["entry"], 102.21)
        self.assertAlmostEqual(trade["exit"], 102.99)
        self.assertEqual(trade["shares"], 97)  # int(10000 / 102.2)
        pnl = (102.99 - 102.21) * 97 - 0.70
        self.assertAlmostEqual(trade["pnl"], pnl)
        self.assertAlmostEqual(trade["ret"], pnl / 10_000)

    def test_long_only_does_not_take_the_short_and_a_second_long_is_refused(self):
        down = session(spike=97.0, last=96.0)
        # 97 is below the lower band 99 when sigma is 0.01 and both anchors are 100.
        self.assertEqual(na.simulate_day(down, 100, {"10:00": 0.01}, side="long")["trades"], [])
        both = na.simulate_day(down, 100, {"10:00": 0.01}, side="both")
        self.assertEqual([t["side"] for t in both["trades"]], ["short"])
        two = session()
        two.insert(3, bar("10:29", 103, 103, 103, 103))
        two.insert(4, bar("10:30", 103, 103, 103, 103))
        got = na.simulate_day(two, 100, {"10:00": 0.01, "10:30": 0.01})
        self.assertEqual(len(got["trades"]), 1)

    def test_the_stop_is_the_higher_of_the_band_and_vwap(self):
        bars = session(last=110)
        # After the entry, the 10:29 close is under the upper band. VWAP is about 100, so the stop is the band.
        bars.insert(3, bar("10:29", 100.4, 100.5, 100.2, 100.4, v=10_000, vw=100.4))
        bars.insert(4, bar("10:30", 100.4, 100.4, 100.4, 100.4))
        got = na.simulate_day(bars, 100, {"10:00": 0.01, "10:30": 0.01})
        self.assertEqual(got["trades"][0]["kind"], "stop")
        self.assertEqual(got["trades"][0]["exit_minute"], "10:30")
        self.assertAlmostEqual(got["trades"][0]["exit"], 100.39)


class Feed:
    """Fourteen warmup sessions and one trade session. Sigma is 1% by the trade day."""

    def __init__(self):
        self.days = [f"2015-12-{d:02d}" for d in range(1, 15)] + ["2016-01-04"]

    def sessions(self, start, end):
        return [(d, "16:00") for d in self.days if start <= d <= end]

    def daily(self, symbols, start, end):
        return {s: [(d, 100, 101, 99, 100, 1_000_000) for d in self.days] for s in symbols}

    def minutes(self, symbols, day):
        close = 102.0 if day == "2016-01-04" else 101.0
        bars = [bar("09:30", 100, 100, 100, 100), bar("09:59", close, close, close, close),
                bar("10:00", close, close, close, close), bar("15:59", close, close, close, close)]
        return {s: bars for s in symbols}


class Replay(unittest.TestCase):
    def test_today_is_not_inside_sigma_and_the_report_renders(self):
        result = bt.replay(Feed(), today="2016-01-05")
        self.assertTrue(result["complete"])
        self.assertFalse(result["go"])  # one session cannot clear 300 trades
        qqq = [t for t in result["books"][("QQQ", "both", na.NOTIONAL)]["trades"]]
        self.assertEqual(len(qqq), 1)
        self.assertEqual(qqq[0]["side"], "long")
        text = bt.render(result)
        self.assertIn("NO-GO", text)
        self.assertIn("What was locked", text)


class Gate(unittest.TestCase):
    def test_every_check_is_required(self):
        good = {"n": 300, "mean": 0.001, "t": 2.01, "sharpe": 0.81, "daily_mean": 0.001,
                "bench_mean": 0.0005, "years": 4, "years_ok": 3}
        long = {"mean": 0.0001}
        checks, go = bt.judge(good, long, True)
        self.assertTrue(go)
        self.assertEqual(len(checks), 6)
        weak = dict(good, t=2.0)
        self.assertFalse(bt.judge(weak, long, True)[1])
        self.assertFalse(bt.judge(good, {"mean": -0.01}, True)[1])
        self.assertFalse(bt.judge(good, long, False)[1])
        short_years = dict(good, years_ok=2)
        self.assertFalse(bt.judge(short_years, long, True)[1])


if __name__ == "__main__":
    unittest.main()
