"""Squeeze extras (display-only, unvalidated): IBKR borrow file and CBOE gamma fuel."""
import datetime as dt
import unittest
from unittest import mock

from bot import borrow

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


if __name__ == "__main__":
    unittest.main()
