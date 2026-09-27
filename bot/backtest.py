"""Squeeze-screener backtest - phase 1 of docs/superpowers/specs/2026-09-27-squeeze-screener-design.md.

    SEC_UA="stockbot backtest (github.com/danielsuisa/stockbot)" python -m bot.backtest

Downloads FINRA short interest, SEC shares-outstanding frames and Yahoo daily bars into .cache/squeeze/
(gitignored), replays every session with bot.squeeze's functions, chooses one scoring variant on the in-sample years
only, then judges it on the out-of-sample years and writes docs/backtest/squeeze-<date>.md plus a CSV of its picks."""
import bisect

from bot import shorts, squeeze

START, SPLIT = "2018-02-01", "2024-01-01"
LIFT, MIN_HITS, MIN_YEARS, EPS = 3.0, 30, 2, 1e-9
PICK_FIELDS = ("t", "score", "si_pct", "dtc", "rvol", "ret5", "brk", "price")


def vname(v):
    return f"w{v['w']:g}/si{v['g_si']:g}/rv{v['g_rv']:g}"


# ---------- tallies ----------
def acc(keep=False):
    """Tally of picks or stock-days: n, hits, per year [n, hits]; with keep, also every dd / r10 / maxup."""
    return {"n": 0, "hits": 0, "years": {}, **({"dd": [], "r10": [], "maxup": []} if keep else {})}


def add(a, year, o):
    a["n"] += 1
    a["hits"] += o["hit"]
    y = a["years"].setdefault(year, [0, 0])
    y[0] += 1
    y[1] += o["hit"]
    if "dd" in a:
        for k in ("dd", "r10", "maxup"):
            a[k].append(o[k])


def rate(a):
    return a["hits"] / a["n"] if a["n"] else None


def lift(a, base, year=None):
    """Hit rate of `a` / hit rate of `base` (overall, or one year); None when either is empty or the base has no hit."""
    if year is None:
        (n, h), (bn, bh) = (a["n"], a["hits"]), (base["n"], base["hits"])
    else:
        (n, h), (bn, bh) = a["years"].get(year, [0, 0]), base["years"].get(year, [0, 0])
    return (h / n) / (bh / bn) if n and bn and bh else None


# ---------- replay ----------
def day_rows(day, prev, end, rep, shares, primary, bars):
    """Eligible rows for session `day`, with features from the bar of `prev` (= D-1), and {ticker: outcome or None}
    (None: no bar or no open on `day`)."""
    rows, outs = [], {}
    for sym, si in rep.items():
        cik, b = primary.get(sym), bars.get(sym)
        if cik is None or not b:
            continue
        i = bisect.bisect_left(b["d"], day) - 1
        if i < 0 or b["d"][i] != prev:
            continue
        row = squeeze.make_row(sym, si, shorts.shares_at(shares, cik, day), squeeze.features(b, i))
        if row is None:
            continue
        rows.append(row)
        nxt = i + 1
        outs[sym] = squeeze.outcome(b, nxt, end) if nxt < len(b["d"]) and b["d"][nxt] == day else None
    return rows, outs


def coverage(c, rep, primary, bars, shares, day):
    """Add one report's symbols to a year's coverage sets: in FINRA, mapped to an SEC company, with Yahoo bars,
    with usable shares outstanding on `day`."""
    for sym in rep:
        c["finra"].add(sym)
        cik = primary.get(sym)
        if cik is None:
            continue
        c["sec_map"].add(sym)
        if bars.get(sym):
            c["yahoo"].add(sym)
            if shorts.shares_at(shares, cik, day):
                c["shares"].add(sym)


def replay(days, cal, reports, shares, primary, bars, lists, bases, keep=()):
    """Walk the sessions `days` (ISO, each in `cal`, the ascending session list).
    reports: [(usable ISO, {symbol: shorts.slim row})] ascending by usable date; shares: shorts.shares_index();
    primary: {symbol: cik}; bars: {symbol: bars}; lists: {name: variant} -> each day's top list (a ticker counts once
    per squeeze.WINDOW sessions); bases: {name: g_si or None} -> every eligible stock-day with si_pct >= g_si
    (None: all) as a hit-rate baseline; keep: list names whose picks are kept.
    -> {"lists": {name: tally}, "bases": {name: tally}, "picks": {name: [...]}, "days": n, "coverage": {year: sets}}"""
    pos = {d: k for k, d in enumerate(cal)}
    usable = [u for u, _ in reports]
    res = {"lists": {n: acc(keep=True) for n in lists}, "bases": {n: acc() for n in bases},
           "picks": {n: [] for n in keep}, "days": 0, "coverage": {}}
    last = {n: {} for n in lists}
    gates = sorted({v["g_si"] for v in lists.values()})
    seen = set()
    for day in days:
        k, j = pos[day], bisect.bisect_right(usable, day) - 1
        if k < 1 or k + squeeze.WINDOW - 1 >= len(cal) or j < 0:
            continue
        rep, year = reports[j][1], day[:4]
        if (j, year) not in seen:
            seen.add((j, year))
            sets = res["coverage"].setdefault(year, {"finra": set(), "sec_map": set(), "yahoo": set(), "shares": set()})
            coverage(sets, rep, primary, bars, shares, day)
        rows, outs = day_rows(day, cal[k - 1], cal[k + squeeze.WINDOW - 1], rep, shares, primary, bars)
        res["days"] += 1
        cands = {g: squeeze.scored(rows, g) for g in gates}
        for name, v in lists.items():
            for r in squeeze.top(cands[v["g_si"]], v["w"], v["g_rv"]):
                o = outs.get(r["t"])
                if o is None or k - last[name].get(r["t"], -squeeze.WINDOW) < squeeze.WINDOW:
                    continue
                last[name][r["t"]] = k
                add(res["lists"][name], year, o)
                if name in res["picks"]:
                    res["picks"][name].append({"date": day, **{f: r[f] for f in PICK_FIELDS}, **o})
        for name, g in bases.items():
            for r in rows:
                o = outs[r["t"]]
                if o is not None and (g is None or r["si_pct"] >= g):
                    add(res["bases"][name], year, o)
    return res


# ---------- decision ----------
def select(res, names, base="all", min_hits=MIN_HITS):
    """In-sample choice among `names`: the highest lift with >= min_hits hits; ties -> more hits, then name.
    None when no list qualifies."""
    ok = [(lift(res["lists"][n], res["bases"][base]) or 0.0, res["lists"][n]["hits"], n) for n in names
          if res["lists"][n]["hits"] >= min_hits]
    return min(ok, key=lambda t: (-t[0], -t[1], t[2]))[2] if ok else None


def verdict(a, base, years, lift_min=LIFT, min_hits=MIN_HITS, min_years=MIN_YEARS):
    """Out-of-sample GO / NO-GO for list tally `a` against `base`: ([(check, passed, value)], go)."""
    total = lift(a, base)
    per = {y: lift(a, base, y) for y in years}
    good = sum(1 for x in per.values() if x is not None and x >= lift_min - EPS)
    checks = [(f"lift ≥ {lift_min:g}× baseline", total is not None and total >= lift_min - EPS, total),
              (f"≥ {min_hits} distinct hits", a["hits"] >= min_hits, a["hits"]),
              (f"lift ≥ {lift_min:g}× in ≥ {min_years} of {len(years)} years", good >= min_years, per)]
    return checks, all(ok for _, ok, _ in checks)
