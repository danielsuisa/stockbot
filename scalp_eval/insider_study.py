"""I2 measurement (ADR 0008 sections 7-9, ADR 0010): event study against matched controls, L7 tradable version against
SPY, account-level simulation. Evaluator side (R4): reads contract tables through scalp_eval.data_access only,
verifies the engine's signed event-set digest, imports nothing from scalp_engine.

    python -m scalp_eval.insider_study --window development|confirmation --pubkey PATH

The confirmation run is refused if the trials ledger already holds one, and any run is refused on a dirty work tree.
"""
import argparse
import bisect
import datetime as dt
import hashlib
import json
import math
import random
import statistics
import subprocess
import sys
from pathlib import Path

import polars as pl

from scalp_eval.costs import order_cost
from scalp_eval.data_access import ET, read_frame
from scalp_eval.verify import load_public_key, verify_artifact

UTC = dt.timezone.utc
ALL = (dt.datetime(2022, 1, 3, 5, tzinfo=UTC), dt.datetime(2026, 11, 2, 4, tzinfo=UTC))
WINDOWS = {"development": (dt.date(2022, 2, 1), dt.date(2024, 6, 28)),
           "confirmation": (dt.date(2024, 7, 1), dt.date(2026, 7, 20))}
POST_S1 = (dt.date(2026, 4, 1), dt.date(2026, 7, 20))
HORIZONS = (5, 20, 60)
K, SHIFT_SESSIONS, DELIST_HAIRCUT, EXCLUDE_DAYS = 5, 5, 0.70, 30
CONTROL_TAG, BOOT_TAG, BOOT_N = "i2-control-v1", "i2-boot-v1", 10_000
MDE_MULT = 2.0 + 0.8416
ACCOUNT, PLAN = 500.0, "tiered"
CLOSE_RULE, OPEN_RULE = "close_minus_5m_v2", "open_plus_5m_v1"
MERGERS = ("cash_mergers", "stock_mergers", "stock_and_cash_mergers")
REPO = Path(__file__).resolve().parent.parent


def seed_of(text):
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


# ---------------------------------------------------------------- market (bars + corporate actions)

