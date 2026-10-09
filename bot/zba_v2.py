"""ZBA v2 (docs/superpowers/specs/2026-10-08-ticket-backtest-design.md, section 14): the owner's improvements to K10s
as 22 pre-registered variants, developed on 2022-01-03 -> 2026-10-08 and judged once on 2016-01-04 -> 2021-12-31.

    python -m bot.zba_v2 --mode dev       # the grid, the selection, the tick-level re-run, the freeze
    python -m bot.zba_v2 --mode holdout   # the frozen finalists, once, on 2016-2021 (refuses to run twice)

Every fetch is cached under .cache/alpaca, so a run stopped by a time limit is restarted and walks the cached days in
seconds (the GitHub workflow re-dispatches itself until the report exists). Stdlib only; keys from the environment."""
import argparse
import bisect
import collections
import csv
import datetime as dt
import functools
import glob
import json
import os
import statistics
import time

from bot import alpaca, ticket, ticket_backtest as tb

DEV = ("2022-01-03", "2026-10-08")
HOLDOUT = ("2016-01-04", "2021-12-31")
DEV_YEARS = ("2022", "2023", "2024", "2025", "2026")
HOLD_YEARS = ("2016", "2017", "2018", "2019", "2020", "2021")
FROZEN = "zba-v2-frozen.json"
BASE = {"stop_atr": 0.10, "spread_d": 0.25}  # K10s
SINGLE = {"K10s": {}, "T15": {"tod_min": 1.5}, "T2": {"tod_min": 2.0}, "T3": {"tod_min": 3.0},
          "L10": {"dv_min": 10e6}, "L25": {"dv_min": 25e6}, "Q5": {"depth_min": 500}, "N": {"news": True},
          "C15": {"confirm": 1.5}, "C2": {"confirm": 2.0}, "F": {"candle": (0.10, 0.60, 0.50)},
          "V10": {"vstop": 0.10}, "V25": {"vstop": 0.25}, "RR": {"rr": (1.0, 2.0)}, "SL": {"limit_d": 0.25},
          "QR": {"rank": "qr"}, "DQ": {"dq": True}}
COMBOS = {"X1": ("T2", "L10", "DQ"), "X2": ("C2", "V25", "DQ"), "X3": ("F", "RR", "SL", "DQ"),
          "X4": ("QR", "T2", "V25", "SL", "DQ"), "X5": ("N", "T2", "DQ")}
VARIANTS = {**SINGLE, **{k: {o: v for s in parts for o, v in SINGLE[s].items()} for k, parts in COMBOS.items()}}
SCREEN_MIN, SELECT_MIN, TOP_RERUN, FINALISTS = 500, 500, 3, 2
HOLD_MIN, HOLD_T, HOLD_T2, HOLD_YEARS_POS, SLIP = 300, 2.0, 2.24, 5, 0.005
DQ_BEFORE = 5  # minutes before the trigger that must all have a bar
COLUMNS = ("variant", "day", "t", "rank", "rel_vol", "side", "at", "entry_type", "level", "limit", "R", "shares",
           "spread", "filled", "fill_at", "fill", "kinds", "gross_r", "cost_r", "net_r", "net_r_short")


# ---------- the session's lists ----------
def dollar_days(days, prev, bars):
    """The 14-day mean of close x volume before each session (the same window as tb.prefilter_days) -> {day: $}."""
    dates, acc = [b[0] for b in bars], [0.0]
    for b in bars:
        acc.append(acc[-1] + b[4] * b[5])
    out = {}
    for d in days:
        k = bisect.bisect_left(dates, d)
        if k >= tb.Z_DAYS + 1 and dates[k - 1] == prev.get(d):
            out[d] = (acc[k] - acc[k - tb.Z_DAYS]) / tb.Z_DAYS
    return out


def _z(xs):
    m = statistics.mean(xs)
    sd = statistics.pstdev(xs)
    return [(x - m) / sd if sd else 0.0 for x in xs]


def qr_top(cands, dv, news, n=tb.Z_TOP):
    """#16: the session's candidates [(t, rel vol, opening bar)] ranked by the equal-weight sum of z-scores of rel vol,
    14-day dollar volume, the first candle's body / range and the catalyst (1 with news) -> the top n, same shape."""
    if not cands:
        return []
    body = [abs(b[3] - b[0]) / (b[1] - b[2]) if b[1] > b[2] else 0.0 for _, _, b in cands]
    cols = [_z([rv for _, rv, _ in cands]), _z([dv.get(t, 0.0) for t, _, _ in cands]), _z(body),
            _z([1.0 if t in news else 0.0 for t, _, _ in cands])]
    score = [sum(c[i] for c in cols) for i in range(len(cands))]
    order = sorted(range(len(cands)), key=lambda i: (-score[i], cands[i][0]))
    return [cands[i] for i in order[:n]]


