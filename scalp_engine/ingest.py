"""L0 ingestion from the Alpaca free plan (P1). Read-only vendor access; append-only store.

    python -m scalp_engine.ingest STEP --start 2023-08-01 --end 2026-10-16 [--workers 8]

STEP: calendar | assets | actions | daily | universe | plan | minute | all.
The end date is always explicit; this module never derives it from the clock. The CLI only checks
that the end day's extended session closed more than 15 minutes ago (incomplete days are never stored).
Every step resumes: units recorded as complete in the manifest are skipped. Library functions
take dates as arguments; reads go through data_access, which never fetches (a miss raises).
"""
import argparse
import calendar as cal_mod
import concurrent.futures as cf
import datetime as dt
import json
import sys
import threading
import time

import polars as pl

from scalp_engine import storage, universe
from scalp_engine.alpaca_client import DATA, TRADING, ApiError, Client
from scalp_engine.data_access import ET, day_bounds, partition_dates, read_frame

UTC = dt.timezone.utc
SOURCE = "alpaca"
SIZE_UNIT_SWITCH = dt.date(2025, 11, 3)  # Alpaca CTA/UTP quote sizes: round lots before, shares from (measured)
# ET sample times: 09:30, 09:35, ..., 11:30 (25), plus the session close - 5 minutes from the calendar
# (15:55 on a normal day, 12:55 on an early close). P1 used a fixed 15:55 (fixed_1555_v1).
QUOTE_TIMES = [dt.time(9 + (30 + 5 * i) // 60, (30 + 5 * i) % 60) for i in range(25)]
CLOSE_SAMPLE_OFFSET = dt.timedelta(minutes=5)
SAMPLE_RULE = "close_minus_5m_v2"
QUOTE_LOOKBACKS_MS = (5_000, 60_000)
WARMUP_SESSIONS = 30
SYMBOL_CHUNK = 400


def now():
    return dt.datetime.now(UTC)


def et(day, hhmm):
    return dt.datetime.combine(day, hhmm, ET).astimezone(UTC)


def z(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S") + ("Z" if not t.microsecond else f".{t.microsecond:06d}Z")


def log(msg):
    print(f"{now():%Y-%m-%dT%H:%M:%SZ} {msg}", flush=True)


def range_bounds(start, end):
    return day_bounds(start)[0], day_bounds(end)[1]


# ---------------------------------------------------------------- calendar

def ingest_calendar(client, start, end):
    table = "calendar"
    days, rid = client.get(TRADING, "/v2/calendar", {"start": start.isoformat(), "end": end.isoformat()},
                           table=table, event_range=range_bounds(start, end))
    got = now()
    done = storage.completed(table)
    for d in days:
        day = dt.date.fromisoformat(d["date"])
        key = storage.partition_key(table, source=SOURCE, date=day)
        if key in done:
            continue
        hm = lambda s: dt.time(int(s.replace(":", "")[:2]), int(s.replace(":", "")[2:]))
        close = hm(d["close"])
        row = {"event_at": day_bounds(day)[0], "trading_date": day, "session_open_at": et(day, hm(d["session_open"])),
               "open_at": et(day, hm(d["open"])), "close_at": et(day, close),
               "session_close_at": et(day, hm(d["session_close"])), "early_close": close < dt.time(16, 0),
               "settlement_date": dt.date.fromisoformat(d["settlement_date"]) if d.get("settlement_date") else None,
               "feed": "none", "request_id": rid, "ingested_at": got, "source": SOURCE, "schema_version": 1}
        storage.write_partition(table, storage.frame(table, [row]), part="calendar-v1", request_ids=[rid],
                                source=SOURCE, date=day)
    log(f"calendar: {len(days)} sessions")


def sessions(start, end):
    """Calendar rows (DataFrame) for sessions in [start, end], from the store."""
    df = read_frame(storage.data_root() / "calendar", *range_bounds(start, end))
    if df.height == 0:
        raise LookupError("calendar not ingested")
    return df.sort("trading_date")


# ---------------------------------------------------------------- assets

def ingest_assets(client):
    table = "asset_snapshots"
    if partition_dates(storage.data_root() / table):
        log("assets: snapshot exists, skipped")
        return
    assets, rid = client.get(TRADING, "/v2/assets", {"status": "all", "asset_class": "us_equity"},
                             table=table, event_range=None)
    got = now()
    rows = [{"event_at": got, "asset_id": a["id"], "symbol": a["symbol"], "name": a.get("name") or None,
             "exchange": a["exchange"], "asset_class": a["class"], "status": a["status"],
             "tradable": bool(a.get("tradable")), "marginable": bool(a.get("marginable")),
             "shortable": bool(a.get("shortable")), "easy_to_borrow": bool(a.get("easy_to_borrow")),
             "fractionable": bool(a.get("fractionable")), "attributes": a.get("attributes") or [],
             "feed": "none", "request_id": rid, "ingested_at": got, "source": SOURCE, "schema_version": 1}
            for a in assets]
    snap = got.astimezone(ET).date()
    storage.write_partition(table, storage.frame(table, rows), part="assets-v1", request_ids=[rid],
                            source=SOURCE, date=snap)
    log(f"assets: {len(rows)} records, snapshot {snap}")


def latest_assets():
    root = storage.data_root() / "asset_snapshots"
    parts = partition_dates(root)
    if not parts:
        raise LookupError("asset snapshot not ingested")
    day = max(parts)
    return read_frame(root, *day_bounds(day)).to_dicts()


# ---------------------------------------------------------------- corporate actions

def _months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first = max(start, dt.date(y, m, 1))
        last = min(end, dt.date(y, m, cal_mod.monthrange(y, m)[1]))
        yield first, last
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _int_or_none(x):
    return int(x) if isinstance(x, (int, float)) and float(x).is_integer() else None


def ingest_actions(client, start, end):
    table = "corporate_actions"
    root = storage.data_root() / table
    if any(r.get("kind") == "step_done" for r in storage.manifest(table)):
        log("actions: done, skipped")
        return
    rows, rids = [], []
    for first, last in _months(start, end):
        token = None
        while True:
            data, rid = client.get(DATA, "/v1/corporate-actions",
                                   {"start": first.isoformat(), "end": last.isoformat(), "limit": 1000,
                                    "page_token": token}, table=table, event_range=range_bounds(first, last))
            rids.append(rid)
            got = now()
            for kind, recs in sorted((data.get("corporate_actions") or {}).items()):
                for r in recs:
                    date_s = r.get("ex_date") or r.get("effective_date") or r.get("process_date")
                    if not date_s:
                        continue
                    day = dt.date.fromisoformat(date_s)
                    if not (start <= day <= end):
                        continue  # outside the requested range (never stored; may be sealed)
                    rows.append({
                        "event_at": day_bounds(day)[0], "action_type": kind,
                        "symbol": r.get("symbol") or r.get("old_symbol") or r.get("target_symbol") or r.get("acquiree_symbol") or "",
                        "new_symbol": r.get("new_symbol") or r.get("acquirer_symbol"),
                        "ratio_new": _int_or_none(r.get("new_rate")), "ratio_old": _int_or_none(r.get("old_rate")),
                        "cash_amount": repr(r["rate"]) if isinstance(r.get("rate"), (int, float)) else None,
                        "declared_at": day_bounds(dt.date.fromisoformat(r["declaration_date"]))[0] if r.get("declaration_date") else None,
                        "vendor_id": r.get("id"),
                        "process_date": dt.date.fromisoformat(r["process_date"]) if r.get("process_date") else None,
                        "raw": json.dumps(r, sort_keys=True, separators=(",", ":")),
                        "feed": "none", "request_id": rid, "ingested_at": got, "source": SOURCE, "schema_version": 2})
            token = data.get("next_page_token")
            if not token:
                break
    df = storage.frame(table, rows)
    done = storage.completed(table)
    for (day,), part in df.with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date()) \
                          .partition_by("d", as_dict=True, maintain_order=True).items():
        if storage.partition_key(table, source=SOURCE, date=day) in done:
            continue
        storage.write_partition(table, part.drop("d"), part="actions-v1", request_ids=part["request_id"].to_list(),
                                source=SOURCE, date=day)
    storage.append_manifest(table, {"kind": "step_done", "table": table, "rows": df.height,
                                    "start": start.isoformat(), "end": end.isoformat()})
    log(f"actions: {df.height} rows")


MERGERS = ("cash_mergers", "stock_mergers", "stock_and_cash_mergers")


def delistings(start, end):
    """(symbol, delisting date, acquirer or None) from stored merger and worthless-removal actions."""
    df = read_frame(storage.data_root() / "corporate_actions", *range_bounds(start, end))
    if df.height == 0:
        return []
    out = set()
    for r in df.filter(pl.col("action_type").is_in(MERGERS + ("worthless_removals",))).iter_rows(named=True):
        raw = json.loads(r["raw"])
        day = r["event_at"].astimezone(ET).date()
        if r["action_type"] == "worthless_removals":
            out.add((raw.get("symbol") or r["symbol"], day, None))
        elif raw.get("acquiree_symbol"):
            out.add((raw["acquiree_symbol"], day, raw.get("acquirer_symbol")))
    return sorted(out, key=lambda x: (x[0], x[1], x[2] or ""))


def candidate_pool(start, end):
    return universe.pool(latest_assets(), renames(start, end), delistings(start, end))


def renames(start, end):
    """(old_symbol, new_symbol, rename date) from stored name-change actions."""
    df = read_frame(storage.data_root() / "corporate_actions", *range_bounds(start, end))
    if df.height == 0:
        return []
    nc = df.filter((pl.col("action_type") == "name_changes") & pl.col("new_symbol").is_not_null())
    days = nc["event_at"].dt.convert_time_zone("America/New_York").dt.date().to_list()
    return sorted(set(zip(nc["symbol"].to_list(), nc["new_symbol"].to_list(), days)))


# ---------------------------------------------------------------- bars

def _bars_frame(table, pages, feed, asof, got):
    cols = {k: [] for k in ("t", "symbol", "o", "h", "l", "c", "v", "vw", "n", "request_id")}
    for page, rid in pages:
        for sym, bars in page.items():
            for b in bars:
                cols["t"].append(b["t"]); cols["symbol"].append(sym)
                for k in ("o", "h", "l", "c", "v", "vw", "n"):
                    cols[k].append(b.get(k))
                cols["request_id"].append(rid)
    df = pl.DataFrame(cols, schema={"t": pl.Utf8, "symbol": pl.Utf8, "o": pl.Float64, "h": pl.Float64,
                                    "l": pl.Float64, "c": pl.Float64, "v": pl.Float64, "vw": pl.Float64,
                                    "n": pl.Float64, "request_id": pl.Utf8})
    df = df.select(
        pl.col("t").str.to_datetime("%Y-%m-%dT%H:%M:%SZ", time_unit="us", time_zone="UTC").alias("event_at"),
        "symbol", pl.col("o").alias("open"), pl.col("h").alias("high"), pl.col("l").alias("low"),
        pl.col("c").alias("close"), pl.col("v").cast(pl.Int64).alias("volume"), pl.col("vw").alias("vwap"),
        pl.col("n").cast(pl.Int64).alias("trade_count"), pl.lit(feed).alias("feed"), pl.lit("raw").alias("adjustment"),
        pl.lit(asof).alias("asof"), "request_id", pl.lit(got).dt.cast_time_unit("us").alias("ingested_at"),
        pl.lit(SOURCE).alias("source"), pl.lit(storage.contract(table)["schema_version"], dtype=pl.Int32).alias("schema_version"))
    return storage.validate(table, df)


class SymbolDropper:
    """Drops symbols the API rejects as invalid (400 'invalid symbol: X') and records them."""

    def __init__(self, table):
        self.table, self.bad, self.lock = table, set(), threading.Lock()

    def run(self, symbols, fetch):
        syms = [s for s in symbols if s not in self.bad]
        while True:
            try:
                return fetch(syms)
            except ApiError as e:
                if e.status == 400 and "invalid symbol" in e.detail:
                    sym = e.detail.split("invalid symbol:")[1].split('"')[0].strip()
                    with self.lock:
                        if sym not in self.bad:
                            self.bad.add(sym)
                            storage.append_manifest(self.table, {"kind": "dropped_symbol", "table": self.table, "symbol": sym})
                    syms = [s for s in syms if s != sym]
                    if not syms:
                        return []
                    continue
                raise


def fetch_bars(client, table, symbols, timeframe, lo, hi_inclusive, guard, feed, dropper):
    pages = []
    for i in range(0, len(symbols), SYMBOL_CHUNK):
        chunk = symbols[i:i + SYMBOL_CHUNK]

        def fetch(syms):
            return list(client.pages("/v2/stocks/bars", {
                "symbols": ",".join(syms), "timeframe": timeframe, "start": z(lo), "end": z(hi_inclusive),
                "limit": 10000, "adjustment": "raw", "feed": feed, "asof": "-", "sort": "asc"},
                "bars", table=table, event_range=guard))
        pages += dropper.run(chunk, fetch)
    return pages


def ingest_daily(client, start, end, workers):
    """Daily bars for the candidate pool. Each run fetches only symbols no earlier run covered and writes
    them as a new part (sip-1d-v1, sip-1d-v2, ...), so a grown pool never rewrites stored partitions."""
    table = "raw_bars_1d"
    pool = candidate_pool(start, end)
    sets = [r for r in storage.manifest(table) if r.get("kind") == "daily_symbol_set"]
    covered = {s for r in sets if r.get("complete") for s in r["symbols"]}
    done_parts = {r["part"] for r in sets if r.get("complete")}
    pending = [r for r in sets if not r.get("complete") and r["part"] not in done_parts]
    if pending:  # resume an interrupted set with its own part name
        part, symbols = pending[-1]["part"], pending[-1]["symbols"]
    else:
        symbols = sorted((set(pool) | set(universe.MARKET_SET)) - covered)  # ETFs: sector and regime inputs (P2)
        if not symbols:
            log("daily: every pool symbol already covered")
            return
        part = f"sip-1d-v{len({r['part'] for r in sets}) + 1}"
        storage.append_manifest(table, {"kind": "daily_symbol_set", "part": part, "symbols": symbols, "complete": False})
    cal = sessions(start, end)
    done = storage.completed(table, part=part)
    dropper = SymbolDropper(table)
    todo = []
    for first, last in _months(start, end):
        days = [d for d in cal["trading_date"].to_list() if first <= d <= last]
        if days and not all(storage.partition_key(table, source=SOURCE, feed="sip", date=d) in done for d in days):
            todo.append((first, last, days))
    log(f"daily: pool {len(pool)} symbols, fetching {len(symbols)} as part {part}, {len(todo)} months to fetch")

    def month(first, last, days):
        lo, hi = range_bounds(first, last)
        pages = fetch_bars(client, table, symbols, "1Day", lo, hi - dt.timedelta(seconds=1), (lo, hi), "sip", dropper)
        df = _bars_frame(table, pages, "sip", "-", now())
        df = df.with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date())
        for d in days:
            if storage.partition_key(table, source=SOURCE, feed="sip", date=d) in done:
                continue
            rows = df.filter(pl.col("d") == d).drop("d")
            storage.write_partition(table, rows, part=part_name, request_ids=rows["request_id"].to_list(),
                                    source=SOURCE, feed="sip", date=d)
        return first, df.height

    part_name = part
    with cf.ThreadPoolExecutor(workers) as ex:
        for first, n in ex.map(lambda a: month(*a), todo):
            log(f"daily: {first:%Y-%m} {n} rows")
    storage.append_manifest(table, {"kind": "daily_symbol_set", "part": part, "symbols": symbols, "complete": True})


# ---------------------------------------------------------------- universe

def ingest_universe(start, end):
    table = "universe_snapshots"
    cal = sessions(start, end)
    days = cal["trading_date"].to_list()
    pool = candidate_pool(start, end)
    snap_time = now()
    done = storage.completed(table, part=universe.METHOD)
    first_month = (start.replace(day=1) + dt.timedelta(days=32)).replace(day=1)  # warm-up month excluded
    n_written = 0
    for first, _ in _months(first_month, end):
        f = next(d for d in days if d >= first)
        if f.month != first.month:
            continue
        key = storage.partition_key(table, source="scalp_engine", date=f)
        if key in done:
            continue
        prior = [d for d in days if d < f][-universe.WINDOW:]
        if len(prior) < universe.WINDOW:
            raise LookupError(f"universe {f}: fewer than {universe.WINDOW} sessions of daily bars before it")
        daily = read_frame(storage.data_root() / "raw_bars_1d", day_bounds(prior[0])[0], day_bounds(prior[-1])[1],
                           columns=["symbol", "close", "volume", "vwap"], require_dates=prior)
        members = universe.reconstitute(daily, prior, universe.candidates(pool, f))
        rows = []
        for m in members.iter_rows(named=True):
            info = pool[m["symbol"]]
            rows.append(_uni_row(f, m["symbol"], info, "top500", m, snap_time))
        for sym in universe.MARKET_SET:
            rows.append(_uni_row(f, sym, {"asset_id": None, "exchange": None, "status": "active", "tradable": None,
                                          "shortable": None, "easy_to_borrow": None, "name_missing": False},
                                 "market_set", None, snap_time))
        storage.write_partition(table, storage.frame(table, rows), part=f"{universe.METHOD}",
                                source="scalp_engine", date=f)
        n_written += 1
        log(f"universe {f}: {members.height} members")
    log(f"universe: {n_written} months written")


def _uni_row(f, sym, info, uni, m, snap_time):
    return {"event_at": day_bounds(f)[0], "snapshot_date": f, "symbol": sym, "asset_id": info["asset_id"],
            "exchange": info["exchange"], "status": info["status"] or "unknown", "tradable": info["tradable"],
            "shortable": info["shortable"], "easy_to_borrow": info["easy_to_borrow"], "backfilled": True,
            "universe": uni, "rank": m["rank"] if m else None, "last_close": m["last_close"] if m else None,
            "median_dollar_volume": m["median_dollar_volume"] if m else None,
            "bars_in_window": m["bars_in_window"] if m else None, "name_missing": info["name_missing"],
            "method_version": universe.METHOD, "feed": "sip", "request_id": "derived:" + universe.METHOD,
            "ingested_at": snap_time, "source": "scalp_engine", "schema_version": 2}


def day_plan(start, end):
    """{session: (members, minute_symbols)}: top500 members of the session's month, and the symbols whose
    minute bars are needed that day (members, market set, 30-session warm-up before first membership)."""
    cal = sessions(start, end)
    days = cal["trading_date"].to_list()
    uni = read_frame(storage.data_root() / "universe_snapshots", *range_bounds(start, end))
    uni = uni.filter(pl.col("method_version") == universe.METHOD)  # superseded methods stay in the store, unused
    by_month = {}
    for row in uni.select("snapshot_date", "symbol", "universe").iter_rows():
        by_month.setdefault((row[0].year, row[0].month), {"top500": set(), "market_set": set()})[row[2]].add(row[1])
    first_member = {}
    for d in days:
        for s in by_month.get((d.year, d.month), {}).get("top500", ()):
            first_member.setdefault(s, d)
    warm = {}
    for s, f in first_member.items():
        i = days.index(f)
        for d in days[max(0, i - WARMUP_SESSIONS):i]:
            warm.setdefault(d, set()).add(s)
    plan = {}
    for d in days:
        m = by_month.get((d.year, d.month), {"top500": set(), "market_set": set()})
        members = m["top500"]
        plan[d] = (sorted(members), sorted(members | set(universe.MARKET_SET) | warm.get(d, set())))
    return plan, cal


# ---------------------------------------------------------------- minute bars and quote samples

def minute_task(client, table, feed, d, symbols, dropper):
    lo, hi = et(d, dt.time(4)), et(d, dt.time(20))
    pages = fetch_bars(client, table, symbols, "1Min", lo, hi - dt.timedelta(minutes=1), (lo, hi), feed, dropper)
    df = _bars_frame(table, pages, feed, "-", now())
    storage.write_partition(table, df, part=f"{feed}-1m-v1", request_ids=df["request_id"].to_list(),
                            source=SOURCE, feed=feed, date=d)
    return df.height


def sample_times(d, close_at):
    """UTC sample times of session d under SAMPLE_RULE."""
    return [et(d, tt) for tt in QUOTE_TIMES] + [close_at - CLOSE_SAMPLE_OFFSET]


def quote_task(client, d, members, close_at, dropper, part="sip-samples-v1"):
    table = "raw_quote_samples"
    unit = "round_lot" if d < SIZE_UNIT_SWITCH else "shares"
    rows, rids = [], []
    for t in sample_times(d, close_at):
        got = now()
        if t > close_at:
            rows += [_q_row(t, s, "after_close", None, unit, 0, "none", got) for s in members]
            continue
        found, need = {}, list(members)
        for lb in QUOTE_LOOKBACKS_MS:
            if not need:
                break
            lo = t - dt.timedelta(milliseconds=lb)

            def fetch(syms):
                return list(client.pages("/v2/stocks/quotes", {
                    "symbols": ",".join(syms), "start": z(lo), "end": z(t), "limit": 10000, "feed": "sip",
                    "asof": "-", "sort": "asc"}, "quotes", table=table, event_range=(lo, t + dt.timedelta(microseconds=1))))
            for page, rid in dropper.run(need, fetch):
                rids.append(rid)
                for sym, qs in page.items():
                    for q in qs:
                        ts = _parse_ns(q["t"])
                        if ts <= t and (sym not in found or ts >= found[sym][0][0]):
                            found[sym] = ((ts, q), rid, lb)
            need = [s for s in need if s not in found]
        got = now()
        for s in members:
            if s in found:
                (ts, q), rid, lb = found[s]
                rows.append(_q_row(t, s, "ok", (ts, q), unit, lb, rid, got))
            else:
                rows.append(_q_row(t, s, "missing", None, unit, QUOTE_LOOKBACKS_MS[-1], "none", got))
    df = storage.frame(table, rows)
    storage.write_partition(table, df, part=part, request_ids=rids, source=SOURCE, feed="sip", date=d)
    return df.height


def resample_early_closes(client, start, end, workers):
    """0.3 (P2): re-sample every early-close session under SAMPLE_RULE as a new part; P1 parts stay as they are."""
    table, part = "raw_quote_samples", "sip-samples-v2"
    plan, cal = day_plan(start, end)
    done = storage.completed(table, part=part)
    days = [(r["trading_date"], r["close_at"]) for r in cal.filter(pl.col("early_close")).iter_rows(named=True)
            if plan[r["trading_date"]][0]
            and storage.partition_key(table, source=SOURCE, feed="sip", date=r["trading_date"]) not in done]
    dropper = SymbolDropper(table)
    log(f"resample-close: {len(days)} early-close sessions to re-sample")
    with cf.ThreadPoolExecutor(workers) as ex:
        for d, n in zip([x[0] for x in days], ex.map(lambda x: quote_task(client, x[0], plan[x[0]][0], x[1], dropper, part),
                                                      days)):
            log(f"resample-close: {d} {n} rows")


def _parse_ns(s):
    """RFC 3339 with up to 9 fractional digits -> aware UTC datetime truncated to microseconds."""
    base, _, frac = s.rstrip("Z").partition(".")
    t = dt.datetime.fromisoformat(base).replace(tzinfo=UTC)
    return t.replace(microsecond=int((frac + "000000")[:6])) if frac else t


def _q_row(t, sym, status, tq, unit, lb, rid, got):
    q = tq[1] if tq else {}
    return {"event_at": t, "symbol": sym, "status": status, "quote_at": tq[0] if tq else None,
            "bid_price": q.get("bp"), "bid_size": _int_or_none(q.get("bs")), "ask_price": q.get("ap"),
            "ask_size": _int_or_none(q.get("as")), "bid_exchange": q.get("bx"), "ask_exchange": q.get("ax"),
            "conditions": q.get("c") if tq else None, "tape": q.get("z"), "size_unit": unit, "lookback_ms": lb,
            "sample_rule": SAMPLE_RULE, "feed": "sip", "request_id": rid, "ingested_at": got, "source": SOURCE,
            "schema_version": 2}


def ingest_minute(client, start, end, workers, feeds=("sip", "iex"), quotes=True):
    plan, cal = day_plan(start, end)
    close_at = dict(zip(cal["trading_date"].to_list(), cal["close_at"].to_list()))
    tasks = []
    for feed in feeds:
        done = storage.completed("raw_bars_1m")
        dropper = SymbolDropper("raw_bars_1m")
        for d, (members, syms) in plan.items():
            if storage.partition_key("raw_bars_1m", source=SOURCE, feed=feed, date=d) not in done:
                tasks.append((d, f"bars-{feed}", lambda d=d, s=syms, f=feed, dr=dropper:
                              minute_task(client, "raw_bars_1m", f, d, s, dr)))
    if quotes:
        done = storage.completed("raw_quote_samples")
        qdrop = SymbolDropper("raw_quote_samples")
        for d, (members, _) in plan.items():
            if members and storage.partition_key("raw_quote_samples", source=SOURCE, feed="sip", date=d) not in done:
                tasks.append((d, "quotes", lambda d=d, m=members: quote_task(client, d, m, close_at[d], qdrop)))
    tasks.sort(key=lambda x: (x[0], x[1]))
    log(f"minute: {len(tasks)} tasks over {len(plan)} sessions")
    t0, n, rows = time.monotonic(), 0, 0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fn): (d, kind) for d, kind, fn in tasks}
        for fut in cf.as_completed(futs):
            d, kind = futs[fut]
            rows += fut.result()  # any failure stops the run; reruns resume from the manifest
            n += 1
            if n % 25 == 0 or n == len(tasks):
                el = time.monotonic() - t0
                log(f"minute: {n}/{len(tasks)} tasks, {rows} rows, {client.stats['requests']} requests, "
                    f"{client.stats['retries']} retries, {el / 60:.1f} min, eta {el / n * (len(tasks) - n) / 60:.0f} min")


