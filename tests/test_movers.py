"""Extended-hours movers: Nasdaq quotes, the +10 % / 10 %-volume rule, reference closes, dedupe, log, message."""
import json
import unittest
from unittest import mock

from bot import common, movers

EXT = {"data": {"lastUpdateInfo": ["Data last updated Sep 28, 2026 08:05 AM ET."],
                "infoTable": {"rows": [{"consolidated": "$11.00 +1.00 (+10.00%)", "volume": "120,000"}]}}}
INFO = {"data": {"primaryData": {"lastSalePrice": "$12.50", "volume": "1,500,000",
                                 "lastTradeTimestamp": "Sep 28, 2026 11:02 AM ET"}}}
CAND = {"t": "AAA", "ref": 10.0, "adv": 1_000_000.0, "si_pct": 0.3, "dtc": 4.0}


class Quote(unittest.TestCase):
    def test_nasdaq_hanging_fits_in_the_step_timeout(self):
        # ~120 stocks, THREADS at a time, each call at worst tries x timeout: must leave the 20-minute step room
        with mock.patch.object(movers.common, "fetch", side_effect=OSError("timed out")) as f, \
                mock.patch("builtins.print"):
            movers.quote("AAA", "post")
        kw = f.call_args.kwargs
        self.assertLessEqual(-(-120 // movers.THREADS) * kw["tries"] * kw["timeout"], 300)

    def test_extended_and_regular(self):
        with mock.patch.object(movers.common, "fetch", return_value=json.dumps(EXT).encode()) as f:
            self.assertEqual(movers.quote("BRK-B", "pre"), {"price": 11.0, "volume": 120000.0, "date": "2026-09-28"})
        self.assertIn("/BRK.B/extended-trading?markettype=pre", f.call_args[0][0])
        with mock.patch.object(movers.common, "fetch", return_value=json.dumps(INFO).encode()):
            self.assertEqual(movers.quote("GME", "regular"), {"price": 12.5, "volume": 1500000.0, "date": "2026-09-28"})

    def test_no_trades_is_none_and_a_failure_is_false(self):
        empty = {"data": {"lastUpdateInfo": [], "infoTable": None}}
        with mock.patch.object(movers.common, "fetch", return_value=json.dumps(empty).encode()):
            self.assertIsNone(movers.quote("GME", "post"))
        with mock.patch.object(movers.common, "fetch", side_effect=OSError("down")), mock.patch("builtins.print"):
            self.assertIs(movers.quote("GME", "post"), False)


class Reference(unittest.TestCase):
    B = {"d": ["2026-09-24", "2026-09-25", "2026-09-28"], "c": [9.0, 10.0, 12.0]}

    def test_per_session(self):
        self.assertEqual(movers.reference(self.B, "pre", "2026-09-28"), 10.0)
        self.assertEqual(movers.reference(self.B, "regular", "2026-09-28"), 10.0)
        self.assertEqual(movers.reference(self.B, "post", "2026-09-28"), 12.0)
        self.assertIsNone(movers.reference(self.B, "post", "2026-09-29"))  # no bar for that day yet
        self.assertIsNone(movers.reference(None, "pre", "2026-09-28"))


class Rule(unittest.TestCase):
    def found(self, price, volume, date="2026-09-28", **c):
        return movers.find([{**CAND, **c}], {"AAA": {"price": price, "volume": volume, "date": date}}, "2026-09-28")

    def test_edges(self):
        self.assertEqual(len(self.found(11.0, 100_000)), 1)  # +10.0 %, 10 % of a normal day
        self.assertEqual(self.found(10.99, 100_000), [])  # +9.9 %
        self.assertEqual(self.found(11.0, 99_999), [])  # 9.9999 % of a normal day
        m = self.found(12.0, 250_000)[0]
        self.assertAlmostEqual(m["gain"], 0.2)
        self.assertAlmostEqual(m["vol_ratio"], 0.25)

    def test_stale_or_missing_inputs_are_not_movers(self):
        self.assertEqual(self.found(20.0, 900_000, date="2026-09-25"), [])  # last session's extended data
        self.assertEqual(self.found(20.0, 900_000, ref=None), [])
        self.assertEqual(self.found(20.0, 900_000, adv=None), [])
        self.assertEqual(movers.find([CAND], {"AAA": None}, "2026-09-28"), [])
        self.assertEqual(movers.find([CAND], {"AAA": False}, "2026-09-28"), [])


class Fresh(unittest.TestCase):
    def test_new_then_only_after_ten_more_points(self):
        state, m = {}, lambda g: [{"t": "AAA", "gain": g}]
        self.assertEqual(len(movers.fresh(m(0.12), state, "2026-09-28", "pre")), 1)
        self.assertEqual(movers.fresh(m(0.20), state, "2026-09-28", "pre"), [])
        self.assertEqual(len(movers.fresh(m(0.22), state, "2026-09-28", "pre")), 1)
        self.assertEqual(len(movers.fresh(m(0.12), state, "2026-09-28", "post")), 1)  # a new session starts over

    def test_manual_returns_all_and_still_marks(self):
        state = {"movers": {"2026-09-01:pre": {"OLD": 0.5}}}
        for _ in range(2):
            self.assertEqual(len(movers.fresh([{"t": "AAA", "gain": 0.15}], state, "2026-09-28", "pre", manual=True)), 1)
        self.assertEqual(movers.fresh([{"t": "AAA", "gain": 0.20}], state, "2026-09-28", "pre"), [])
        self.assertEqual(list(state["movers"]), ["2026-09-28:pre"])


class Log(unittest.TestCase):
    def test_append_and_prune(self):
        book = {"entries": [{"date": "2025-01-01", "t": "OLD"}]}
        movers.log(book, [{"t": "AAA", "price": 11.0, "ref": 10.0, "gain": 0.1, "vol_ratio": 0.2, "si_pct": 0.3}],
                   "2026-09-28", "pre", False)
        self.assertEqual([e["t"] for e in book["entries"]], ["AAA"])
        self.assertEqual((book["entries"][0]["session"], book["entries"][0]["manual"]), ("pre", False))


class Text(unittest.TestCase):
    M = {"t": "AAA", "gain": 0.15, "vol_ratio": 0.25, "si_pct": 0.3, "dtc": 4.0, "price": 11.5}

    def test_messages_are_rtl_clean(self):
        t = movers.text([self.M], "pre", "2026-09-28", ranks={"AAA": 3}, failed=2)
        for part in ("<code>AAA</code>", "<code>+15%</code>", "מקום <code>3</code>", "לא נבדק", "<code>2</code>"):
            self.assertIn(part, t)
        self.assertEqual(movers.text([], "post", "2026-09-28"), "")
        manual = movers.text([], "regular", "2026-09-28", manual=True)
        closed = movers.text([], None, "2026-09-28", manual=True)
        self.assertIn("▶️", manual)
        self.assertIn("סגור", closed)
        for x in (t, manual, closed):
            self.assertEqual(common.rtl_bad_lines(x), [])


if __name__ == "__main__":
    unittest.main()
