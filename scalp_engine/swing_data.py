"""S1 ingestion (ADR 0019 section 2): EDGAR submissions (8-K items, annual forms) for universe CIKs, SEC header
acceptance times of Item 2.02 8-Ks, the A6 top-up (2026-10-19 -> 2026-10-20), minute bars (A5) and 09:35 samples for
the swing tickets. P1 mechanics; check_range before every request; nothing on or after 2026-11-02. No outcome.

    python -m scalp_engine.swing_data STEP
    STEP: ciks | submissions | headers | topup | plan | minute | open935
"""
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import sys

import polars as pl

from scalp_engine import ingest, storage
from scalp_engine import insider_data as D
from scalp_engine import insider_prices as P
from scalp_engine import swing_signals as W
from scalp_engine.data_access import ET, check_range, day_bounds, read_frame

UTC = dt.timezone.utc
FIRST = dt.date(2022, 1, 3)
LAST_FILE = dt.date(2026, 10, 20)
HEADERS_FROM = dt.date(2022, 9, 1)
TOPUP = (dt.date(2026, 10, 19), dt.date(2026, 10, 20))  # A6: to the last complete session before the run
BYTES_PER_ROW = 19.3  # measured on raw_bars_1m (T1)
FLOOR_GB, BUDGET_H = 8.0, 24.0
NEVER = dt.datetime(2026, 11, 2, 4, tzinfo=UTC)
FORMS = {"8-K", "8-K/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
SUB_PAGE = "https://data.sec.gov/submissions/{name}"
REG_OPEN, REG_CLOSE = dt.time(9, 30), dt.time(16, 0)
o = pl.col


def derived(name):
    return storage.data_root() / "derived" / name


def log(m):
    ingest.log(m)


def guard(lo, hi):
    if hi > NEVER:
        raise ValueError("S1 never requests anything on or after 2026-11-02")
    check_range(lo, hi)


# ---------------------------------------------------------------- universe CIKs (counts / identity only)

def universe_ciks():
    b, sessions, obs, sic, _, _ = _inputs_no_filings()
    b = W.apply_survivorship(b)
    f = W.features(b).join(W.sessions_dv20(b, sessions), on=["symbol", "d"], how="left")
    empty = pl.DataFrame(schema={"cik": pl.Utf8, "filing_date": pl.Date, "form": pl.Utf8})
    f = W.attach_identity(f, obs, sic, empty).filter(o("d").is_between(W.SIG_FIRST, W.SIG_LAST))
    u = f.filter(W.universe_mask())
    return sorted({c for c in u["cik"].to_list() if c})


def _inputs_no_filings():
    root = storage.data_root()
    hi = day_bounds(dt.date(2026, 10, 16))[1]
    b = read_frame(root / "raw_bars_1d", W.ALL[0], hi, columns=["symbol", "feed", "open", "high", "low", "close", "volume", "ingested_at"])
    b = b.filter(o("feed") == "sip").with_columns(d=o("event_at").dt.convert_time_zone("America/New_York").dt.date())
    b = b.sort("ingested_at").unique(subset=["symbol", "d"], keep="last").select("symbol", "d", "open", "high", "low", "close", "volume")
    sessions = sorted(read_frame(root / "calendar", W.ALL[0], hi, columns=["trading_date"])["trading_date"].unique().to_list())
    fil = read_frame(root / "insider_filings", *W.ALL, columns=["issuer_cik", "issuer_symbol", "filing_date", "owner_seq"])
    obs = (fil.filter(o("owner_seq") == 0).select("issuer_cik", "issuer_symbol", "filing_date").drop_nulls().unique()
           .select(symbol=o("issuer_symbol").map_elements(P.norm, return_dtype=pl.Utf8), obs_d=o("filing_date"), cik=o("issuer_cik"))
           .drop_nulls().sort(["symbol", "obs_d", "cik"]).unique(subset=["symbol", "obs_d"], keep="last"))
    sic = read_frame(root / "issuer_sic", *W.ALL, columns=["cik", "sic", "ingested_at"])
    sic = sic.sort("ingested_at").unique(subset=["cik"], keep="last").select("cik", "sic")
    return b, sessions, obs, sic, None, None


def step_ciks():
    ciks = universe_ciks()
    derived("s1_universe_ciks.json").write_text(json.dumps(ciks) + "\n", encoding="utf-8")
    log(f"universe CIKs: {len(ciks)}")


# ---------------------------------------------------------------- EDGAR submissions

def parse_block(block, cik, rid, got):
    """Rows of a submissions block (recent or an older page) filed in [2022-01-03, 2026-10-19] with a wanted form.
    Rows outside the range are dropped at parse time and never stored (whole-document source)."""
    forms, dates = block.get("form") or [], block.get("filingDate") or []
    accs, items, acc_t = block.get("accessionNumber") or [], block.get("items") or [], block.get("acceptanceDateTime") or []
    out = []
    for i in range(len(forms)):
        if forms[i] not in FORMS or i >= len(dates) or not dates[i]:
            continue
        fd = dt.date.fromisoformat(dates[i])
        if not (FIRST <= fd <= LAST_FILE):
            continue
        out.append({"event_at": D.midnight_et(fd), "cik": str(cik), "accession": accs[i], "form": forms[i], "filing_date": fd,
                    "items": (items[i] if i < len(items) else "") or None,
                    "acceptance_json": (acc_t[i] if i < len(acc_t) else "") or None, "feed": "none", "request_id": rid,
                    "ingested_at": got, "source": "sec", "schema_version": 1})
    return out


def step_submissions(http, workers=8):
    table = "issuer_filings"
    ciks = json.loads(derived("s1_universe_ciks.json").read_text(encoding="utf-8"))
    done = {r["cik"] for r in storage.manifest(table) if r.get("kind") == "cik_done"}
    todo = [c for c in ciks if c not in done]
    rng = (D.midnight_et(FIRST), D.midnight_et(LAST_FILE + dt.timedelta(days=1)))
    guard(*rng)
    log(f"submissions: {len(ciks)} CIKs, {len(todo)} to fetch")

    def one(c):
        body, rid = http.get(D.SUBMISSIONS.format(cik=int(c)), table=table, declared_range=rng)
        got = dt.datetime.now(UTC)
        if not body:
            return c, [], 1
        doc = json.loads(body)
        rows = parse_block((doc.get("filings") or {}).get("recent") or {}, c, rid, got)
        pages = 1
        for f in (doc.get("filings") or {}).get("files") or []:
            to = f.get("filingTo")
            if to and dt.date.fromisoformat(to) < FIRST:
                continue  # an older page entirely before 2022-01-03: never requested
            pb, prid = http.get(SUB_PAGE.format(name=f["name"]), table=table, declared_range=rng)
            pages += 1
            if pb:
                rows += parse_block(json.loads(pb), c, prid, dt.datetime.now(UTC))
        return c, rows, pages

    batch, cks, n, pages = [], [], 0, 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for c, rows, pg in ex.map(one, todo):
            batch += rows
            cks.append(c)
            n += 1
            pages += pg
            if len(cks) >= 300 or n == len(todo):
                if batch:
                    seen, uniq = set(), []
                    for r in batch:  # an accession appears once per filer CIK; keep the first
                        if r["accession"] not in seen:
                            seen.add(r["accession"])
                            uniq.append(r)
                    D._write_by_day(table, uniq, f"s1-sub-{hashlib.sha256(','.join(cks).encode()).hexdigest()[:12]}")
                for x in cks:
                    storage.append_manifest(table, {"kind": "cik_done", "cik": x})
                log(f"submissions: {n}/{len(todo)} CIKs, {pages} requests; http {http.stats}")
                batch, cks = [], []


def filings_frame():
    df = read_frame(storage.data_root() / "issuer_filings", *W.ALL)
    return df.sort("ingested_at").unique(subset=["accession"], keep="last") if df.height else df


# ---------------------------------------------------------------- SEC headers for Item 2.02 8-Ks

def required_8k():
    f = filings_frame()
    return (f.filter(o("form").is_in(["8-K", "8-K/A"]) & o("items").fill_null("").str.contains(r"2\.02")
                     & o("filing_date").is_between(HEADERS_FROM, LAST_FILE + dt.timedelta(days=1)))
            .select("accession", issuer_cik=o("cik"), filing_date=o("filing_date"), document_type=o("form")))


def step_headers(http, workers=8):
    """Reuses insider_data.ingest_headers on the 8-K list (header_day_done markers are per filing date and part)."""
    table = "filing_acceptance"
    req = required_8k()
    have = read_frame(storage.data_root() / table, *W.ALL, columns=["accession", "method", "status"])
    have = set(have.filter((o("method") == "header") & (o("status") == "ok"))["accession"].to_list()) if have.height else set()
    todo = req.filter(~o("accession").is_in(sorted(have)))
    log(f"headers: {req.height} Item 2.02 8-Ks, {todo.height} to fetch")

    def one(r):
        url = D.HEADER_URL.format(cik=r["issuer_cik"], nodash=r["accession"].replace("-", ""), acc=r["accession"])
        body, rid = http.get(url, table=table, declared_range=D.header_range(r["filing_date"]))
        raw, at = D.parse_acceptance((body or b"").decode("utf-8", "replace"))
        return r, raw, at, rid

    for (fd,), g in todo.sort("filing_date").partition_by("filing_date", as_dict=True, maintain_order=True).items():
        with cf.ThreadPoolExecutor(workers) as ex:
            res = list(ex.map(one, g.iter_rows(named=True)))
        got = dt.datetime.now(UTC)
        rows = [{"event_at": at if at else D.midnight_et(fd), "accession": r["accession"], "cik": r["issuer_cik"], "raw": raw,
                 "status": "ok" if at else "missing", "method": "header", "feed": "none", "request_id": rid,
                 "ingested_at": got, "source": "sec", "schema_version": 1} for r, raw, at, rid in res]
        D._write_by_day(table, rows, f"s1-hdr-{fd.isoformat()}")
        log(f"headers {fd}: {g.height}, missing {sum(1 for x in res if x[2] is None)}; http {http.stats}")


# ---------------------------------------------------------------- A6 top-up (2026-10-19)

def missing_daily_symbols(symbols, first, last):
    """Symbols never requested by any daily-bar symbol set covering every session of [first, last] (A6)."""
    from scalp_engine import t1_data as T
    covered = set()
    for a, b, syms in T.daily_sets():
        if a <= first and last <= b:
            covered |= syms
    return sorted(set(symbols) - covered)


def held_samples(d, t):
    """Symbols with a quote sample at exactly UTC time t on session d (any rule)."""
    q = read_frame(storage.data_root() / "raw_quote_samples", *day_bounds(d), columns=["symbol"])
    return {s for s, e in zip(q["symbol"].to_list(), q["event_at"].to_list()) if e == t} if q.height else set()


def step_topup(cl):
    """A6: the calendar and corporate actions for 2026-10-19 -> 20 are held from T1 (checked, never re-fetched);
    daily bars only for pool symbols never requested for those sessions (requested symbols only, held keys dropped);
    close - 5 min samples on both sessions for pre-filter and ticket symbols not already sampled at that time."""
    from scalp_engine import t1_data as T
    have_cal = {r["partition"] for r in storage.manifest("calendar") if r.get("kind") == "partition"}
    have_act = {r["partition"] for r in storage.manifest("corporate_actions") if r.get("kind") == "partition"}
    days = [d for d in P.session_list() if TOPUP[0] <= d <= TOPUP[1]]
    for d in days:
        if storage.partition_key("calendar", source="alpaca", date=d) not in have_cal:
            raise LookupError(f"calendar {d} not held: run the T1 top-up first")
    act_done = any(r.get("kind") == "step_done_t1" and r.get("end", "") >= TOPUP[1].isoformat()
                   for r in storage.manifest("corporate_actions"))
    if not act_done:
        raise LookupError("corporate actions 2026-10-19 -> 20 not held (T1 top-up)")
    log(f"topup: calendar and corporate actions held for {len(days)} sessions "
        f"({sum(1 for d in days if storage.partition_key('corporate_actions', source='alpaca', date=d) in have_act)} with actions)")
    miss = missing_daily_symbols(P.pool_symbols(), TOPUP[0], TOPUP[1])
    log(f"topup daily: {len(miss)} pool symbols never requested for {TOPUP[0]} -> {TOPUP[1]}")
    if miss:
        T.fetch_daily_kept(cl, miss, TOPUP[0], TOPUP[1], "sip-1d-s1t")
    b = P.bars(dt.date(2026, 9, 1), TOPUP[1])
    sessions = [d for d in P.session_list() if d <= TOPUP[1]]
    pre = P.prefilter_plan(b, sessions, P.price_factors(P.actions()))
    tk = read_frame(storage.data_root() / "swing_tickets", *W.ALL, columns=["symbol"])
    if tk.height == 0:
        raise LookupError("no swing tickets: build them (swing_signals build) before the close - 5 min top-up")
    cal = read_frame(storage.data_root() / "calendar", *ingest.range_bounds(TOPUP[0], TOPUP[1]), columns=["trading_date", "close_at"])
    done = storage.completed("raw_quote_samples", part="s1-close-t1")
    dropper = ingest.SymbolDropper("raw_quote_samples")
    for d, close_at in cal.select("trading_date", "close_at").iter_rows():
        if storage.partition_key("raw_quote_samples", source="alpaca", feed="sip", date=d) in done:
            continue
        t = close_at - ingest.CLOSE_SAMPLE_OFFSET
        syms = sorted((set(pre.get(d, [])) | set(tk["symbol"].to_list())) - held_samples(d, t))
        guard(t - dt.timedelta(minutes=1), t + dt.timedelta(minutes=1))
        n, reqs = P.sample_quotes(cl, d, syms, t, P.CLOSE_RULE, "s1-close-t1", dropper)
        log(f"topup close-5 {d}: {n} samples, {reqs} requests")


# ---------------------------------------------------------------- minute bars and 09:35 samples (functions of tickets)

def ticket_windows(first=1, last=22, sets=("H1", "S-b", "controls"), families=("gaphold",)):
    """{session: set(symbols)} for sessions t + first .. t + last of the tickets of the given sets and families."""
    tk = read_frame(storage.data_root() / "swing_tickets", *W.ALL, columns=["set", "family", "symbol", "signal_date"])
    tk = tk.filter(o("set").is_in(list(sets)) & o("family").is_in(list(families))).select("symbol", "signal_date").unique()
    sessions = sorted(read_frame(storage.data_root() / "calendar", *W.ALL, columns=["trading_date"])["trading_date"].unique().to_list())
    idx = {d: i for i, d in enumerate(sessions)}
    out = {}
    for s, t in tk.iter_rows():
        i = idx[t]
        for j in range(i + first, min(i + last + 1, len(sessions))):
            out.setdefault(sessions[j], set()).add(s)
    return out


def full_session_held():
    """{session: symbols} whose regular session 09:30-15:59 SIP minutes are fully held: P1's full-day parts
    (sip-1m-v1, symbols present), or a T1 stage-1 fetch (04:00-09:55) together with a T1 top-20 fetch (09:56-19:59).
    A partial holding (stage 1 only) is fetched again; the held keys are dropped on write."""
    from scalp_engine import t1_data as T
    held = {}
    for r in storage.manifest("raw_bars_1m"):
        if r.get("kind") == "partition" and r.get("file") == "part-sip-1m-v1.parquet":
            d = dt.date.fromisoformat(r["partition"].rsplit("date=", 1)[1])
            held[d] = None  # resolved lazily from the file's symbols
    s1, top = T.fetch_lists("t1s1"), T.fetch_lists("t1top")
    out = {}
    for (d, feed), syms in top.items():
        if feed == "sip":
            out.setdefault(d, set()).update(syms & s1.get((d, "sip"), set()))
    return held, out


def held_full(d, p1_days, t1_full):
    from scalp_engine import t1_data as T
    return (T._p1_held(d, "sip") if d in p1_days else set()) | t1_full.get(d, set())


def held_quotes(rule):
    q = read_frame(storage.data_root() / "raw_quote_samples", *W.ALL, columns=["symbol", "sample_rule"])
    q = q.filter(o("sample_rule") == rule).with_columns(d=o("event_at").dt.convert_time_zone("America/New_York").dt.date())
    out = {}
    for s, d in q.select("symbol", "d").iter_rows():
        out.setdefault(d, set()).add(s)
    return out


def minute_need(last):
    p1_days, t1_full = full_session_held()
    win = ticket_windows(1, last)
    need = {}
    for d, syms in win.items():
        if d > TOPUP[1]:
            continue
        rest = sorted(syms - held_full(d, p1_days, t1_full))
        if rest:
            need[d] = rest
    return need


def budget(need, qneed, free):
    pairs = sum(len(v) for v in need.values())
    req = sum(-(-len(v) // P.CHUNK) + -(-len(v) * 390 // 10000) for v in need.values())
    qreq = sum(2 * -(-len(v) // P.CHUNK) + 4 for v in qneed.values())
    disk = pairs * 390 * BYTES_PER_ROW / 1e9
    hours = (req + qreq) / 170 / 60
    return {"pairs": pairs, "requests_est": req, "open935_requests_est": qreq, "hours": round(hours, 2),
            "disk_gb_est": round(disk, 2), "free_after_gb": round(free - disk - 0.1, 2),
            "pass": hours <= BUDGET_H and free - disk - 0.1 >= FLOOR_GB}


def step_plan():
    """ADR 0019 A5 and section 2: the primary scope if it fits the budget, else the fallback (re-checked), else stop.
    Decided from the signed ticket lists before any outcome; recorded for the minute step."""
    import shutil
    free = shutil.disk_usage("D:/").free / 1e9
    q = ticket_windows(1, 3)
    hq = held_quotes(P.OPEN_RULE)
    qneed = {d: sorted(s - hq.get(d, set())) for d, s in q.items() if d <= TOPUP[1]}
    qneed = {d: v for d, v in qneed.items() if v}
    primary = minute_need(22)
    rec = {"free_gb": round(free, 2), "primary": budget(primary, qneed, free)}
    need, scope = primary, "primary (t+1..t+22)"
    if not rec["primary"]["pass"]:
        fallback = minute_need(3)
        rec["fallback"] = budget(fallback, qneed, free)
        need, scope = fallback, "fallback (t+1..t+3)"
        if not rec["fallback"]["pass"]:
            scope = "stop: both scopes fail the budget"
    rec["scope"] = scope
    rec["pass"] = not scope.startswith("stop")
    rec["decided_at"] = dt.datetime.now(UTC).isoformat()
    derived("s1_fetch_plan.json").write_text(json.dumps({**rec, "minute_need": {d.isoformat(): v for d, v in need.items()},
                                                         "open_need": {d.isoformat(): v for d, v in qneed.items()}}) + "\n",
                                             encoding="utf-8")
    print(json.dumps(rec, indent=1))


def step_minute(cl, workers=4):
    from scalp_engine import t1_data as T
    plan = json.loads(derived("s1_fetch_plan.json").read_text(encoding="utf-8"))
    if not plan.get("pass"):
        raise SystemExit("no A5 scope fits the budget (s1_fetch_plan.json): stop and report")
    table = "raw_bars_1m"
    done = storage.completed(table, part="sip-1m-s1-v1")
    dropper = ingest.SymbolDropper(table)
    todo = [(dt.date.fromisoformat(d), s) for d, s in sorted(plan["minute_need"].items())
            if storage.partition_key(table, source="alpaca", feed="sip", date=dt.date.fromisoformat(d)) not in done]
    log(f"minute ({plan['scope']}): {len(todo)} sessions to fetch")

    def one(x):
        d, syms = x
        return d, T.fetch_minutes(cl, d, "sip", syms, REG_OPEN, dt.time(15, 59), "s1-v1", dropper)

    n = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for d, rows in ex.map(one, todo):
            n += 1
            if n % 25 == 0 or n == len(todo):
                log(f"minute: {n}/{len(todo)} sessions; requests {cl.stats['requests']}")


def step_open935(cl, workers=4):
    plan = json.loads(derived("s1_fetch_plan.json").read_text(encoding="utf-8"))
    done = storage.completed("raw_quote_samples", part="s1-open-v1")
    dropper = ingest.SymbolDropper("raw_quote_samples")
    todo = [(dt.date.fromisoformat(d), s) for d, s in sorted(plan["open_need"].items())
            if storage.partition_key("raw_quote_samples", source="alpaca", feed="sip", date=dt.date.fromisoformat(d)) not in done]
    log(f"open935: {len(todo)} sessions")

    def one(x):
        d, syms = x
        t = ingest.et(d, P.OPEN_SAMPLE)
        guard(t - dt.timedelta(minutes=1), t + dt.timedelta(minutes=1))
        return P.sample_quotes(cl, d, syms, t, P.OPEN_RULE, "s1-open-v1", dropper)

    n = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for _ in ex.map(one, todo):
            n += 1
            if n % 50 == 0 or n == len(todo):
                log(f"open935: {n}/{len(todo)}; requests {cl.stats['requests']}")


def main(argv=None):
    step = (argv or sys.argv[1:] or [""])[0]
    log(f"data root {storage.data_root()}; step {step}")
    if step == "ciks":
        step_ciks()
    elif step in ("submissions", "headers"):
        http = D.sec_http()
        (step_submissions if step == "submissions" else step_headers)(http)
        log(f"http {http.stats}")
    elif step == "plan":
        step_plan()
    elif step in ("topup", "minute", "open935"):
        cl = P.client()
        {"topup": step_topup, "minute": step_minute, "open935": step_open935}[step](cl)
        log(f"client stats {cl.stats}")
    else:
        raise SystemExit("usage: python -m scalp_engine.swing_data ciks|submissions|headers|topup|plan|minute|open935")
    return 0


if __name__ == "__main__":
    sys.exit(main())
