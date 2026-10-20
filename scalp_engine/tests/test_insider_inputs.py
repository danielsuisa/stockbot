"""I2 engine inputs (ADR 0010 section 1): the universe-(b) table builder and the signed artifact digest."""
import datetime as dt
import unittest

import polars as pl
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scalp_engine import insider_inputs as U
from scalp_engine.signing import sign_artifact


def sessions(n, start=dt.date(2022, 1, 3)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


class Universe(unittest.TestCase):
    def test_flags_deciles_cik(self):
        s = sessions(40)
        rows = []
        for sym, px, vol in (("AAA", 10.0, 20000), ("BBB", 5.0, 100000), ("CCC", 0.5, 2_000_000), ("DDD", 3.0, 50000)):
            rows += [(sym, d, px, vol) for d in s]
        b = pl.DataFrame(rows, schema={"symbol": pl.Utf8, "d": pl.Date, "close": pl.Float64, "volume": pl.Int64}, orient="row")
        sp = pl.DataFrame([(sym, d, 0.01 if sym != "DDD" else 0.05) for sym in ("AAA", "BBB", "CCC", "DDD") for d in s],
                          schema={"symbol": pl.Utf8, "d": pl.Date, "spread": pl.Float64}, orient="row")
        obs = pl.DataFrame([("AAA", dt.date(2021, 6, 1), "1"), ("BBB", dt.date(2020, 1, 1), "2"), ("CCC", s[0], "3"),
                            ("DDD", s[0], "4")], schema={"symbol": pl.Utf8, "d": pl.Date, "cik": pl.Utf8}, orient="row")
        sic = pl.DataFrame({"cik": ["1", "2", "3", "4"], "sic": ["3674", "2834", "0000", "6770"]})
        u = U.universe_frame(b, s, {"CCC": [(s[30], 20.0)]}, sp, obs, sic)
        u = u.with_columns(pl.col("session").cast(pl.Date))
        day = u.filter(pl.col("session") == s[25])
        aaa = day.filter(pl.col("symbol") == "AAA").to_dicts()[0]
        self.assertEqual((aaa["universe_b"], aaa["cik"], aaa["sic2"]), (True, "1", "36"))
        bbb = day.filter(pl.col("symbol") == "BBB").to_dicts()[0]
        self.assertIsNone(bbb["cik"])  # last observation more than 400 days before
        self.assertEqual(day.filter(pl.col("symbol") == "DDD")["spread_ok"][0], False)  # 5% spread
        self.assertNotIn("CCC", day["symbol"].to_list())  # $0.50 fails the pre-filter
        ccc = u.filter((pl.col("session") == s[30]) & (pl.col("symbol") == "CCC")).to_dicts()[0]
        self.assertEqual((ccc["price"], ccc["price_ok"], ccc["sic2"]), (10.0, True, None))  # 1:20 split effective on d; SIC 0000
        dec = dict(zip(day.filter(pl.col("universe_b"))["symbol"], day.filter(pl.col("universe_b"))["dv_decile"]))
        self.assertEqual(dec, {"AAA": 1, "BBB": 6})  # n = 2: ranks 0, 1 -> deciles 1, 6
        self.assertIsNone(day.filter(pl.col("symbol") == "DDD")["dv_decile"][0])
        self.assertTrue(all(x >= dt.date(2022, 2, 1) for x in u["session"].to_list()))


class Signing(unittest.TestCase):
    def test_artifact_signature_covers_domain_and_payload(self):
        import base64
        from scalp_engine.contracts import canonical_json
        from scalp_engine.signing import ARTIFACT_DOMAIN
        key = Ed25519PrivateKey.generate()
        payload = {"kind": "insider_event_set", "table": "insider_events", "rule_version": "x", "rows": 3, "sha256": "a" * 64}
        env = sign_artifact(payload, key)
        sig = base64.urlsafe_b64decode(env["signature"]["value"] + "==")
        key.public_key().verify(sig, ARTIFACT_DOMAIN + b"\n" + canonical_json(payload))
        with self.assertRaises(Exception):
            key.public_key().verify(sig, ARTIFACT_DOMAIN + b"\n" + canonical_json(dict(payload, rows=4)))
        with self.assertRaises(Exception):
            sign_artifact(dict(payload, rows=-1), key)

    def test_canonical_hash_ignores_ingest_time_and_order(self):
        a = pl.DataFrame({"b": [2, 1], "a": ["y", "x"], "ingested_at": [1, 2]})
        b = pl.DataFrame({"a": ["x", "y"], "b": [1, 2], "ingested_at": [9, 9]})
        self.assertEqual(U.canonical_sha256(a), U.canonical_sha256(b))


if __name__ == "__main__":
    unittest.main()