# ---------------------------------------------------------------- plan (budget)

def budget(start, end, sip_rows=445, iex_rows=214, quote_req_per_sample=4.5, per_minute=170, bytes_per_row=None):
    """Request, time and disk estimate for the minute step from the probe's per-symbol-day averages."""
    plan, cal = day_plan(start, end)
    req = {"sip": 0, "iex": 0, "quotes": 0}
    rows = {"sip": 0, "iex": 0, "quotes": 0}
    for d, (members, syms) in plan.items():
        for feed, per in (("sip", sip_rows), ("iex", iex_rows)):
            r = len(syms) * per
            rows[feed] += r
            req[feed] += max(1, -(-r // 10000)) + (len(syms) - 1) // SYMBOL_CHUNK
        if members:
            req["quotes"] += int((len(QUOTE_TIMES) + 1) * quote_req_per_sample)
            rows["quotes"] += (len(QUOTE_TIMES) + 1) * len(members)
    total = sum(req.values())
    bpr = bytes_per_row or {"sip": 22, "iex": 22, "quotes": 40}
    disk = sum(rows[k] * bpr[k] for k in rows)
    return {"sessions": len(plan), "member_sessions": sum(1 for m, _ in plan.values() if m),
            "symbols_minute": len({s for _, ss in plan.values() for s in ss}),
            "symbols_members": len({s for m, _ in plan.values() for s in m}),
            "requests": req, "rows": rows, "total_requests": total,
            "hours_at_rate": round(total / per_minute / 60, 2), "disk_gb_est": round(disk / 1e9, 2)}


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.ingest")
    ap.add_argument("step", choices=["calendar", "assets", "actions", "daily", "universe", "plan", "minute",
                                     "resample-close", "all"])
    ap.add_argument("--start", required=True, type=dt.date.fromisoformat)
    ap.add_argument("--end", required=True, type=dt.date.fromisoformat)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--per-minute", type=int, default=170)
    ap.add_argument("--feeds", default="sip,iex")
    ap.add_argument("--no-quotes", action="store_true")
    a = ap.parse_args(argv)
    if a.end < a.start:
        ap.error("--end before --start")
    # completeness: the end day's extended session (20:00 ET) must have closed >= 15 min ago
    if now() < et(a.end, dt.time(20)) + dt.timedelta(minutes=15):
        ap.error(f"--end {a.end} is not a complete day yet")
    log(f"data root {storage.data_root()}; step {a.step}; {a.start}..{a.end}")
    client = None if a.step in ("universe", "plan") else Client(per_minute=a.per_minute)
    steps = ["calendar", "assets", "actions", "daily", "universe", "minute"] if a.step == "all" else [a.step]
    for step in steps:
        if step == "calendar":
            ingest_calendar(client, a.start, a.end)
        elif step == "assets":
            ingest_assets(client)
        elif step == "actions":
            ingest_actions(client, a.start, a.end)
        elif step == "daily":
            ingest_daily(client, a.start, a.end, a.workers)
        elif step == "universe":
            ingest_universe(a.start, a.end)
        elif step == "plan":
            print(json.dumps(budget(a.start, a.end, per_minute=a.per_minute), indent=2))
        elif step == "resample-close":
            resample_early_closes(client, a.start, a.end, a.workers)
        elif step == "minute":
            ingest_minute(client, a.start, a.end, a.workers, tuple(a.feeds.split(",")), not a.no_quotes)
    if client:
        log(f"client stats {client.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
