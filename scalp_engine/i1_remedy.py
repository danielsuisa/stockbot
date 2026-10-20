"""I2 Stage 0: the I1 duplicate-key remedy (I1 addendum). No network, no outcome.

I1's daily-bar part sip-1d-i1c (2026-10-01..16) and its close - 5 min quote part i1-close-v1 on seven early-close
days repeated keys P1 already held (71,667 identical copies). For each affected date file:
  1. write a new part holding only the keys P1 lacks (sip-1d-i1c2, i1-close-v2), with the rows unchanged;
  2. assert that every key of the old file is either identical in P1 or present, identical, in the new part;
  3. move the old file aside with the store's set-aside suffix (.orphan-N: never read), and record it.
Then the store-wide duplicate check must be 0, and the universe flags and the event table, rebuilt from the repaired
store, must hash exactly as in I1.

    python -m scalp_engine.i1_remedy baseline | remedy | recheck
"""
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

import polars as pl

from scalp_engine import ingest, storage
from scalp_engine import insider_prices as P
from scalp_engine.data_access import partition_dates, read_frame, read_parts

UTC = dt.timezone.utc
ALL = (dt.datetime(2022, 1, 3, 5, tzinfo=UTC), dt.datetime(2026, 11, 2, 4, tzinfo=UTC))
BARS = {"table": "raw_bars_1d", "old": "sip-1d-i1c", "new": "sip-1d-i1c2", "p1": ("sip-1d-v1", "sip-1d-v2", "sip-1d-v3"),
        "key": ["symbol", "event_at"],
        "values": ["open", "high", "low", "close", "volume", "vwap", "trade_count", "feed", "adjustment"],
        "segments": {"source": "alpaca", "feed": "sip"}}
QUOTES = {"table": "raw_quote_samples", "old": "i1-close-v1", "new": "i1-close-v2", "p1": ("sip-samples-v2",),
          "key": ["symbol", "event_at", "sample_rule"],
          "values": ["status", "quote_at", "bid_price", "bid_size", "ask_price", "ask_size", "bid_exchange", "ask_exchange",
                     "conditions", "tape", "size_unit", "lookback_ms", "feed"],
          "segments": {"source": "alpaca", "feed": "sip"}}
DUP_KEYS = {"raw_bars_1d": ["symbol", "event_at"], "raw_quote_samples": ["symbol", "event_at", "sample_rule"],
            "corporate_actions": ["vendor_id"], "calendar": ["trading_date"], "insider_filings": ["accession", "owner_seq"],
            "insider_lines": ["accession", "line_seq"], "filing_acceptance": ["accession", "method"], "issuer_sic": ["cik"],
            "cik_symbol_map": ["kind", "cik", "symbol", "event_at"], "insider_events": ["event_id"]}


def derived(name):
    return storage.data_root() / "derived" / name


def _hash_frame(df):
    rows = df.sort(df.columns).to_dicts()
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def flags_hash(events):
    rows = [dict(e) for e in events]
    for r in rows:
        r["_k"] = f"{r['trigger']}|{r['cik']}|{r['accepted_at']}|{r['completing']}"
    return P.canonical_sha256(rows, "_k", drop=())


def table_hash(df):
    return _hash_frame(df.drop([c for c in ("ingested_at",) if c in df.columns]))


def stored_events():
    return read_frame(storage.data_root() / "insider_events", *ALL).drop("event_at").sort("event_id")


# ---------------------------------------------------------------- baseline

def baseline():
    flagged = json.loads(derived("i1_events_flagged.json").read_text(encoding="utf-8"))["events"]
    ev = read_frame(storage.data_root() / "insider_events", *ALL)
    rec = {"at": dt.datetime.now(UTC).isoformat(), "flags_sha256": flags_hash(flagged), "flag_rows": len(flagged),
           "event_table_sha256": table_hash(ev.select(sorted(ev.columns))), "event_rows": ev.height}
    path = derived("i2_stage0_baseline.json")
    if path.exists():
        raise FileExistsError(f"{path} exists: the baseline is taken once, before the remedy")
    path.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(rec, indent=1))


# ---------------------------------------------------------------- remedy

def _set_aside_done(table):
    return {r["partition"] for r in storage.manifest(table) if r.get("kind") == "set_aside"}


def _rows_tuple(df, cols):
    return [tuple(json.dumps(v, default=str, sort_keys=True) for v in r) for r in df.select(cols).iter_rows()]


