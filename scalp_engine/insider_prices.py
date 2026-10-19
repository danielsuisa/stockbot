"""I1 Alpaca side (ADR 0009 sections 4-5): calendar, corporate actions from 2022-01-03, SIP daily bars for every event
issuer and every possible universe (b) member, the bad-price rule, split adjustment at read time, CIK -> point-in-time
symbol, SIP NBBO samples, and the universe (b)/(c) flags as of the entry decision. No outcome is computed: no price
after an event's entry decision is read for any event (the exit-day quote samples are stored, not used).

    python -m scalp_engine.insider_prices STEP
    STEP: base | bars | plan | close-quotes | flags | open-quotes | budget
Alpaca keys: ALPACA_KEY_ID / ALPACA_SECRET_KEY from the environment only (envload); if unset, nothing is requested.
"""
import argparse
import bisect
import concurrent.futures as cf
import datetime as dt
import json
import re
import statistics
import sys

import numpy as np
import polars as pl

from scalp_engine import envload, ingest, storage, universe
from scalp_engine.data_access import ET, check_range, day_bounds, read_frame

UTC = dt.timezone.utc
SOURCE = "alpaca"
FIRST = dt.date(2022, 1, 3)
LAST = dt.date(2026, 10, 16)              # last complete session when I1 ran (<= 2026-10-30)
P1_FIRST, P1_LAST = dt.date(2023, 8, 1), dt.date(2026, 9, 30)  # daily bars already stored by P1 (sip-1d-v1..v3)
DV_SESSIONS, MIN_PRICE_B, MIN_DV_B, MAX_SPREAD_B = 20, 1.0, 100_000.0, 0.03
MIN_PRICE_C, MIN_DV_C, MAX_SPREAD_C = 2.0, 1_000_000.0, 0.01
HORIZONS = (5, 20, 60)
CLOSE_RULE, OPEN_RULE = "close_minus_5m_v2", "open_plus_5m_v1"
OPEN_SAMPLE = dt.time(9, 35)
SPLIT_FACTORS = (2, 3, 4, 5, 8, 10, 20, 50)
CHUNK = 400


def log(msg):
    ingest.log(msg)


def client():
    envload.require("ALPACA_KEY_ID", "ALPACA_SECRET_KEY")
    return ingest.Client(per_minute=170)


# ---------------------------------------------------------------- calendar and corporate actions

def ingest_actions_i1(cl, start=FIRST, end=P1_FIRST - dt.timedelta(days=1)):
    """Corporate actions for [2022-01-03, 2023-07-31] (P1 stored 2023-08-01 onward) as part actions-i1."""
    table = "corporate_actions"
    if any(r.get("kind") == "step_done_i1" for r in storage.manifest(table)):
        log("actions-i1: done, skipped")
        return
    rows = []
    for first, last in ingest._months(start, end):
        token = None
        while True:
            data, rid = cl.get(ingest.DATA, "/v1/corporate-actions", {"start": first.isoformat(), "end": last.isoformat(),
                               "limit": 1000, "page_token": token}, table=table, event_range=ingest.range_bounds(first, last))
            got = ingest.now()
            for kind, recs in sorted((data.get("corporate_actions") or {}).items()):
                for r in recs:
                    date_s = r.get("ex_date") or r.get("effective_date") or r.get("process_date")
                    if not date_s:
                        continue
                    day = dt.date.fromisoformat(date_s)
                    if not (start <= day <= end):
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
    done = storage.completed(table, part="actions-i1")
    for (day,), part in df.with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date()) \
                          .partition_by("d", as_dict=True, maintain_order=True).items():
        if storage.partition_key(table, source=SOURCE, date=day) not in done:
            storage.write_partition(table, part.drop("d"), part="actions-i1", request_ids=part["request_id"].to_list(),
                                    source=SOURCE, date=day)
    storage.append_manifest(table, {"kind": "step_done_i1", "table": table, "rows": df.height,
                                    "start": start.isoformat(), "end": end.isoformat()})
    log(f"actions-i1: {df.height} rows")


def actions():
    df = read_frame(storage.data_root() / "corporate_actions", *ingest.range_bounds(FIRST, LAST))
    return df.sort("ingested_at").unique(subset=["vendor_id"], keep="last") if df.height else df


