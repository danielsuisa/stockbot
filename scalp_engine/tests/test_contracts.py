"""B4 contracts: table contracts, recommendation schema, canonical JSON, flat-YAML strictness."""
import copy
import tempfile
import unittest
from pathlib import Path

from scalp_engine import contracts as c

EXAMPLE = c.CONTRACTS / "examples" / "recommendation.example.json"


class Tables(unittest.TestCase):
    def test_all_table_contracts_valid_with_mandatory_columns(self):
        tables = c.table_contracts()
        self.assertEqual(set(tables), {"raw_bars_1m", "raw_bars_1d", "raw_quotes", "raw_quote_samples", "raw_news",
                                       "corporate_actions", "universe_snapshots", "feature_values", "calendar",
                                       "asset_snapshots"})
        for name, t in tables.items():
            with self.subTest(table=name):
                cols = {col["name"] for col in t["columns"]}
                self.assertLessEqual(set(c.MANDATORY_COLUMNS), cols)
                self.assertTrue(t["append_only"])

    def test_feature_rows_carry_available_at(self):
        cols = {col["name"]: col for col in c.table_contracts()["feature_values"]["columns"]}
        self.assertFalse(cols["available_at"]["nullable"])


class Recommendation(unittest.TestCase):
    def setUp(self):
        self.schema = c.load_schema("recommendation")
        self.rec = c.load_json(EXAMPLE)

    def test_example_is_valid(self):
        c.validate(self.rec, self.schema)

    def test_invalid_payloads_are_rejected(self):
        mutations = {
            "missing stop": lambda r: r.pop("stop_price"),
            "float price": lambda r: r.update(stop_price=139.85),
            "extra key": lambda r: r.update(order_id="x"),
            "zero qty": lambda r: r.update(qty=0),
            "bool qty": lambda r: r.update(qty=True),
            "bad side": lambda r: r.update(side="buy"),
            "p above 1": lambda r: r.update(p="1.2"),
            "naive time": lambda r: r.update(created_at="2026-10-16T14:05:00"),
            "bad feature id": lambda r: r.update(feature_versions=["vwap_dev@1.0.0"]),
            "stp_lmt without stop": lambda r: r.update(entry={"type": "STP_LMT", "limit_price": "1.00"}),
            "empty risk checks": lambda r: r.update(risk_checks=[]),
        }
        for label, mutate in mutations.items():
            rec = copy.deepcopy(self.rec)
            mutate(rec)
            with self.subTest(label), self.assertRaises(c.ContractError):
                c.validate(rec, self.schema)

    def test_unknown_schema_keywords_fail_closed(self):
        with self.assertRaises(c.ContractError):
            c.validate(1, {"type": "integer", "multipleOf": 2})


class Canonical(unittest.TestCase):
    def test_sorted_compact_utf8(self):
        self.assertEqual(c.canonical_json({"b": 1, "a": [True, None, "x\n\u001f"], "A": {}}),
                         b'{"A":{},"a":[true,null,"x\\n\\u001f"],"b":1}')

    def test_key_order_does_not_matter(self):
        self.assertEqual(c.canonical_json({"a": 1, "b": 2}), c.canonical_json({"b": 2, "a": 1}))

    def test_outside_the_jcs_subset_raises(self):
        for bad in ({"x": 1.5}, {"x": "\u00e9"}, {"x": 2**53}, {1: "x"}, {"x": float("nan")}):
            with self.subTest(bad=bad), self.assertRaises(c.ContractError):
                c.canonical_json(bad)


class FlatYaml(unittest.TestCase):
    def _load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.yaml"
            p.write_text(text, encoding="utf-8")
            return c.load_flat_yaml(p)

    def test_scalars(self):
        import datetime as dt
        self.assertEqual(self._load("# c\na: 1\nb: 2016-01-01  # note\nc: America/New_York\nd: 'q'\n"),
                         {"a": 1, "b": dt.date(2016, 1, 1), "c": "America/New_York", "d": "q"})

    def test_dotted_keys(self):
        self.assertEqual(self._load("window.x_1.role: veto_only\n"), {"window.x_1.role": "veto_only"})

    def test_rejects_richer_yaml(self):
        for text in ("a:\n  b: 1\n", "a: [1, 2]\n", "a: 1\na: 2\n", "a: &x 1\n", "- a\n", "a: |\n"):
            with self.subTest(text=text), self.assertRaises(c.ContractError):
                self._load(text)


if __name__ == "__main__":
    unittest.main()