def tod_fn(bars, prior):
    """#2: minute -> today's volume 09:30 through that minute / the mean of the same window over the prior sessions
    (each {minute: volume}; a session without bars counts 0); None when the prior mean is 0."""
    today = collections.Counter({b[0]: b[5] for b in bars if b[0] >= "09:30"})
    minutes = [ticket.hhmm(ticket.mins("09:30") + i) for i in range(390)]

    def cum(vols):
        out, s = {}, 0.0
        for m in minutes:
            s += vols.get(m, 0)
            out[m] = s
        return out
    mine, base = cum(today), [cum(p) for p in prior]

    def f(m):
        ref = sum(c.get(m, 0.0) for c in base) / len(base) if base else 0.0
        return mine.get(m, 0.0) / ref if ref else None
    return f


def dq_reason(bars, at, open_="09:30"):
    """#17 (minutes up to the entry minute only: no look-ahead) -> the first failing reason or None."""
    have = {b[0]: b for b in bars}
    n = ticket.mins(at)
    need = [ticket.hhmm(n - i) for i in range(DQ_BEFORE + 1) if ticket.hhmm(n - i) >= open_]
    if any(m not in have for m in need):
        return "dq: missing bars"
    for m, o, h, l, c, v, vw in bars:
        if not open_ <= m <= at:
            continue
        if vw is not None and v and not l - 1e-9 <= vw <= h + 1e-9:
            return "dq: vwap outside the bar"
        if not v and h != l:
            return "dq: price moved on zero volume"
    return None


# ---------- one variant on one ticker-day ----------
def quote_fns(t, day):
    """spread(minute) and depth(minute) from the same SIP quote at the minute's start (one cached request)."""
    def q(m):
        return alpaca.quote_full(t, alpaca.utc(day, m))

    def spread(m):
        x = q(m)
        if not x or x[0] <= 0 or x[1] <= 0 or x[1] < x[0]:
            return None
        return round(max(x[1] - x[0], tb.TICK), 4)

    def depth(m):
        x = q(m)
        return (x[2], x[3]) if x else None
    return spread, depth


def play(variant, d, t, rank, rv, bars, info, hours, extra):
    """-> (reason, None) or (None, trade row) for one variant on one ticker-day. info: the prefilter's (atr, avg
    volume, dollar volume); extra: {"news": set, "tod": callable or None}."""
    opt = VARIANTS[variant]
    atr, _, dv = info
    if opt.get("dv_min") and dv < opt["dv_min"]:
        return "liquidity", None
    if opt.get("news") and t not in extra["news"]:
        return "news", None
    spread, depth = quote_fns(t, d)
    ctx = {"day": d, "open": hours[d][0], "close": hours[d][1], "atr": atr, "open_rel_vol": rv, "from": hours[d][0],
           **BASE, **{k: v for k, v in opt.items() if k in ("confirm", "candle", "vstop", "rr", "limit_d")}}
    if opt.get("tod_min"):
        ctx.update(tod=extra["tod"] or (lambda m: None), tod_min=opt["tod_min"])
    if opt.get("depth_min"):
        ctx.update(depth=depth, depth_min=opt["depth_min"])
    reason, row = tb._play("ZBA", "REGULAR", bars, ctx, spread, d, t, "C")
    if row and opt.get("dq"):
        bad = dq_reason(bars, row["at"], hours[d][0])
        if bad:
            return bad, None
    if row:
        row.update(rank=rank, rel_vol=rv, variant=variant)
    return reason, row