def calendar():
    return ingest.sessions(FIRST, LAST)


# ---------------------------------------------------------------- symbols

_PREFIX = re.compile(r"^(?:NASDAQ|NYSE|NYSEAMERICAN|NYSEARCA|AMEX|OTC|OTCQB|OTCQX|BATS|CBOE)\s*[:\-]\s*", re.I)


def norm(sym):
    """Filed symbol -> Alpaca form: upper case, no exchange prefix, class separator '.' (BRK-B, BRK/B -> BRK.B)."""
    s = _PREFIX.sub("", (sym or "").strip().upper())
    s = re.sub(r"\s+", "", s).replace("/", ".").replace("-", ".")
    return s or None


def pool_symbols(event_symbols=()):
    pool = ingest.candidate_pool(FIRST, LAST)
    return sorted(set(pool) | set(universe.MARKET_SET) | {s for s in event_symbols if s and re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", s)})


# ---------------------------------------------------------------- daily bars

def fetch_daily(cl, symbols, start, end, part, workers=4):
    """SIP daily bars for symbols over [start, end] as part `part`, month by month, resumable by partition."""
    table = "raw_bars_1d"
    sets = [r for r in storage.manifest(table) if r.get("kind") == "daily_symbol_set" and r.get("part") == part]
    if any(r.get("complete") for r in sets):
        log(f"bars {part}: complete, skipped")
        return
    if sets:
        symbols = sets[-1]["symbols"]
    else:
        storage.append_manifest(table, {"kind": "daily_symbol_set", "part": part, "symbols": symbols, "complete": False,
                                        "start": start.isoformat(), "end": end.isoformat()})
    cal = ingest.sessions(start, end)
    done = storage.completed(table, part=part)
    dropper = ingest.SymbolDropper(table)
    todo = []
    for first, last in ingest._months(start, end):
        days = [d for d in cal["trading_date"].to_list() if first <= d <= last]
        if days and not all(storage.partition_key(table, source=SOURCE, feed="sip", date=d) in done for d in days):
            todo.append((first, last, days))
    log(f"bars {part}: {len(symbols)} symbols, {len(todo)} months to fetch")

    def month(first, last, days):
        lo, hi = ingest.range_bounds(first, last)
        pages = ingest.fetch_bars(cl, table, symbols, "1Day", lo, hi - dt.timedelta(seconds=1), (lo, hi), "sip", dropper)
        df = ingest._bars_frame(table, pages, "sip", "-", ingest.now())
        df = df.with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date())
        for d in days:
            if storage.partition_key(table, source=SOURCE, feed="sip", date=d) in done:
                continue
            rows = df.filter(pl.col("d") == d).drop("d")
            storage.write_partition(table, rows, part=part, request_ids=rows["request_id"].unique().to_list(),
                                    source=SOURCE, feed="sip", date=d)
        return first, df.height

    with cf.ThreadPoolExecutor(workers) as ex:
        for first, n in ex.map(lambda a: month(*a), todo):
            log(f"bars {part}: {first:%Y-%m} {n} rows; requests {cl.stats['requests']}")
    storage.append_manifest(table, {"kind": "daily_symbol_set", "part": part, "symbols": symbols, "complete": True,
                                    "start": start.isoformat(), "end": end.isoformat()})


def p1_symbols():
    return {s for r in storage.manifest("raw_bars_1d") if r.get("kind") == "daily_symbol_set" and r.get("complete")
            and r["part"].startswith("sip-1d-v") for s in r["symbols"]}


def bars(start=FIRST, end=LAST, symbols=None):
    """Raw SIP daily bars, one row per (symbol, session): the latest ingested copy wins."""
    df = read_frame(storage.data_root() / "raw_bars_1d", *ingest.range_bounds(start, end),
                    columns=["symbol", "feed", "open", "high", "low", "close", "volume", "ingested_at"], symbols=symbols)
    if df.height == 0:
        return df
    df = df.filter(pl.col("feed") == "sip").with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date())
    return df.sort("ingested_at").unique(subset=["symbol", "d"], keep="last").sort(["symbol", "d"])


# ---------------------------------------------------------------- adjustments and the bad-price rule

