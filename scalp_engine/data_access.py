"""The only module in scalp_engine allowed to read tabular data (ADR 0001 section 4).

Every read names a UTC half-open range [start, end) on event_at. A range that could include
any holdout row raises HoldoutSealedError *before* a file is opened. The guard works in
three steps:
  1. range check against scalp_contracts/holdout.yaml (unbounded ranges count as overlapping);
  2. partition pruning on the date={YYYY-MM-DD} directory (America/New_York date of event_at),
     so files of sealed or out-of-range dates are never opened;
  3. a row predicate applied while streaming, before a row is materialized, plus a post-read
     assertion that no returned row falls inside the holdout.
The only way past step 1 is an unseal record for a named run in the append-only ledger
scalp_contracts/holdout_ledger.jsonl, which only the owner writes (it is committed empty).

P0 backends: CSV and JSON Lines (stdlib). The Parquet backend lands in P1 together with its
dependency (ADR 0001 section 6); until then .parquet raises NotImplementedError.
"""
import csv
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from scalp_engine.contracts import CONTRACTS, load_flat_yaml

HOLDOUT_FILE = CONTRACTS / "holdout.yaml"
LEDGER_FILE = CONTRACTS / "holdout_ledger.jsonl"
_PARTITION = re.compile(r"^date=(\d{4}-\d{2}-\d{2})$")
_UNSEAL_KEYS = {"run_id", "approved_by", "approved_at", "purpose", "holdout_sha256"}


class HoldoutSealedError(PermissionError):
    """A read could touch the sealed holdout without an unseal record."""


@dataclass(frozen=True)
class Holdout:
    first_date: dt.date  # America/New_York, inclusive
    last_date: dt.date  # America/New_York, inclusive
    start: dt.datetime  # UTC, inclusive
    end: dt.datetime  # UTC, exclusive
    sha256: str  # of holdout.yaml, bound into unseal records

    def contains(self, t):
        return self.start <= _utc(t, "t") < self.end

    def overlaps(self, start, end):
        lo = self.start if start is None else _utc(start, "start")
        hi = self.end if end is None else _utc(end, "end")
        return lo < self.end and hi > self.start


def load_holdout(path=HOLDOUT_FILE):
    cfg = load_flat_yaml(path)
    if cfg.get("schema_version") != 1:
        raise ValueError(f"{path}: unsupported schema_version {cfg.get('schema_version')!r}")
    first, last = cfg["first_date"], cfg["last_date"]
    if not (isinstance(first, dt.date) and isinstance(last, dt.date) and first <= last):
        raise ValueError(f"{path}: first_date / last_date must be dates with first <= last")
    tz = ZoneInfo(cfg["timezone"])
    start = dt.datetime.combine(first, dt.time(0), tz).astimezone(dt.timezone.utc)
    end = dt.datetime.combine(last + dt.timedelta(days=1), dt.time(0), tz).astimezone(dt.timezone.utc)
    digest = hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()  # same on any checkout
    return Holdout(first, last, start, end, digest)


def _utc(t, name):
    if not isinstance(t, dt.datetime):
        raise TypeError(f"{name} must be a datetime, got {type(t).__name__}")
    if t.tzinfo is None or t.utcoffset() is None:
        raise TypeError(f"{name} must be timezone-aware (naive datetimes are ambiguous)")
    return t.astimezone(dt.timezone.utc)


# ---------------------------------------------------------------- unseal

def unseal_record(run_id, holdout=None, ledger=LEDGER_FILE):
    """Return the ledger's unseal record for run_id, or raise HoldoutSealedError.

    A record is one JSON object per line with keys run_id, approved_by, approved_at, purpose and
    holdout_sha256 (the sha256 of holdout.yaml it approves). There is deliberately no function
    that writes the ledger."""
    holdout = holdout or load_holdout()
    path = Path(ledger)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        rec = json.loads(line)
        if set(rec) != _UNSEAL_KEYS:
            raise HoldoutSealedError(f"{path}:{n}: malformed unseal record")
        if rec["run_id"] == run_id and rec["holdout_sha256"] == holdout.sha256:
            return rec
    raise HoldoutSealedError(f"holdout is sealed: no unseal record for run {run_id!r} in {path.name}")


def check_range(start, end, *, unseal_run_id=None, holdout=None):
    """Raise unless [start, end) is disjoint from the holdout (or the run is unsealed)."""
    holdout = holdout or load_holdout()
    if start is not None and end is not None and _utc(start, "start") >= _utc(end, "end"):
        raise ValueError("empty range: start must be before end")
    if holdout.overlaps(start, end):
        if unseal_run_id is None:
            raise HoldoutSealedError(
                f"range [{start}, {end}) overlaps the sealed holdout "
                f"[{holdout.start.isoformat()}, {holdout.end.isoformat()})")
        unseal_record(unseal_run_id, holdout)
    return holdout


def assert_outside_holdout(rows, holdout, time_column="event_at"):
    """Post-read assertion: no materialized row may carry a holdout event time."""
    for row in rows:
        if holdout.contains(row[time_column]):
            raise HoldoutSealedError(f"holdout row materialized at {row[time_column].isoformat()}")


# ---------------------------------------------------------------- reads

def read_table(root, start, end, *, time_column="event_at", unseal_run_id=None):
    """Rows with start <= event_at < end from a Hive-partitioned table under root.

    Returns a list of dicts sorted by (event_at, file path, line), with event_at parsed to
    an aware UTC datetime; other values stay as strings (CSV) or JSON values (JSONL)."""
    holdout = check_range(start, end, unseal_run_id=unseal_run_id)
    lo, hi = _utc(start, "start"), _utc(end, "end")
    tz = ZoneInfo("America/New_York")
    rows = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        day = _partition_date(path, root)
        day_lo = dt.datetime.combine(day, dt.time(0), tz).astimezone(dt.timezone.utc)
        day_hi = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tz).astimezone(dt.timezone.utc)
        if day_hi <= lo or day_lo >= hi:
            continue  # pruned: the file is never opened
        if unseal_run_id is None and holdout.overlaps(day_lo, day_hi):
            continue  # a sealed partition is never opened
        for n, row in enumerate(_scan(path)):
            t = _parse_time(row.get(time_column), path, n)
            if lo <= t < hi and (unseal_run_id is not None or not holdout.contains(t)):
                row[time_column] = t
                rows.append((t, str(path), n, row))
    rows.sort(key=lambda r: r[:3])
    out = [r[3] for r in rows]
    if unseal_run_id is None:
        assert_outside_holdout(out, holdout, time_column)
    return out


def _partition_date(path, root):
    for part in path.relative_to(root).parts[:-1]:
        m = _PARTITION.match(part)
        if m:
            return dt.date.fromisoformat(m.group(1))
    raise ValueError(f"{path}: no date=YYYY-MM-DD partition directory")


def _scan(path):
    """Yield one dict per row without holding the file in memory."""
    if path.suffix == ".csv":
        with open(path, newline="", encoding="utf-8") as f:
            yield from csv.DictReader(f)
    elif path.suffix == ".jsonl":
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
    elif path.suffix == ".parquet":
        raise NotImplementedError("Parquet backend arrives in P1 with its dependency (ADR 0001 section 6)")
    else:
        raise ValueError(f"{path}: unsupported file type")


def _parse_time(value, path, n):
    if not isinstance(value, str):
        raise ValueError(f"{path} row {n}: missing time column")
    t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(t, f"{path} row {n}")
