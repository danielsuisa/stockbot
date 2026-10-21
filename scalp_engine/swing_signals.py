"""Swing tickets (ADR 0013 section 1, ADR 0019 sections 3-4, 7): universe, H1 / S-a / S-b signals, tickets, random
controls, counts and signed sets. Every signal and ticket at t uses data dated <= t only (daily bars <= t; 8-K header
acceptance <= t 20:00 ET; filed-symbol and annual-report filings dated <= t). No outcome is computed here.

    python -m scalp_engine.swing_signals build     # tickets + controls -> swing_tickets, counts, signed digests
"""
import datetime as dt
import hashlib
import json
import random
import sys

import polars as pl

from scalp_engine import ingest, storage
from scalp_engine.data_access import ET, day_bounds, read_frame

UTC = dt.timezone.utc
RULES_SHA256 = "9f0475fad96c4f7c102d62ad450609b14ee6336f44f78105b0ff9ed950e7b80e"  # ADR 0019: ADR 0013 section 1 text
RULE_VERSION = "adr0019-s1"
SIG_FIRST, SIG_LAST = dt.date(2022, 10, 1), dt.date(2026, 9, 17)
WINDOWS = {"development": (dt.date(2022, 10, 1), dt.date(2024, 12, 31)),
           "confirmation": (dt.date(2025, 1, 1), dt.date(2026, 9, 17))}
MIN_PRICE, MIN_DV, CIK_DAYS, GAP, HOLD_RANGE = 5.0, 10e6, 400, 1.03, 0.70
DEDUPE_SESSIONS, K, RISK, CAP_VALUE, TICK = 20, 3, 25.0, 1000.0, 0.01
FUND_SIC = {"6221", "6722", "6726"}
ANNUAL = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
FPI_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}
ISSUE = dt.time(20, 0)
ALL = (dt.datetime(2022, 1, 3, 5, tzinfo=UTC), dt.datetime(2026, 11, 2, 4, tzinfo=UTC))
o = pl.col


def tick(x):
    """Round to the cent, half up (ADR 0014 section 3)."""
    return float(int(x * 100 + 0.5 + 1e-9)) / 100.0


def issue_at(d):
    return dt.datetime.combine(d, ISSUE, ET).astimezone(UTC)


def window_of(d):
    for name, (lo, hi) in WINDOWS.items():
        if lo <= d <= hi:
            return name
    return None


def rules_text_sha256(adr_path):
    """ADR 0019: the ADR 0013 text from "**Frozen ticket rules (H1).**" up to "## 2. Measurement", UTF-8, LF."""
    from pathlib import Path
    s = Path(adr_path).read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(s[s.index("**Frozen ticket rules (H1).**"):s.index("## 2. Measurement")].encode()).hexdigest()


# ---------------------------------------------------------------- survivorship (A7)

def drop_after(b, until):
    """Rows of a survivorship name dated on or after its delisting or rename are dropped (ticker reuse)."""
    if not until:
        return b
    cut = pl.DataFrame({"symbol": list(until), "_until": list(until.values())}, schema={"symbol": pl.Utf8, "_until": pl.Date})
    return b.join(cut, on="symbol", how="left").filter(o("_until").is_null() | (o("d") < o("_until"))).drop("_until")


def apply_survivorship(b):
    """A7: every survivorship name (T2 method) must have been requested for daily bars over its valid sessions in
    the signal range, else LookupError; then drop_after."""
    from scalp_engine import t1_data as T
    spans = T.universe_spans()
    until = {k: v for k, v in spans.items() if v is not None}
    sess = [d for d, _ in T.sessions(T.HARD_FIRST, SIG_LAST)]
    gaps = T.spanned_gaps(T.coverage_gaps(set(until), T.daily_sets(), T.HARD_FIRST, SIG_LAST, sess), until)
    if gaps:
        raise LookupError(f"survivorship daily coverage gaps: {[(str(a), str(z), len(m)) for a, z, m in gaps]}")
    return drop_after(b, until)


# ---------------------------------------------------------------- features (bars <= t only)

