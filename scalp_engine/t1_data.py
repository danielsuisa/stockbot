"""T1 ingestion (ADR 0016 section 8): the top-up, the stage-1 replay inputs, the opening relative-volume denominators,
the top-20 full sessions and the NBBO grid. Append-only, one manifest record per partition, every request checked
against the holdout and T1's own range before the network, resumable (a finished partition is never fetched again).

    python -m scalp_engine.t1_data topup|stage1|watch|denominators|top20|grid|dupcheck|budget [--from D] [--to D]

Fetch lists come only from data dated at or before the decision time: the daily prefilter for session t uses bars
<= t-1; the watchlist uses the SIP minutes 04:00-09:29 and the IEX minutes 09:30-09:34 of t (decided 09:38 ET)."""
import argparse
import bisect
import concurrent.futures as cf
import datetime as dt
import json
import shutil
import threading
import time
from zoneinfo import ZoneInfo

import polars as pl

from scalp_engine import ingest, insider_prices, storage
from scalp_engine import intraday_rules as R
from scalp_engine.data_access import check_range, day_bounds, read_frame, read_parts

UTC, ET = dt.timezone.utc, ZoneInfo("America/New_York")
SOURCE = "alpaca"
HARD_FIRST, HARD_END = dt.date(2022, 1, 3), dt.date(2026, 11, 2)  # nothing before; nothing on or after
DEV = (dt.date(2022, 2, 1), dt.date(2024, 12, 31))
CONF = (dt.date(2025, 1, 2), dt.date(2026, 10, 16))  # the last complete session in the store at ADR 0016's commit
TOPUP_FIRST = dt.date(2026, 10, 19)
TOPUP_CAP = dt.date(2026, 10, 30)
P1_FIRST, P1_LAST = dt.date(2023, 8, 1), dt.date(2026, 10, 16)
P1_PARTS = {"sip": "sip-1m-v1", "iex": "iex-1m-v1"}
BARS, QUOTES = "raw_bars_1m", "raw_quote_samples"
CHUNK = 100  # symbols per bars request (the bot's CHUNK)
QCHUNK = 20  # symbols per quote request
FLOOR_BYTES = int(8.2e9)  # stop before the 8 GB floor
GRID_RULE = "t1_grid_v1"
RELVOL_SESSIONS = R.RELVOL_SESSIONS
_lock = threading.Lock()


class DiskFloor(RuntimeError):
    pass


def log(msg):
    ingest.log(msg)


def guard(lo, hi):
    """T1's own range on top of the holdout guard: [2022-01-03, 2026-11-02) ET."""
    if lo < ingest.et(HARD_FIRST, dt.time(0)) or hi > ingest.et(HARD_END, dt.time(0)):
        raise ValueError(f"outside T1's range: {lo} .. {hi}")
    check_range(lo, hi)


def disk_check():
    free = shutil.disk_usage(storage.data_root()).free
    if free < FLOOR_BYTES:
        raise DiskFloor(f"free {free / 1e9:.2f} GB < {FLOOR_BYTES / 1e9:.1f} GB")
    return free


def derived(name):
    p = storage.data_root() / "derived" / "t1" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- sessions, universe, prefilter

def sessions(start=HARD_FIRST, end=TOPUP_CAP):
    cal = ingest.sessions(start, end)
    return [(r["trading_date"], r["close_at"].astimezone(ET).time()) for r in cal.iter_rows(named=True)]


def window_sessions(first, last):
    return [d for d, _ in sessions() if first <= d <= last]


def listed_universe(assets=None):
    """The bot's universe() filter on the store's asset snapshot, active and inactive (ADR 0016 section 1)."""
    out = set()
    for a in assets if assets is not None else ingest.latest_assets():
        sym, name = a["symbol"], (a.get("name") or "").upper()
        if a.get("exchange") in R.EXCHANGES and sym.isalpha() and len(sym) <= 5 \
                and not any(w in name.split() for w in R.FUND_WORDS):
            out.add(sym)
    return out


def survivor_names(listed, uni, delistings, renames):
    """{symbol: valid_until} of names Alpaca's asset list omits (ADR 0017 item 7; P1's top500_v3 method): an old
    ticker renamed into a universe name (valid until the rename), and an acquired or worthless-removed ticker
    (valid until its last delisting), unless a listed record holds the symbol, the symbol is not 1-5 letters, or
    the acquirer is a listed non-universe security (a fund reorganization). The rename date wins, as in P1."""
    def ok(sym):
        return sym.isalpha() and len(sym) <= 5
    out = {}
    for old, new, day in sorted(renames):
        if old == new or old in listed or not ok(old) or new not in uni:
            continue
        out[old] = min(out.get(old, day), day)
    last = {}
    for sym, day, acq in sorted(delistings, key=lambda x: (x[0], x[1], x[2] or "")):
        if sym in listed or not ok(sym) or (acq and acq in listed and acq not in uni):
            continue
        last[sym] = max(last.get(sym, day), day)
    for sym, day in last.items():
        out.setdefault(sym, day)
    return out