# ---------- the run ----------
def lists(days, sessions, hours, prev, syms, t0, need_news=True):
    """Pass 1: per session the prefilter, the candidates, the two top-20 lists and the news set ->
    ({day: {"rel", "qr", "news"}}, {day: {t: (atr, avg vol, dollar vol)}}, counts)."""
    look = (dt.date.fromisoformat(days[0]) - dt.timedelta(60)).isoformat()
    pre = collections.defaultdict(dict)
    for i in range(0, len(syms), 100):
        for t, bars in alpaca.daily(syms[i:i + 100], look, days[-1]).items():
            dv = dollar_days(days, prev, bars)
            for d, x in tb.prefilter_days(days, prev, bars).items():
                pre[d][t] = (x["atr"], x["avg_vol"], dv.get(d, 0.0))
        if i % 2000 == 0:
            print(f"daily bars {i}/{len(syms)}, {alpaca.REQUESTS[0]} requests ({time.time() - t0:.0f}s)", flush=True)
    k0 = sessions.index(days[0])
    span = sessions[max(0, k0 - tb.Z_DAYS):sessions.index(days[-1]) + 1]
    need = collections.defaultdict(set)
    for k, d in enumerate(span):
        if d >= days[0]:
            for s in span[max(0, k - tb.Z_DAYS):k + 1]:
                need[s] |= set(pre[d])
    out, window, counts = {}, collections.deque(maxlen=tb.Z_DAYS), collections.Counter()
    for k, d in enumerate(span):
        ob = alpaca.opening_bars(sorted(need[d]), d) if need[d] else {}
        if d >= days[0] and len(window) == tb.Z_DAYS:
            cands = tb.stocks_in_play({t: ob.get(t) for t in pre[d]}, list(window), n=10 ** 9)
            news = alpaca.news_symbols(prev[d], d) if need_news else set()
            rel = cands[:tb.Z_TOP]
            qr = qr_top(cands, {t: pre[d][t][2] for t, _, _ in cands}, news)
            out[d] = {"rel": [(t, rv) for t, rv, _ in rel], "qr": [(t, rv) for t, rv, _ in qr], "news": news}
            counts["candidates"] += len(cands)
            counts["top ticker-days (rel vol)"] += len(rel)
            counts["top ticker-days (quality rank)"] += len(qr)
        window.append(ob)
        if k % 50 == 0:
            print(f"lists {d}: {alpaca.REQUESTS[0]} requests ({time.time() - t0:.0f}s)", flush=True)
    return out, pre, counts


def run_grid(variants, days, sessions, hours, prev, tops, pre, t0):
    """Pass 2: every variant on every ticker-day of its list -> (rows, {variant: Counter of skip reasons}, gaps)."""
    window = {d: sessions[sessions.index(d) - tb.Z_DAYS:sessions.index(d)] for d in days}
    needvol = collections.defaultdict(set)
    want_tod = any(VARIANTS[v].get("tod_min") for v in variants)
    if want_tod:
        for d in days:
            union = {t for t, _ in tops[d]["rel"]} | {t for t, _ in tops[d]["qr"]}
            for s in window[d]:
                needvol[s] |= union

    @functools.lru_cache(maxsize=32)
    def vol(s):
        return alpaca.minute_volumes(sorted(needvol[s]), s)
    rows, reasons, gaps = [], collections.defaultdict(collections.Counter), collections.Counter()
    for k, d in enumerate(days):
        lst = {"rel": tops[d]["rel"], "qr": tops[d]["qr"]}
        union = sorted({t for v in lst.values() for t, _ in v})
        mb = alpaca.minute_bars(union, d) if union else {}
        for t in union:
            bars = mb.get(t) or []
            if not bars:
                gaps["ticker-days without minute bars"] += 1
                continue
            tod = tod_fn(bars, [vol(s).get(t, {}) for s in window[d]]) if want_tod else None
            for v in variants:
                ranked = lst["qr" if VARIANTS[v].get("rank") == "qr" else "rel"]
                rank = next((i for i, (x, _) in enumerate(ranked, 1) if x == t), None)
                if rank is None:
                    continue
                reason, row = play(v, d, t, rank, ranked[rank - 1][1], bars, pre[d][t], hours,
                                   {"news": tops[d]["news"], "tod": tod})
                if row:
                    rows.append(row)
                    if not row["filled"]:
                        reasons[v]["no fill"] += 1
                else:
                    reasons[v][reason] += 1
        if k % 20 == 0 or k == len(days) - 1:
            print(f"grid {d}: {len(rows)} tickets, {alpaca.REQUESTS[0]} requests ({time.time() - t0:.0f}s)", flush=True)
    return rows, reasons, gaps