def features(b):
    """b: symbol, d, open, high, low, close, volume (one row per symbol and session with a bar), sorted.
    -> per row: prev_high, atr14, sma50, sma200, hi20 (max high of the 20 previous bars), dv20 (20-bar median of
    close*volume; ADR 0014 section 3 counts a session without a bar as 0, applied by `sessions_dv20`)."""
    b = b.sort(["symbol", "d"])
    prev_close = o("close").shift(1).over("symbol")
    tr = pl.max_horizontal(o("high") - o("low"), (o("high") - prev_close).abs(), (o("low") - prev_close).abs())
    return b.with_columns(
        prev_high=o("high").shift(1).over("symbol"),
        atr14=tr.rolling_mean(14, min_samples=14).over("symbol"),
        sma50=o("close").rolling_mean(50, min_samples=50).over("symbol"),
        sma200=o("close").rolling_mean(200, min_samples=200).over("symbol"),
        hi20=o("high").shift(1).rolling_max(20, min_samples=20).over("symbol"))


def sessions_dv20(b, sessions):
    """(symbol, d, dv20): median close*volume over the 20 calendar sessions ending d, a session without a bar = 0."""
    sess = pl.DataFrame({"d": sessions, "i": list(range(len(sessions)))}, schema={"d": pl.Date, "i": pl.Int32})
    x = b.select("symbol", "d", dv=o("close") * o("volume")).join(sess, on="d", how="inner")
    rng = x.group_by("symbol").agg(o("i").min().alias("lo"), o("i").max().alias("hi"))
    rng = rng.with_columns(lo=pl.max_horizontal(o("lo") - 19, pl.lit(0)).cast(pl.Int32))
    grid = rng.select("symbol", pl.int_ranges("lo", o("hi") + 1, dtype=pl.Int32).alias("i")).explode("i", empty_as_null=True)
    g = grid.join(x.drop("d"), on=["symbol", "i"], how="left").sort(["symbol", "i"])
    g = g.with_columns(dv20=o("dv").fill_null(0.0).rolling_median(20, min_samples=20).over("symbol"))
    return g.filter(o("dv").is_not_null()).join(sess, on="i").select("symbol", "d", "dv20")


def signal_flags(f):
    gaphold = ((o("open") >= GAP * o("prev_high")) & (o("close") >= o("open"))
               & ((o("close") - o("low")) >= HOLD_RANGE * (o("high") - o("low"))))
    nday = (o("hi20").is_not_null() & (o("close") > o("hi20")) & o("sma200").is_not_null()
            & (o("close") > o("sma200")) & (o("sma50") > o("sma200")))
    return f.with_columns(gaphold=gaphold.fill_null(False), nday=nday.fill_null(False))


# ---------------------------------------------------------------- universe (ADR 0014 section 3)

def attach_identity(f, obs, sic, annual):
    """CIK (latest filed-symbol observation <= d within 400 days), SIC class, A3 foreign-issuer status (latest annual
    report filed <= d). obs: symbol, obs_d, cik; sic: cik, sic; annual: cik, filing_date, form."""
    f = f.sort(["symbol", "d"])
    f = f.join_asof(obs.sort(["symbol", "obs_d"]), left_on="d", right_on="obs_d", by="symbol", strategy="backward")
    f = f.with_columns(cik=pl.when((o("d") - o("obs_d")).dt.total_days() <= CIK_DAYS).then(o("cik")))
    s = sic.with_columns(sic4=o("sic").cast(pl.Utf8).str.zfill(4)).select("cik", "sic4")
    f = f.join(s, on="cik", how="left")
    a = (annual.filter(o("form").is_in(sorted(ANNUAL))).select("cik", a_d=o("filing_date"), a_form=o("form"))
         .sort(["cik", "a_d"]))
    f = f.sort(["cik", "d"]).join_asof(a, left_on="d", right_on="a_d", by="cik", strategy="backward")
    return f.with_columns(
        fpi=o("a_form").is_in(sorted(FPI_FORMS)).fill_null(False),
        no_annual=o("a_form").is_null(),
        operating=(o("sic4").is_not_null() & (o("sic4") != "0000") & ~o("sic4").is_in(sorted(FUND_SIC))))


def universe_mask():
    return (o("close") >= MIN_PRICE) & (o("dv20") >= MIN_DV) & o("cik").is_not_null() & o("operating")


# ---------------------------------------------------------------- earnings (A2, header acceptance times)

def earnings_index(acc):
    """{cik: sorted UTC acceptance times} of Item 2.02 8-Ks (header times only)."""
    out = {}
    for cik, t in acc.select("cik", "accepted_at").iter_rows():
        out.setdefault(cik, []).append(t)
    return {k: sorted(v) for k, v in out.items()}