def valid_on(spans, sym, day):
    """A universe symbol is eligible on `day` unless it was delisted or renamed away on or before it."""
    until = spans.get(sym)
    return until is None or day < until


_SPANS = {}


def universe_spans():
    """{symbol: valid_until or None}: the listed universe (no end) plus the survivorship names."""
    if not _SPANS:
        assets = ingest.latest_assets()
        listed, uni = {a["symbol"] for a in assets}, listed_universe(assets)
        added = survivor_names(listed, uni, ingest.delistings(HARD_FIRST, CONF[1]), ingest.renames(HARD_FIRST, CONF[1]))
        _SPANS.update({x: None for x in uni})
        _SPANS.update(added)
    return _SPANS


def universe():
    return set(universe_spans())


def coverage_gaps(uni, sets, first, last, session_days=None):
    """[(start, end, sorted missing symbols)]: the date ranges in [first, last] where a universe symbol was never
    requested by any daily-bar symbol set (start, end, symbols). Adjacent ranges with the same gap are merged."""
    cuts = {first, last + dt.timedelta(days=1)}
    for a, b, _ in sets:
        for x in (a, b + dt.timedelta(days=1)):
            if first < x <= last:
                cuts.add(x)
    cuts = sorted(cuts)
    out = []
    for a, nxt in zip(cuts, cuts[1:]):
        b = nxt - dt.timedelta(days=1)
        covered = set().union(*[syms for sa, sb, syms in sets if sa <= a and b <= sb]) if sets else set()
        miss = sorted(set(uni) - covered)
        if out and out[-1][2] == miss and out[-1][1] + dt.timedelta(days=1) == a:
            out[-1] = (out[-1][0], b, miss)
        else:
            out.append((a, b, miss))
    if session_days is not None:  # a range without a session (a weekend between two sets) is no gap
        out = [g for g in out if any(g[0] <= x <= g[1] for x in session_days)]
    return [g for g in out if g[2]]


FILL_TAG = "t2"  # daily-fill part prefix of this phase (T1 wrote t1f0, t1f1)


def spanned_gaps(gaps, spans):
    """Coverage gaps without the symbols that were no longer valid when the gap starts."""
    out = []
    for a, b, miss in gaps:
        m = [x for x in miss if spans.get(x) is None or a < spans[x]]
        if m:
            out.append((a, b, m))
    return out


def fetch_daily_kept(cl, symbols, first, last, part):
    """SIP daily bars for symbols over [first, last], month by month, as part `part`: only requested symbols, no
    key any part already holds, one partition per session; a symbol-set record closes the step."""
    table = "raw_bars_1d"
    done = storage.completed(table, part=part)
    dropper = ingest.SymbolDropper(table)
    cal = [d for d, _ in sessions(first, last)]
    for m0, m1 in ingest._months(first, last):
        days = [d for d in cal if m0 <= d <= m1
                and storage.partition_key(table, source=SOURCE, feed="sip", date=d) not in done]
        if not days:
            continue
        lo, hi = ingest.range_bounds(m0, m1)
        guard(lo, hi)
        pages = ingest.fetch_bars(cl, table, symbols, "1Day", lo, hi - dt.timedelta(seconds=1), (lo, hi), "sip", dropper)
        df = keep_requested(ingest._bars_frame(table, pages, "sip", "-", ingest.now()), symbols)
        df = df.with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date())
        for d in days:
            rows = df.filter(pl.col("d") == d).drop("d")
            held = read_frame(storage.data_root() / table, *day_bounds(d), columns=["symbol"], symbols=symbols)
            rows = drop_held(rows, set(zip(held["symbol"].to_list(), held["event_at"].to_list())) if held.height else set())
            disk_check()
            storage.write_partition(table, rows, part=part, request_ids=rows["request_id"].unique().to_list(),
                                    source=SOURCE, feed="sip", date=d)
        log(f"daily {part}: {m0:%Y-%m} {df.height} rows; requests {cl.stats['requests']}")
    storage.append_manifest(table, {"kind": "daily_symbol_set", "part": part, "symbols": sorted(symbols),
                                    "complete": True, "start": first.isoformat(), "end": last.isoformat()})


def daily_sets():
    """Complete daily-bar symbol sets with their ranges (P1's v1-v3 records carry none: P1's range)."""
    out = []
    for r in storage.manifest("raw_bars_1d"):
        if r.get("kind") == "daily_symbol_set" and r.get("complete"):
            a = dt.date.fromisoformat(r["start"]) if r.get("start") else P1_FIRST
            b = dt.date.fromisoformat(r["end"]) if r.get("end") else P1_LAST
            out.append((a, b, set(r["symbols"])))
    return out


