"""Squeeze-screener backtest - phase 1 of docs/superpowers/specs/2026-09-27-squeeze-screener-design.md.

    SEC_UA="<name> <contact email>" python -m bot.backtest   (SEC rejects a User-Agent without an email: 403)

Downloads FINRA short interest, SEC shares-outstanding frames and Yahoo daily bars into .cache/squeeze/
(gitignored), replays every session with bot.squeeze's functions, chooses one scoring variant on the in-sample years
only, then judges it on the out-of-sample years and writes docs/backtest/squeeze-<date>.md plus a CSV of its picks."""
import argparse
import array
import bisect
import concurrent.futures as cf
import csv
import datetime as dt
import gzip
import json
import statistics
import time

from bot import common, market, shorts, squeeze

CACHE = common.ROOT / ".cache" / "squeeze"
OUT = common.ROOT / "docs" / "backtest"
HISTORY_FROM = "2017-09-01"  # Yahoo warm-up before START
NAN = float("nan")
_DATES = {}  # one shared str object per session date (memory)

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
        feat = squeeze.features(b, i)
        if feat:
            feat["price"] = actual_close(b, i)  # the $1 gate needs the price traded then, not the split-adjusted one
        row = squeeze.make_row(sym, si, shorts.shares_at(shares, cik, day), feat)
        if row is None:
            continue
        rows.append(row)
        nxt = i + 1
        outs[sym] = squeeze.outcome(b, nxt, end) if nxt < len(b["d"]) and b["d"][nxt] == day else None
    return rows, outs


def actual_close(b, i):
    """Close of bar i as it traded: Yahoo back-adjusts closes for later splits, so undo every split dated after
    bar i (b["s"] = [[date, numerator / denominator], ...]; a 1:20 reverse split -> x 0.05)."""
    f = 1.0
    for day, ratio in b.get("s", ()):
        if day > b["d"][i]:
            f *= ratio
    return b["c"][i] * f


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


# ---------- downloads (cached) ----------
def _cached(path, make):
    """gz-JSON disk cache: the saved value, else make() - saved only when it is a real value, so a failed download
    is retried on the next run."""
    if path.exists():
        return json.loads(gzip.decompress(path.read_bytes()))
    val = make()
    if val:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(val, separators=(",", ":")).encode()))
    return val


def _parallel(fn, items, threads):
    with cf.ThreadPoolExecutor(max(1, threads)) as ex:
        return dict(zip(items, ex.map(fn, items)))


def to_bars(r):
    """Yahoo chart result -> bars {"d","o","h","l","c","v","s"}: oldest first, UTC session dates, one bar per date
    (the last), bars without a close dropped; a missing high/low falls back to the close, volume to 0, open stays
    None; "s" = the split events [[date, numerator / denominator], ...] (prices are split-adjusted)."""
    if not r:
        return None
    ts = r.get("timestamp") or []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    cols = [q.get(k) or [None] * len(ts) for k in ("open", "high", "low", "close", "volume")]
    b = {k: [] for k in "dohlcv"}
    for t, o, h, l, c, v in zip(ts, *cols):
        if not c:
            continue
        day = dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat()
        if b["d"] and b["d"][-1] == day:
            for k in "dohlcv":
                b[k].pop()
        for k, x in zip("dohlcv", (day, o or None, h or c, l or c, c, v or 0)):
            b[k].append(x)
    if not b["d"]:
        return None
    splits = ((r.get("events") or {}).get("splits") or {}).values()
    b["s"] = sorted([dt.datetime.fromtimestamp(x["date"], dt.timezone.utc).date().isoformat(),
                     x["numerator"] / x["denominator"]] for x in splits if x.get("denominator"))
    return b


def pack(b):
    """Cached bars -> compact in-memory bars: shared date strings, float arrays (a missing open -> NaN)."""
    return {"d": [_DATES.setdefault(d, d) for d in b["d"]],
            "o": array.array("d", (NAN if x is None else x for x in b["o"])),
            **{k: array.array("d", b[k]) for k in "hlcv"}, "s": b.get("s", [])}


def load_reports(dates, threads=4):
    """{settlement date: {symbol: slim row}} (one cached file per date)."""
    return _parallel(lambda d: _cached(CACHE / "finra" / f"{d}.json.gz", lambda: shorts.report(d)), dates, threads)


def load_frames(first=2017, last=None):
    """Every quarterly SEC shares-outstanding frame from `first` to this year (cached; unpublished ones skipped)."""
    out = []
    for y in range(first, (last or dt.date.today().year) + 1):
        for q in (1, 2, 3, 4):
            rows = _cached(CACHE / "sec" / f"CY{y}Q{q}I.json.gz",
                           lambda: [[c, e, v] for c, (e, v) in shorts.frame(y, q).items()])
            out.append({int(c): (e, v) for c, e, v in rows or []})
    return out


