"""Append-only Parquet store under SCALP_DATA_ROOT (ADR 0002 section 6). Writes only; reads go
through data_access.

Layout: <root>/<table>/<partition segments>/part-<name>.parquet, one part per ingestion batch.
Parts are written to a temp file and renamed into place; an existing part is never overwritten.
<root>/manifests/<table>.jsonl records every vendor request (params, rows, body sha256) and every
committed partition (file, rows, file sha256). A partition with a 'partition' record is complete;
reruns skip it.
"""
import datetime as dt
import hashlib
import json
import os
import threading
from pathlib import Path

import polars as pl

from scalp_engine.contracts import table_contracts

DEFAULT_ROOT = r"D:\scalp-data"
_TYPES = {
    "timestamp[us, tz=UTC]": pl.Datetime("us", "UTC"),
    "date32": pl.Date,
    "string": pl.Utf8,
    "float64": pl.Float64,
    "int64": pl.Int64,
    "int32": pl.Int32,
    "bool": pl.Boolean,
    "list<string>": pl.List(pl.Utf8),
}
_lock = threading.Lock()


def data_root():
    root = Path(os.environ.get("SCALP_DATA_ROOT") or DEFAULT_ROOT).resolve()
    repo = Path(__file__).resolve().parent.parent
    if root == repo or repo in root.parents:
        raise PermissionError(f"SCALP_DATA_ROOT must be outside the repository: {root}")
    return root


def contract(table):
    return table_contracts()[table]


def schema(table):
    """Polars schema of a table contract, in contract column order."""
    return {c["name"]: _TYPES[c["type"]] for c in contract(table)["columns"]}


def frame(table, rows):
    """Build a DataFrame from row dicts with exactly the contract's columns and types."""
    sch = schema(table)
    df = pl.DataFrame(rows, schema=sch) if rows else pl.DataFrame(schema=sch)
    return validate(table, df)


def validate(table, df):
    c = contract(table)
    sch = schema(table)
    if list(df.columns) != list(sch):
        raise ValueError(f"{table}: columns {df.columns} != contract {list(sch)}")
    df = df.cast(sch, strict=True)
    for col in c["columns"]:
        if not col["nullable"] and df[col["name"]].null_count():
            raise ValueError(f"{table}: column {col['name']!r} has nulls")
    if df.height and (df["schema_version"] != c["schema_version"]).any():
        raise ValueError(f"{table}: schema_version must be {c['schema_version']}")
    return df


def partition_path(table, **segments):
    """<root>/<table>/<k=v>/... in the contract's partition order."""
    path = data_root() / table
    for seg in contract(table)["partitioning"]:
        key = seg.split("=")[0]
        path /= f"{key}={segments[key]}"
    return path


def partition_key(table, **segments):
    return str(partition_path(table, **segments).relative_to(data_root())).replace("\\", "/")


def manifest_path(table):
    return data_root() / "manifests" / f"{table}.jsonl"


def append_manifest(table, record):
    path = manifest_path(table)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def manifest(table):
    path = manifest_path(table)
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def completed(table, part=None):
    """Partition keys recorded as complete in the manifest (for one part name, if given)."""
    name = None if part is None else f"part-{part}.parquet"
    return {r["partition"] for r in manifest(table)
            if r.get("kind") == "partition" and (name is None or r["file"] == name)}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_partition(table, df, *, part, request_ids=(), **segments):
    """Write df as <partition>/part-<part>.parquet (atomic), then record it in the manifest.

    The part must not exist yet (append-only). Rows are sorted by the contract's primary key so the
    file content does not depend on fetch order."""
    df = validate(table, df)
    pk = contract(table)["primary_key"]
    df = df.sort([c for c in pk if c in df.columns])
    folder = partition_path(table, **segments)
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"part-{part}.parquet"
    if final.exists():
        if partition_key(table, **segments) in completed(table, part):
            raise FileExistsError(f"append-only store: {final} already exists")
        # renamed into place by a run that died before its manifest record: set it aside, unread
        n = sum(1 for _ in folder.glob(f"part-{part}.parquet.orphan-*"))
        os.replace(final, folder / f"part-{part}.parquet.orphan-{n}")
    tmp = folder / f".part-{part}.parquet.tmp"
    df.write_parquet(tmp, compression="zstd", statistics=True)
    digest = sha256_file(tmp)
    os.replace(tmp, final)
    append_manifest(table, {
        "kind": "partition", "table": table, "partition": partition_key(table, **segments),
        "file": final.name, "rows": df.height, "file_sha256": digest,
        "request_ids": sorted(set(request_ids)),
        "written_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    return final