def step_daily_fill(cl, end=CONF[1]):
    """Daily bars for every (universe symbol, date range) never requested (T1 defect 2026-10-21), one part each."""
    cal = [d for d, _ in sessions(HARD_FIRST, end)]
    gaps = spanned_gaps(coverage_gaps(universe() | {"SPY"}, daily_sets(), HARD_FIRST, end, cal), universe_spans())
    for k, (a, b, syms) in enumerate(gaps):
        log(f"daily fill {a}..{b}: {len(syms)} symbols")
        fetch_daily_kept(cl, syms, a, b, part=f"sip-1d-{FILL_TAG}f{k}")
    left = spanned_gaps(coverage_gaps(universe() | {"SPY"}, daily_sets(), HARD_FIRST, end, cal), universe_spans())
    log(f"daily fill: {len(gaps)} gaps filled, {len(left)} left")


class Daily:
    """Raw SIP daily bars of the universe, one row per (symbol, session), for the bot's daily_stats(). Refuses to
    load while a universe symbol was never requested for part of the range (the prefilter would drop it)."""

    def __init__(self, end):
        uni = universe()
        self.spans = universe_spans()
        gaps = spanned_gaps(coverage_gaps(uni | {"SPY"}, daily_sets(), HARD_FIRST, end,
                                          [d for d, _ in sessions(HARD_FIRST, end)]), self.spans)
        if gaps:
            raise LookupError(f"daily coverage gaps: {[(str(a), str(b), len(m)) for a, b, m in gaps]}")
        df = insider_prices.bars(HARD_FIRST, end)
        df = df.filter(pl.col("symbol").is_in(sorted(uni))).sort(["symbol", "d"])
        self.by = {}
        for (sym,), g in df.partition_by("symbol", as_dict=True, maintain_order=True).items():
            self.by[sym] = ([d.isoformat() for d in g["d"].to_list()],
                            list(zip(g["d"].cast(pl.Utf8).to_list(), g["open"].to_list(), g["high"].to_list(),
                                     g["low"].to_list(), g["close"].to_list(), g["volume"].to_list())))
        self.sessions = [d.isoformat() for d, _ in sessions(HARD_FIRST, end)]

    def stats(self, day):
        """daily_stats(universe(), prev_day, start) at session `day` (start = before[-31], or the first held)."""
        before = [d for d in self.sessions if d < day.isoformat()]
        prev_day, start = before[-1], before[max(0, len(before) - max(R.ADV_SESSIONS, 30) - 1)]
        rows = {}
        for sym, (dates, rs) in self.by.items():
            if not valid_on(self.spans, sym, day):
                continue
            lo, hi = bisect.bisect_left(dates, start), bisect.bisect_right(dates, prev_day)
            if hi - lo >= 16:
                rows[sym] = rs[lo:hi]
        return R.daily_stats_from(rows)


# ---------------------------------------------------------------- minute bars

def _p1_held(day, feed):
    if not (P1_FIRST <= day <= P1_LAST):
        return set()
    df = read_parts(storage.data_root() / BARS, day, {P1_PARTS[feed]})
    return set(df["symbol"].unique().to_list()) if df.height else set()


def keep_requested(df, symbols):
    """Rows of the requested symbols only: Alpaca can return bars under a symbol the request never named (AHPAW,
    T2 defect 2026-10-21); such rows are not stored."""
    return df.filter(pl.col("symbol").is_in(sorted(set(symbols)))) if df.height else df


def drop_held(df, held):
    """Rows of df whose (symbol, event_at) key no held part has (no key is stored twice)."""
    if not held or df.height == 0:
        return df
    keys = pl.DataFrame({"symbol": [k[0] for k in held], "event_at": [k[1] for k in held]},
                        schema={"symbol": pl.Utf8, "event_at": df.schema["event_at"]})
    return df.join(keys, on=["symbol", "event_at"], how="anti")


def held_minute_keys(day, feed, symbols):
    """(symbol, event_at) keys of `day` and `feed` already held by any part, for the given symbols."""
    lo, hi = day_bounds(day)
    df = read_frame(storage.data_root() / BARS, lo, hi, columns=["symbol", "feed"], symbols=symbols)
    if df.height == 0:
        return set()
    df = df.filter(pl.col("feed") == feed)
    return set(zip(df["symbol"].to_list(), df["event_at"].to_list()))


