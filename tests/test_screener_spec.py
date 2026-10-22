"""Gate C / D: every metric the catalog references has exactly one row in docs/screener/metric-spec.md with an
evidence tag, and one row in docs/screener/data-sources.md with an availability class."""
import re
import unittest
from pathlib import Path

from bot.screener import catalog

DOCS = Path(__file__).resolve().parents[1] / "docs" / "screener"
TAGS = ("OBSERVED", "INFERRED", "ASSUMED", "UNKNOWN", "DECISION")
ROW = re.compile(r"^\| `([a-z0-9_]+)` \|(.*)\| ([^|]+) \|\s*$", re.M)


def catalog_metrics():
    out = set()
    for f in catalog.load()["filters"]:
        out.add(f["metric"])
        for o in f["options"].values():
            if "metric" in o:
                out.add(o["metric"])
            if o.get("pred") and o["pred"][0] == "all":
                out |= {p["metric"] for p in o["pred"][1]}
    return out


def rows(name, section="## Metrics"):
    text = (DOCS / name).read_text("utf-8")
    text = text.split(section, 1)[1].split("\n## ", 1)[0]
    return ROW.findall(text)


class Spec(unittest.TestCase):
    def test_every_catalog_metric_has_exactly_one_spec_row_with_an_evidence_tag(self):
        got = rows("metric-spec.md")
        ids = [m for m, _, _ in got]
        self.assertEqual(len(ids), len(set(ids)), "duplicate spec rows")
        self.assertEqual(sorted(catalog_metrics() - set(ids)), [])
        for m, _, ev in got:
            self.assertTrue(any(t in ev for t in TAGS), f"{m}: no evidence tag in {ev!r}")

    def test_every_catalog_metric_has_exactly_one_source_row(self):
        got = rows("data-sources.md")
        ids = [m for m, _, _ in got]
        self.assertEqual(len(ids), len(set(ids)), "duplicate source rows")
        self.assertEqual(sorted(catalog_metrics() - set(ids)), [])
        for m, middle, _ in got:
            self.assertRegex(middle, r"\b(EXACT|APPROX|UNAVAILABLE_FREE)\b", m)


if __name__ == "__main__":
    unittest.main()
