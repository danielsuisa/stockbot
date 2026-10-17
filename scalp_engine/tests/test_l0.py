"""L0 ingestion units on synthetic data (no network, temp data root): Parquet guard, append-only store,
manifest, universe method, request guard."""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import polars as pl

from scalp_engine import alpaca_client, data_access as da, ingest, storage, universe

UTC = dt.timezone.utc


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"SCALP_DATA_ROOT": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def bars(self, times, symbol="TEST", close=10.0):
        return storage.frame("raw_bars_1m", [{
            "event_at": t, "symbol": symbol, "open": close, "high": close, "low": close, "close": close,
            "volume": 100, "vwap": close, "trade_count": 1, "feed": "sip", "adjustment": "raw", "asof": "-",
            "request_id": "r", "ingested_at": utc(2026, 10, 17), "source": "synthetic", "schema_version": 2}
            for t in times])


class Store(TempRoot):
    def test_write_then_read_frame(self):
        storage.write_partition("raw_bars_1m", self.bars([utc(2024, 6, 12, 14, 31), utc(2024, 6, 12, 14, 30)]),
                                part="p1", source="synthetic", feed="sip", date=dt.date(2024, 6, 12))
        df = da.read_frame(Path(self.tmp.name) / "raw_bars_1m", utc(2024, 6, 12, 4), utc(2024, 6, 13, 4))
        self.assertEqual(df["event_at"].to_list(), [utc(2024, 6, 12, 14, 30), utc(2024, 6, 12, 14, 31)])  # pk-sorted
        rec = [r for r in storage.manifest("raw_bars_1m") if r["kind"] == "partition"]
        self.assertEqual(rec[0]["rows"], 2)
        self.assertEqual(rec[0]["partition"], "raw_bars_1m/source=synthetic/feed=sip/date=2024-06-12")

    def test_append_only(self):
        kw = dict(part="p1", source="synthetic", feed="sip", date=dt.date(2024, 6, 12))
        storage.write_partition("raw_bars_1m", self.bars([utc(2024, 6, 12, 14, 30)]), **kw)
        with self.assertRaises(FileExistsError):
            storage.write_partition("raw_bars_1m", self.bars([utc(2024, 6, 12, 14, 30)]), **kw)

    def test_orphan_from_crashed_run_is_set_aside(self):
        kw = dict(source="synthetic", feed="sip", date=dt.date(2024, 6, 12))
        folder = storage.partition_path("raw_bars_1m", **kw)
        folder.mkdir(parents=True)
        (folder / "part-p1.parquet").write_bytes(b"partial")
        storage.write_partition("raw_bars_1m", self.bars([utc(2024, 6, 12, 14, 30)]), part="p1", **kw)
        self.assertTrue((folder / "part-p1.parquet.orphan-0").exists())
        self.assertEqual(len(da.partition_dates(Path(self.tmp.name) / "raw_bars_1m")[dt.date(2024, 6, 12)]), 1)

    def test_contract_enforced(self):
        bad = self.bars([utc(2024, 6, 12, 14, 30)]).drop("feed")
        with self.assertRaises(ValueError):
            storage.validate("raw_bars_1m", bad)
        with self.assertRaises(ValueError):  # non-nullable column with a null
            storage.validate("raw_bars_1m", self.bars([utc(2024, 6, 12, 14, 30)]).with_columns(pl.lit(None).alias("feed")))

    def test_data_root_outside_repo(self):
        with mock.patch.dict(os.environ, {"SCALP_DATA_ROOT": str(Path(storage.__file__).parent)}):
            with self.assertRaises(PermissionError):
                storage.data_root()