def summary(xs, days, years):
    """Filled trades of one variant -> stats, random p95, the book, and the owner's #19 metrics."""
    st = tb.stats(xs)
    rm = tb.random_means(xs) if xs else []
    wins, losses = [x["net_r"] for x in xs if x["net_r"] > 0], [x["net_r"] for x in xs if x["net_r"] <= 0]
    book = tb.portfolio(xs, days) if xs else None
    return {**st, "p95": tb.pctl(rm, tb.PCTL) if rm else None, "random": rm, "book": book,
            "avg_win": tb._mean(wins), "avg_loss": tb._mean(losses), "expectancy": st["mean"],
            "year_means": {y: st["years"].get(y, {}).get("mean") for y in years}}


def select(screen, real):
    """14.4: the top TOP_RERUN by bar-model mean (>= SCREEN_MIN trades) -> re-run; among them, by tick-level mean,
    those with >= SELECT_MIN trades, t >= 2 and every development year positive -> up to FINALISTS names."""
    ok = [v for v, s in real.items() if s["n"] >= SELECT_MIN and (s["t"] or 0) >= 2
          and all(m is not None and m > 0 for m in s["year_means"].values())]
    return sorted(ok, key=lambda v: -real[v]["mean"])[:FINALISTS]


def rerun_pick(screen):
    ok = [v for v, s in screen.items() if s["n"] >= SCREEN_MIN and s["mean"] is not None]
    return sorted(ok, key=lambda v: -screen[v]["mean"])[:TOP_RERUN]


def holdout_checks(xs, s, n_final):
    """14.5's six gates on the holdout's realistic trades -> ([(check, passed)], GO?)."""
    rb = tb.robustness(xs, (SLIP,)) if len(xs) > 1 else {}
    tmin = HOLD_T2 if n_final == 2 else HOLD_T
    m = s["mean"]
    slip = rb.get(f"+${SLIP:g} a share each side", (None, None))
    top = rb.get("drop top 1%", (None, None))
    pos = sum(1 for y in HOLD_YEARS if (s["year_means"].get(y) or 0) > 0)
    checks = [(f"at least {HOLD_MIN} trades ({s['n']})", s["n"] >= HOLD_MIN),
              (f"mean net R > 0 with t >= {tmin:g}", m is not None and m > 0 and (s["t"] or 0) >= tmin),
              ("mean above the 95th percentile of 20 random-side means", m is not None and s["p95"] is not None
               and m > s["p95"]),
              (f"positive in at least {HOLD_YEARS_POS} of 6 years ({pos})", pos >= HOLD_YEARS_POS),
              (f"t >= 2 with +${SLIP:g} a share each side", (slip[1] or 0) >= 2),
              ("mean > 0 without the top 1% of trades", (top[0] or 0) > 0)]
    return checks, all(ok for _, ok in checks)


def _calendar(rng):
    look = (dt.date.fromisoformat(rng[0]) - dt.timedelta(60)).isoformat()
    hours = alpaca.calendar(look, rng[1])
    sessions = sorted(hours)
    days = [d for d in sessions if rng[0] <= d <= rng[1]]
    return look, hours, sessions, days, {d: sessions[k - 1] for k, d in enumerate(sessions) if k}


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for x in rows:
            w.writerow([round(x.get(c), 4) if isinstance(x.get(c), float) else x.get(c) for c in COLUMNS])


def dev(a, t0, run):
    rng = (a.start or DEV[0], a.end or DEV[1])
    look, hours, sessions, days, prev = _calendar(rng)
    syms = tb.asset_universe(alpaca.assets())
    tops, pre, counts = lists(days, sessions, hours, prev, syms, t0)
    rows, reasons, gaps = run_grid(list(VARIANTS), days, sessions, hours, prev, tops, pre, t0)
    screen = {v: summary([x for x in rows if x["variant"] == v and x["filled"]], days, DEV_YEARS) for v in VARIANTS}
    picks = rerun_pick(screen)
    real, real_rows, status = {}, {}, {}
    for v in picks:  # 14.4 step 2: the tick-level fill minute and Rule 201
        real_rows[v], status[v] = tb._realism(rows, v, hours, look, rng[1])
        real[v] = summary(real_rows[v], days, DEV_YEARS)
    finalists = select(screen, real)
    os.makedirs(a.out, exist_ok=True)
    if finalists:
        frozen = {"frozen": run, "finalists": finalists, "params": {v: VARIANTS[v] for v in finalists},
                  "base": BASE, "dev": {v: {k: real[v][k] for k in ("n", "mean", "t")} for v in finalists},
                  "holdout": HOLDOUT, "spec": "docs/superpowers/specs/2026-10-08-ticket-backtest-design.md section 14"}
        with open(os.path.join(a.out, FROZEN), "w") as f:
            json.dump(frozen, f, indent=1, default=list)
    meta = {"run": run, "range": rng, "runtime": time.time() - t0, "requests": alpaca.REQUESTS[0],
            "counts": dict(counts), "gaps": dict(gaps)}
    with open(os.path.join(a.out, f"ticket-zba-v2-{run}.md"), "w", encoding="utf-8") as f:
        f.write(render_dev(meta, screen, reasons, picks, real, status, finalists))
    _write_csv(os.path.join(a.out, f"ticket-zba-v2-{run}-realistic.csv"), [x for v in picks for x in real_rows[v]])
    print(f"ZBA v2 dev: finalists {finalists or 'none (NO-GO)'} ({time.time() - t0:.0f}s, {alpaca.REQUESTS[0]} requests)")
    return 0