def price_factors(acts):
    """{symbol: [(ex_date, factor)]}: multiply a raw price before ex_date by factor to put it on the ex_date share
    basis. Splits: old_rate / new_rate; stock dividends: 1 / rate (ADR 0009 section 4; adjustments at read time)."""
    out = {}
    for r in acts.filter(pl.col("action_type").is_in(["forward_splits", "reverse_splits", "stock_dividends"])).iter_rows(named=True):
        raw = json.loads(r["raw"])
        day = r["event_at"].astimezone(ET).date()
        if r["action_type"] == "stock_dividends":
            rate = raw.get("rate")
            f = 1.0 / rate if isinstance(rate, (int, float)) and rate > 0 else None
        else:
            o, n = raw.get("old_rate"), raw.get("new_rate")
            f = float(o) / float(n) if isinstance(o, (int, float)) and isinstance(n, (int, float)) and n else None
        if f and raw.get("symbol"):
            out.setdefault(raw["symbol"], []).append((day, f))
    return {s: sorted(v) for s, v in out.items()}


def adjust_to(price, price_day, basis_day, factors):
    """Raw price of price_day on the share basis of basis_day (> price_day): every factor with
    price_day < ex_date <= basis_day applies."""
    for ex, f in factors:
        if price_day < ex <= basis_day:
            price *= f
    return price


def bad_bars(rows, factors=()):
    """ADR 0009 section 4 bad-price rule on one symbol's bars sorted by date.
    rows: [(date, open, high, low, close, volume)] -> {date: reason}.
    (a) malformed: OHLC <= 0, high < low, open/close outside [low, high], volume < 0;
    (b) spike: split-adjusted |c_t / c_{t-1} - 1| > 0.75 that reverts by more than half within 3 sessions, with no
        corporate action on the symbol between t-1 and t+3;
    (c) missed split: a close-to-close ratio within 2% of a split factor with no recorded split -> every bar from t on."""
    out = {}
    ok = []
    for r in rows:
        d, o, h, l, c, v = r
        if min(o, h, l, c) <= 0 or h < l or not (l <= o <= h) or not (l <= c <= h) or (v is not None and v < 0):
            out[d] = "malformed"
        else:
            ok.append(r)
    ex_days = [ex for ex, _ in factors]
    adj = [adjust_to(r[4], r[0], ok[-1][0], factors) if factors else r[4] for r in ok]
    for i in range(1, len(ok)):
        d0, d = ok[i - 1][0], ok[i][0]
        if any(d0 < ex <= (ok[min(i + 3, len(ok) - 1)][0]) for ex in ex_days):
            continue
        ratio = adj[i] / adj[i - 1]
        if any(abs(ratio * k - 1) <= 0.02 or abs(ratio / k - 1) <= 0.02 for k in SPLIT_FACTORS):
            for r in ok[i:]:
                out.setdefault(r[0], "missed_split")
            break
        if abs(ratio - 1) > 0.75:
            move = adj[i] - adj[i - 1]
            later = adj[i + 1:i + 4]
            if later and any(abs(x - adj[i]) > 0.5 * abs(move) and (x - adj[i]) * move < 0 for x in later):
                out.setdefault(d, "spike")
    return out


# ---------------------------------------------------------------- point-in-time symbol (ADR 0009 section 4)

