"""Squeeze screener: features, eligibility, ranking and outcome."""
import unittest

from bot import squeeze


def bars(n, price=10.0, vol=1_000_000.0):
    return {"d": [f"2026-01-{k + 1:02d}" if k < 31 else f"2026-02-{k - 30:02d}" for k in range(n)],
            "o": [price] * n, "h": [price] * n, "l": [price] * n, "c": [price] * n, "v": [vol] * n}


class Features(unittest.TestCase):
    def test_windows_and_values(self):
        b = bars(30)
        b["v"][25] = 3_000_000.0
        b["c"][20] = 8.0
        b["c"][25] = 12.0
        f = squeeze.features(b, 25)
        self.assertAlmostEqual(f["rvol"], 3.0)  # v[25] / mean(v[5:25])
        self.assertAlmostEqual(f["ret5"], 12.0 / 8.0 - 1)  # c[25] / c[20]
        self.assertAlmostEqual(f["brk"], 12.0 / 10.0)  # c[25] / max(c[5:25])
        self.assertEqual(f["price"], 12.0)

    def test_never_reads_bar_d(self):
        b = bars(30)
        before = squeeze.features(b, 25)
        b["v"][26], b["c"][26] = 9e9, 99.0
        self.assertEqual(squeeze.features(b, 25), before)

    def test_short_history_and_zero_volume(self):
        self.assertIsNone(squeeze.features(bars(30), 19))
        self.assertIsNone(squeeze.features(bars(30, vol=0.0), 25))


class MakeRow(unittest.TestCase):
    FEAT = {"price": 5.0, "dollar": 3e6, "rvol": 2.0, "ret5": 0.1, "brk": 1.05}

    def test_row(self):
        r = squeeze.make_row("GME", {"si": 30, "dtc": 0.0, "chg": None, "date": "2026-09-15"}, 100, self.FEAT)
        self.assertAlmostEqual(r["si_pct"], 0.3)
        self.assertEqual((r["t"], r["dtc"], r["si_date"], r["rvol"]), ("GME", 0.0, "2026-09-15", 2.0))

    def test_filters(self):
        si = {"si": 30, "dtc": 2.0}
        self.assertIsNone(squeeze.make_row("X", si, None, self.FEAT))
        self.assertIsNone(squeeze.make_row("X", None, 100, self.FEAT))
        self.assertIsNone(squeeze.make_row("X", si, 100, None))
        self.assertIsNone(squeeze.make_row("X", si, 100, {**self.FEAT, "price": 0.99}))
        self.assertIsNone(squeeze.make_row("X", si, 100, {**self.FEAT, "dollar": 1_999_999}))


def row(t, si_pct, dtc=1.0, rvol=1.0, ret5=0.0, brk=1.0):
    return {"t": t, "si_pct": si_pct, "dtc": dtc, "rvol": rvol, "ret5": ret5, "brk": brk}


class Ranking(unittest.TestCase):
    def test_pct_ranks_ties(self):
        self.assertEqual([round(x, 4) for x in squeeze.pct_ranks([5, 1, 5, 3])], [0.8333, 0.0, 0.8333, 0.3333])
        self.assertEqual(squeeze.pct_ranks([7]), [1.0])
        self.assertEqual(squeeze.pct_ranks([]), [])

    def test_gates_order_and_cut(self):
        rows = [row("LOW", 0.05, rvol=9), row("A", 0.30, dtc=5, rvol=3, ret5=0.2, brk=1.2),
                row("B", 0.20, dtc=2, rvol=2, ret5=0.1, brk=1.1), row("QUIET", 0.40, dtc=9, rvol=1.2)]
        got = squeeze.rank(rows, {"w": 0.5, "g_si": 0.10, "g_rv": 1.5})
        self.assertEqual([r["t"] for r in got], ["A", "B"])  # LOW fails the SI gate, QUIET the rvol gate
        self.assertGreater(got[0]["score"], got[1]["score"])
        self.assertEqual(len(squeeze.rank([row(f"T{k}", 0.2, rvol=2) for k in range(15)],
                                          {"w": 0.5, "g_si": 0.1, "g_rv": 1.5})), squeeze.TOP)

    def test_ties_break_by_ticker_and_ranks_are_within_candidates(self):
        got = squeeze.rank([row("ZZ", 0.2, rvol=2), row("AA", 0.2, rvol=2)], {"w": 0.5, "g_si": 0.1, "g_rv": 1.5})
        self.assertEqual([r["t"] for r in got], ["AA", "ZZ"])
        c = squeeze.scored([row("X", 0.05, rvol=50), row("Y", 0.3, rvol=2), row("Z", 0.2, rvol=1)], 0.1)
        self.assertEqual([r["t"] for r in c], ["Y", "Z"])
        self.assertEqual(c[0]["trigger"] > c[1]["trigger"], True)

    def test_empty_day(self):
        self.assertEqual(squeeze.rank([], squeeze.GRID[0]), [])
        self.assertEqual(len(squeeze.GRID), 27)


class Outcome(unittest.TestCase):
    def test_hit_edge_and_values(self):
        b = bars(40)
        b["h"][25], b["l"][27], b["c"][30] = 15.0, 5.0, 12.5  # binary-exact ratios
        o = squeeze.outcome(b, 21, b["d"][30])
        self.assertEqual(o, {"hit": True, "maxup": 0.5, "dd": -0.5, "r10": 0.25})
        b["h"][25] = 14.99
        self.assertFalse(squeeze.outcome(b, 21, b["d"][30])["hit"])

    def test_window_stops_at_end(self):
        b = bars(40)
        b["h"][31] = 30.0
        self.assertFalse(squeeze.outcome(b, 21, b["d"][30])["hit"])

    def test_halt_uses_existing_bars(self):
        b = bars(40)
        end = b["d"][30]
        for k in ("d", "o", "h", "l", "c", "v"):
            del b[k][28:31]  # no bars for sessions 28-30
        self.assertAlmostEqual(squeeze.outcome(b, 21, end)["r10"], 0.0)

    def test_no_open_is_no_trade(self):
        for bad in (None, float("nan"), 0.0):
            b = bars(40)
            b["o"][21] = bad
            self.assertIsNone(squeeze.outcome(b, 21, b["d"][30]))


if __name__ == "__main__":
    unittest.main()