def fetch_minutes(cl, day, feed, symbols, first, last, part, dropper):
    """1-minute bars [first, last] ET (both inclusive) of `symbols` on `day` -> partition part, plus a symbol-list
    record (an empty fetch list still writes its empty partition: the step is then complete for that day)."""
    lo, hi = ingest.et(day, first), ingest.et(day, last)
    guard(lo, hi + dt.timedelta(minutes=1))
    pages = []
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]

        def fetch(syms):
            return list(cl.pages("/v2/stocks/bars", {
                "symbols": ",".join(syms), "timeframe": "1Min", "start": ingest.z(lo), "end": ingest.z(hi),
                "limit": 10000, "adjustment": "raw", "feed": feed, "asof": "-", "sort": "asc"},
                "bars", table=BARS, event_range=(lo, hi + dt.timedelta(minutes=1))))
        pages += dropper.run(chunk, fetch)
    df = keep_requested(ingest._bars_frame(BARS, pages, feed, "-", ingest.now()), symbols)
    df = drop_held(df, held_minute_keys(day, feed, symbols) if symbols else set())
    with _lock:
        disk_check()
        storage.write_partition(BARS, df, part=f"{feed}-1m-{part}", request_ids=df["request_id"].to_list(),
                                source=SOURCE, feed=feed, date=day)
        storage.append_manifest(BARS, {"kind": "t1_fetch_list", "part": f"{feed}-1m-{part}", "date": day.isoformat(),
                                       "feed": feed, "first": first.strftime("%H:%M"), "last": last.strftime("%H:%M"),
                                       "symbols": symbols})
    return df.height


def fetch_lists(prefix):
    """{(date, feed): set(symbols)} of every T1 part whose name starts with `prefix` (t1s1 covers t1s1 and t1s1b),
    from the fetch-list records."""
    out = {}
    for r in storage.manifest(BARS):
        if r.get("kind") == "t1_fetch_list" and r["part"].split("-1m-", 1)[1].startswith(prefix):
            out.setdefault((dt.date.fromisoformat(r["date"]), r["feed"]), set()).update(r["symbols"])
    return out


def _run(days, task, workers, label):
    t0, n = time.time(), 0
    ex = cf.ThreadPoolExecutor(workers)
    try:
        futs = {ex.submit(task, d): d for d in days}
        for f in cf.as_completed(futs):
            n += 1
            msg = f.result()
            if n % 20 == 0 or n == len(days):
                el = time.time() - t0
                log(f"{label}: {n}/{len(days)} days, {el / 60:.1f} min, projected {el / n * len(days) / 3600:.2f} h; "
                    f"{msg}; free {disk_check() / 1e9:.2f} GB")
    except BaseException:
        ex.shutdown(wait=True, cancel_futures=True)
        raise
    ex.shutdown(wait=True)


def step_stage1(cl, days, workers=4, sfx=""):
    """Stage-1 replay inputs: SIP and IEX 04:00-09:55 for every prefiltered symbol (ADR 0016 section 8, step 2).
    With a suffix (a later pass), only symbol-days no earlier t1s1 part fetched; days with none are skipped."""
    daily = Daily(max(days))
    part = "t1s1" + sfx
    done = {f: storage.completed(BARS, part=f"{f}-1m-{part}") for f in ("sip", "iex")}
    prior = fetch_lists("t1s1")
    todo, lists = [], {}
    for d in days:
        stats = sorted(daily.stats(d))
        for f in ("sip", "iex"):
            if storage.partition_key(BARS, source=SOURCE, feed=f, date=d) in done[f]:
                continue
            syms = sorted(set(stats) - prior.get((d, f), set()) - _p1_held(d, f))
            if syms or not sfx:
                lists[(d, f)] = syms
        if any((d, f) in lists for f in ("sip", "iex")):
            todo.append(d)
    dropper = ingest.SymbolDropper(BARS)
    log(f"stage1{sfx}: {len(todo)} days to fetch, {sum(len(v) for v in lists.values())} symbol-day-feeds")

    def task(d):
        out = []
        for feed in ("sip", "iex"):
            if (d, feed) not in lists:
                continue
            syms = lists[(d, feed)]
            out.append(f"{feed} {len(syms)} syms {fetch_minutes(cl, d, feed, syms, dt.time(4), dt.time(9, 55), part, dropper)} rows")
        return f"{d} " + ", ".join(out) + f"; requests {cl.stats['requests']}"
    _run(todo, task, workers, "stage1" + sfx)


# ---------------------------------------------------------------- the watchlist (pre-re-rank top 20)

def minute_frame(day, symbols, feed, first, last):
    """Minute bars of `day` for symbols on `feed`, first <= minute <= last (ET 'HH:MM'), from every part held."""
    lo, hi = day_bounds(day)
    df = read_frame(storage.data_root() / BARS, lo, hi, columns=["symbol", "open", "high", "low", "close", "volume",
                                                                  "vwap", "feed", "ingested_at"], symbols=symbols)
    if df.height == 0:
        return df
    df = df.filter(pl.col("feed") == feed).with_columns(
        m=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.strftime("%H:%M"))
    df = df.filter((pl.col("m") >= first) & (pl.col("m") <= last))
    return df.sort("ingested_at").unique(subset=["symbol", "event_at"], keep="last").sort(["symbol", "event_at"])


