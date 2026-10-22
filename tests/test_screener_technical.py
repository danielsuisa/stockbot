"""Screener technical metrics (bot/screener/metrics/technical.py): hand-computed fixtures, offline. RSI on the
canonical Wilder / StockCharts 14-period example, Wilder ATR with gap days (both alphas), SMA / price crosses, 52-week
highs including today, volatility, performance, beta, candlesticks, SSR, and every None path."""
import unittest

from bot.screener.metrics import technical as t

# StockCharts "RSI" ChartSchool table (Wilder 14-period example), 33 closes
WILDER = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
          46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
          43.42, 42.66, 43.13]


def bars(rows):
    """[(h, l, c), ...] -> highs, lows, closes."""
    return [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]


# 16 bars -> 15 TRs. Bar 0 (10.5, 9.5, 10) has no TR. Bars 1-6 and 8-14 have h - l = 1 and no gap: TR = 1.
# Bar 7 gaps up: prev close 10, h 11.5, l 10.8 -> TR = max(11.5, 10) - min(10.8, 10) = 1.5 (h - l alone is 0.7).
# Bar 15 gaps up: prev close 11.2, h 13, l 12.4 -> TR = 13 - 11.2 = 1.8 (h - l alone is 0.6).
GAP = ([(10.5, 9.5, 10.0)] * 7 + [(11.5, 10.8, 11.2)] + [(11.7, 10.7, 11.2)] * 7 + [(13.0, 12.4, 12.8)])
# seed = (13 x 1 + 1.5) / 14 = 29/28 = 1.0357142857...
# alpha 1/14: 29/28 + (1.8 - 29/28) / 14 = 1.0357142857 + 0.7642857143 / 14 = 1.0903061224...
# alpha 2/15: 29/28 + 2/15 x (1.8 - 29/28) = 1.0357142857 + 0.1019047619 = 1.1376190476...
ATR_WILDER = 29 / 28 + (1.8 - 29 / 28) / 14
ATR_EMA = 29 / 28 + 2 / 15 * (1.8 - 29 / 28)


class Sma(unittest.TestCase):
    def test_mean_of_last_n(self):
        self.assertAlmostEqual(t.sma([1, 2, 3, 4, 5], 3), 4.0)  # (3 + 4 + 5) / 3

    def test_none(self):
        self.assertIsNone(t.sma([1, 2], 3))
        self.assertIsNone(t.sma([1, None, 3], 2))


class Rsi(unittest.TestCase):
    def test_first_value(self):
        # first 14 changes: gains 0.06+0.72+0.50+0.27+0.32+0.42+0.24+0.14+0.67 = 3.34, losses 0.25+0.54+0.19+0.42
        # = 1.40 (the 14th change is 0); avg 0.238571 / 0.1, RS = 2.385714, RSI = 100 - 100/3.385714 = 70.4641
        self.assertAlmostEqual(t.rsi(WILDER[:15]), 70.46, delta=0.1)
        self.assertAlmostEqual(t.rsi(WILDER[:15]), 100 - 100 / (1 + 3.34 / 1.40), places=9)

    def test_final_value(self):
        # 18 more Wilder steps; exact arithmetic gives 37.7888 (StockCharts rounds its averages: 37.77)
        self.assertAlmostEqual(t.rsi(WILDER), 37.77, delta=0.2)

    def test_window(self):
        long = [50 + (i % 7) - (i % 3) for i in range(400)]
        self.assertAlmostEqual(t.rsi(long), t.rsi(long[-250:]), places=12)
        self.assertNotAlmostEqual(t.rsi(long, window=400), t.rsi(long), places=12)

    def test_edges(self):
        self.assertEqual(t.rsi(list(range(1, 20))), 100.0)  # no losses
        self.assertEqual(t.rsi([5.0] * 20), 50.0)  # no change at all

    def test_none(self):
        self.assertIsNone(t.rsi(WILDER[:14]))  # 14 closes = 13 changes
        self.assertIsNone(t.rsi(None))
        self.assertIsNone(t.rsi(WILDER[:14] + [None]))