def load_bars(symbols, threads=8):
    """{symbol: packed bars} for every symbol Yahoo has daily bars for since HISTORY_FROM (cached per symbol)."""
    p1 = int(dt.datetime.fromisoformat(HISTORY_FROM).replace(tzinfo=dt.timezone.utc).timestamp())
    p2 = int(time.time())

    def one(sym):
        b = _cached(CACHE / "yahoo-v2" / f"{sym}.json.gz",  # v2: with split events
                    lambda: to_bars(market.chart(sym, period1=p1, period2=p2, interval="1d", events="split")))
        return pack(b) if b else None
    return {s: b for s, b in _parallel(one, symbols, threads).items() if b}


# ---------- report ----------
def _pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def _x(x):
    return "—" if x is None else f"{x:.2f}×"


def _med(xs):
    return statistics.median(xs) if xs else None


HEAD = ["| List | Picks | Hits | Hit rate | Lift | Median worst drop | Median 10-session return |",
        "|---|---:|---:|---:|---:|---:|---:|"]


def _line(name, a, base):
    return (f"| {name} | {a['n']} | {a['hits']} | {_pct(rate(a))} | {_x(lift(a, base))} | "
            f"{_pct(_med(a.get('dd', [])))} | {_pct(_med(a.get('r10', [])))} |")


def _base_line(name, a):
    return f"| {name} (every stock-day) | {a['n']} | {a['hits']} | {_pct(rate(a))} | 1.00× | — | — |"


def render(meta, r_in, grid, chosen, r_oos=None, checks=(), go=False):
    """The Markdown report."""
    L = [f"# Squeeze screener backtest — {meta['run']}", "", f"**Verdict: {'GO ✅' if go else 'NO-GO ❌'}**", "",
         "Rule, fixed on 2026-09-27 before any result (out-of-sample years only): the top-10 hit rate is ≥ 3× the "
         "baseline hit rate, ≥ 30 distinct hits, and ≥ 3× in at least 2 of the 3 years. Hit = the highest price in "
         "sessions D … D+9 reaches ≥ 1.5× the open of D. Baseline = every eligible stock-day.", ""]
    if checks:
        L += ["| Check | Result | Value |", "|---|---|---|"]
        for label, ok, val in checks:
            shown = ", ".join(f"{y}: {_x(x)}" for y, x in val.items()) if isinstance(val, dict) else \
                (_x(val) if isinstance(val, float) or val is None else str(val))
            L.append(f"| {label} | {'pass' if ok else 'fail'} | {shown} |")
        L.append("")
    L += ["## Data", "", *[f"- {x}" for x in meta["lines"]], ""]
    base_in = r_in["bases"]["all"]
    L += [f"## In-sample ({START} … {SPLIT}, exclusive): every grid variant", "",
          "Selection: highest lift among variants with ≥ 30 in-sample hits (ties → more hits).", "", *HEAD]
    order = sorted(grid, key=lambda n: -(lift(r_in["lists"][n], base_in) or 0))
    L += [_line(("**" + n + "** ← chosen") if n == chosen else n, r_in["lists"][n], base_in) for n in order]
    L += [_line(n, a, base_in) for n, a in r_in["lists"].items() if n not in grid]
    L += [_base_line(n, a) for n, a in r_in["bases"].items()]
    L.append("")
    if chosen is None or r_oos is None:
        L += ["No variant reached 30 in-sample hits, so nothing was frozen and the out-of-sample years were not "
              "evaluated → NO-GO.", ""]
        return "\n".join(L)
    base = r_oos["bases"]["all"]
    L += [f"## Out-of-sample ({SPLIT} → last full window): the frozen variant `{chosen}`", "", *HEAD]
    L += [_line(n, a, base) for n, a in r_oos["lists"].items()]
    L += [_base_line(n, a) for n, a in r_oos["bases"].items()]
    sel = r_oos["lists"]["selected"]
    L += ["", "### By year", "", "| Year | Picks | Hits | Hit rate | Baseline rate | Lift |", "|---|---:|---:|---:|---:|---:|"]
    for y in sorted(base["years"]):
        n, h = sel["years"].get(y, [0, 0])
        bn, bh = base["years"][y]
        L.append(f"| {y} | {n} | {h} | {_pct(h / n if n else None)} | {_pct(bh / bn if bn else None)} | "
                 f"{_x(lift(sel, base, y))} |")
    L += ["", "## Coverage (survivorship check)", "",
          "| Year | FINRA symbols | Mapped to an SEC company | With Yahoo bars | With usable shares |",
          "|---|---:|---:|---:|---:|"]
    cov = {**r_in["coverage"], **r_oos["coverage"]}
    for y in sorted(cov):
        c = cov[y]
        n = len(c["finra"]) or 1
        L.append(f"| {y} | {len(c['finra'])} | {len(c['sec_map'])} ({len(c['sec_map']) / n:.0%}) | "
                 f"{len(c['yahoo'])} ({len(c['yahoo']) / n:.0%}) | {len(c['shares'])} ({len(c['shares']) / n:.0%}) |")
    L += ["", "## Known biases", "",
          "- Survivorship: symbols are matched through today's SEC ticker list and Yahoo only serves live tickers, so "
          "delisted and renamed stocks are missing (see coverage). The baseline comes from the same covered stocks, so "
          "the lift is like-for-like; the absolute hit rates are not.",
          "- Multi-class issuers and splits between the SEC figure and the FINRA date add noise to the short-interest %.",
          "- Yahoo bad ticks can create or hide a hit; the picks CSV lets any hit be checked by hand.", ""]
    return "\n".join(L)