def bars_by_symbol(df):
    """-> {symbol: [("HH:MM", o, h, l, c, v, vw)]} ascending (the bot's bar tuples)."""
    out = {}
    for s, m, o, h, l, c, v, vw in df.select("symbol", "m", "open", "high", "low", "close", "volume", "vwap").iter_rows():
        out.setdefault(s, []).append((m, o, h, l, c, v, vw))
    return out


def stage1_inputs(day, stats):
    """pre = {symbol: (SIP volume 04:00-09:29, last close)}, opens = {symbol: IEX 09:30 5-minute bar rows}."""
    syms = sorted(stats)
    sip = minute_frame(day, syms, "sip", "04:00", "09:29")
    pre = {}
    if sip.height:
        for (s,), g in sip.partition_by("symbol", as_dict=True, maintain_order=True).items():
            pre[s] = (int(g["volume"].sum()), g["close"][-1])
    iex = minute_frame(day, syms, "iex", "09:30", "09:34")
    opens = {s: [("09:30", b[0][1])] for s, b in bars_by_symbol(iex).items()} if iex.height else {}
    return pre, opens


def step_watch(days):
    """Stage 1 per session -> derived/t1/watch/<date>.json (the pre-re-rank top 20 with the bot's fields)."""
    daily = Daily(max(days))
    for d in days:
        p = derived(f"watch/{d.isoformat()}.json")
        if p.exists():
            continue
        stats = daily.stats(d)
        pre, opens = stage1_inputs(d, stats)
        watch = R.watchlist_from(stats, pre, opens)
        p.write_text(json.dumps({"date": d.isoformat(), "decided_at": "09:38", "prefilter": len(stats),
                                 "watch": watch}, indent=1), encoding="utf-8")
    log(f"watch: {len(days)} sessions")


def watch_of(d):
    p = derived(f"watch/{d.isoformat()}.json")
    return json.loads(p.read_text(encoding="utf-8"))["watch"] if p.exists() else None


def step_denominators(cl, days, workers=4, tag=""):
    """SIP 09:30-09:34 of the 14 sessions before t for t's watchlist, where no part holds that symbol-day."""
    cal = [d for d, _ in sessions()]
    s1 = fetch_lists("t1s1")
    for k, v in fetch_lists("t1den").items():
        s1.setdefault(k, set()).update(v)
    need = {}
    for d in days:
        w = watch_of(d)
        if w is None:
            raise LookupError(f"watch missing for {d}")
        prior = [x for x in cal if x < d][-RELVOL_SESSIONS:]
        for p in prior:
            for s in (x["symbol"] for x in w):
                need.setdefault(p, set()).add(s)
    part = "t1den" + tag
    done = storage.completed(BARS, part=f"sip-1m-{part}")
    todo = []
    for p, syms in sorted(need.items()):
        if storage.partition_key(BARS, source=SOURCE, feed="sip", date=p) in done:
            continue
        held = s1.get((p, "sip"), set()) | _p1_held(p, "sip")
        if syms - held:
            todo.append((p, sorted(syms - held)))
    dropper = ingest.SymbolDropper(BARS)
    log(f"denominators: {len(todo)} sessions, {sum(len(s) for _, s in todo)} symbol-days")
    lists = dict(todo)

    def task(p):
        n = fetch_minutes(cl, p, "sip", lists[p], dt.time(9, 30), dt.time(9, 34), part, dropper)
        return f"{p} {len(lists[p])} syms {n} rows; requests {cl.stats['requests']}"
    _run([p for p, _ in todo], task, workers, "denominators")


def step_top20(cl, days, workers=4, sfx=""):
    """SIP and IEX 09:56-20:00 for each session's top 20 (pre-re-rank), except P1-held symbol-days and (a later
    pass) symbol-days an earlier t1top part fetched."""
    part = "t1top" + sfx
    done = {f: storage.completed(BARS, part=f"{f}-1m-{part}") for f in ("sip", "iex")}
    prior = fetch_lists("t1top")
    todo, lists = [], {}
    for d in days:
        w = {x["symbol"] for x in watch_of(d)}
        for f in ("sip", "iex"):
            if storage.partition_key(BARS, source=SOURCE, feed=f, date=d) in done[f]:
                continue
            syms = sorted(w - prior.get((d, f), set()) - _p1_held(d, f))
            if syms or not sfx:
                lists[(d, f)] = syms
        if any((d, f) in lists for f in ("sip", "iex")):
            todo.append(d)
    dropper = ingest.SymbolDropper(BARS)
    log(f"top20{sfx}: {len(todo)} sessions")

    def task(d):
        out = []
        for feed in ("sip", "iex"):
            if (d, feed) not in lists:
                continue
            syms = lists[(d, feed)]
            out.append(f"{feed} {len(syms)} {fetch_minutes(cl, d, feed, syms, dt.time(9, 56), dt.time(19, 59), part, dropper)}")
        return f"{d} " + ", ".join(out) + f"; requests {cl.stats['requests']}"
    _run(todo, task, workers, "top20" + sfx)