class ParquetGuard(TempRoot):
    def setUp(self):
        super().setUp()
        self.root = Path(self.tmp.name) / "raw_bars_1m"
        for day, times in [(dt.date(2021, 12, 31), [utc(2021, 12, 31, 20)]),  # sealed
                           (dt.date(2022, 1, 3), [utc(2022, 1, 3, 15)]),
                           (dt.date(2026, 10, 30), [utc(2026, 10, 30, 15)]),
                           (dt.date(2026, 11, 2), [utc(2026, 11, 2, 15)])]:  # sealed
            storage.write_partition("raw_bars_1m", self.bars(times), part="p", source="synthetic", feed="sip", date=day)

    def test_overlap_raises(self):
        with self.assertRaises(da.HoldoutSealedError):
            da.read_frame(self.root, utc(2021, 12, 30), utc(2022, 1, 5))
        with self.assertRaises(da.HoldoutSealedError):
            da.read_frame(self.root, utc(2026, 10, 30), utc(2026, 11, 3))

    def test_between_windows(self):
        df = da.read_frame(self.root, utc(2022, 1, 1, 5), utc(2026, 11, 2, 5))
        self.assertEqual(df["event_at"].to_list(), [utc(2022, 1, 3, 15), utc(2026, 10, 30, 15)])

    def test_mislabelled_partition_rows_are_filtered_and_asserted(self):
        storage.write_partition("raw_bars_1m", self.bars([utc(2021, 12, 31, 15), utc(2022, 1, 4, 15)]), part="bad",
                                source="synthetic", feed="sip", date=dt.date(2022, 1, 4))
        df = da.read_frame(self.root, utc(2022, 1, 1, 5), utc(2022, 1, 5, 5))
        self.assertEqual(df["event_at"].to_list(), [utc(2022, 1, 3, 15), utc(2022, 1, 4, 15)])

    def test_cache_miss_raises_never_fetches(self):
        with self.assertRaises(da.MissingPartitionError):
            da.read_frame(self.root, utc(2022, 1, 1, 5), utc(2022, 1, 6, 5),
                          require_dates=[dt.date(2022, 1, 3), dt.date(2022, 1, 5)])

    def test_symbol_and_column_pushdown(self):
        df = da.read_frame(self.root, utc(2022, 1, 1, 5), utc(2026, 11, 2, 5), columns=["symbol", "close"],
                           symbols={"TEST"})
        self.assertEqual(df.columns, ["event_at", "symbol", "close"])


class Universe(unittest.TestCase):
    def a(self, symbol, name, exchange="NYSE", status="active"):
        return {"id": symbol, "symbol": symbol, "name": name, "exchange": exchange, "status": status}

    def test_classify(self):
        keep = [self.a("AAPL", "Apple Inc. Common Stock", "NASDAQ"), self.a("BRK.B", "Berkshire Hathaway Inc."),
                self.a("IVZ", "Invesco Ltd."), self.a("WT", "WisdomTree, Inc."), self.a("BBW", "Build-A-Bear Workshop, Inc."),
                self.a("UCTT", "Ultra Clean Holdings, Inc.", "NASDAQ"), self.a("BABA", "Alibaba Group Holding Limited American Depositary Shares"),
                self.a("OLDCO", "", "NASDAQ", "inactive"), self.a("GONE_DELISTED", "", "NYSE", "inactive")]
        drop = [self.a("SPY", "SPDR S&P 500 ETF Trust", "ARCA"), self.a("QQQ", "Invesco QQQ Trust, Series 1", "NASDAQ"),
                self.a("TQQQ", "ProShares UltraPro QQQ", "NASDAQ"), self.a("JPM.PRL", "JPMorgan Chase & Co. Depositary Shares 4.625% Preferred"),
                self.a("EONR.WS", "EON Resources Inc. Warrants"), self.a("NBA.U", "New Beginnings Acquisition Corp. Units, each consisting"),
                self.a("GTERW", "Globa Terra Acquisition Corporation Warrants", "NASDAQ"), self.a("GIWWR", "GigCapital8 Corp. Rights", "NASDAQ"),
                self.a("GAB", "The Gabelli Equity Trust Fund"), self.a("X", "Something", "OTC"), self.a("B002455", "", "NYSE")]
        for asset in keep:
            with self.subTest(keep=asset["symbol"]):
                self.assertTrue(universe.classify(asset)[0], universe.classify(asset))
        for asset in drop:
            with self.subTest(drop=asset["symbol"]):
                self.assertFalse(universe.classify(asset)[0])
        self.assertFalse(universe.classify(self.a("ONEQ", "Fidelity Nasdaq Composite Index Tracking Stock", "NASDAQ"))[0])
        self.assertFalse(universe.classify(self.a("QQQX", "Nuveen Nasdaq 100 Trust, Series A", "NASDAQ"))[0])
        self.assertEqual(universe.pool(drop), {})
        p = universe.pool(keep, renames=[("SQ", "AAPL", dt.date(2025, 1, 21))])
        self.assertIn("GONE", p)  # _DELISTED stripped
        self.assertTrue(p["OLDCO"]["name_missing"])
        self.assertEqual(p["SQ"]["status"], "renamed")

    def test_renames_bound_validity_and_inherit_classification(self):
        assets = [self.a("XYZ", "Block, Inc. Class A Common Stock"), self.a("SPYM", "SPDR Portfolio S&P 500 ETF", "ARCA"),
                  self.a("STRC", "Strategy Inc Variable Rate Series A Perpetual Preferred Stock", "NASDAQ")]
        renames = [("SQ", "XYZ", dt.date(2025, 1, 21)), ("SPLG", "SPYM", dt.date(2025, 10, 31)),
                   ("STRC", "PDYN", dt.date(2024, 4, 8)), ("SILJ", "SILJ", dt.date(2024, 1, 29))]
        p = universe.pool(assets, renames)
        self.assertNotIn("SPLG", p)  # became an ETF: not common
        self.assertNotIn("STRC", p)  # ticker now held by a preferred: its own record decides
        self.assertEqual((p["SQ"]["valid_until"], p["XYZ"]["valid_from"]), (dt.date(2025, 1, 21), dt.date(2025, 1, 21)))
        self.assertEqual(universe.candidates(p, dt.date(2025, 1, 2)), {"SQ"})
        self.assertEqual(universe.candidates(p, dt.date(2025, 2, 3)), {"XYZ"})

    def test_reconstitute_uses_prior_window_only(self):
        days = [dt.date(2024, 5, 1) + dt.timedelta(days=i) for i in range(20)]
        rows = []
        for i, d in enumerate(days):
            t = dt.datetime.combine(d, dt.time(4), UTC)
            rows += [{"symbol": "BIG", "event_at": t, "close": 50.0, "volume": 1_000_000, "vwap": 50.0},
                     {"symbol": "SMALL", "event_at": t, "close": 20.0, "volume": 1_000, "vwap": 20.0},
                     {"symbol": "PENNY", "event_at": t, "close": 2.0, "volume": 9_000_000, "vwap": 2.0}]
            if i >= 10:  # only 10 bars: below MIN_BARS
                rows.append({"symbol": "NEW", "event_at": t, "close": 30.0, "volume": 9_000_000, "vwap": 30.0})
        m = universe.reconstitute(pl.DataFrame(rows), days, {"BIG", "SMALL", "PENNY", "NEW"})
        self.assertEqual(m["symbol"].to_list(), ["BIG", "SMALL"])
        self.assertEqual(m["rank"].to_list(), [1, 2])
        with self.assertRaises(ValueError):
            universe.reconstitute(pl.DataFrame(rows), days[:-1], {"BIG"})