class SymbolMap:
    def __init__(self, filings, bar_idx, renames):
        """filings: insider_filings frame; bar_idx: {symbol: sorted calendar indices of its SIP daily bars};
        renames: [(old, new, date)] from name-change actions."""
        obs = (filings.filter(pl.col("owner_seq") == 0).select("issuer_cik", "issuer_symbol", "filing_date")
               .drop_nulls().unique())
        self.by_cik, self.by_sym = {}, {}
        for cik, s, d in obs.iter_rows():
            n = norm(s)
            if not n:
                continue
            self.by_cik.setdefault(cik, []).append((d, n, s))
            self.by_sym.setdefault(n, []).append((d, cik))
        for v in self.by_cik.values():
            v.sort()
        for v in self.by_sym.values():
            v.sort()
        self.bar_idx, self.renames = bar_idx, sorted(renames, key=lambda x: x[2])
        self.observations = obs

    def has_bar_before(self, sym, ent_i):
        """A SIP daily bar in the 10 sessions before the entry session (index ent_i in the calendar)."""
        arr = self.bar_idx.get(sym)
        if arr is None or not len(arr):
            return False
        j = int(np.searchsorted(arr, ent_i))
        return j > 0 and arr[j - 1] >= ent_i - 10

    def ambiguous(self, sym, cik, t_day):
        lo = t_day - dt.timedelta(days=180)
        return any(lo <= d <= t_day and c != cik for d, c in self.by_sym.get(sym, []))

    def resolve(self, cik, filed_symbol, t_day, entry, ent_i):
        """-> (symbol or None, method, detail)."""
        s = norm(filed_symbol)
        if s and self.ambiguous(s, cik, t_day):
            return None, "ambiguous", f"{s} filed by another CIK within 180 days"
        if s and self.has_bar_before(s, ent_i):
            return s, "filed", None
        cur, seen = s, set()
        while cur and cur not in seen:
            seen.add(cur)
            nxt = next((new for old, new, day in self.renames if old == cur and day <= entry), None)
            if nxt and self.has_bar_before(nxt, ent_i):
                return nxt, "rename", f"{s} -> {nxt}"
            cur = nxt
        lo = t_day - dt.timedelta(days=180)
        for d, n, raw in reversed(self.by_cik.get(cik, [])):
            if lo <= d <= t_day and n != s and self.has_bar_before(n, ent_i) and not self.ambiguous(n, cik, t_day):
                return n, "recent_filed", f"{raw} filed {d}"
        return None, "unmapped", s


def bar_index(b, sessions):
    """{symbol: sorted numpy array of the calendar indices of its bar sessions}."""
    sess = pl.DataFrame({"d": sessions, "i": list(range(len(sessions)))}, schema={"d": pl.Date, "i": pl.Int32})
    x = b.select("symbol", "d").join(sess, on="d", how="inner").group_by("symbol").agg(pl.col("i").sort())
    return {sym: np.asarray(ii, dtype=np.int32) for sym, ii in x.iter_rows()}


# ---------------------------------------------------------------- quote samples

def sample_quotes(cl, d, symbols, t, rule, part, dropper):
    """SIP NBBO prevailing at UTC time t for each symbol (look-backs 5 s, then 60 s), one partition part per date."""
    table = "raw_quote_samples"
    unit = "round_lot" if d < ingest.SIZE_UNIT_SWITCH else "shares"
    found, need, rids = {}, sorted(set(symbols)), []
    for lb in ingest.QUOTE_LOOKBACKS_MS:
        if not need:
            break
        lo = t - dt.timedelta(milliseconds=lb)
        for i in range(0, len(need), CHUNK):
            chunk = need[i:i + CHUNK]

            def fetch(syms):
                return list(cl.pages("/v2/stocks/quotes", {"symbols": ",".join(syms), "start": ingest.z(lo), "end": ingest.z(t),
                                                           "limit": 10000, "feed": "sip", "asof": "-", "sort": "asc"},
                                     "quotes", table=table, event_range=(lo, t + dt.timedelta(microseconds=1))))
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
        r["sample_rule"] = rule
        rows.append(r)
    df = storage.frame(table, rows)
    storage.write_partition(table, df, part=part, request_ids=rids, source=SOURCE, feed="sip", date=d)
    return df.height, len(rids)


def spreads(rule, start=FIRST, end=LAST, symbols=None):
    """{(symbol, date): quoted spread (ask - bid) / mid} for samples of `rule` with a valid two-sided quote."""
    df = read_frame(storage.data_root() / "raw_quote_samples", *ingest.range_bounds(start, end),
                    columns=["symbol", "status", "bid_price", "ask_price", "sample_rule", "ingested_at"], symbols=symbols)
    if df.height == 0:
        return {}
    df = df.filter((pl.col("sample_rule") == rule) & (pl.col("status") == "ok")
                   & (pl.col("bid_price") > 0) & (pl.col("ask_price") >= pl.col("bid_price")))
    df = df.with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date())
    df = df.sort("ingested_at").unique(subset=["symbol", "d"], keep="last")
    sp = (df["ask_price"] - df["bid_price"]) / ((df["ask_price"] + df["bid_price"]) / 2)
    return dict(zip(zip(df["symbol"].to_list(), df["d"].to_list()), sp.to_list()))


# ---------------------------------------------------------------- pre-entry state (no outcome)