class Market:
    """Sessions, daily bars and corporate actions. bars: {symbol: (sorted session indices, opens, closes)} as lists;
    splits: {symbol: [(ex_date, share_multiplier)]}; divs: {symbol: [(ex_date, cash per share)]};
    mergers: {symbol: [(effective_date, kind, ratio, cash, acquirer)]}; end: last session of the store."""

    def __init__(self, sessions, bars, splits=None, divs=None, mergers=None, end=None):
        self.sessions = list(sessions)
        self.idx = {d: i for i, d in enumerate(self.sessions)}
        self.bars = bars
        self.splits, self.divs, self.mergers = splits or {}, divs or {}, mergers or {}
        self.end = end or self.sessions[-1]

    def bar(self, sym, i):
        """(open, close) of session index i, or None."""
        b = self.bars.get(sym)
        if b is None:
            return None
        j = bisect.bisect_left(b[0], i)
        if j < len(b[0]) and b[0][j] == i:
            return float(b[1][j]), float(b[2][j])
        return None

    def last_bar(self, sym, upto=None):
        """(index, close) of the symbol's last bar (on or before index `upto` if given)."""
        b = self.bars.get(sym)
        if b is None or not len(b[0]):
            return None
        j = len(b[0]) if upto is None else bisect.bisect_right(b[0], upto)
        return (int(b[0][j - 1]), float(b[2][j - 1])) if j else None

    def first_close_after(self, sym, i):
        b = self.bars.get(sym)
        j = bisect.bisect_right(b[0], i)
        return (int(b[0][j]), float(b[2][j])) if j < len(b[0]) else None

    def entry(self, sym, i1, shift=True):
        """(index, open) of the entry: day 1, else (events only) the first open within the next 5 sessions."""
        for j in range(i1, i1 + (SHIFT_SESSIONS + 1 if shift else 1)):
            if j >= len(self.sessions):
                break
            b = self.bar(sym, j)
            if b is not None:
                return j, b[0]
        return None

    def _position(self, sym, d1, d_to, reinvest=True):
        """Shares and held cash per share bought at entry, after actions with ex-date in (d1, d_to] (ADR 0010 2)."""
        events = [(ex, "split", f) for ex, f in self.splits.get(sym, ()) if d1 < ex <= d_to]
        events += [(ex, "div", a) for ex, a in self.divs.get(sym, ()) if d1 < ex <= d_to]
        shares, cash, div_cash = 1.0, 0.0, 0.0
        for ex, kind, v in sorted(events, key=lambda x: (x[0], x[1] != "split")):
            if kind == "split":
                shares *= v
            else:
                received = shares * v
                div_cash += received
                b = self.bar(sym, self.idx[ex]) if ex in self.idx else None
                if reinvest and b is not None:
                    shares += received / b[1]
                else:
                    cash += received
        return shares, cash, div_cash

    def exit(self, sym, i1, iH):
        """(case, exit session index, per-share exit value, actions-through date). Cases: close | merger | delisting |
        gap (ADR 0010 section 2)."""
        d1, dH = self.sessions[i1], self.sessions[iH]
        b = self.bar(sym, iH)
        if b is not None:
            return "close", iH, b[1], dH
        for eff, kind, ratio, cash, acquirer in sorted(self.mergers.get(sym, ())):
            if d1 < eff <= dH:
                if kind == "cash_mergers":
                    return "merger_cash", self.idx.get(eff, iH), cash, eff
                ia = self.idx.get(eff)
                acq = self.bar(acquirer, ia) if (acquirer and ia is not None) else None
                if acq is None:
                    lb = self.last_bar(sym, ia if ia is not None else iH)
                    return "merger_no_acquirer", lb[0], lb[1], eff
                return "merger_stock", ia, ratio * acq[1] + (cash or 0.0), eff
        last = self.last_bar(sym)
        if last[0] < iH:
            return "delisting", last[0], last[1] * DELIST_HAIRCUT, self.sessions[last[0]]
        nxt = self.first_close_after(sym, iH)
        return "gap", nxt[0], nxt[1], dH

    def total_return(self, sym, i1, H, shift=True):
        """Day-1 open to day-H close total return (ADR 0010 section 2) -> dict, or {'drop': reason}."""
        e = self.entry(sym, i1, shift)
        if e is None:
            return {"drop": "no_entry_open"}
        i1, px = e
        iH = i1 + H - 1
        if iH >= len(self.sessions) or self.sessions[iH] > self.end:
            return {"drop": "horizon_beyond_store"}
        case, ix, value, through = self.exit(sym, i1, iH)
        shares, cash, _ = self._position(sym, self.sessions[i1], through)
        return {"r": (shares * value + cash) / px - 1.0, "case": case, "i1": i1, "iH": iH, "exit_i": ix, "entry": px}