class Atr(unittest.TestCase):
    def test_wilder_with_gaps(self):
        self.assertAlmostEqual(t.atr(*bars(GAP)), ATR_WILDER, delta=1e-9)
        self.assertAlmostEqual(ATR_WILDER, 1.0903061224, delta=1e-9)

    def test_alpha_2_over_15(self):
        self.assertAlmostEqual(t.atr(*bars(GAP), alpha=2 / 15), ATR_EMA, delta=1e-9)
        self.assertAlmostEqual(ATR_EMA, 1.1376190476, delta=1e-9)

    def test_seed_only(self):
        # 15 bars -> 14 TRs -> the seed alone, 29/28
        self.assertAlmostEqual(t.atr(*bars(GAP[:15])), 29 / 28, delta=1e-9)

    def test_window(self):
        rows = [(10 + i % 5, 9 - i % 3, 9.5 + i % 4 * 0.1) for i in range(300)]
        self.assertAlmostEqual(t.atr(*bars(rows)), t.atr(*bars(rows[-250:])), places=12)

    def test_none(self):
        self.assertIsNone(t.atr(*bars(GAP[:14])))
        h, l, c = bars(GAP)
        self.assertIsNone(t.atr(h, l, c[:-1]))
        self.assertIsNone(t.atr(h, l[:-1] + [None], c))
        self.assertIsNone(t.atr(None, l, c))


class Volatility(unittest.TestCase):
    H, L, C = [50, 11, 12], [1, 10, 10], [1, 10.5, 11]

    def test_low_denominator(self):
        # last 2 bars: (11-10)/10 = 0.1, (12-10)/10 = 0.2 -> 0.15
        self.assertAlmostEqual(t.volatility(self.H, self.L, self.C, 2), 0.15, places=12)

    def test_close_denominator(self):
        # 1/10.5 = 0.0952381, 2/11 = 0.1818182 -> 0.1385281
        self.assertAlmostEqual(t.volatility(self.H, self.L, self.C, 2, denom="close"), (1 / 10.5 + 2 / 11) / 2,
                               places=12)

    def test_none(self):
        self.assertIsNone(t.volatility(self.H, self.L, self.C, 4))
        self.assertIsNone(t.volatility([1, 2], [0, 1], [1, 2], 2))  # a low of 0
        self.assertIsNone(t.volatility([1, 2], [1, 1], [0, 2], 2, denom="close"))


class Perf(unittest.TestCase):
    def test_perf(self):
        self.assertAlmostEqual(t.perf([100, 110, 121], 2), 0.21, places=12)
        self.assertAlmostEqual(t.perf([100, 110, 121], 1), 0.1, places=12)

    def test_perf_none(self):
        self.assertIsNone(t.perf([100, 110, 121], 3))
        self.assertIsNone(t.perf([0, 110, 121], 2))

    def test_ytd_year_boundary(self):
        # base = 2025-12-31's 100 (the last close of an earlier year); 110 / 100 - 1
        dates = ["2025-12-30", "2025-12-31", "2026-01-02", "2026-01-05"]
        self.assertAlmostEqual(t.perf_ytd(dates, [90, 100, 105, 110]), 0.1, places=12)

    def test_ytd_none(self):
        self.assertIsNone(t.perf_ytd(["2026-01-02", "2026-01-05"], [100, 110]))  # no earlier-year bar
        self.assertIsNone(t.perf_ytd(["2025-12-31", "2026-01-02"], [0, 110]))
        self.assertIsNone(t.perf_ytd([], []))