def holdout(a, t0, run):
    path = os.path.join(a.out, FROZEN)
    if not os.path.exists(path):
        raise SystemExit(f"no {path}: the development selection has no finalist (or was not run) - no holdout")
    if glob.glob(os.path.join(a.out, "ticket-zba-v2-holdout-*.md")):
        raise SystemExit("the holdout was already run once (spec 14.5: single-shot)")
    frozen = json.load(open(path))
    finalists = frozen["finalists"]
    for v in finalists:  # the frozen parameters must still be the code's
        if json.loads(json.dumps(VARIANTS[v], default=list)) != json.loads(json.dumps(frozen["params"][v], default=list)):
            raise SystemExit(f"{v}'s parameters changed since the freeze")
    look, hours, sessions, days, prev = _calendar(HOLDOUT)
    syms = tb.asset_universe(alpaca.assets())
    news = any(VARIANTS[v].get("news") or VARIANTS[v].get("rank") == "qr" for v in finalists)
    tops, pre, counts = lists(days, sessions, hours, prev, syms, t0, news)
    rows, reasons, gaps = run_grid(finalists, days, sessions, hours, prev, tops, pre, t0)
    res = {}
    for v in finalists:
        rr, status = tb._realism(rows, v, hours, look, HOLDOUT[1])
        s = summary(rr, days, HOLD_YEARS)
        checks, go = holdout_checks(rr, s, len(finalists))
        res[v] = {"s": s, "checks": checks, "go": go, "status": dict(status), "rows": rr,
                  "robust": tb.robustness(rr, (SLIP, 0.01)) if len(rr) > 1 else None}
    meta = {"run": run, "range": HOLDOUT, "runtime": time.time() - t0, "requests": alpaca.REQUESTS[0],
            "counts": dict(counts), "gaps": dict(gaps), "frozen": frozen}
    with open(os.path.join(a.out, f"ticket-zba-v2-holdout-{run}.md"), "w", encoding="utf-8") as f:
        f.write(render_holdout(meta, res, reasons))
    _write_csv(os.path.join(a.out, f"ticket-zba-v2-holdout-{run}-realistic.csv"),
               [x for v in finalists for x in res[v]["rows"]])
    for v in finalists:
        print(f"ZBA v2 holdout {v}: {'GO' if res[v]['go'] else 'NO-GO'} ({res[v]['s']['n']} trades, "
              f"mean {tb._f(res[v]['s']['mean'])}R)")
    return 0


# ---------- reports ----------
def _row(name, s):
    y = " | ".join(tb._f(s["year_means"].get(k)) for k in s["year_means"])
    return (f"| {name} | {s['n']} | {tb._f(s['mean'])} | {tb._f(s['t'], '{:.2f}')} | {tb._f(s['win'], '{:.0%}')} | "
            f"{tb._f(s['avg_win'])} | {tb._f(s['avg_loss'])} | {tb._f(s['p95'])} | "
            f"{tb._f((s['book'] or {}).get('max_dd'), '{:.1%}')} | {y} |")


def _head(years):
    return [f"| Variant | Trades | Mean net R | t | Win | Avg win | Avg loss | Random p95 | Book max DD | "
            + " | ".join(years) + " |", "|---|" + "---:|" * (9 + len(years) - 1)]