# ---------------------------------------------------------------- the NBBO grid

def grid_times(d, close_t):
    """Every scan close 09:40-11:30 and the time exit (15:55, or close - 5 minutes on early closes), ET."""
    exit_t = min(dt.time(15, 55), (dt.datetime.combine(d, close_t) - dt.timedelta(minutes=5)).time())
    return [dt.time(int(h[:2]), int(h[3:])) for h in R.scan_times("morning")] + [exit_t]


def _held_quotes(d):
    lo, hi = day_bounds(d)
    df = read_frame(storage.data_root() / QUOTES, lo, hi, columns=["symbol"])
    return set(zip(df["symbol"].to_list(), df["event_at"].to_list())) if df.height else set()


def sample_at(cl, d, symbols, t, dropper):
    """SIP NBBO prevailing at t for each symbol (look-backs 5 s then 60 s): insider_prices.sample_quotes' rule,
    returning rows instead of writing them."""
    unit = "round_lot" if d < ingest.SIZE_UNIT_SWITCH else "shares"
    found, need, rids = {}, sorted(set(symbols)), []
    for lb in ingest.QUOTE_LOOKBACKS_MS:
        if not need:
            break
        lo = t - dt.timedelta(milliseconds=lb)
        guard(lo, t + dt.timedelta(microseconds=1))
        for i in range(0, len(need), QCHUNK):
            chunk = need[i:i + QCHUNK]

            def fetch(syms):
                return list(cl.pages("/v2/stocks/quotes", {"symbols": ",".join(syms), "start": ingest.z(lo), "end": ingest.z(t),
                                                           "limit": 10000, "feed": "sip", "asof": "-", "sort": "asc"},
                                     "quotes", table=QUOTES, event_range=(lo, t + dt.timedelta(microseconds=1))))
            for page, rid in dropper.run(chunk, fetch):
                rids.append(rid)
                for sym, qs in page.items():
                    for q in qs:
                        ts = ingest._parse_ns(q["t"])
                        if ts <= t and (sym not in found or ts >= found[sym][0][0]):
                            found[sym] = ((ts, q), rid, lb)
        need = [s for s in need if s not in found]
    got = ingest.now()
    rows = []
    for s in sorted(set(symbols)):
        if s in found:
            (ts, q), rid, lb = found[s]
            r = ingest._q_row(t, s, "ok", (ts, q), unit, lb, rid, got)
        else:
            r = ingest._q_row(t, s, "missing", None, unit, ingest.QUOTE_LOOKBACKS_MS[-1], "none", got)
        r["sample_rule"] = GRID_RULE
        rows.append(r)
    return rows, rids


def step_grid(cl, days, workers=4, sfx=""):
    closes = dict(sessions())
    part = "sip-t1grid" + sfx
    done = storage.completed(QUOTES, part=part)
    todo = [d for d in days if storage.partition_key(QUOTES, source=SOURCE, feed="sip", date=d) not in done]
    dropper = ingest.SymbolDropper(QUOTES)
    log(f"grid{sfx}: {len(todo)} sessions")

    def task(d):
        syms = [x["symbol"] for x in watch_of(d)]
        held = _held_quotes(d)
        rows, rids, skipped = [], [], 0
        for tt in grid_times(d, closes[d]):
            t = ingest.et(d, tt)
            todo_syms = [s for s in syms if (s, t) not in held]
            skipped += len(syms) - len(todo_syms)
            if todo_syms:
                r, i = sample_at(cl, d, todo_syms, t, dropper)
                rows += r
                rids += i
        df = storage.frame(QUOTES, rows)
        if sfx and not rows:
            return f"{d} nothing new, {skipped} held"
        with _lock:
            disk_check()
            storage.write_partition(QUOTES, df, part=part, request_ids=rids, source=SOURCE, feed="sip", date=d)
        return f"{d} {df.height} samples, {skipped} held; requests {cl.stats['requests']}"
    _run(todo, task, workers, "grid" + sfx)


# ---------------------------------------------------------------- top-up and checks

def last_complete_session(now=None):
    """The last session whose 20:00 ET extended hours ended at least 30 minutes ago, capped at 2026-10-30."""
    now = now or dt.datetime.now(UTC)
    cut = (now.astimezone(ET) - dt.timedelta(hours=20, minutes=30)).date()
    return min(cut, TOPUP_CAP)


