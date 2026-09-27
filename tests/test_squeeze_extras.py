"""Squeeze extras (display-only, unvalidated): IBKR borrow file and CBOE gamma fuel."""
import datetime as dt
import unittest
from unittest import mock

from bot import borrow, options

USA = ("#BOF|2026.09.25|09:45:02\r\n#SYM|CUR|NAME|CON|ISIN|REBATERATE|FEERATE|AVAILABLE|\r\n"
       "GME|USD|GAMESTOP CORP-CLASS A|36285269|US36467W1099|3.9102|0.2498|>10000000|\r\n"
       "BRK B|USD|BERKSHIRE HATH-CL B|72063691|US0846707026|4.0|0.25|5000000|\r\n"
       "HTZ|USD|HERTZ GLOBAL|1|X|-40.1|45.5|0|\r\n"
       "BAD|USD|BROKEN||||NaNx||\r\n"
       "SAP|EUR|SAP SE|2|DE|1|0.3|1000|\r\n#EOF|5\r\n")


class Borrow(unittest.TestCase):
    def test_parse(self):
        got = borrow.parse(USA)
        self.assertEqual(got["GME"], {"fee": 0.2498, "avail": 10_000_000, "more": True})
        self.assertEqual(got["BRK-B"], {"fee": 0.25, "avail": 5_000_000, "more": False})
        self.assertEqual(got["HTZ"], {"fee": 45.5, "avail": 0, "more": False})
        self.assertEqual(got["BAD"], {"fee": None, "avail": None, "more": False})
        self.assertNotIn("SAP", got)

    def test_unreachable_is_empty(self):
        with mock.patch.object(borrow.ftplib, "FTP", side_effect=TimeoutError("timed out")), mock.patch("builtins.print"):
            self.assertEqual(borrow.fetch(timeout=1), {})


CHAIN = {"iv30": 53.07, "options": [
    {"option": "GME261002C00020000", "open_interest": 1000.0, "volume": 500.0, "delta": 0.6},
    {"option": "GME261016C00030000", "open_interest": 2000.0, "volume": 100.0, "delta": 0.2},
    {"option": "GME261218C00030000", "open_interest": 5000.0, "volume": 10.0, "delta": 0.4},  # 81 days out
    {"option": "GME261002P00020000", "open_interest": 4000.0, "volume": 1.0, "delta": -0.4},
    {"option": "GME1261002C00020000", "open_interest": 9e9, "volume": 9e9, "delta": 1.0}]}  # adjusted series


class Gamma(unittest.TestCase):
    def test_near_dated_call_exposure(self):
        g = options.gamma(CHAIN, 1_000_000, dt.date(2026, 9, 28))
        # near calls: 1000 x 100 x 0.6 + 2000 x 100 x 0.2 = 100,000 shares = 10% of 1,000,000
        self.assertAlmostEqual(g["exposure"], 0.10)
        self.assertAlmostEqual(g["vol_oi"], 600 / 3000)
        self.assertAlmostEqual(g["pc_oi"], 4000 / 8000)
        self.assertEqual(g["iv30"], 53.07)

    def test_expired_and_missing(self):
        self.assertIsNone(options.gamma({"options": []}, 1_000_000, dt.date(2026, 9, 28)))
        self.assertIsNone(options.gamma(CHAIN, None, dt.date(2026, 9, 28)))
        late = options.gamma(CHAIN, 1_000_000, dt.date(2026, 12, 1))  # only the December calls are near: 17 days
        self.assertAlmostEqual(late["exposure"], 5000 * 100 * 0.4 / 1_000_000)

    def test_chain_failure_is_none(self):
        with mock.patch.object(options.common, "fetch", side_effect=OSError("down")), mock.patch("builtins.print"):
            self.assertIsNone(options.chain("GME"))
        with mock.patch.object(options.common, "fetch", return_value=b'{"data": {"options": []}}') as f:
            self.assertEqual(options.chain("BRK-B"), {"options": []})
            self.assertIn("/BRK.B.json", f.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
