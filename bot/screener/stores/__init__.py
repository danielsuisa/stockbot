"""The engine's cached stores: gz-JSON files under .cache/screener/ (SCREENER_CACHE overrides; gitignored), written to
a temp file and renamed (never half-written). Each store is {"meta": {"as_of", "source", "rows", ...}, "data": ...}.
COUNTS counts this run's requests per source (the run's budget line)."""
import datetime as dt
import gzip
import json
from pathlib import Path

from bot import common

SAVED = [False]  # a store was written this run (the workflow's cache-save condition, written at once)
COUNTS = {"alpaca": 0, "sec": 0, "finra": 0, "nt": 0, "occ": 0}
MAX_AGE_DAYS = {"bars": 4, "long": 40, "profile": 10, "universe": 4, "shortint": 31, "fundamentals": 120,
                "reference": 200, "ownership": 200}  # calendar days (bars: 1 session plus a weekend and a holiday)


def root():
    return Path(common.env("SCREENER_CACHE", str(common.ROOT / ".cache" / "screener")))


def path(name):
    return root() / f"{name}.json.gz"


def load(name):
    """Store `name` -> {"meta", "data"} or None when it was never written (or is unreadable)."""
    p = path(name)
    if not p.exists():
        return None
    try:
        return json.loads(gzip.decompress(p.read_bytes()))
    except (OSError, ValueError, EOFError):
        return None


def save(name, data, source, rows=None, **meta):
    """Write store `name` atomically -> its meta. as_of = now (UTC ISO) unless given."""
    m = {"as_of": meta.pop("as_of", None) or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "source": source, "rows": len(data) if rows is None and hasattr(data, "__len__") else rows, **meta}
    p = path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(gzip.compress(json.dumps({"meta": m, "data": data}, separators=(",", ":")).encode(), 6))
    tmp.replace(p)
    mark_saved()
    return m


def mark_saved():
    """Record that a store changed, and tell the workflow at once (GITHUB_OUTPUT `changed=true`): a step killed
    by its timeout never reaches the run's final output, and the cache-save step would skip everything written."""
    if SAVED[0]:
        return
    SAVED[0] = True
    out = common.env("GITHUB_OUTPUT")
    if out and common.env("SCREENER_MODE"):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("changed=true\n")


def age_days(meta, now=None):
    """Days since a store's as_of (float), or None without one."""
    if not meta or not meta.get("as_of"):
        return None
    t = dt.datetime.fromisoformat(meta["as_of"].replace("Z", "+00:00"))
    return ((now or dt.datetime.now(dt.timezone.utc)) - t).total_seconds() / 86400


def stale(name, meta, now=None):
    """True when the store is missing or older than its max age."""
    a = age_days(meta, now)
    return a is None or a > MAX_AGE_DAYS.get(name, 7)


def count(source, n=1):
    COUNTS[source] = COUNTS.get(source, 0) + n