def step_topup(cl, end):
    """Calendar, corporate actions and daily bars 2026-10-19 -> end (ADR 0016 section 8, step 1)."""
    if not (TOPUP_FIRST <= end <= TOPUP_CAP):
        raise ValueError(f"top-up end {end} outside {TOPUP_FIRST}..{TOPUP_CAP}")
    guard(*ingest.range_bounds(TOPUP_FIRST, end))
    ingest.ingest_calendar(cl, TOPUP_FIRST, end)
    tag = f"t1-{end.isoformat()}"
    table = "corporate_actions"
    if not any(r.get("kind") == "step_done_t1" and r.get("end") == end.isoformat() for r in storage.manifest(table)):
        have = {r["partition"] for r in storage.manifest(table) if r.get("kind") == "partition"}
        rows = []
        token = None
        while True:
            data, rid = cl.get(ingest.DATA, "/v1/corporate-actions", {"start": TOPUP_FIRST.isoformat(), "end": end.isoformat(),
                               "limit": 1000, "page_token": token}, table=table, event_range=ingest.range_bounds(TOPUP_FIRST, end))
            got = ingest.now()
            for kind, recs in sorted((data.get("corporate_actions") or {}).items()):
                for r in recs:
                    date_s = r.get("ex_date") or r.get("effective_date") or r.get("process_date")
                    if not date_s:
                        continue
                    day = dt.date.fromisoformat(date_s)
                    if not (TOPUP_FIRST <= day <= end):
                        continue
                    rows.append({"event_at": day_bounds(day)[0], "action_type": kind,
                                 "symbol": r.get("symbol") or r.get("old_symbol") or r.get("target_symbol") or r.get("acquiree_symbol") or "",
                                 "new_symbol": r.get("new_symbol") or r.get("acquirer_symbol"),
                                 "ratio_new": ingest._int_or_none(r.get("new_rate")), "ratio_old": ingest._int_or_none(r.get("old_rate")),
                                 "cash_amount": repr(r["rate"]) if isinstance(r.get("rate"), (int, float)) else None,
                                 "declared_at": day_bounds(dt.date.fromisoformat(r["declaration_date"]))[0] if r.get("declaration_date") else None,
                                 "vendor_id": r.get("id"),
                                 "process_date": dt.date.fromisoformat(r["process_date"]) if r.get("process_date") else None,
                                 "raw": json.dumps(r, sort_keys=True, separators=(",", ":")), "feed": "none", "request_id": rid,
                                 "ingested_at": got, "source": SOURCE, "schema_version": 2})
            token = data.get("next_page_token")
            if not token:
                break
        df = storage.frame(table, rows)
        written = 0
        if df.height:
            for (day,), part in df.with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date()) \
                                  .partition_by("d", as_dict=True, maintain_order=True).items():
                if storage.partition_key(table, source=SOURCE, date=day) in have:
                    continue  # a date already held is never written twice
                storage.write_partition(table, part.drop("d"), part="actions-t1", request_ids=part["request_id"].to_list(),
                                        source=SOURCE, date=day)
                written += part.height
        storage.append_manifest(table, {"kind": "step_done_t1", "table": table, "rows": written, "end": end.isoformat()})
        log(f"actions top-up: {written} rows")
    symbols = sorted(universe() | {"SPY"})
    held = {r["partition"] for r in storage.manifest("raw_bars_1d") if r.get("kind") == "partition"}
    days = [d for d in window_sessions(TOPUP_FIRST, end)
            if storage.partition_key("raw_bars_1d", source=SOURCE, feed="sip", date=d) not in held]
    if days:
        lo, hi = topup_daily_window(days[0], days[-1])
        guard(lo, hi)
        dropper = ingest.SymbolDropper("raw_bars_1d")
        pages = ingest.fetch_bars(cl, "raw_bars_1d", symbols, "1Day", lo, hi, (lo, hi), "sip", dropper)
        df = ingest._bars_frame("raw_bars_1d", pages, "sip", "-", ingest.now())
        df = df.with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date())
        for d in days:
            rows = df.filter(pl.col("d") == d).drop("d")
            disk_check()
            storage.write_partition("raw_bars_1d", rows, part=f"sip-1d-{tag}b", request_ids=rows["request_id"].unique().to_list(),
                                    source=SOURCE, feed="sip", date=d)
        storage.append_manifest("raw_bars_1d", {"kind": "daily_symbol_set", "part": f"sip-1d-{tag}b", "symbols": symbols,
                                                "complete": True, "start": days[0].isoformat(), "end": days[-1].isoformat()})
        log(f"daily top-up: {len(days)} sessions, {df.height} rows")
    log(f"top-up to {end} done")


def topup_daily_window(first, last):
    """[first 00:00 ET, last 20:00 ET]: the daily bar of `last` is final after the extended session, and the free
    plan refuses a request whose end is within the last 15 minutes (the calendar day's end can still be ahead)."""
    return ingest.et(first, dt.time(0)), ingest.et(last, dt.time(20))