class PreEntry:
    """Price and 20-session dollar volume as of a session's close, from bars dated <= that session only."""

    def __init__(self, b, sessions):
        self.sessions = sessions
        self.idx = {d: i for i, d in enumerate(sessions)}
        self.close, self.dv = {}, {}
        for (sym,), g in b.partition_by("symbol", as_dict=True, maintain_order=True).items():
            self.close[sym] = dict(zip(g["d"].to_list(), g["close"].to_list()))
            self.dv[sym] = dict(zip(g["d"].to_list(), (g["close"] * g["volume"]).to_list()))

    def last_close(self, sym, d):
        """(close, date) of the last bar on or before d within 10 sessions, or (None, None)."""
        c = self.close.get(sym, {})
        i = self.idx[d]
        for j in range(i, max(-1, i - 10), -1):
            x = c.get(self.sessions[j])
            if x is not None:
                return x, self.sessions[j]
        return None, None

    def dv20(self, sym, d):
        """Median of close * volume over the 20 sessions ending at d; a session without a bar counts 0."""
        i = self.idx[d]
        if i + 1 < DV_SESSIONS:
            return None
        v = self.dv.get(sym, {})
        return statistics.median(v.get(self.sessions[j], 0.0) for j in range(i - DV_SESSIONS + 1, i + 1))


def prefilter_plan(b, sessions, factors):
    """{session: [symbols]} that can be in universe (b) as of that close (necessary condition; ADR 0009 section 5):
    a bar that session with close >= $1 (or >= $1 after a split effective the next session), and the median of
    close * volume over the 20 sessions ending there >= $100K, a session without a bar counting 0."""
    sess = pl.DataFrame({"d": sessions, "i": list(range(len(sessions)))}, schema={"d": pl.Date, "i": pl.Int32})
    x = b.select("symbol", "d", "close", "volume").join(sess, on="d", how="inner")
    rng = x.group_by("symbol").agg(pl.col("i").min().alias("lo"), pl.col("i").max().alias("hi"))
    rng = rng.with_columns(lo=pl.max_horizontal(pl.col("lo") - (DV_SESSIONS - 1), pl.lit(0)).cast(pl.Int32))
    grid = rng.select("symbol", pl.int_ranges("lo", pl.col("hi") + 1, dtype=pl.Int32).alias("i")).explode("i", empty_as_null=True)
    g = grid.join(x.drop("d"), on=["symbol", "i"], how="left").sort(["symbol", "i"])
    g = g.with_columns(dv=(pl.col("close") * pl.col("volume")).fill_null(0.0))
    g = g.with_columns(dv20=pl.col("dv").rolling_median(DV_SESSIONS, min_samples=DV_SESSIONS).over("symbol"))
    idx = {d: i for i, d in enumerate(sessions)}
    nxt = [(sym, idx[ex] - 1, f) for sym, fs in factors.items() for ex, f in fs if ex in idx and idx[ex] > 0]
    nf = (pl.DataFrame(nxt, schema={"symbol": pl.Utf8, "i": pl.Int32, "f": pl.Float64}, orient="row")
          .group_by(["symbol", "i"]).agg(pl.col("f").product()))
    g = g.join(nf, on=["symbol", "i"], how="left")
    ok = g.filter(pl.col("close").is_not_null() & (pl.col("dv20") >= MIN_DV_B)
                  & ((pl.col("close") >= MIN_PRICE_B) | (pl.col("close") * pl.col("f").fill_null(1.0) >= MIN_PRICE_B)))
    out = {}
    for i, syms in ok.group_by("i").agg(pl.col("symbol").sort()).iter_rows():
        out[sessions[i]] = syms
    return out


# ---------------------------------------------------------------- steps

def session_list():
    return calendar()["trading_date"].to_list()


def entry_date(available_at, cal_rows):
    """First regular-session open strictly after available_at."""
    for r in cal_rows:
        if r["open_at"] > available_at:
            return r["trading_date"]
    return None