class RequestGuard(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"ALPACA_KEY_ID": "test", "ALPACA_SECRET_KEY": "test"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_sealed_request_never_reaches_the_network(self):
        c = alpaca_client.Client()
        with mock.patch("urllib.request.urlopen") as net:
            for rng in [(utc(2021, 12, 1), utc(2022, 1, 5)), (utc(2026, 10, 30), utc(2026, 11, 3)), (None, utc(2023, 1, 1))]:
                with self.subTest(rng=rng), self.assertRaises(da.HoldoutSealedError):
                    c.get(alpaca_client.DATA, "/v2/stocks/bars", {}, table="raw_bars_1m", event_range=rng)
            net.assert_not_called()

    def test_only_read_endpoints(self):
        c = alpaca_client.Client()
        with mock.patch("urllib.request.urlopen") as net, self.assertRaises(ValueError):
            c.get(alpaca_client.TRADING, "/v2/orders", {}, table="x", event_range=None)
        net.assert_not_called()

    def test_missing_credentials(self):
        with mock.patch.dict(os.environ, {"ALPACA_KEY_ID": ""}), self.assertRaises(alpaca_client.CredentialsMissing):
            alpaca_client.Client()


class Helpers(unittest.TestCase):
    def test_parse_ns_truncates_to_microseconds(self):
        self.assertEqual(ingest._parse_ns("2024-06-12T13:59:59.912345678Z"), utc(2024, 6, 12, 13, 59, 59, 912345))
        self.assertEqual(ingest._parse_ns("2024-06-12T13:59:59Z"), utc(2024, 6, 12, 13, 59, 59))

    def test_quote_times(self):
        self.assertEqual(len(ingest.QUOTE_TIMES), 26)
        self.assertEqual((ingest.QUOTE_TIMES[0], ingest.QUOTE_TIMES[24], ingest.QUOTE_TIMES[25]),
                         (dt.time(9, 30), dt.time(11, 30), dt.time(15, 55)))

    def test_et_conversion_across_dst(self):
        self.assertEqual(ingest.et(dt.date(2024, 6, 12), dt.time(9, 30)), utc(2024, 6, 12, 13, 30))
        self.assertEqual(ingest.et(dt.date(2024, 12, 12), dt.time(9, 30)), utc(2024, 12, 12, 14, 30))


if __name__ == "__main__":
    unittest.main()