def dupcheck(days=None):
    """The I1 duplicate check (every table in i1_remedy.DUP_KEYS) plus raw_bars_1m (symbol, event_at, feed) and the
    T1 grid, per date for the dates T1 wrote."""
    from scalp_engine import i1_remedy
    out = i1_remedy.duplicates()
    dates = sorted({dt.date.fromisoformat(r["partition"].rsplit("date=", 1)[1]) for r in storage.manifest(BARS)
                    if r.get("kind") == "partition" and "-t1" in r.get("file", "")})
    if days:
        dates = [d for d in dates if d in set(days)]
    rows = dups = 0
    for d in dates:
        df = read_frame(storage.data_root() / BARS, *day_bounds(d), columns=["symbol", "feed"])
        rows += df.height
        dups += df.height - df.unique(subset=["symbol", "event_at", "feed"]).height
    out[BARS] = {"rows": rows, "duplicate_keys": dups, "dates": len(dates)}
    out["total"] = sum(v["duplicate_keys"] for v in out.values() if isinstance(v, dict))
    return out


DEN_REMEDY = {"table": BARS, "old": "sip-1m-t1den", "p1": ("sip-1m-t1s1b",), "new": "sip-1m-t1den2",
              "key": ["symbol", "event_at"],
              "values": ["open", "high", "low", "close", "volume", "vwap", "trade_count", "feed", "adjustment"],
              "segments": {"source": SOURCE, "feed": "sip"}}


DAILY_REMEDY = {"table": "raw_bars_1d", "old": "sip-1d-t2f0", "p1": ("sip-1d-t1f0",), "new": "sip-1d-t2f0b",
                "key": ["symbol", "event_at"],
                "values": ["open", "high", "low", "close", "volume", "vwap", "trade_count", "feed", "adjustment"],
                "segments": {"source": SOURCE, "feed": "sip"}}


def step_remedy(which="t1den"):
    """Duplicate-key remedies (I2 routine): the old part's keys held, value for value, by an earlier part are set
    aside with their partition file; the rest of that file is rewritten as the new part.
    t1den (T1, 2026-10-21): t1den keys that t1s1b fetched again. daily (T2, 2026-10-21): AHPAW daily keys that
    t1f0 stored without requesting them and t2f0 requested."""
    from scalp_engine import i1_remedy
    spec, reason = ((DEN_REMEDY, "T1 duplicate-key remedy (T1 report): t1den keys fetched again by t1s1b with identical "
                                 "values") if which == "t1den" else
                    (DAILY_REMEDY, "T2 duplicate-key remedy (T2 report): daily keys t1f0 stored unrequested and t2f0 "
                                   "fetched again, identical values"))
    table = spec["table"]
    dates = sorted({dt.date.fromisoformat(r["partition"].rsplit("date=", 1)[1]) for r in storage.manifest(table)
                    if r.get("kind") == "partition" and r["file"] == f"part-{spec['old']}.parquet"})
    done = {r["partition"] for r in storage.manifest(table) if r.get("kind") == "set_aside"}
    seg = spec["segments"]
    out = []
    for d in dates:
        if storage.partition_key(table, date=d, **seg) in done:
            continue
        r = i1_remedy.remedy_one(spec, d, reason=reason)
        if r:
            out.append(r)
    log(f"remedy {which}: {len(out)} partitions, {sum(r['identical_in_p1'] for r in out)} duplicate keys set aside")
    return out


def budget(days):
    daily = Daily(max(days))
    n = {d: len(daily.stats(d)) for d in days}
    total = sum(n.values())
    return {"sessions": len(days), "prefilter_symbol_days": total, "per_session": round(total / max(1, len(days)), 1)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["topup", "dailyfill", "stage1", "watch", "denominators", "top20", "grid",
                                     "dupcheck", "budget", "remedy"])
    ap.add_argument("--sfx", default="", help="part suffix for a later pass (fetches only what no earlier part did)")
    ap.add_argument("--from", dest="first", default=DEV[0].isoformat())
    ap.add_argument("--to", dest="last", default=CONF[1].isoformat())
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tag", default="", help="denominators part suffix for a later run ('p' for parity sessions)")
    a = ap.parse_args(argv)
    first, last = dt.date.fromisoformat(a.first), dt.date.fromisoformat(a.last)
    if last >= HARD_END or first < HARD_FIRST:
        raise SystemExit("outside T1's range")
    days = window_sessions(first, last)
    if a.step in ("dupcheck", "budget"):
        print(json.dumps(dupcheck() if a.step == "dupcheck" else budget(days), indent=1, default=str))
        return 0
    if a.step == "watch":
        step_watch(days)
        return 0
    if a.step == "remedy":
        print(json.dumps(step_remedy(a.tag or "t1den"), indent=1))
        return 0
    cl = insider_prices.client()
    if a.step == "topup":
        step_topup(cl, min(last, last_complete_session()))
    elif a.step == "dailyfill":
        step_daily_fill(cl, last)
    else:
        if a.step == "denominators":
            step_denominators(cl, days, a.workers, a.tag)
        else:
            {"stage1": step_stage1, "top20": step_top20, "grid": step_grid}[a.step](cl, days, a.workers, a.sfx)
    log(f"{a.step}: done; http {cl.stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
