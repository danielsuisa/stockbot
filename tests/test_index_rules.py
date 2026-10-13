"""Index program phase 2, task 2 (spec section 4, D1, E4): the rules T, TOM and D on SYNTHETIC closes. The only real
data used is the exchange calendar's dates (the wiring count of 8.2)."""
import unittest

from bot import index_probe, index_rules as ir


def weekdays(start, n):
    import datetime as dt
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(1)
    return out


def rows_of(days, closes, opens=None):
    opens = opens or closes
    return [(d, o, max(o, c) + 1, min(o, c) - 1, c) for d, o, c in zip(days, opens, closes)]


class Averages(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(ir.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        self.assertEqual(ir.sma([100.2] * 5, 5)[-1], sum([100.2] * 5) / 5)  # the mean of the slice: no running drift
        self.assertFalse(100.2 > ir.sma([100.0, 100.2, 100.2, 100.2, 100.2, 100.2], 5)[-1])  # a tie is not "above"

    def test_rsi2_by_hand(self):
        got = ir.rsi2([10, 11, 10.5, 10.5, 12])
        self.assertEqual(got[:2], [None, None])
        self.assertAlmostEqual(got[2], 100 - 100 / 3)  # avg gain 0.5, avg loss 0.25
        self.assertAlmostEqual(got[3], 100 - 100 / 3)  # (0.5 + 0) / 2, (0.25 + 0) / 2
        self.assertAlmostEqual(got[4], 100 - 100 / 15)  # (0.25 + 1.5) / 2 = 0.875, 0.0625
        self.assertEqual(ir.rsi2([10, 11, 12, 13])[2:], [100.0, 100.0])  # average loss 0


class Trend(unittest.TestCase):
    def test_month_ends(self):
        days = ["2020-01-15", "2020-01-28", "2020-02-03", "2020-02-27", "2020-03-02"]
        self.assertEqual(ir.month_ends(days), ["2020-01-28", "2020-02-27", "2020-03-02"])

    def test_sma10_on_off_by_hand(self):
        days = [f"2020-{m:02d}-28" for m in range(1, 13)]  # one row a month: each is its month's last day
        closes = [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 1]
        ev = dict(ir.trend_states(rows_of(days, closes)))
        self.assertEqual(sorted(ev), ["2020-10-28", "2020-11-28", "2020-12-28"])  # ten month-ends needed
        self.assertTrue(ev["2020-10-28"])  # 10 > mean(1..10) = 5.5
        self.assertTrue(ev["2020-11-28"])  # 11 > mean(2..11) = 6.5
        self.assertFalse(ev["2020-12-28"])  # 1 < mean(3..11, 1) = 6.4
        # each state takes effect at the next trading day's open; equal states make no change point; the last
        # month-end has no next day in the file
        self.assertEqual(ir.trend_changes(rows_of(days, closes)), [("2020-11-28", "open", True)])
        rows = rows_of(days + ["2021-01-04"], closes + [2.0])
        self.assertEqual(ir.trend_changes(rows), [("2020-11-28", "open", True), ("2021-01-04", "open", False)])

    def test_months_parameter(self):
        days = [f"2020-{m:02d}-28" for m in range(1, 13)]
        closes = [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 1]
        self.assertEqual(sorted(dict(ir.trend_states(rows_of(days, closes), months=8))),
                         [f"2020-{m:02d}-28" for m in range(8, 13)])


class TurnOfMonth(unittest.TestCase):
    def test_tom_window(self):
        days = [d for d in index_probe.nyse_days("2019-01-01", "2019-03-31")[0]]
        pos = ir.tom_positions(days)
        jan = [p for p in pos if p[0].startswith("2019-01")][0]
        self.assertEqual(jan, ("2019-01-30", "2019-02-05"))  # L = 01-31; exit: 3rd trading day of Feb (1, 4, 5)
        held = ir.held_after_close([jan], days)
        self.assertEqual(sorted(held), ["2019-01-30", "2019-01-31", "2019-02-01", "2019-02-04"])  # four days

    def test_tom_planned_day_not_trading(self):
        days = index_probe.nyse_days("2012-10-01", "2012-11-30")[0]  # Sandy: 10-29 and 10-30 did not trade
        oct_ = [p for p in ir.tom_positions(days) if p[0].startswith("2012-10")][0]
        self.assertEqual(oct_, ("2012-10-31", "2012-11-05"))  # planned 10-30 -> the next close; exit counts traded days
        self.assertEqual(len(ir.held_after_close([oct_], days)), 3)

    def test_rules_skip_missing_day(self):
        days = [d for d in index_probe.nyse_days("2011-01-03", "2011-03-31")[0] if d != "2011-02-17"]  # D1
        feb = [p for p in ir.tom_positions(days) if p[0].startswith("2011-02")][0]
        self.assertEqual(feb, ("2011-02-25", "2011-03-03"))
        self.assertNotIn("2011-02-17", ir.held_after_close(ir.tom_positions(days), days))

    def test_tom_wiring_851(self):
        """8.2: TOM holds SPY after the close on 851 days of P, on the exchange calendar less 2011-02-17 (D1)."""
        days = [d for d in index_probe.nyse_days("2008-11-01", "2026-09-30")[0] if d != "2011-02-17"]
        held = ir.held_after_close(ir.tom_positions(days), days)
        self.assertEqual(sum(1 for d in held if "2009-01-02" <= d <= "2026-09-30"), 851)


class Dip(unittest.TestCase):
    def dip_series(self):
        days = weekdays("2020-01-01", 215)
        closes = [100.0 + 0.5 * k for k in range(205)]  # a steady rise to 202: RSI2 = 100
        closes += [200.0, 198.0]  # RSI2 at 205: gain 0.25, loss 1 -> 20; at 206: 0.125, 1.5 -> 7.69
        closes += [208.0] * 8  # 207: 208 > SMA5 = (201.5 + 202 + 200 + 198 + 208) / 5 = 201.9
        return days, rows_of(days, closes)

    def test_dip_entry_exit(self):
        days, rows = self.dip_series()
        self.assertAlmostEqual(ir.rsi2([r[4] for r in rows])[206], 100 - 100 / (1 + 0.125 / 1.5))
        pos = ir.dip_positions(rows)
        self.assertEqual(pos, [(days[206], days[207], days[207], days[208])])  # signal, entry, exit signal, exit
        self.assertEqual(sorted(ir.held_after_close([(days[207], days[208])], days)), [days[207]])
        # the exit signal may fall on the entry day itself ("on or after the entry day"); no stop, no time limit

    def test_dip_thresholds_parameter(self):
        _, rows = self.dip_series()
        self.assertEqual(ir.dip_positions(rows, rsi_max=5), [])  # 7.69 is not below 5
        self.assertEqual(len(ir.dip_positions(rows, rsi_max=15)), 1)

    def test_dip_needs_sma200(self):
        days = weekdays("2020-01-01", 50)
        self.assertEqual(ir.dip_positions(rows_of(days, [100.0 - k for k in range(50)])), [])

    def test_no_entry_while_open(self):
        days = weekdays("2020-01-01", 230)
        closes = [100.0 + 0.5 * k for k in range(205)] + [200.0, 198.0, 196.0, 194.0] + [193.0 - k for k in range(21)]
        pos = ir.dip_positions(rows_of(days, closes))
        self.assertEqual(len(pos), 1)  # 207 and 208 are dips too, but the first position is still open
        self.assertIsNone(pos[0][3])  # falling every day: never above SMA5, open at the file's end


if __name__ == "__main__":
    unittest.main()