def market_from_store(symbols=None):
    """Market from the store (raw SIP daily bars; corporate actions deduplicated by vendor id)."""
    root = data_root()
    cal = read_frame(root / "calendar", *ALL, columns=["trading_date"]).sort("trading_date")
    sessions = cal["trading_date"].unique().sort().to_list()
    b = read_frame(root / "raw_bars_1d", *ALL, columns=["symbol", "feed", "open", "close", "ingested_at"], symbols=symbols)
    b = b.filter(pl.col("feed") == "sip").with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date())
    b = b.sort("ingested_at").unique(subset=["symbol", "d"], keep="last")
    sess = pl.DataFrame({"d": sessions, "i": list(range(len(sessions)))}, schema={"d": pl.Date, "i": pl.Int32})
    b = b.join(sess, on="d", how="inner").sort(["symbol", "i"])
    bars = {}
    for sym, ii, oo, cc in b.group_by("symbol").agg("i", "open", "close").iter_rows():
        bars[sym] = (list(ii), [float(x) for x in oo], [float(x) for x in cc])
    acts = read_frame(root / "corporate_actions", *ALL)
    acts = acts.sort("ingested_at").unique(subset=["vendor_id"], keep="last")
    splits, divs, mergers = {}, {}, {}
    for r in acts.iter_rows(named=True):
        raw = json.loads(r["raw"])
        day = r["event_at"].astimezone(ET).date()
        t = r["action_type"]
        if t in ("forward_splits", "reverse_splits"):
            o, n = raw.get("old_rate"), raw.get("new_rate")
            if raw.get("symbol") and isinstance(o, (int, float)) and isinstance(n, (int, float)) and o > 0:
                splits.setdefault(raw["symbol"], []).append((day, float(n) / float(o)))
        elif t == "stock_dividends":
            rate = raw.get("rate")
            if raw.get("symbol") and isinstance(rate, (int, float)) and rate > 0:
                splits.setdefault(raw["symbol"], []).append((day, float(rate)))
        elif t == "cash_dividends":
            rate = raw.get("rate")
            if raw.get("symbol") and isinstance(rate, (int, float)) and rate > 0:
                divs.setdefault(raw["symbol"], []).append((day, float(rate)))
        elif t in MERGERS and raw.get("acquiree_symbol"):
            ar, tr = raw.get("acquirer_rate"), raw.get("acquiree_rate")
            ratio = float(ar) / float(tr) if isinstance(ar, (int, float)) and isinstance(tr, (int, float)) and tr else 0.0
            cash = raw.get("rate") if t == "cash_mergers" else raw.get("cash_rate")
            mergers.setdefault(raw["acquiree_symbol"], []).append(
                (day, t, ratio, float(cash) if isinstance(cash, (int, float)) else 0.0, raw.get("acquirer_symbol")))
    return Market(sessions, bars, splits, divs, mergers, end=sessions[-1])


def data_root():
    import os
    return Path(os.environ.get("SCALP_DATA_ROOT") or r"D:\scalp-data")


# ---------------------------------------------------------------- inputs (signed event set, universe table)

def canonical_sha256(df):
    """ADR 0010 section 1.1, re-derived here (R4)."""
    df = df.drop([c for c in ("ingested_at",) if c in df.columns])
    df = df.select(sorted(df.columns))
    rows = df.sort(df.columns).to_dicts()
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def verified_events(pubkey_path):
    root = data_root()
    env = json.loads((root / "derived" / "i2_event_set.signed.json").read_text(encoding="utf-8"))
    payload = verify_artifact(env, [load_public_key(pubkey_path)])
    ev = read_frame(root / "insider_events", *ALL)
    h = canonical_sha256(ev)
    if payload["kind"] != "insider_event_set" or payload["rows"] != ev.height or payload["sha256"] != h:
        raise ValueError(f"event set does not match its signed digest ({h} vs {payload['sha256']})")
    return ev, payload


def universe_table():
    return read_frame(data_root() / "universe_b_daily", *ALL,
                      columns=["session", "symbol", "cik", "sic2", "universe_b", "dv_decile"]).drop("event_at")


def universe_digest():
    path = data_root() / "manifests" / "universe_b_daily.jsonl"
    recs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    files = sorted(r["file_sha256"] for r in recs if r.get("kind") == "partition")
    return hashlib.sha256("".join(files).encode()).hexdigest()


# ---------------------------------------------------------------- populations

def overlap(events, H, idx):
    """ADR 0010 section 4: per (issuer, trigger), drop events whose entry is within H sessions of the last kept one."""
    kept, last, dropped = [], {}, 0
    for e in sorted(events, key=lambda e: (e["entry_date"], e["available_at"], e["event_id"])):
        k = (e["cik"], e["trigger"])
        i = idx[e["entry_date"]]
        if k in last and i - last[k] < H:
            dropped += 1
            continue
        last[k] = i
        kept.append(e)
    return kept, dropped


def exclusion_index(events):
    """{cik: sorted entry dates} and {symbol: sorted entry dates} of every event in the table (any trigger, flags)."""
    by_cik, by_sym = {}, {}
    for e in events:
        by_cik.setdefault(e["cik"], []).append(e["entry_date"])
        if e.get("symbol"):
            by_sym.setdefault(e["symbol"], []).append(e["entry_date"])
    return {k: sorted(v) for k, v in by_cik.items()}, {k: sorted(v) for k, v in by_sym.items()}