def remedy_one(spec, day):
    table, root = spec["table"], storage.data_root() / spec["table"]
    key, vals = spec["key"], spec["values"]
    df = read_parts(root, day, {spec["old"], *spec["p1"], spec["new"]})
    if df.height == 0:
        return None
    old = df.filter(pl.col("_part") == spec["old"])
    p1 = df.filter(pl.col("_part").is_in(list(spec["p1"])))
    if old.height == 0 or p1.height == 0:
        return None
    dup = old.join(p1.select(key).unique(), on=key, how="semi")
    if dup.height == 0:
        return None
    # identical in P1, value by value
    a = dup.sort(key)
    b = p1.join(dup.select(key), on=key, how="semi").sort(key)
    if a.height != b.height or _rows_tuple(a, key + vals) != _rows_tuple(b, key + vals):
        raise AssertionError(f"{table} {day}: a duplicated key differs from its P1 copy")
    keep = old.join(p1.select(key).unique(), on=key, how="anti").drop("_part")
    seg = dict(spec["segments"], date=day)
    if keep.height and storage.partition_key(table, **seg) not in storage.completed(table, part=spec["new"]):
        storage.write_partition(table, keep, part=spec["new"], request_ids=keep["request_id"].unique().to_list(), **seg)
    new = read_parts(root, day, {spec["new"]}).drop("_part") if keep.height else keep
    # every key of the old file is identical in P1 or in the new part
    covered = pl.concat([b.select(key + vals), new.select(key + vals)])
    if old.height != covered.height or sorted(_rows_tuple(old, key + vals)) != sorted(_rows_tuple(covered, key + vals)):
        raise AssertionError(f"{table} {day}: the old file is not fully covered by P1 and the new part")
    src = next(Path(p) for p in partition_dates(root)[day] if Path(p).name == f"part-{spec['old']}.parquet")
    n = sum(1 for _ in src.parent.glob(f"{src.name}.orphan-*"))
    dst = src.with_name(f"{src.name}.orphan-{n}")
    digest = storage.sha256_file(src)
    os.replace(src, dst)
    storage.append_manifest(table, {
        "kind": "set_aside", "table": table, "partition": storage.partition_key(table, **seg), "file": src.name,
        "moved_to": dst.name, "file_sha256": digest, "rows": old.height, "identical_in_p1": dup.height,
        "kept_in_part": spec["new"] if keep.height else None, "kept_rows": keep.height,
        "reason": "I1 duplicate-key remedy (I1 addendum): keys already held by P1 with identical values",
        "at": dt.datetime.now(UTC).isoformat()})
    return {"date": day.isoformat(), "rows": old.height, "identical_in_p1": dup.height, "kept": keep.height}


def remedy():
    out = {}
    for spec in (BARS, QUOTES):
        root = storage.data_root() / spec["table"]
        done = _set_aside_done(spec["table"])
        res = []
        for day, paths in sorted(partition_dates(root).items()):
            if day < dt.date(2022, 1, 3) or not any(Path(p).name == f"part-{spec['old']}.parquet" for p in paths):
                continue
            if storage.partition_key(spec["table"], **dict(spec["segments"], date=day)) in done:
                continue
            r = remedy_one(spec, day)
            if r:
                res.append(r)
                ingest.log(f"remedy {spec['table']} {r}")
        out[spec["table"]] = res
    derived("i2_stage0_remedy.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: {"files": len(v), "rows": sum(x["rows"] for x in v),
                          "identical_in_p1": sum(x["identical_in_p1"] for x in v),
                          "kept": sum(x["kept"] for x in v)} for k, v in out.items()}, indent=1))


# ---------------------------------------------------------------- re-check

def duplicates():
    out = {}
    for table, key in DUP_KEYS.items():
        df = read_frame(storage.data_root() / table, *ALL, columns=[c for c in key if c != "event_at"])
        out[table] = {"rows": df.height, "duplicate_keys": df.height - df.unique(subset=key).height}
    return out


def recheck():
    base = json.loads(derived("i2_stage0_baseline.json").read_text(encoding="utf-8"))
    dups = duplicates()
    mapped = json.loads(derived("i1_events_mapped.json").read_text(encoding="utf-8"))["events"]
    flags = P.flags_for(mapped)
    rebuilt = storage.frame("insider_events", P.event_rows(flags, ingest.now()))
    rec = {"at": dt.datetime.now(UTC).isoformat(), "duplicates": dups,
           "duplicate_total": sum(v["duplicate_keys"] for v in dups.values()),
           "flags_sha256": flags_hash(flags), "flags_equal": flags_hash(flags) == base["flags_sha256"],
           "event_table_sha256": table_hash(rebuilt.select(sorted(rebuilt.columns))),
           "event_rows": rebuilt.height}
    rec["event_table_equal"] = rec["event_table_sha256"] == base["event_table_sha256"]
    rec["pass"] = rec["duplicate_total"] == 0 and rec["flags_equal"] and rec["event_table_equal"]
    derived("i2_stage0_recheck.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(rec, indent=1))
    return 0 if rec["pass"] else 1


def main(argv=None):
    step = (argv or sys.argv[1:] or ["recheck"])[0]
    if step == "baseline":
        baseline()
        return 0
    if step == "remedy":
        remedy()
        return 0
    return recheck()


if __name__ == "__main__":
    sys.exit(main())