class HighLow(unittest.TestCase):
    PREV = [80 + i % 20 for i in range(251)]  # previous 251 sessions, max 99
    PREV[100] = 100.0  # the 52-week max

    def test_new_high_includes_today(self):
        self.assertTrue(t.new_high(self.PREV + [100.0], 252))  # equal to the previous max counts
        self.assertFalse(t.new_high(self.PREV + [99.99], 252))

    def test_from_high(self):
        highs = self.PREV + [95.0]
        self.assertAlmostEqual(t.from_high(90.0, highs, 252), -0.1, places=12)  # 90 / 100 - 1

    def test_from_low_and_new_low(self):
        lows = [10, 8, 9, 12]
        self.assertAlmostEqual(t.from_low(12, lows, 4), 0.5, places=12)  # 12 / 8 - 1
        self.assertAlmostEqual(t.from_low(12, lows, 2), 12 / 9 - 1, places=12)
        self.assertTrue(t.new_low([10, 8, 9, 8], 4))
        self.assertFalse(t.new_low([10, 8, 9, 8.01], 4))

    def test_none(self):
        self.assertIsNone(t.new_high([1, 2], 3))
        self.assertIsNone(t.new_low([1, 2], 3))
        self.assertIsNone(t.from_high(1, [1, 2], 3))
        self.assertIsNone(t.from_low(1, [1, 2], 3))
        self.assertIsNone(t.from_high(None, [1, 2], 2))
        self.assertIsNone(t.from_low(1, [0, 2], 2))


class Cross(unittest.TestCase):
    def test_cross(self):
        self.assertEqual(t.cross(1, 3, 2, 2), "above")
        self.assertEqual(t.cross(2, 1, 2, 2), "below")  # from equal counts
        self.assertIsNone(t.cross(3, 4, 2, 2))
        self.assertIsNone(t.cross(None, 4, 2, 2))

    def test_price_cross_sma(self):
        # above: yesterday SMA3 of [10, 10, 9] = 9.667, close 9 <= it; today SMA3 of [10, 9, 12] = 10.333, 12 > it
        self.assertEqual(t.price_cross_sma([10, 10, 10, 9, 12], 3), "above")
        # below: yesterday SMA3 = 31/3 = 10.333, close 11 >= it; today SMA3 of [10, 11, 8] = 9.667, 8 < it
        self.assertEqual(t.price_cross_sma([10, 10, 10, 11, 8], 3), "below")
        # none: yesterday 4 > SMA3 3, today 5 > SMA3 4
        self.assertIsNone(t.price_cross_sma([1, 2, 3, 4, 5], 3))
        self.assertIsNone(t.price_cross_sma([1, 2, 3], 3))

    def test_sma20_sma50(self):
        # 31 x 100, 19 x 90, then 300. Yesterday: SMA20 = (100 + 19 x 90) / 20 = 90.5 <= SMA50 = (3100 + 1710) / 50
        # = 96.2. Today: SMA20 = (1710 + 300) / 20 = 100.5 > SMA50 = (3000 + 1710 + 300) / 50 = 100.2
        closes = [100.0] * 31 + [90.0] * 19 + [300.0]
        self.assertEqual(t.sma_cross(closes, 20, 50), "above")
        self.assertEqual(t.sma_cross(closes, 50, 20), "below")
        self.assertIsNone(t.sma_cross(closes[:-1] + [90.0], 20, 50))  # 90.5 vs 96.2 -> 90 vs 96, no change
        self.assertIsNone(t.sma_cross(closes[1:], 20, 50))  # 50 closes

    def test_sma_cross_small(self):
        # yesterday SMA2 = SMA3 = 3; today SMA2 = 1.5 < SMA3 = 2
        self.assertEqual(t.sma_cross([3, 3, 3, 0], 2, 3), "below")


class Beta(unittest.TestCase):
    @staticmethod
    def closes(rets, start=100.0):
        out = [start]
        for r in rets:
            out.append(out[-1] * (1 + r))
        return out

    def test_exact_slope(self):
        ri = [((i * 7) % 11 - 5) / 100 for i in range(60)]  # -5% .. +5%
        idx, stk = self.closes(ri), self.closes([1.5 * r for r in ri], 50.0)
        self.assertAlmostEqual(t.beta(stk, idx), 1.5, delta=1e-9)

    def test_only_last_n(self):
        # 10 older months with slope -1 are outside the last 60 returns
        old = [0.02, -0.03, 0.01, 0.04, -0.02, 0.03, -0.01, 0.02, -0.04, 0.01]
        ri = old + [((i * 7) % 11 - 5) / 100 for i in range(60)]
        rs = [-r for r in old] + [1.5 * r for r in ri[10:]]
        self.assertAlmostEqual(t.beta(self.closes(rs), self.closes(ri)), 1.5, delta=1e-9)

    def test_min_months(self):
        ri = [((i * 7) % 11 - 5) / 100 for i in range(24)]
        idx, stk = self.closes(ri), self.closes([1.5 * r for r in ri])
        self.assertAlmostEqual(t.beta(stk, idx), 1.5, delta=1e-9)  # 24 returns: enough
        self.assertIsNone(t.beta(stk[1:], idx[1:]))  # 23 returns

    def test_none(self):
        self.assertIsNone(t.beta([100.0] * 30, [100.0] * 30))  # var == 0
        self.assertIsNone(t.beta([100.0] * 30, [100.0] * 29))
        self.assertIsNone(t.beta(None, [100.0] * 30))