def _near(dates, d):
    if not dates:
        return False
    j = bisect.bisect_left(dates, d - dt.timedelta(days=EXCLUDE_DAYS))
    return j < len(dates) and dates[j] <= d + dt.timedelta(days=EXCLUDE_DAYS)


def draw_controls(event, cands, market, i1, excl_cik, excl_sym):
    """ADR 0010 section 3. cands: universe-(b) rows of the event's entry date (dicts with symbol, cik, sic2,
    dv_decile). -> (control symbols, level 'sic2'|'sic1'|None, skipped_no_open)."""
    d = event["entry_date"]
    base = [c for c in cands if c["dv_decile"] == event["dv_decile"] and c["sic2"] and c["symbol"] != event["symbol"]
            and c["cik"] != event["cik"] and not _near(excl_cik.get(c["cik"], ()), d)
            and not _near(excl_sym.get(c["symbol"], ()), d)]
    rnd_seed = seed_of(f"{event['event_id']}|{CONTROL_TAG}")
    skipped = 0
    for level, pool in (("sic2", [c for c in base if c["sic2"] == event["sic2"]]),
                        ("sic1", [c for c in base if c["sic2"][:1] == event["sic2"][:1]])):
        perm = sorted(c["symbol"] for c in pool)
        random.Random(rnd_seed).shuffle(perm)
        got = []
        for s in perm:
            if market.bar(s, i1) is None:
                skipped += 1
                continue
            got.append(s)
            if len(got) == K:
                break
        if len(got) == K or (level == "sic1" and got):
            return got, level, skipped
    return [], None, skipped


# ---------------------------------------------------------------- statistics

def clustered(values, weeks):
    """Mean, CR1 clustered SE, t, n, G (ADR 0010 section 4)."""
    n = len(values)
    if n < 2:
        return {"n": n, "mean": values[0] if values else None, "se": None, "t": None, "G": len(set(weeks))}
    m = sum(values) / n
    g = {}
    for v, w in zip(values, weeks):
        g[w] = g.get(w, 0.0) + (v - m)
    G = len(g)
    if G < 2:
        return {"n": n, "mean": m, "se": None, "t": None, "G": G}
    se = math.sqrt(G / (G - 1) * sum(s * s for s in g.values())) / n
    return {"n": n, "mean": m, "se": se, "t": m / se if se > 0 else None, "G": G}


def bootstrap_ci(values, weeks, tag=BOOT_TAG, n=BOOT_N):
    by = {}
    for v, w in zip(values, weeks):
        s, c = by.get(w, (0.0, 0))
        by[w] = (s + v, c + 1)
    keys = sorted(by)
    if len(keys) < 2:
        return None
    sums = [by[k][0] for k in keys]
    cnts = [by[k][1] for k in keys]
    rng = random.Random(seed_of(tag))
    G, means = len(keys), []
    for _ in range(n):
        tot = cnt = 0.0
        for _ in range(G):
            j = rng.randrange(G)
            tot += sums[j]
            cnt += cnts[j]
        means.append(tot / cnt)
    means.sort()
    return [_pct(means, 2.5), _pct(means, 97.5)]