def write_csv(path, picks):
    cols = ["date", *PICK_FIELDS, "hit", "maxup", "dd", "r10"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for p in picks:
            w.writerow({k: round(p[k], 4) if isinstance(p[k], float) else p[k] for k in cols})


# ---------- run ----------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Squeeze screener backtest -> docs/backtest/squeeze-<date>.md")
    ap.add_argument("--start", default=START)
    ap.add_argument("--split", default=SPLIT)
    ap.add_argument("--threads", type=int, default=8)
    a = ap.parse_args(argv)
    t0, run = time.time(), dt.date.today().isoformat()
    primary = {t: cik for cik, t in common.cik_tickers().items()}
    dates = [d for d in shorts.settlement_dates() if d >= "2017-12-01"]
    raw = load_reports(dates, a.threads // 2)
    cal = load_bars(["SPY"])["SPY"]["d"]
    reports = [(u, shorts.remap(raw[d], primary)) for d in dates if raw.get(d) and (u := shorts.usable_from(d, cal))]
    frames = load_frames()
    shares = shorts.shares_index(frames)
    syms = sorted({s for _, rep in reports for s in rep if s in primary})
    bars = load_bars(syms, a.threads)
    print(f"data: {len(reports)} reports, {len(shares)} SEC filers, {len(bars)}/{len(syms)} symbols with bars "
          f"({time.time() - t0:.0f}s)", flush=True)
    grid = {vname(v): v for v in squeeze.GRID}
    gates = sorted({v["g_si"] for v in squeeze.GRID})
    trig = {"w": 0.0, "g_si": -1.0, "g_rv": 0.0}
    diag = {**{f"fuel-only si{g:g}": {"w": 1.0, "g_si": g, "g_rv": 0.0} for g in gates}, "trigger-only": trig}
    ins = [d for d in cal if a.start <= d < a.split]
    r_in = replay(ins, cal, reports, shares, primary, bars, {**grid, **diag},
                  {"all": None, **{f"SI ≥ {g:.0%}": g for g in gates}})
    chosen = select(r_in, list(grid))
    print(f"in-sample: {r_in['days']} sessions, chosen {chosen} ({time.time() - t0:.0f}s)", flush=True)
    r_oos, checks, go, picks = None, [], False, []
    if chosen:
        v = grid[chosen]
        oos = [d for d in cal if d >= a.split]
        lists = {"selected": v, "fuel-only": {"w": 1.0, "g_si": v["g_si"], "g_rv": 0.0}, "trigger-only": trig}
        r_oos = replay(oos, cal, reports, shares, primary, bars, lists,
                       {"all": None, f"SI ≥ {v['g_si']:.0%}": v["g_si"]}, keep=("selected",))
        checks, go = verdict(r_oos["lists"]["selected"], r_oos["bases"]["all"], sorted(r_oos["bases"]["all"]["years"]))
        picks = r_oos["picks"]["selected"]
    last_full = cal[-squeeze.WINDOW]
    meta = {"run": run, "lines": [
        f"FINRA reports: {len(reports)} (settlements {dates[0]} … {dates[-1]}), usable from the 8th session after "
        f"settlement", f"SEC shares-outstanding frames: {sum(1 for f in frames if f)}, {len(shares)} filers",
        f"Symbols (FINRA NNM/SC/NYSE/AMEX, primary SEC ticker): {len(syms)}, with Yahoo bars: {len(bars)}",
        f"Sessions: in-sample {r_in['days']}, out-of-sample {r_oos['days'] if r_oos else 0} (last full 10-session "
        f"window starts {last_full})", f"Runtime {time.time() - t0:.0f}s · `python -m bot.backtest`"]}
    OUT.mkdir(parents=True, exist_ok=True)
    md = OUT / f"squeeze-{run}.md"
    md.write_text(render(meta, r_in, grid, chosen, r_oos, checks, go), "utf-8")
    if picks:
        write_csv(OUT / f"squeeze-{run}-picks.csv", picks)
    print(f"{'GO' if go else 'NO-GO'} -> {md}")
    return 0 if go else 3


if __name__ == "__main__":
    raise SystemExit(main())
