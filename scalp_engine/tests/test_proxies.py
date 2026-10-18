"""P2 proxies: parse-time range filter, EDGAR acceptance-time correction, FOMC/BLS parsers, CIK picking. No network."""
import datetime as dt
import os
import unittest
from unittest import mock

from scalp_engine import data_access as da, proxies

UTC = dt.timezone.utc


class AcceptanceTime(unittest.TestCase):
    # (submissions JSON acceptanceDateTime, filing-header ACCEPTANCE-DATETIME in ET), measured 2026-10-18 (ADR 0003 0.3)
    VECTORS = [
        ("2025-01-31T02:30:23.000Z", "20250130163023"), ("2024-11-01T00:30:25.000Z", "20241031163025"),
        ("2024-08-02T00:30:26.000Z", "20240801163026"), ("2024-05-03T00:30:34.000Z", "20240502163034"),
        ("2024-02-02T02:30:30.000Z", "20240201163030"), ("2023-11-03T00:30:32.000Z", "20231102163032"),
        ("2025-01-30T02:08:56.000Z", "20250129160856"), ("2024-10-31T00:04:07.000Z", "20241030160407"),
        ("2024-07-31T00:03:22.000Z", "20240730160322"), ("2024-04-26T00:03:19.000Z", "20240425160319"),
        ("2024-01-31T02:03:17.000Z", "20240130160317"), ("2023-10-25T00:03:29.000Z", "20231024160329"),
    ]

    def test_measured_vectors(self):
        for js, header in self.VECTORS:
            et = dt.datetime.strptime(header, "%Y%m%d%H%M%S").replace(tzinfo=da.ET)
            with self.subTest(js=js):
                self.assertEqual(proxies.json_acceptance_utc(js), et.astimezone(UTC))


class Parsers(unittest.TestCase):
    def test_bls_archive_names_and_range(self):
        html = ('<a href="/news.release/archives/cpi_01152025.htm">x</a><a href="/news.release/archives/cpi_12102019.htm">'
                '</a><a href="/news.release/archives/cpi_11132026.htm"></a>')
        got = proxies.parse_bls_archive(html, "cpi", dt.date(2023, 6, 1), dt.date(2026, 10, 16))
        self.assertEqual(got, [dt.date(2025, 1, 15)])  # 2019 (sealed) and 2026-11 (after range) dropped at parse time

    def test_fomc_parser(self):
        html = ('<h4>2024 FOMC Meetings</h4><div class="fomc-meeting__month col-xs-5"><strong>Apr/May</strong></div>'
                '<div class="fomc-meeting__date col-xs-4">30-1</div><div class="fomc-meeting__month"><strong>December</strong>'
                '</div><div class="fomc-meeting__date">17-18*</div><h4>2021 FOMC Meetings</h4>'
                '<div class="fomc-meeting__month"><strong>June</strong></div><div class="fomc-meeting__date">15-16*</div>')
        got = proxies.parse_fomc(html, dt.date(2023, 6, 1), dt.date(2026, 10, 16))
        self.assertEqual([d for d, _, _ in got], [dt.date(2024, 5, 1), dt.date(2024, 12, 18)])

    def test_dilution_forms(self):
        for f in ("S-1", "S-1/A", "S-3", "S-3ASR", "S-3/A", "424B2", "424B5"):
            self.assertTrue(proxies.DILUTION_FORMS.match(f), f)
        for f in ("S-8", "S-4", "424H", "10-K", "S-11", "F-3"):
            self.assertFalse(proxies.DILUTION_FORMS.match(f), f)


class FtsPick(unittest.TestCase):
    def test_exact_ticker(self):
        hits = [{"display_names": ["Splunk Inc.  (SPLK)  (CIK 0001353283)"], "ciks": ["0001353283"]},
                {"display_names": ["Cisco  (CSCO)  (CIK 0000858877)"], "ciks": ["0000858877"]}]
        self.assertEqual(proxies.fts_pick("SPLK", hits), ("0001353283", "fts_exact"))

    def test_frequency_among_tickerless(self):
        p = {"display_names": ["PIONEER NATURAL RESOURCES CO  (CIK 0001038357)"], "ciks": ["0001038357"]}
        o = {"display_names": ["EOG RESOURCES INC  (EOG)  (CIK 0000821189)"], "ciks": ["0000821189"]}
        self.assertEqual(proxies.fts_pick("PXD", [p, p, p, o, o]), ("0001038357", "fts_frequency"))
        self.assertEqual(proxies.fts_pick("PXD", [p, o]), (None, None))  # a single hit is not enough
        q = {"display_names": ["OTHER CO  (CIK 0000000001)"], "ciks": ["0000000001"]}
        self.assertEqual(proxies.fts_pick("PXD", [p, p, q, q]), (None, None))  # tie


class Identity(unittest.TestCase):
    def test_missing_user_agent(self):
        with mock.patch.dict(os.environ, {"SEC_USER_AGENT": ""}), self.assertRaises(proxies.IdentityMissing):
            proxies.Http()

    def test_bls_gets_only_its_own_agent(self):
        env = {"SEC_USER_AGENT": "Test Name test@example.com", "BLS_USER_AGENT": ""}
        with mock.patch.dict(os.environ, env):
            http = proxies.Http()
            self.assertEqual(http.agent("data.sec.gov"), "Test Name test@example.com")
            self.assertNotIn("@", http.agent("cdn.cboe.com"))
            with self.assertRaises(proxies.IdentityMissing):  # never falls back to the SEC identity
                http.agent("www.bls.gov")
        with mock.patch.dict(os.environ, dict(env, BLS_USER_AGENT="Other bls@example.org")):
            self.assertEqual(proxies.Http().agent("www.bls.gov"), "Other bls@example.org")

    def test_sealed_declared_range_never_reaches_network(self):
        with mock.patch.dict(os.environ, {"SEC_USER_AGENT": "Test Name test@example.com"}):
            http = proxies.Http()
            with mock.patch("urllib.request.urlopen") as net, self.assertRaises(da.HoldoutSealedError):
                http.get(proxies.CBOE_VIX, table="vix_daily", declared_range=(None, dt.datetime(2026, 1, 1, tzinfo=UTC)))
            net.assert_not_called()


if __name__ == "__main__":
    unittest.main()