def plan_steps():
    """Symbols to sample per session (close - 5 min): the prefilter, plus event issuers on entry - 1 and on the exit
    days of H = 5, 20, 60."""
    sessions = session_list()
    idx = {d: i for i, d in enumerate(sessions)}
    pre = prefilter_plan(bars(), sessions, price_factors(actions()))
    ev = json.loads(derived("i1_events_mapped.json").read_text(encoding="utf-8"))["events"]
    extra = {}
    for e in ev:
        if not e.get("symbol") or not e.get("entry_date"):
            continue
        i = idx.get(dt.date.fromisoformat(e["entry_date"]))
        if not i:
            continue
        extra.setdefault(sessions[i - 1], set()).add(e["symbol"])
        for H in HORIZONS:
            if i + H - 1 < len(sessions):
                extra.setdefault(sessions[i + H - 1], set()).add(e["symbol"])
    first = min(dt.date.fromisoformat(e["entry_date"]) for e in ev if e.get("entry_date") and e.get("symbol"))
    start = sessions[max(0, idx[first] - 1)]
    return {d: sorted(set(pre.get(d, [])) | extra.get(d, set())) for d in sessions if d >= start}


def write_cik_map(sm, resolutions):
    table, got = "cik_symbol_map", ingest.now()
    rows = [{"event_at": day_bounds(d)[0], "kind": "observation", "cik": cik, "symbol": norm(sym), "filed_symbol": sym,
             "method": "filed", "detail": None, "feed": "none", "request_id": "derived:insider_filings", "ingested_at": got,
             "source": "scalp_engine", "schema_version": 1}
            for cik, sym, d in sm.observations.iter_rows() if norm(sym)]
    rows += [{"event_at": day_bounds(dt.date.fromisoformat(r["entry_date"]))[0], "kind": "resolution", "cik": r["cik"],
              "symbol": r["symbol"], "filed_symbol": r["filed_symbol"], "method": r["map_method"], "detail": r["map_detail"],
              "feed": "none", "request_id": "derived:i1-map-v1", "ingested_at": got, "source": "scalp_engine",
              "schema_version": 1} for r in resolutions if r.get("entry_date")]
    df = storage.frame(table, rows).unique(subset=["kind", "cik", "symbol", "event_at"], keep="first", maintain_order=True)
    done = storage.completed(table, part="i1-map-v1")
    d = pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date()
    for (day,), g in df.with_columns(_d=d).partition_by("_d", as_dict=True, maintain_order=True).items():
        if storage.partition_key(table, source="scalp_engine", date=day) not in done:
            storage.write_partition(table, g.drop("_d"), part="i1-map-v1", source="scalp_engine", date=day)


def derived(name):
    return storage.data_root() / "derived" / name


def step_base(cl):
    ingest.ingest_calendar(cl, FIRST, LAST)
    ingest_actions_i1(cl)


def step_bars(cl, workers):
    ev = json.loads(derived("i1_events_raw.json").read_text(encoding="utf-8"))["events"]
    syms = pool_symbols({norm(e["filed_symbol"]) for e in ev})
    fetch_daily(cl, syms, FIRST, P1_FIRST - dt.timedelta(days=1), "sip-1d-i1a", workers)
    fetch_daily(cl, sorted(set(syms) - p1_symbols()), P1_FIRST, P1_LAST, "sip-1d-i1b", workers)
    fetch_daily(cl, syms, P1_LAST + dt.timedelta(days=1), LAST, "sip-1d-i1c", workers)


def step_map():
    """Entry date and point-in-time symbol for every raw event; writes cik_symbol_map and i1_events_mapped.json."""
    from scalp_engine import insider_data as D
    raw = json.loads(derived("i1_events_raw.json").read_text(encoding="utf-8"))
    cal_rows = calendar().select("trading_date", "open_at").to_dicts()
    sessions = [r["trading_date"] for r in cal_rows]
    sm = SymbolMap(D.filings_frame(), bar_index(bars(), sessions), ingest.renames(FIRST, LAST))
    out, counts = [], {}
    for e in raw["events"]:
        t = dt.datetime.fromisoformat(e["accepted_at"])
        avail = t + dt.timedelta(minutes=5)
        ent = entry_date(avail, cal_rows)
        r = dict(e, available_at=avail.isoformat(), entry_date=ent.isoformat() if ent else None)
        if ent is None:
            r.update(symbol=None, map_method="no_session", map_detail=None)
        else:
            sym, method, detail = sm.resolve(e["cik"], e["filed_symbol"], t.astimezone(ET).date(), ent,
                                             bisect.bisect_left(sessions, ent))
            r.update(symbol=sym, map_method=method, map_detail=detail)
        counts[r["map_method"]] = counts.get(r["map_method"], 0) + 1
        out.append(r)
    write_cik_map(sm, out)
    derived("i1_events_mapped.json").write_text(json.dumps({"counts": counts, "events": out}) + "\n", encoding="utf-8")
    log(f"map: {counts}")