class Candlestick(unittest.TestCase):
    DECLINE = [15, 14, 13, 12, 11, 10.5]  # 10.5 < 15 five sessions earlier
    RISE = [10, 11, 12, 13, 14, 15]

    def test_doji(self):
        # rng 1, body 0.05 <= 0.1, upper 0.45, lower 0.5
        self.assertEqual(t.candlestick(10, 10.5, 9.5, 10.05), {"d"})

    def test_dragonfly(self):
        # rng 1.05, body 0.02, upper 0.03 <= 0.105, lower 1.0 >= 0.63
        self.assertEqual(t.candlestick(10, 10.05, 9, 10.02), {"d", "dd", "lls"})

    def test_gravestone(self):
        # rng 1.05, body 0.02, lower 0.03, upper 1.0
        self.assertEqual(t.candlestick(10, 11, 9.95, 9.98), {"d", "gd", "lus"})

    def test_marubozu(self):
        # rng 1.03, body 1 >= 0.9785
        self.assertEqual(t.candlestick(10, 11.02, 9.99, 11), {"mw"})
        self.assertEqual(t.candlestick(11, 11.01, 9.98, 10), {"mb"})

    def test_spinning_top(self):
        # rng 1, body 0.2 (not a doji, <= 0.3), both shadows 0.4 >= body
        self.assertEqual(t.candlestick(10, 10.6, 9.6, 10.2), {"stw"})
        self.assertEqual(t.candlestick(10.2, 10.6, 9.6, 10), {"stb"})

    def test_hammer(self):
        # rng 0.72, body 0.2, lower 0.5 >= 0.4 and >= 0.432 (lls), upper 0.02 <= 0.072
        self.assertEqual(t.candlestick(10, 10.22, 9.5, 10.2, self.DECLINE), {"h", "lls"})
        self.assertEqual(t.candlestick(10, 10.22, 9.5, 10.2, self.RISE), {"lls"})  # no prior decline
        self.assertEqual(t.candlestick(10, 10.22, 9.5, 10.2), {"lls"})
        self.assertEqual(t.candlestick(10, 10.22, 9.5, 10.2, self.DECLINE[1:]), {"lls"})  # 5 closes: too few

    def test_inverted_hammer(self):
        # rng 0.72, body 0.2, upper 0.5, lower 0.02
        self.assertEqual(t.candlestick(10, 10.7, 9.98, 10.2, self.DECLINE), {"ih", "lus"})

    def test_degenerate(self):
        self.assertEqual(t.candlestick(10, 10, 10, 10), frozenset())
        self.assertIsNone(t.candlestick(10, None, 9, 10))


class Ssr(unittest.TestCase):
    def test_ssr(self):
        self.assertTrue(t.ssr(10, 8.9))  # 8.9 <= 9.0, triggered today
        self.assertTrue(t.ssr(10, 9.5, 10, 8.9))  # carried over from yesterday
        self.assertFalse(t.ssr(10, 9.5, 10, 9.5))
        self.assertFalse(t.ssr(10, 9.5))

    def test_none(self):
        self.assertIsNone(t.ssr(None, 9))
        self.assertIsNone(t.ssr(10, None))


class DayRange(unittest.TestCase):
    def test_day_range(self):
        self.assertAlmostEqual(t.day_range(11, 10), 0.1, places=12)
        self.assertIsNone(t.day_range(11, 0))
        self.assertIsNone(t.day_range(None, 10))


if __name__ == "__main__":
    unittest.main()
