"""The only module in scalp_eval allowed to read tabular data (ADR 0001 section 4, ADR 0002 section 2).

Every read names a UTC half-open range [start, end) on event_at. A range that could include a row
of any sealed holdout window raises HoldoutSealedError *before* a file is opened. Steps:
  1. range check against every window in scalp_contracts/holdout.yaml (unbounded ranges count
     as overlapping); ingestion requests call the same check_range before any network call;
  2. partition pruning on the date={YYYY-MM-DD} directory (America/New_York date of event_at),
     so files of sealed or out-of-range dates are never opened;
  3. a row predicate applied before a row is materialized, plus a post-read assertion that no
     returned row falls inside any window.
The only way past step 1 is an unseal record per run *and* window in the append-only ledger
scalp_contracts/holdout_ledger.jsonl, which only the owner writes (it is committed empty).
"""
import csv
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from scalp_eval.contracts import CONTRACTS, load_flat_yaml

HOLDOUT_FILE = CONTRACTS / "holdout.yaml"
LEDGER_FILE = CONTRACTS / "holdout_ledger.jsonl"
ET = ZoneInfo("America/New_York")
_PARTITION = re.compile(r"^date=(\d{4}-\d{2}-\d{2})$")
_WINDOW_KEY = re.compile(r"^window\.([a-z][a-z0-9_]*)\.(first_date|last_date|role|rationale)$")
_ROLES = {"veto_only", "confirmation"}
_UNSEAL_KEYS = {"run_id", "window", "approved_by", "approved_at", "purpose", "holdout_sha256"}


class HoldoutSealedError(PermissionError):
    """A read could touch a sealed holdout window without an unseal record."""


@dataclass(frozen=True)
class Window:
    name: str
    role: str
    first_date: dt.date  # America/New_York trading date, inclusive
    last_date: dt.date  # America/New_York trading date, inclusive
    start: dt.datetime  # UTC, inclusive
    end: dt.datetime  # UTC, exclusive
    rationale: str

    def contains(self, t):
        return self.start <= _utc(t, "t") < self.end

    def overlaps(self, start, end):
        lo = self.start if start is None else _utc(start, "start")
        hi = self.end if end is None else _utc(end, "end")
        return lo < self.end and hi > self.start


@dataclass(frozen=True)
class Holdout:
    windows: tuple  # of Window, sorted by start
    sha256: str  # of holdout.yaml with LF line endings, bound into unseal records

    def contains(self, t):
        return any(w.contains(t) for w in self.windows)

    def overlapping(self, start, end):
        """Names of the windows that [start, end) overlaps (None bounds are unbounded)."""
        return [w.name for w in self.windows if w.overlaps(start, end)]

    def overlaps(self, start, end):
        return bool(self.overlapping(start, end))


def load_holdout(path=HOLDOUT_FILE):
    cfg = load_flat_yaml(path)
    if cfg.get("schema_version") != 2:
        raise ValueError(f"{path}: unsupported schema_version {cfg.get('schema_version')!r}")
    tz = ZoneInfo(cfg["timezone"])
    fields = {}
    for key, val in cfg.items():
        if key in ("schema_version", "timezone"):
            continue
        m = _WINDOW_KEY.match(key)
        if not m:
            raise ValueError(f"{path}: unknown key {key!r}")
        fields.setdefault(m.group(1), {})[m.group(2)] = val
    windows = []
    for name, f in fields.items():
        if set(f) != {"first_date", "last_date", "role", "rationale"}:
            raise ValueError(f"{path}: window {name!r} needs first_date, last_date, role and rationale")
        first, last = f["first_date"], f["last_date"]
        if not (isinstance(first, dt.date) and isinstance(last, dt.date) and first <= last):
            raise ValueError(f"{path}: window {name!r}: dates must satisfy first_date <= last_date")
        if f["role"] not in _ROLES:
            raise ValueError(f"{path}: window {name!r}: role must be one of {sorted(_ROLES)}")
        start = dt.datetime.combine(first, dt.time(0), tz).astimezone(dt.timezone.utc)
        end = dt.datetime.combine(last + dt.timedelta(days=1), dt.time(0), tz).astimezone(dt.timezone.utc)
        windows.append(Window(name, f["role"], first, last, start, end, f["rationale"]))
    if not windows:
        raise ValueError(f"{path}: no windows")
    windows.sort(key=lambda w: w.start)
    for a, b in zip(windows, windows[1:]):
        if b.start < a.end:
            raise ValueError(f"{path}: windows {a.name!r} and {b.name!r} overlap")
    digest = hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()  # same on any checkout
    return Holdout(tuple(windows), digest)