def earnings_hit(times, lo, hi, closed_hi=True):
    import bisect
    j = bisect.bisect_left(times, lo)
    if j >= len(times):
        return False
    return times[j] <= hi if closed_hi else times[j] < hi


def a2_window(d, prev_session):
    """[t-1 16:00 ET, t 20:00 ET] (A2)."""
    return (dt.datetime.combine(prev_session, dt.time(16, 0), ET).astimezone(UTC), issue_at(d))


# ---------------------------------------------------------------- tickets

def make_ticket(family, high, low, atr):
    """ADR 0013 section 1 rule 5 / ADR 0014 section 3. -> dict or None when no ATR."""
    if atr is None or atr != atr or atr <= 0:
        return None
    trigger = tick(high + TICK)
    limit = tick(trigger + 0.25 * atr)
    stop = tick(max(low, trigger - 2 * atr) - TICK) if family == "gaphold" else tick(trigger - 2 * atr)
    r = round(trigger - stop, 2)
    if r <= 0:
        return None
    target = tick(trigger + 2 * r)
    qty = min(int(RISK // r), int(CAP_VALUE // limit))
    return {"trigger": trigger, "limit": limit, "stop": stop, "r": r, "target": target, "qty": qty, "atr14": atr}


def dedupe(rows, idx):
    """One live ticket per symbol: drop a signal within 20 sessions of the symbol's last kept signal."""
    kept, last = [], {}
    for r in sorted(rows, key=lambda r: (r["symbol"], r["d"])):
        i = idx[r["d"]]
        if r["symbol"] in last and i - last[r["symbol"]] < DEDUPE_SESSIONS:
            continue
        last[r["symbol"]] = i
        kept.append(r)
    return kept


def control_seed(ticket_id):
    return int.from_bytes(hashlib.sha256(f"{ticket_id}|s1-control-v1".encode()).digest()[:8], "big")


def draw_controls(ticket_id, family, cands):
    """K controls by the seeded walk (ADR 0014 section 4). cands: {symbol: (high, low, atr)} already filtered."""
    perm = sorted(cands)
    random.Random(control_seed(ticket_id)).shuffle(perm)
    out = []
    for s in perm:
        tk = make_ticket(family, *cands[s])
        if tk and tk["qty"] > 0:
            out.append((s, tk))
            if len(out) == K:
                break
    return out


def build(b, sessions, obs, sic, annual, acc, survivors=()):
    """Everything from inputs dated <= each t. b: daily bars (symbol, d, open, high, low, close, volume).
    survivors: survivorship names (A7), for the counts only. -> (tickets list of dicts, counts dict)."""
    survivors = set(survivors)
    idx = {d: i for i, d in enumerate(sessions)}
    f = signal_flags(features(b)).join(sessions_dv20(b, sessions), on=["symbol", "d"], how="left")
    f = attach_identity(f, obs, sic, annual)
    f = f.filter(o("d").is_between(SIG_FIRST, SIG_LAST))
    base = f.filter((o("close") >= MIN_PRICE) & (o("dv20") >= MIN_DV) & o("cik").is_not_null() & o("operating"))
    uni = base.filter(~o("fpi"))
    eidx = earnings_index(acc)
    counts = {"a3_excluded": {}, "a2_excluded": {}, "late_8k": {}, "zero_qty": {}, "signals": {}, "no_annual_on_file": {},
              "survivor_signals": {}}

    def bump(key, s, d, n=1):
        k = f"{d.year}|{s}|{window_of(d)}"
        counts[key][k] = counts[key].get(k, 0) + n

    for fam, setname in (("gaphold", "S-b"), ("nday", "S-a")):
        for r in base.filter(o(fam) & o("fpi")).select("d").iter_rows():
            bump("a3_excluded", setname, r[0])
    rows = {fam: [dict(r) for r in uni.filter(o(fam)).select("symbol", "d", "cik", "high", "low", "atr14",
                                                                "no_annual").iter_rows(named=True)]
            for fam in ("gaphold", "nday")}
    # A2 on gap-and-hold signals (H1 = S-b without earnings)
    h1_raw, earn_syms = [], {}
    for r in rows["gaphold"]:
        i = idx[r["d"]]
        lo, hi = a2_window(r["d"], sessions[i - 1])
        if earnings_hit(eidx.get(r["cik"], []), lo, hi):
            bump("a2_excluded", "H1", r["d"])
            earn_syms.setdefault(r["d"], set()).add(r["symbol"])
            continue
        h1_raw.append(r)
    sets = {"H1": dedupe(h1_raw, idx), "S-b": dedupe(rows["gaphold"], idx), "S-a": dedupe(rows["nday"], idx)}
    fam_of = {"H1": "gaphold", "S-b": "gaphold", "S-a": "nday"}
    # late filings (reported only): Item 2.02 accepted in (t 20:00 ET, t + 4 sessions]
    for r in sets["H1"]:
        i = idx[r["d"]]
        end = sessions[min(i + 4, len(sessions) - 1)]
        if earnings_hit(eidx.get(r["cik"], []), issue_at(r["d"]), dt.datetime.combine(end, dt.time(23, 59), ET).astimezone(UTC)):
            bump("late_8k", "H1", r["d"])
    # earnings symbols for the gaphold control exclusion (A2 window), across the whole universe
    by_day = {}
    for d, g in uni.select("d", "symbol", "cik", "high", "low", "atr14", "gaphold", "nday").partition_by("d", as_dict=True).items():
        by_day[d[0]] = g
    tickets, cache_cands = [], {}

    def candidates(d, fam):
        key = (d, fam)
        if key not in cache_cands:
            g = by_day[d].filter(~o(fam))
            prev = sessions[idx[d] - 1]
            lo, hi = a2_window(d, prev)
            c = {}
            for s, cik, h, l, a in g.select("symbol", "cik", "high", "low", "atr14").iter_rows():
                if fam == "gaphold" and earnings_hit(eidx.get(cik, []), lo, hi):
                    continue
                c[s] = (h, l, a)
            cache_cands[key] = c
        return cache_cands[key]

    done_controls = {}
    for setname, rs in sets.items():
        fam = fam_of[setname]
        for r in rs:
            tk = make_ticket(fam, r["high"], r["low"], r["atr14"])
            if tk is None:
                continue
            if tk["qty"] <= 0:
                bump("zero_qty", setname, r["d"])
                continue
            bump("signals", setname, r["d"])
            if r["symbol"] in survivors:
                bump("survivor_signals", setname, r["d"])
            if r["no_annual"]:
                bump("no_annual_on_file", setname, r["d"])
            tid = f"{fam}|{r['symbol']}|{r['d'].isoformat()}"
            tickets.append({"ticket_id": tid, "set": setname, "family": fam, "parent_id": None, "symbol": r["symbol"],
                            "cik": r["cik"], "signal_date": r["d"], **tk})
            if tid not in done_controls:
                cands = dict(candidates(r["d"], fam))
                cands.pop(r["symbol"], None)
                done_controls[tid] = draw_controls(tid, fam, cands)
                for k, (s, ctk) in enumerate(done_controls[tid]):
                    tickets.append({"ticket_id": f"{tid}|c{k}", "set": "controls", "family": fam, "parent_id": tid,
                                    "symbol": s, "cik": None, "signal_date": r["d"], **ctk})
    return tickets, counts


# ---------------------------------------------------------------- store I/O

def load_inputs(last=dt.date(2026, 10, 20)):
    root = storage.data_root()
    hi = day_bounds(last)[1]
    b = read_frame(root / "raw_bars_1d", ALL[0], hi, columns=["symbol", "feed", "open", "high", "low", "close", "volume",
                                                                "ingested_at"])
    b = b.filter(o("feed") == "sip").with_columns(d=o("event_at").dt.convert_time_zone("America/New_York").dt.date())
    b = b.sort("ingested_at").unique(subset=["symbol", "d"], keep="last").select("symbol", "d", "open", "high", "low",
                                                                                 "close", "volume")
    b = apply_survivorship(b)
    cal = read_frame(root / "calendar", ALL[0], hi, columns=["trading_date"])
    sessions = sorted(cal["trading_date"].unique().to_list())
    fil = read_frame(root / "insider_filings", *ALL, columns=["issuer_cik", "issuer_symbol", "filing_date", "owner_seq"])
    from scalp_engine.insider_prices import norm
    obs = (fil.filter(o("owner_seq") == 0).select("issuer_cik", "issuer_symbol", "filing_date").drop_nulls().unique()
           .select(symbol=o("issuer_symbol").map_elements(norm, return_dtype=pl.Utf8), obs_d=o("filing_date"), cik=o("issuer_cik"))
           .drop_nulls().sort(["symbol", "obs_d", "cik"]).unique(subset=["symbol", "obs_d"], keep="last"))
    sic = read_frame(root / "issuer_sic", ALL[0], ALL[1], columns=["cik", "sic", "ingested_at"])
    sic = sic.sort("ingested_at").unique(subset=["cik"], keep="last").select("cik", "sic")
    ifl = read_frame(root / "issuer_filings", *ALL, columns=["cik", "form", "filing_date", "items", "accession"])
    annual = ifl.filter(o("form").is_in(sorted(ANNUAL))).select("cik", "filing_date", "form")
    acc = read_frame(root / "filing_acceptance", *ALL, columns=["accession", "status", "method"])
    acc = acc.filter((o("method") == "header") & (o("status") == "ok")).select("accession", accepted_at=o("event_at"))
    k8 = ifl.filter(o("form").is_in(["8-K", "8-K/A"]) & o("items").fill_null("").str.contains(r"2\.02")).select("accession", "cik")
    acc = k8.join(acc, on="accession", how="inner").select("cik", "accepted_at")
    return b, sessions, obs, sic, annual, acc


def ticket_rows(tickets, got):
    rows = []
    for t in tickets:
        rows.append({"event_at": issue_at(t["signal_date"]), "ticket_id": t["ticket_id"], "set": t["set"],
                     "family": t["family"], "parent_id": t["parent_id"], "window": window_of(t["signal_date"]),
                     "symbol": t["symbol"], "cik": t["cik"], "signal_date": t["signal_date"],
                     "trigger": t["trigger"], "limit": t["limit"], "stop": t["stop"], "target": t["target"], "r": t["r"],
                     "qty": t["qty"], "atr14": t["atr14"], "expiry_sessions": 3, "time_exit_sessions": 20,
                     "rules_sha256": RULES_SHA256, "feed": "none", "request_id": "derived:s1-tickets-v1",
                     "ingested_at": got, "source": "scalp_engine", "schema_version": 1})
    return rows


def canonical_sha256(df):
    df = df.drop([c for c in ("ingested_at",) if c in df.columns])
    df = df.select(sorted(df.columns))
    rows = df.sort(df.columns).to_dicts()
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def write_and_sign(tickets, counts, key_path=r"D:\scalp-env\keys\p3_signing.pem"):
    from scalp_engine.signing import load_private_key, sign_artifact
    got = ingest.now()
    df = storage.frame("swing_tickets", ticket_rows(tickets, got))
    if df.unique(subset=["set", "ticket_id"]).height != df.height:
        raise ValueError("duplicate (set, ticket_id)")
    done = storage.completed("swing_tickets", part="s1-tickets-v1")
    d = o("event_at").dt.convert_time_zone("America/New_York").dt.date()
    for (day,), g in df.with_columns(_d=d).partition_by("_d", as_dict=True, maintain_order=True).items():
        if storage.partition_key("swing_tickets", source="scalp_engine", date=day) not in done:
            storage.write_partition("swing_tickets", g.drop("_d"), part="s1-tickets-v1", source="scalp_engine", date=day)
    stored = read_frame(storage.data_root() / "swing_tickets", *ALL)
    key = load_private_key(key_path)
    out = {}
    for setname in ("H1", "S-a", "S-b", "controls"):
        for w in WINDOWS:
            sub = stored.filter((o("set") == setname) & (o("window") == w))
            payload = {"kind": "swing_ticket_set", "table": "swing_tickets", "rule_version": RULE_VERSION,
                       "rows": sub.height, "sha256": canonical_sha256(sub), "set": setname, "window": w,
                       "rules_sha256": RULES_SHA256}
            env = sign_artifact(payload, key)
            name = f"s1_{setname.replace('-', '').lower()}_{w}.signed.json"
            (storage.data_root() / "derived" / name).write_text(json.dumps(env, indent=1) + "\n", encoding="utf-8")
            out[name] = payload
    (storage.data_root() / "derived" / "s1_counts.json").write_text(json.dumps(counts, indent=1, default=str) + "\n",
                                                                     encoding="utf-8")
    return out


def main(argv=None):
    step = (argv or sys.argv[1:] or [""])[0]
    if step != "build":
        raise SystemExit("usage: python -m scalp_engine.swing_signals build")
    from scalp_engine import t1_data as T
    b, sessions, obs, sic, annual, acc = load_inputs()
    survivors = {k for k, v in T.universe_spans().items() if v is not None}
    tickets, counts = build(b, sessions, obs, sic, annual, acc, survivors)
    out = write_and_sign(tickets, counts)
    print(json.dumps({k: {"rows": v["rows"]} for k, v in out.items()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
