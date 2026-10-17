"""B3 static guards (stdlib ast, no new dependency).

1. Only data_access.py may call tabular readers (polars / pandas / pyarrow / duckdb read, scan,
   query and connect functions; also csv readers and sqlite3.connect).
2. scalp_engine never imports scalp_eval (R4), and neither imports stockbot's live `bot` package."""
import ast
import re
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent
NAME = PACKAGE.name
OTHER_SIDE = "scalp_eval"
ALLOWED = {PACKAGE / "data_access.py"}
FORBIDDEN_IMPORTS = {OTHER_SIDE, "bot"}

TABULAR = {"polars", "pandas", "pyarrow", "duckdb", "csv", "sqlite3"}
READER = re.compile(r"^(read_\w*|scan_\w*|query|sql|execute|connect|ParquetFile|ParquetDataset|dataset"
                    r"|open_csv|reader|DictReader|SQLContext)$")


def _aliases(tree):
    """Map local names to dotted module paths for imports of tabular modules."""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in TABULAR:
                    out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in TABULAR:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def reader_calls(source, filename="<src>"):
    """Return [(line, name)] for every direct tabular-reader call or reader import in source."""
    tree = ast.parse(source, filename)
    alias = _aliases(tree)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in TABULAR:
            hits += [(node.lineno, f"{node.module}.{a.name}") for a in node.names if READER.match(a.name)]
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if name in ("__import__", "importlib.import_module") and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.split(".")[0] in TABULAR:
                hits.append((node.lineno, f"dynamic import {arg.value}"))
            continue
        if not name:
            continue
        head, _, rest = name.partition(".")
        full = f"{alias[head]}.{rest}" if head in alias and rest else alias.get(head, name)
        if full.split(".")[0] in TABULAR and READER.match(full.rsplit(".", 1)[-1]):
            hits.append((node.lineno, full))
    return hits


def imports(source, filename="<src>"):
    tree = ast.parse(source, filename)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append((node.lineno, node.module))
        elif isinstance(node, ast.Call) and _dotted(node.func) in ("__import__", "importlib.import_module"):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                out.append((node.lineno, node.args[0].value))
    return out


def sources():
    return sorted(PACKAGE.rglob("*.py"))


class CheckerSelfTest(unittest.TestCase):
    """The checker must catch the call shapes it claims to; otherwise the guard is vacuous."""

    def test_detects_reader_calls(self):
        bad = {
            "import polars as pl\npl.read_parquet('x')": "polars.read_parquet",
            "import polars as pl\npl.scan_parquet('x')": "polars.scan_parquet",
            "import pandas\npandas.read_csv('x')": "pandas.read_csv",
            "import pyarrow.parquet as pq\npq.read_table('x')": "pyarrow.parquet.read_table",
            "from pyarrow import parquet as pq\npq.ParquetFile('x')": "pyarrow.parquet.ParquetFile",
            "import pyarrow.dataset as ds\nds.dataset('x')": "pyarrow.dataset.dataset",
            "import duckdb\nduckdb.connect()": "duckdb.connect",
            "import duckdb\nduckdb.sql('select 1')": "duckdb.sql",
            "import duckdb as d\nd.query('select 1')": "duckdb.query",
            "from polars import read_csv\nread_csv('x')": "polars.read_csv",
            "import csv\ncsv.DictReader(f)": "csv.DictReader",
            "import sqlite3\nsqlite3.connect('x')": "sqlite3.connect",
            "import importlib\nimportlib.import_module('polars')": "dynamic import polars",
        }
        for src, expected in bad.items():
            with self.subTest(src=src):
                self.assertIn(expected, [h[1] for h in reader_calls(src)])

    def test_allows_non_reading_calls(self):
        ok = "import polars as pl\ndf = pl.DataFrame({'a': [1]})\ndf.filter(pl.col('a') > 0)\nimport csv\ncsv.writer(f)"
        self.assertEqual(reader_calls(ok), [])


class StaticGuards(unittest.TestCase):
    def test_only_data_access_calls_tabular_readers(self):
        offenders = []
        for path in sources():
            if path in ALLOWED:
                continue
            for line, name in reader_calls(path.read_text(encoding="utf-8"), str(path)):
                offenders.append(f"{path.relative_to(PACKAGE.parent)}:{line} {name}")
        self.assertEqual(offenders, [], "tabular reads must go through data_access.py")

    def test_data_access_is_the_reader(self):
        # guards against the allowlist pointing at a file that no longer reads anything
        self.assertTrue(reader_calls((PACKAGE / "data_access.py").read_text(encoding="utf-8")))

    def test_no_cross_imports(self):
        offenders = []
        for path in sources():
            for line, mod in imports(path.read_text(encoding="utf-8"), str(path)):
                if mod.split(".")[0] in FORBIDDEN_IMPORTS:
                    offenders.append(f"{path.relative_to(PACKAGE.parent)}:{line} imports {mod}")
        self.assertEqual(offenders, [], f"{NAME} must not import {sorted(FORBIDDEN_IMPORTS)}")


if __name__ == "__main__":
    unittest.main()