def _pct(sorted_vals, q):
    """Linear-interpolation percentile (numpy's default method) of a sorted list."""
    k = (len(sorted_vals) - 1) * q / 100.0
    f = math.floor(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def describe(values, weeks):
    c = clustered(values, weeks)
    out = {**c, "median": statistics.median(values) if values else None,
           "hit_rate": sum(v > 0 for v in values) / len(values) if values else None,
           "ci95": bootstrap_ci(values, weeks) if values else None}
    out["mde"] = MDE_MULT * c["se"] if c.get("se") else None
    return out


def iso_week(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# ---------------------------------------------------------------- event study

def event_study(events, market, ub_by_day, excl, H, counts):
    """Abnormal returns for one population and horizon. -> list of (event, AR, R, controls)."""
    excl_cik, excl_sym = excl
    out = []
    for e in events:
        rows = ub_by_day.get(e["entry_date"], {})
        me = rows.get(e["symbol"])
        if me is None or not me["universe_b"]:
            counts["no_universe_row"] += 1
            continue
        if not me["sic2"] or me["dv_decile"] is None:
            counts["no_sic"] += 1
            continue
        i1 = market.idx[e["entry_date"]]
        r = market.total_return(e["symbol"], i1, H, shift=True)
        if "drop" in r:
            counts[r["drop"]] += 1
            continue
        counts["shifted"] += r["i1"] != i1
        ev = dict(e, sic2=me["sic2"], dv_decile=me["dv_decile"])
        ctrl, level, skipped = draw_controls(ev, [c for c in rows.values() if c["universe_b"]], market, r["i1"],
                                             excl_cik, excl_sym)
        counts["control_redraws"] += skipped
        if not ctrl:
            counts["no_controls"] += 1
            continue
        counts[f"controls_{level}"] += 1
        if len(ctrl) < K:
            counts["controls_fewer_than_k"] += 1
        rc = []
        for s in ctrl:
            x = market.total_return(s, r["i1"], H, shift=False)
            if "drop" in x:
                counts["control_" + x["drop"]] += 1
                continue
            counts["control_exit_" + x["case"]] += 1
            rc.append(x["r"])
        if not rc:
            counts["no_controls"] += 1
            continue
        counts["event_exit_" + r["case"]] += 1
        out.append((e, r["r"] - statistics.fmean(rc), r["r"], ctrl))
    return out


# ---------------------------------------------------------------- L7 tradable version

def quote_half_spread(quotes, sym, d):
    q = quotes.get((sym, d))
    if not q:
        return None
    bid, ask = q
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    return (ask - bid) / 2.0


def l7_trade(e, market, open_q, close_q, H, counts):
    """One $500 Tiered trade (ADR 0010 section 5) -> dict or None (counted drop)."""
    sym = e["symbol"]
    i1 = market.idx[e["entry_date"]]
    ent = market.entry(sym, i1, shift=True)
    if ent is None:
        counts["no_entry_open"] += 1
        return None
    i1, op = ent
    iH = i1 + H - 1
    if iH >= len(market.sessions):
        counts["horizon_beyond_store"] += 1
        return None
    d1 = market.sessions[i1]
    hs = quote_half_spread(open_q, sym, d1)
    if hs is None:
        hs = quote_half_spread(close_q, sym, market.sessions[i1 - 1])
    if hs is None:
        counts["no_entry_quote"] += 1
        return None
    entry_px = op + hs
    shares = math.floor(ACCOUNT / entry_px)
    if shares <= 0:
        counts["zero_shares"] += 1
        return None
    case, ix, value, through = market.exit(sym, i1, iH)
    exit_px, sell = value, False
    if case in ("close", "gap"):
        hx = quote_half_spread(close_q, sym, market.sessions[ix])
        if hx is None:
            hx = quote_half_spread(close_q, sym, market.sessions[ix - 1])
        if hx is None:
            counts["no_exit_quote"] += 1
            return None
        exit_px, sell = value - hx, True
    mult, _, div_cash = market._position(sym, d1, through, reinvest=False)
    held = shares * mult
    cost_in = order_cost(PLAN, "buy", shares, entry_px, d1)
    cost_out = order_cost(PLAN, "sell", held, exit_px, market.sessions[ix]) if sell else 0.0
    pnl = held * exit_px + shares * div_cash - shares * entry_px - cost_in - cost_out
    spy = market.total_return("SPY", i1, H, shift=False)
    if "drop" in spy:
        counts["no_spy"] += 1
        return None
    counts["exit_" + case] += 1
    net = pnl / ACCOUNT
    return {"event_id": e["event_id"], "entry_date": e["entry_date"], "net": net, "spy": spy["r"], "excess": net - spy["r"],
            "cost_pct": (cost_in + cost_out) / ACCOUNT, "shares": shares, "entry_px": entry_px, "i1": i1, "exit_i": ix,
            "mult": mult, "div_per_share": div_cash, "exit_px": exit_px, "sell": sell, "case": case,
            "d1": d1, "dx": market.sessions[ix], "available_at": e["available_at"]}


def account_sim(trades, market=None):
    """One $500 cash account, one position at a time, T+1 settlement: the proceeds of an exit on session X are usable
    from session X + 1 (ADR 0010 section 5). Size = floor(settled cash / entry). Reported only."""
    cash, busy_until, n, skipped = ACCOUNT, -1, 0, 0
    for t in sorted(trades, key=lambda t: (t["available_at"], t["event_id"])):
        if t["i1"] <= busy_until:
            skipped += 1
            continue
        shares = math.floor(cash / t["entry_px"])
        if shares <= 0:
            skipped += 1
            continue
        held = shares * t["mult"]
        cost = order_cost(PLAN, "buy", shares, t["entry_px"], t["d1"])
        cost += order_cost(PLAN, "sell", held, t["exit_px"], t["dx"]) if t["sell"] else 0.0
        cash += held * t["exit_px"] + shares * t["div_per_share"] - shares * t["entry_px"] - cost
        busy_until = t["exit_i"]
        n += 1
    return {"trades": n, "skipped": skipped, "final_equity": cash, "return": cash / ACCOUNT - 1.0}


# ---------------------------------------------------------------- the run

def population(ev_rows, trigger, flag, lo, hi):
    return [e for e in ev_rows if e["trigger"] == trigger and e.get("symbol") and e.get(flag) is True
            and e.get("fpi") is not True and lo <= e["entry_date"] <= hi]


def run(window, pubkey):
    lo, hi = WINDOWS[window]
    ev, payload = verified_events(pubkey)
    ev_rows = ev.rename({"event_at": "available_at"}).to_dicts()  # event_at is available_at (ADR 0009 section 7)
    ub = universe_table()
    ub_by_day = {}
    for r in ub.iter_rows(named=True):
        ub_by_day.setdefault(r["session"], {})[r["symbol"]] = r
    market = market_from_store()
    c_syms = sorted({e["symbol"] for e in ev_rows if e.get("symbol") and e.get("universe_c") is True})
    excl = exclusion_index(ev_rows)
    q = read_frame(data_root() / "raw_quote_samples", *ALL,
                   columns=["symbol", "status", "bid_price", "ask_price", "sample_rule", "ingested_at"], symbols=c_syms)
    q = q.filter(pl.col("sample_rule").is_in([CLOSE_RULE, OPEN_RULE]) & (pl.col("status") == "ok"))
    q = q.with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date()).sort("ingested_at")
    q = q.unique(subset=["symbol", "d", "sample_rule"], keep="last")
    close_q = {(s, d): (b, a) for s, d, b, a, r in q.select("symbol", "d", "bid_price", "ask_price", "sample_rule").iter_rows() if r == CLOSE_RULE}
    open_q = {(s, d): (b, a) for s, d, b, a, r in q.select("symbol", "d", "bid_price", "ask_price", "sample_rule").iter_rows() if r == OPEN_RULE}
    result = {"window": window, "event_set": payload, "fpi_excluded": {}, "study": {}, "l7": {}, "account": {}}
    for trig in ("cluster", "big"):
        base_b = [e for e in ev_rows if e["trigger"] == trig and e.get("symbol") and e.get("universe_b") is True
                  and lo <= e["entry_date"] <= hi]
        result["fpi_excluded"][trig] = sum(e.get("fpi") is True for e in base_b)
        for H in HORIZONS:
            counts = _Counter()
            pop = population(ev_rows, trig, "universe_b", lo, hi)
            pop, counts["overlap_dropped"] = overlap(pop, H, market.idx)
            counts["population_after_overlap"] = len(pop)
            res = event_study(pop, market, ub_by_day, excl, H, counts)
            vals = [x[1] for x in res]
            weeks = [iso_week(x[0]["entry_date"]) for x in res]
            entry = {"all": describe(vals, weeks), "counts": dict(counts), "raw_return_mean": statistics.fmean([x[2] for x in res]) if res else None}
            if window == "confirmation":
                ps = [(v, w) for v, w, x in zip(vals, weeks, res) if POST_S1[0] <= x[0]["entry_date"] <= POST_S1[1]]
                entry["post_s1"] = describe([p[0] for p in ps], [p[1] for p in ps])
            result["study"][f"{trig}/H{H}"] = entry
            counts = _Counter()
            popc = population(ev_rows, trig, "universe_c", lo, hi)
            popc, counts["overlap_dropped"] = overlap(popc, H, market.idx)
            counts["population_after_overlap"] = len(popc)
            trades = [t for t in (l7_trade(e, market, open_q, close_q, H, counts) for e in popc) if t]
            weeks = [iso_week(t["entry_date"]) for t in trades]
            entry = {"excess": describe([t["excess"] for t in trades], weeks),
                     "net": describe([t["net"] for t in trades], weeks),
                     "cost_pct_mean": statistics.fmean([t["cost_pct"] for t in trades]) if trades else None,
                     "counts": dict(counts)}
            if window == "confirmation":
                ps = [t for t in trades if POST_S1[0] <= t["entry_date"] <= POST_S1[1]]
                entry["post_s1"] = {"excess": describe([t["excess"] for t in ps], [iso_week(t["entry_date"]) for t in ps])}
            result["l7"][f"{trig}/H{H}"] = entry
            if trig == "cluster" and H == 20:
                result["account"] = account_sim(trades, market)
                i0, i9 = market.idx[min(d for d in market.sessions if d >= lo)], market.idx[max(d for d in market.sessions if d <= hi)]
                spy = market.bar("SPY", i0), market.bar("SPY", i9)
                result["account"]["spy_buy_hold"] = spy[1][1] / spy[0][0] - 1.0 if all(spy) else None
    g_i = result["study"]["cluster/H20"]["all"]
    g_ii = result["l7"]["cluster/H20"]["excess"]
    result["gate"] = {"i": {"mean": g_i["mean"], "t": g_i["t"], "pass": bool(g_i["mean"] is not None and g_i["mean"] > 0
                                                                              and g_i["t"] is not None and g_i["t"] >= 2.0)},
                      "ii": {"mean_excess": g_ii["mean"], "pass": bool(g_ii["mean"] is not None and g_ii["mean"] > 0)}}
    return result


class _Counter(dict):
    def __missing__(self, k):
        return 0


# ---------------------------------------------------------------- ledger and CLI

def ledger_path():
    return data_root() / "derived" / "trials_i2.jsonl"


def ledger():
    p = ledger_path()
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, check=True).stdout.decode().strip()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_eval.insider_study")
    ap.add_argument("--window", required=True, choices=list(WINDOWS))
    ap.add_argument("--pubkey", required=True)
    a = ap.parse_args(argv)
    if git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("refused: the work tree has uncommitted changes")
    if a.window == "confirmation" and any(r["window"] == "confirmation" for r in ledger()):
        raise SystemExit("refused: the confirmation run already happened (trials ledger)")
    commit = git("rev-parse", "HEAD")
    started = dt.datetime.now(UTC)
    run_id = f"i2-{a.window}-{started:%Y%m%dT%H%M%SZ}"
    res = run(a.window, a.pubkey)
    res["run"] = {"run_id": run_id, "commit": commit, "started": started.isoformat(), "finished": dt.datetime.now(UTC).isoformat(),
                  "universe_digest": universe_digest()}
    out = data_root() / "derived" / f"i2_{a.window}_{started:%Y%m%dT%H%M%SZ}.json"
    blob = json.dumps(res, indent=1, default=str) + "\n"
    out.write_text(blob, encoding="utf-8")
    rec = {"run_id": run_id, "window": a.window, "commit": commit, "started": res["run"]["started"],
           "finished": res["run"]["finished"], "event_set_sha256": res["event_set"]["sha256"],
           "universe_digest": res["run"]["universe_digest"], "output": out.name,
           "output_sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest()}
    with open(ledger_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    print(json.dumps({"run": rec, "gate": res["gate"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