def step_plan():
    plan = plan_steps()
    n = sum(len(v) for v in plan.values())
    req = sum(2 * -(-len(v) // CHUNK) + 6 for v in plan.values())  # two look-backs per chunk plus paging allowance
    rec = {"sessions": len(plan), "symbol_samples": n, "requests_est": req,
           "hours_at_170_per_min": round(req / 170 / 60, 2), "plan": {d.isoformat(): v for d, v in plan.items()}}
    derived("i1_quote_plan.json").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    log(f"plan: {len(plan)} sessions, {n} samples, about {req} requests ({rec['hours_at_170_per_min']} h)")


def step_close_quotes(cl, workers):
    rec = json.loads(derived("i1_quote_plan.json").read_text(encoding="utf-8"))
    cal = {r["trading_date"]: r["close_at"] for r in calendar().iter_rows(named=True)}
    done = storage.completed("raw_quote_samples", part="i1-close-v1")
    todo = [(dt.date.fromisoformat(d), syms) for d, syms in sorted(rec["plan"].items())
            if storage.partition_key("raw_quote_samples", source=SOURCE, feed="sip", date=dt.date.fromisoformat(d)) not in done]
    dropper = ingest.SymbolDropper("raw_quote_samples")
    log(f"close-quotes: {len(todo)} sessions to sample")
    n = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(sample_quotes, cl, d, syms, cal[d] - ingest.CLOSE_SAMPLE_OFFSET, CLOSE_RULE, "i1-close-v1",
                          dropper): d for d, syms in todo}
        for f in cf.as_completed(futs):
            f.result()
            n += 1
            if n % 20 == 0 or n == len(todo):
                log(f"close-quotes: {n}/{len(todo)} sessions; requests {cl.stats['requests']}")


def flags_for(events):
    """Universe (b)/(c) flags as of the entry decision: last close before entry (split-adjusted to the entry basis),
    20-session dollar volume ending at that close, quoted spread at that session's close - 5 min."""
    sessions = session_list()
    syms = sorted({e["symbol"] for e in events if e.get("symbol")})
    b = bars(symbols=syms)
    pre = PreEntry(b, sessions)
    factors = price_factors(actions())
    sp = spreads(CLOSE_RULE, symbols=syms)
    out = []
    for e in events:
        r = dict(e, pre_close=None, pre_dv20=None, pre_spread=None, universe_b=None, universe_c=None, flag_detail=None)
        if not e.get("symbol") or not e.get("entry_date"):
            r["flag_detail"] = "unmapped or no session"
            out.append(r)
            continue
        ent = dt.date.fromisoformat(e["entry_date"])
        prev = sessions[pre.idx[ent] - 1]
        c, cd = pre.last_close(e["symbol"], prev)
        if c is None:
            r.update(flag_detail="no bar in the 10 sessions before entry", universe_b=False, universe_c=False)
            out.append(r)
            continue
        price = adjust_to(c, cd, ent, factors.get(e["symbol"], ()))
        dv, s = pre.dv20(e["symbol"], prev), sp.get((e["symbol"], prev))
        r.update(pre_close=price, pre_dv20=dv, pre_spread=s)
        if s is None:
            r.update(flag_detail="no valid quote at the prior close - 5 min", universe_b=False, universe_c=False)
        else:
            r["universe_b"] = price >= MIN_PRICE_B and (dv or 0) >= MIN_DV_B and s <= MAX_SPREAD_B
            r["universe_c"] = price >= MIN_PRICE_C and (dv or 0) >= MIN_DV_C and s <= MAX_SPREAD_C
        out.append(r)
    return out


def step_flags():
    ev = json.loads(derived("i1_events_mapped.json").read_text(encoding="utf-8"))["events"]
    out = flags_for(ev)
    derived("i1_events_flagged.json").write_text(json.dumps({"events": out}) + "\n", encoding="utf-8")
    log(f"flags: {sum(bool(e['universe_b']) for e in out)} in (b), {sum(bool(e['universe_c']) for e in out)} in (c)")


def step_open_quotes(cl, workers):
    ev = json.loads(derived("i1_events_flagged.json").read_text(encoding="utf-8"))["events"]
    by_day = {}
    for e in ev:
        if e.get("universe_c") and e.get("entry_date"):
            by_day.setdefault(dt.date.fromisoformat(e["entry_date"]), set()).add(e["symbol"])
    done = storage.completed("raw_quote_samples", part="i1-open-v1")
    todo = [(d, s) for d, s in sorted(by_day.items())
            if storage.partition_key("raw_quote_samples", source=SOURCE, feed="sip", date=d) not in done]
    dropper = ingest.SymbolDropper("raw_quote_samples")
    log(f"open-quotes: {len(todo)} entry sessions")
    with cf.ThreadPoolExecutor(workers) as ex:
        list(ex.map(lambda x: sample_quotes(cl, x[0], sorted(x[1]), ingest.et(x[0], OPEN_SAMPLE), OPEN_RULE,
                                            "i1-open-v1", dropper), todo))
    log(f"open-quotes: done; requests {cl.stats['requests']}")


def write_event_table(_docs=None):
    """insider_events (ADR 0009 section 7): events whose entry date is in [2022-02-01, 2026-07-20]."""
    from scalp_engine import insider_data as D
    from scalp_engine import insider_rule as R
    ev = json.loads(derived("i1_events_flagged.json").read_text(encoding="utf-8"))["events"]
    info = {r["cik"]: r for r in D.sic_frame().iter_rows(named=True)}
    got = ingest.now()
    rows = []
    for e in ev:
        if not e.get("entry_date") or not ("2022-02-01" <= e["entry_date"] <= "2026-07-20"):
            continue
        t = dt.datetime.fromisoformat(e["accepted_at"])
        si = info.get(e["cik"])
        rows.append({"event_at": dt.datetime.fromisoformat(e["available_at"]),
                     "event_id": f"{e['trigger']}-{e['cik']}-{t:%Y%m%dT%H%M%SZ}-{e['completing']}",
                     "trigger": e["trigger"], "cik": e["cik"], "symbol": e.get("symbol"), "map_method": e["map_method"],
                     "accepted_at": t, "entry_date": dt.date.fromisoformat(e["entry_date"]), "accessions": e["accessions"],
                     "people": e.get("people"), "total_usd": e.get("total_usd"),
                     "fpi": (si["annual_form"] in ("20-F", "40-F")) if si else None, "sic": si["sic"] if si else None,
                     "pre_close": e.get("pre_close"), "pre_dv20": e.get("pre_dv20"), "pre_spread": e.get("pre_spread"),
                     "universe_b": e.get("universe_b"), "universe_c": e.get("universe_c"), "flag_detail": e.get("flag_detail"),
                     "rule_version": R.RULE_VERSION, "feed": "none", "request_id": "derived:i1-events-v1",
                     "ingested_at": got, "source": "scalp_engine", "schema_version": 1})
    df = storage.frame("insider_events", rows)
    if df["event_id"].n_unique() != df.height:
        raise ValueError("duplicate event_id")
    done = storage.completed("insider_events", part="i1-events-v1")
    d = pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date()
    for (day,), g in df.with_columns(_d=d).partition_by("_d", as_dict=True, maintain_order=True).items():
        if storage.partition_key("insider_events", source="scalp_engine", date=day) not in done:
            storage.write_partition("insider_events", g.drop("_d"), part="i1-events-v1", source="scalp_engine", date=day)
    log(f"insider_events: {df.height} rows")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.insider_prices")
    ap.add_argument("step", choices=["base", "bars", "map", "plan", "close-quotes", "flags", "open-quotes", "table"])
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    log(f"data root {storage.data_root()}; step {a.step}")
    if a.step in ("map", "plan", "flags", "table"):
        {"map": step_map, "plan": step_plan, "flags": step_flags, "table": write_event_table}[a.step]()
        return 0
    cl = client()
    {"base": lambda: step_base(cl), "bars": lambda: step_bars(cl, a.workers),
     "close-quotes": lambda: step_close_quotes(cl, a.workers),
     "open-quotes": lambda: step_open_quotes(cl, a.workers)}[a.step]()
    log(f"client stats {cl.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