def _utc(t, name):
    if not isinstance(t, dt.datetime):
        raise TypeError(f"{name} must be a datetime, got {type(t).__name__}")
    if t.tzinfo is None or t.utcoffset() is None:
        raise TypeError(f"{name} must be timezone-aware (naive datetimes are ambiguous)")
    return t.astimezone(dt.timezone.utc)


def day_bounds(day):
    """UTC [start, end) of an America/New_York calendar date."""
    lo = dt.datetime.combine(day, dt.time(0), ET).astimezone(dt.timezone.utc)
    hi = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), ET).astimezone(dt.timezone.utc)
    return lo, hi


# ---------------------------------------------------------------- unseal

def unseal_record(run_id, window, holdout=None, ledger=LEDGER_FILE):
    """Return the ledger's unseal record for (run_id, window), or raise HoldoutSealedError.

    A record is one JSON object per line with keys run_id, window, approved_by, approved_at,
    purpose and holdout_sha256 (the sha256 of holdout.yaml it approves). There is deliberately no
    function that writes the ledger."""
    holdout = holdout or load_holdout()
    if window not in {w.name for w in holdout.windows}:
        raise ValueError(f"unknown holdout window {window!r}")
    path = Path(ledger)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        rec = json.loads(line)
        if set(rec) != _UNSEAL_KEYS:
            raise HoldoutSealedError(f"{path}:{n}: malformed unseal record")
        if rec["run_id"] == run_id and rec["window"] == window and rec["holdout_sha256"] == holdout.sha256:
            return rec
    raise HoldoutSealedError(f"window {window!r} is sealed: no unseal record for run {run_id!r} in {path.name}")


def check_range(start, end, *, unseal_run_id=None, holdout=None, ledger=LEDGER_FILE):
    """Raise unless [start, end) is disjoint from every window, or the run is unsealed for each
    window it overlaps. Used by every read and every ingestion request."""
    holdout = holdout or load_holdout()
    if start is not None and end is not None and _utc(start, "start") >= _utc(end, "end"):
        raise ValueError("empty range: start must be before end")
    hit = holdout.overlapping(start, end)
    if hit and unseal_run_id is None:
        raise HoldoutSealedError(f"range [{start}, {end}) overlaps sealed holdout window(s) {hit}")
    for name in hit:
        unseal_record(unseal_run_id, name, holdout, ledger)
    return holdout


def assert_outside_holdout(rows, holdout, time_column="event_at"):
    """Post-read assertion: no materialized row may carry an event time inside any window."""
    for row in rows:
        if holdout.contains(row[time_column]):
            raise HoldoutSealedError(f"holdout row materialized at {row[time_column].isoformat()}")


# ---------------------------------------------------------------- reads

def read_table(root, start, end, *, time_column="event_at", unseal_run_id=None):
    """Rows with start <= event_at < end from a Hive-partitioned CSV / JSONL table under root.

    Returns a list of dicts sorted by (event_at, file path, line), with event_at parsed to
    an aware UTC datetime; other values stay as strings (CSV) or JSON values (JSONL)."""
    holdout = check_range(start, end, unseal_run_id=unseal_run_id)
    lo, hi = _utc(start, "start"), _utc(end, "end")
    rows = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        day_lo, day_hi = day_bounds(_partition_date(path, root))
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
        raise ValueError(f"{path}: Parquet tables are read with read_frame")
    else:
        raise ValueError(f"{path}: unsupported file type")


def _parse_time(value, path, n):
    if not isinstance(value, str):
        raise ValueError(f"{path} row {n}: missing time column")
    t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(t, f"{path} row {n}")