def render_dev(meta, screen, reasons, picks, real, status, finalists):
    L = [f"# ZBA v2 — development (spec section 14) — {meta['run']}", "", "## סיכום", ""]
    if finalists:
        L.append("- נבחרו לבדיקה החד־פעמית על `2016–2021`: " + ", ".join(f"`{v}`" for v in finalists)
                 + ". הפרמטרים הוקפאו ב־`zba-v2-frozen.json` לפני ההרצה.")
    else:
        L.append("- אף גרסה לא עמדה בכלל הבחירה (לפחות `500` עסקאות, `t ≥ 2`, וחיובית בכל שנה). ההחלטה: `NO-GO`, "
                 "ובדיקת `2016–2021` לא תורץ.")
    L += [f"- סוננו `{len(screen)}` גרסאות במודל הנרות, ו־`{len(picks)}` הטובות הורצו שוב עם מילוי לפי עסקאות בודדות.",
          "- כל הכללים והספים נקבעו בסעיף `14` לפני ההרצה.", "",
          "## Screen (bar model, 2022-01-03 → 2026-10-08)", ""] + _head(DEV_YEARS)
    L += [_row(v, s) for v, s in screen.items()]
    L += ["", "## Tick-level re-run (the selection uses these)", ""] + _head(DEV_YEARS)
    L += [_row(v + (" ← finalist" if v in finalists else ""), real[v]) for v in picks]
    L += ["", "Status of the re-run trades: " + "; ".join(f"{v}: " + ", ".join(f"{k} {n}" for k, n in status[v].items())
                                                       for v in picks), "",
          "## Skip reasons by variant", "", "| Variant | Reasons |", "|---|---|"]
    L += [f"| {v} | " + ", ".join(f"{k} {n}" for k, n in sorted(r.items(), key=lambda kv: -kv[1])) + " |"
          for v, r in reasons.items()]
    L += ["", "## Data", "", f"- Range {meta['range'][0]} … {meta['range'][1]} · runtime {meta['runtime']:.0f}s · "
          f"Alpaca requests {meta['requests']}"] + [f"- {k}: {n}" for k, n in {**meta["counts"], **meta["gaps"]}.items()]
    L += ["", "`python -m bot.zba_v2 --mode dev` · data: Alpaca SIP and news (Basic plan)", ""]
    return "\n".join(L)


def render_holdout(meta, res, reasons):
    L = [f"# ZBA v2 — holdout 2016-2021 (spec 14.5, single run) — {meta['run']}", "", "## סיכום", ""]
    for v, r in res.items():
        s = r["s"]
        L.append(f"- הגרסה `{v}`: {'GO ✅' if r['go'] else 'NO-GO ❌'} — עסקאות `{s['n']}`, ממוצע נטו "
                 f"`{tb._f(s['mean'])}R`, סטטיסטי t `{tb._f(s['t'], '{:.2f}')}`")
    L += ["- זו הבדיקה החד־פעמית. ביקום חסרות מניות שנמחקו מהמסחר אחר כך (סעיף `14.0`), ולכן גם `GO` כאן "
          "הוא ראיה חלקית, ומעקב הצל קובע.", ""]
    for v, r in res.items():
        L += [f"## {v} — gates", "", "| Gate | Pass |", "|---|---|"]
        L += [f"| {c} | {'pass' if ok else 'fail'} |" for c, ok in r["checks"]]
        L += ["", *_head(HOLD_YEARS), _row(v, r["s"]), "",
              "Trades: " + ", ".join(f"{k} {n}" for k, n in r["status"].items()), ""]
        if r["robust"]:
            L += ["| Stress (not gated) | Mean net R | t |", "|---|---:|---:|"]
            L += [f"| {n} | {tb._f(x[0])} | {tb._f(x[1], '{:.2f}')} |" for n, x in r["robust"].items() if n != "quarters"]
            L.append("")
        L += ["Skip reasons: " + ", ".join(f"{k} {n}" for k, n in sorted(reasons[v].items(), key=lambda kv: -kv[1])), ""]
    L += ["## Data", "", f"- Range {meta['range'][0]} … {meta['range'][1]} · runtime {meta['runtime']:.0f}s · Alpaca "
          f"requests {meta['requests']} · frozen {meta['frozen']['frozen']}"]
    L += [f"- {k}: {n}" for k, n in {**meta["counts"], **meta["gaps"]}.items()]
    return "\n".join(L + [""])


def main(argv=None):
    ap = argparse.ArgumentParser(description="ZBA v2 (spec section 14)")
    ap.add_argument("--mode", choices=("dev", "holdout"), required=True)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--out", default="docs/backtest")
    a = ap.parse_args(argv)
    run = dt.date.today().isoformat()
    return (dev if a.mode == "dev" else holdout)(a, time.time(), run)


if __name__ == "__main__":
    raise SystemExit(main())
