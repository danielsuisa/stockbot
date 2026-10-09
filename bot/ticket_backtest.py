"""The order-ticket backtest (spec docs/superpowers/specs/2026-10-08-ticket-backtest-design.md): fills and exits on
1-minute bars with costs, the random-direction mirror, the candidate universes, statistics and the verdict.

Fills are conservative (spec section 6): a buy limit fills only when a minute's low reaches it, at the limit; a stop
fills at the stop, or at the minute's open when it opened beyond it; a minute that touches both stop and target counts
as the stop. On the fill minute a stop counts only when its side of the minute comes after the fill, on the usual bar
path (open-low-high-close for a minute that closes at or above its open, else open-high-low-close), the same for
both directions. Results are in R: P&L / (R x shares)."""
import argparse
import bisect
import collections
import csv
import datetime as dt
import os
import random
import re
import statistics
import time
from zoneinfo import ZoneInfo

from bot import alpaca, backtest, common, shorts, ticket
from bot.ticket import hhmm, mins

COMMISSION, MIN_ORDER = 0.0035, 0.35  # dollars per share, minimum per order (each side)
TIME_STOP, TRAIL_PAD = 60, 0.10  # ORIGINAL: minutes to target 1 after the fill; trail below the last higher low


def _after(o, h, l, c, at_open, down):
    """Which extremes of a fill minute come after the fill, on the usual bar path (open-low-high-close when the minute
    closes at or above its open, else open-high-low-close) -> (low after, high after). at_open: filled at the open;
    down: reached on the way down (a limit under the open), else on the way up (a stop over the open)."""
    if at_open:
        return True, True
    bull = c >= o
    return (True, bull) if down else (not bull, True)


def _fill(t, bars):
    """-> (index into bars, price, (low after, high after)) of the entry fill, or None."""
    at, until = t["at"], t["valid_until"]
    limit = float("inf") if t["limit"] is None else t["limit"]  # a plain stop: no limit
    triggered = t["entry_type"] == "LMT"
    for i, (m, o, h, l, c, v, vw) in enumerate(bars):
        if m < at:
            continue
        if m >= until:
            return None
        if not triggered:  # a stop(-limit): the first minute whose high is above the level (or at it: "touch")
            if not (h >= t["level"] if t.get("touch") else h > t["level"]):
                continue
            triggered = True
            px = max(o, t["level"])
            if px <= limit:
                return i, px, _after(o, h, l, c, o >= t["level"], False)
        if l <= limit:
            return i, limit, _after(o, h, l, c, o <= limit, True)
    return None


def _mirror(bars):
    return [(m, -o, -l, -h, -c, v, None if vw is None else -vw) for m, o, h, l, c, v, vw in bars]


def simulate(t, bars1, ctx, direction=1):
    """A ticket (ticket.signal) played on the day's 1-minute bars -> {"filled", "fill_at", "fill", "exits": [{"qty",
    "price", "at", "kind"}], "gross_r", "cost_r", "net_r"}. direction -1: the same entry moment and price as a short
    (stops and targets mirrored around the fill, same costs) - the random-direction benchmark. A ticket with "side" -1
    (ZBA) is a short: it is played as a long on mirrored prices; direction stays relative to the ticket's side."""
    t0 = ctx["open"] if t["session"] == "REGULAR" else ticket.PRE_OPEN
    bars = [b for b in bars1 if t0 <= b[0] < ctx["close"]]
    side = t.get("side", 1)
    if side == -1:  # a short in long space
        bars = _mirror(bars)
        t = {**t, "level": -t["level"], "limit": None if t["limit"] is None else -t["limit"]}
    got = _fill(t, bars)
    if got is None:
        return {"filled": False, "fill_at": None, "fill": None, "exits": [], "gross_r": 0, "cost_r": 0, "net_r": 0}
    i, fill, after = got
    play = bars if direction == 1 else _mirror(bars)
    entry = fill * direction
    first = after[0] if direction == 1 else after[1]  # the stop's side of the fill minute comes after the fill
    exits = (_original if t["rules"] == "ORIGINAL" else _improved)(t, play, i, entry, t0, first)
    risk = t["R"] * t["shares"]
    gross = sum((e["price"] - entry) * e["qty"] for e in exits) / risk
    orders = [t["shares"]] if t["rules"] == "ORIGINAL" else [g["qty"] for g in t["legs"]]  # IMPROVED: 2 brackets
    commission = sum(max(MIN_ORDER, q * COMMISSION) for q in orders + [e["qty"] for e in exits])
    cost = (commission + t["shares"] * t["spread"]) / risk  # half the spread in, half out
    for e in exits:
        e["price"] = round(e["price"] * direction * side, 4)
    return {"filled": True, "fill_at": bars[i][0], "fill": round(fill * side, 4), "exits": exits, "gross_r": gross,
            "cost_r": cost, "net_r": gross - cost}


def _stop_px(o, l, stop):
    """The fill of a sell stop in a minute, or None: at the open when it opened at or below the stop."""
    return o if o <= stop else stop if l <= stop else None


def _original(t, bars, i, entry, t0, first=True):
    """The owner's management (long space): stop on all shares until target 1; then the rest's stop at entry, trailed
    to the last higher low - 0.10, exit at the next open after a 5-minute close under VWAP; time stop 60 minutes after
    the fill while target 1 is not hit; anything open exits at the last minute's close."""
    R = t["R"]
    stop, target = round(entry - R, 4), round(entry + 2 * R, 4)
    qty_a, qty_b = t["legs"][0]["qty"], t["legs"][1]["qty"]
    b5 = ticket.to5(bars)
    sw = ticket.swings(b5)
    k = next((j for j, b in enumerate(b5) if mins(b["end"]) > mins(bars[i][0])), len(b5))  # first bar not yet closed
    exits, t1, open_qty = [], False, qty_a + qty_b
    fill_m = mins(bars[i][0])

    def out(qty, px, at, kind):
        exits.append({"qty": qty, "price": px, "at": at, "kind": kind})

    m, o, h, l, c = bars[i][:5]
    if first and l <= stop:  # the fill minute: the stop only
        out(open_qty, stop, m, "stop")
        return exits
    for m, o, h, l, c, v, vw in bars[i + 1:]:
        now, vwap_exit = mins(m), False
        while k < len(b5) and mins(b5[k]["end"]) <= now:  # 5-minute bars that closed before this minute
            if t1:
                hl = ticket.last_higher_low(sw, k)
                if hl is not None:
                    stop = max(stop, round(hl - TRAIL_PAD, 4))
                w = ticket.vwap(bars, t0, b5[k]["end"])
                vwap_exit = vwap_exit or (w is not None and b5[k]["c"] < w)
            k += 1
        if vwap_exit:
            out(open_qty, o, m, "vwap")
            return exits
        if not t1 and now >= fill_m + TIME_STOP:
            out(open_qty, o, m, "time")
            return exits
        px = _stop_px(o, l, stop)
        if px is not None:
            out(open_qty, px, m, "stop" if not t1 else "breakeven" if stop == entry else "trail")
            return exits
        if not t1 and h >= target:
            out(qty_a, target, m, "target")
            t1, open_qty, stop = True, qty_b, max(stop, entry)
    out(open_qty, c, m, "eod")
    return exits


def _improved(t, bars, i, entry, t0, first=True):
    """Two legs (long space), each with its own stop at fill - D; leg A also a target at fill + 2D; both flat at
    their exit_at minute (the open), else at the last minute's close."""
    D = t["R"]
    stop = round(entry - D, 4)
    legs = [{"qty": g["qty"], "target": None if g["target"] is None else round(entry + 2 * D, 4),
             "exit_at": g["exit_at"]} for g in t["legs"]]
    exits = []

    def out(g, px, at, kind):
        exits.append({"qty": g["qty"], "price": px, "at": at, "kind": kind})
        legs.remove(g)

    m, o, h, l, c = bars[i][:5]
    if first and l <= stop:
        for g in list(legs):
            out(g, stop, m, "stop")
        return exits
    for m, o, h, l, c, v, vw in bars[i + 1:]:
        for g in list(legs):
            if g["exit_at"] and m >= g["exit_at"]:
                out(g, o, m, "eod")
        px = _stop_px(o, l, stop)
        for g in list(legs):
            if px is not None:
                out(g, px, m, "stop")
            elif g["target"] is not None and h >= g["target"]:
                out(g, g["target"], m, "target")
        if not legs:
            return exits
    for g in list(legs):
        out(g, c, m, "eod")
    return exits


# ---------- candidate universes ----------
SHARES_MAX, SHORT_SHARES_MIN, AVG_VOL_MIN, SHORT_AVG_MIN, PRICE_MIN = 50_000_000, 0.20, 500_000, 5.0, 2.0
AVG_DAYS, REL_VOL_MIN, PRE_VOL_MIN, SPLIT_RATIO, OPEN_DAYS = 63, 2.0, 0.10, 2.0, 14


def universe_a(path):
    """The earlier backtest's picks CSV -> [(day, ticker)] in file order."""
    with open(path, newline="") as f:
        return [(r["date"], r["t"]) for r in csv.DictReader(f)]


def fixed_b(day, reports, shares, primary, daily, counts=None):
    """The current screen's fixed part, from data before `day`: {ticker: {"short", "shares", "avg_vol",
    "prev_close", "atr"}}. reports [(usable ISO, {ticker: shorts.slim row})] ascending; shares shorts.shares_index();
    primary {ticker: cik}; daily {ticker: alpaca daily bars} (any span; only bars before `day` are used). SEC shares
    outstanding stand in for the float (no free float history); listed options are not checked (no history). A
    ticker-day whose closes jump by more than 2x inside the window (a split: raw prices) is skipped and counted in
    counts["skipped_split"]."""
    j = bisect.bisect_right([u for u, _ in reports], day) - 1
    if j < 0:
        return {}
    out = {}
    for t, row in reports[j][1].items():
        cik, short = primary.get(t), row.get("si") or 0
        n = cik and shorts.shares_at(shares, cik, day)
        if not n or not n < SHARES_MAX or not short / n > SHORT_SHARES_MIN or t not in daily:
            continue
        bars = [b for b in daily[t] if b[0] < day][-AVG_DAYS:]
        if len(bars) < AVG_DAYS:
            continue
        avg, prev = sum(b[5] for b in bars) / AVG_DAYS, bars[-1][4]
        if not (avg > AVG_VOL_MIN and short / avg > SHORT_AVG_MIN and prev > PRICE_MIN):
            continue
        if any(not 1 / SPLIT_RATIO <= b[4] / a[4] <= SPLIT_RATIO for a, b in zip(bars, bars[1:]) if a[4]):
            if counts is not None:
                counts["skipped_split"] = counts.get("skipped_split", 0) + 1
            continue
        out[t] = {"short": short, "shares": n, "avg_vol": avg, "prev_close": prev, "atr": ticket.atr14(bars, day)}
    return out


def eligible_from(session, bars1, avg_vol, open_):
    """The minute a stock enters the live list (rules are evaluated only once it has closed): REGULAR - the first
    minute from the open whose cumulative regular volume is over 2x the average day; PREMARKET - the first
    pre-market minute whose cumulative pre-market volume is at least 10% of it, with a last price over $2."""
    cum = 0
    for m, o, h, l, c, v, vw in bars1:
        if (m >= open_) != (session == "REGULAR"):
            continue
        cum += v
        if session == "REGULAR" and cum / avg_vol > REL_VOL_MIN:
            return m
        if session == "PREMARKET" and cum / avg_vol >= PRE_VOL_MIN and c > PRICE_MIN:
            return m
    return None


def open_rel_vol(symbol, day, sessions):
    """The 09:30-09:35 volume of `day` / the mean 09:30-09:35 volume of the 14 sessions before it (one request of
    5-minute bars); None with fewer than 14 prior sessions that traded at the open."""
    k = bisect.bisect_left(sessions, day)
    if k < OPEN_DAYS:
        return None
    prior = sessions[k - OPEN_DAYS:k]
    first = {b[0][:10]: b[5] for b in alpaca.bars(symbol, alpaca.utc(prior[0], "09:30"), alpaca.utc(day, "09:35"),
                                                   "5Min") if b[0][11:] == "09:30"}
    vols = [first[d] for d in prior if d in first]
    if len(vols) < OPEN_DAYS or not sum(vols):
        return None
    return first.get(day, 0) / (sum(vols) / OPEN_DAYS)


# ---------- statistics and verdict ----------
RULES, SESSIONS, UNIVERSES = ("ORIGINAL", "IMPROVED"), ("REGULAR", "PREMARKET"), ("A", "B", "AB")
TUNE_END, VERDICT_START = "2024-12-31", "2025-01-01"
MIN_TRADES, MIN_T, SEEDS, PCTL = 100, 2.0, 20, 0.95
LOOKBACK = "2023-06-01"  # daily bars from here: 63 sessions + ATR before 2024-01-02
PICKS = "docs/backtest/squeeze-2026-09-27-picks.csv"
NY = ZoneInfo("America/New_York")


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def stats(trades):
    """Filled trades ({"day", "net_r", "fill", "kinds"}) -> {"n", "mean", "t", "win", "median", "years": {year: {"n",
    "mean"}}, "buckets": {"<5" | ">=5": {"n", "mean"}}, "exits": {kind: count}}."""
    xs = [x["net_r"] for x in trades]
    n = len(xs)
    out = {"n": n, "mean": _mean(xs), "t": None, "win": None, "median": None, "years": {}, "buckets": {},
           "exits": {}}
    if not n:
        return out
    sd = statistics.stdev(xs) if n > 1 else 0.0
    out.update(t=out["mean"] / (sd / n ** 0.5) if sd else None, win=sum(x > 0 for x in xs) / n,
               median=statistics.median(xs))
    groups = collections.defaultdict(list)
    for x in trades:
        groups[("y", x["day"][:4])].append(x["net_r"])
        groups[("b", "<5" if x["fill"] < 5 else ">=5")].append(x["net_r"])
    for (kind, key), v in sorted(groups.items()):
        out["years" if kind == "y" else "buckets"][key] = {"n": len(v), "mean": _mean(v)}
    out["exits"] = dict(collections.Counter(k for x in trades for k in x["kinds"].split("/") if k))
    return out


def pctl(xs, q):
    """Inclusive linear-interpolation percentile."""
    xs = sorted(xs)
    pos = q * (len(xs) - 1)
    lo = int(pos)
    return xs[lo] if lo + 1 >= len(xs) else xs[lo] + (xs[lo + 1] - xs[lo]) * (pos - lo)


def verdict(real, random_means, years=("2025", "2026")):
    """The pre-registered verdict on the verdict period's trades -> ([(check, passed)], GO?); `years`: each must have
    a positive mean."""
    st = stats(real)
    p95 = pctl(random_means, PCTL) if random_means else None
    m = st["mean"]
    names, years = " and in ".join(years), [st["years"].get(y, {}).get("mean") for y in years]
    checks = [(f"at least {MIN_TRADES} trades ({st['n']})", st["n"] >= MIN_TRADES),
              (f"mean net R > 0 with t >= {MIN_T:g}", m is not None and m > 0 and (st["t"] or 0) >= MIN_T),
              (f"mean net R above the 95th percentile of {SEEDS} random-direction means",
               m is not None and p95 is not None and m > p95),
              (f"mean net R > 0 in {names}", all(y is not None and y > 0 for y in years))]
    return checks, all(ok for _, ok in checks)


def random_means(trades, seeds=SEEDS):
    """For each seed: the mean net R with each ticker-day's direction a coin flip (random.Random per seed and
    ticker-day), the short taking the same entry moment and price."""
    out = []
    for seed in range(seeds):
        xs = [x["net_r"] if random.Random(f"{seed}|{x['day']}|{x['t']}").random() < 0.5 else x["net_r_short"]
              for x in trades]
        out.append(_mean(xs) if xs else 0.0)
    return out


# ---------- universe C: ZBA "Stocks in Play" (spec section 11) ----------
Z_TOP, Z_PRICE, Z_AVG_VOL, Z_ATR, Z_REL_VOL, Z_DAYS = 20, 5.0, 1_000_000, 0.50, 1.0, 14
Z_EXCHANGES = ("NYSE", "NASDAQ")
FUND = re.compile(r"\bETF\b|\bETN\b|\bFund\b|iShares|ProShares|Direxion|SPDR|Invesco QQQ|VanEck|WisdomTree|"
                  r"Global X|GraniteShares|Leveraged|\b\d(\.\d)?[xX]\b|\bTrust, Series\b")


def asset_universe(assets):
    """Alpaca assets -> the stock symbols ZBA's universe draws from: NYSE / NASDAQ, 1-5 capital letters, names
    without fund / ETF markers (sorted, unique)."""
    return sorted({a["symbol"] for a in assets if a.get("exchange") in Z_EXCHANGES
                   and re.fullmatch(r"[A-Z]{1,5}", a.get("symbol") or "") and not FUND.search(a.get("name") or "")})


def prefilter(day, daily, prev=None):
    """Raw daily bars before `day` -> {ticker: {"atr", "avg_vol"}} with 14-day average volume >= 1M, ATR14 > $0.50,
    no close jump over 2x in the 15-bar window, 15 bars of history and (when `prev` is given) a bar on `prev`, the
    session before."""
    out = {}
    for t, bars in daily.items():
        k = bisect.bisect_left([b[0] for b in bars], day)
        win = bars[max(0, k - Z_DAYS - 1):k]
        if len(win) < Z_DAYS + 1 or (prev and win[-1][0] != prev):
            continue
        avg = sum(b[5] for b in win[1:]) / Z_DAYS
        if avg < Z_AVG_VOL or any(not 1 / SPLIT_RATIO <= b[4] / a[4] <= SPLIT_RATIO for a, b in zip(win, win[1:]) if a[4]):
            continue
        atr = ticket.atr14(win, day)
        if atr and atr > Z_ATR:
            out[t] = {"atr": atr, "avg_vol": avg}
    return out


def prefilter_days(days, prev, bars):
    """prefilter() for one ticker over many sessions in one pass (prefix sums) -> {day: {"atr", "avg_vol"}}."""
    if len(bars) < Z_DAYS + 1:
        return {}
    dates, vol, trs, bad = [b[0] for b in bars], [0.0], [0.0], [0]
    for k, b in enumerate(bars):
        pc = bars[k - 1][4] if k else None
        vol.append(vol[-1] + b[5])
        trs.append(trs[-1] + (b[2] - b[3] if pc is None else max(b[2] - b[3], abs(b[2] - pc), abs(b[3] - pc))))
        bad.append(bad[-1] + (1 if pc and not 1 / SPLIT_RATIO <= b[4] / pc <= SPLIT_RATIO else 0))
    out = {}
    for d in days:
        k = bisect.bisect_left(dates, d)  # bars[:k] are before d; the window is bars[k - 15:k]
        if k < Z_DAYS + 1 or dates[k - 1] != prev.get(d):
            continue
        lo = k - Z_DAYS  # the 14 bars after the window's first
        avg = (vol[k] - vol[lo]) / Z_DAYS
        if avg < Z_AVG_VOL or bad[k] - bad[lo]:
            continue
        atr = (trs[k] - trs[lo]) / Z_DAYS
        if atr > Z_ATR:
            out[d] = {"atr": atr, "avg_vol": avg}
    return out


def stocks_in_play(today, prior, n=Z_TOP):
    """Opening 5-minute bars of the day {ticker: (o, h, l, c, v) or None} and of the 14 sessions before (dicts; a
    missing bar counts 0) -> the top n [(ticker, relative volume, bar)] with an open over $5 and relative volume >= 1,
    highest first; [] with fewer than 14 prior sessions."""
    if len(prior) < Z_DAYS:
        return []
    prior = prior[-Z_DAYS:]
    out = []
    for t, b in today.items():
        if not b or not b[0] > Z_PRICE:
            continue
        base = sum((p.get(t) or (0, 0, 0, 0, 0))[4] for p in prior) / Z_DAYS
        if base and b[4] / base >= Z_REL_VOL:
            out.append((t, b[4] / base, b))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out[:n]


def portfolio(trades, days, risk=0.01, cap=4.0):
    """ZBA-style book: each trade risks `risk` of capital; a day's positions are scaled down together to at most
    `cap` x gross exposure (as if all were open at once) -> {"daily", "total", "cagr", "sharpe", "max_dd", "days"}."""
    by = collections.defaultdict(list)
    for x in trades:
        by[x["day"]].append(x)
    daily = []
    for d in days:
        xs = by.get(d, [])
        gross = sum(risk * x["fill"] / x["R"] for x in xs)
        scale = min(1.0, cap / gross) if gross else 1.0
        daily.append(scale * sum(risk * x["net_r"] for x in xs))
    eq = peak = 1.0
    dd = 0.0
    for r in daily:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    n = len(daily)
    sd = statistics.stdev(daily) if n > 1 else 0.0
    return {"daily": daily, "total": eq - 1, "cagr": eq ** (252 / n) - 1 if n else None,
            "sharpe": _mean(daily) / sd * 252 ** 0.5 if sd else None, "max_dd": dd, "days": n}


# ---------- report ----------
ASSUMPTIONS = (
    "Universe A: the 1,330 out-of-sample picks of docs/backtest/squeeze-2026-09-27-picks.csv, each tested on its day "
    "in both sessions from the session start (all tickers still listed: survivorship bias).",
    "Universe B: each session, tickers in SEC's current map (survivorship bias) with FINRA short interest usable that "
    "day, SEC shares outstanding as the float (< 50M; short / shares > 20%), the mean volume of the last 63 daily "
    "bars > 500K, short / that mean > 5, previous close > $2; listed options not checked (no history).",
    "Universe B enters the list at the first minute with regular volume > 2x the average day (REGULAR) or pre-market "
    "volume >= 10% of it with a price over $2 (PREMARKET); rules act only after that minute closes.",
    "Ticker-days with a day-over-day close ratio < 0.5 or > 2 in the 63-day window are skipped (raw prices, splits).",
    "A ticker-day in both A and B counts once when pooled (B's result).",
    "5-minute bars are built from the 1-minute bars that exist and used only after they close; swings need the 2 "
    "bars after them; VWAP runs from 09:30 (REGULAR) or 04:00 (PREMARKET).",
    "ORIGINAL: the owner's rules with two fixes: the trail uses the most recent higher low; swing high / higher low "
    "use the 2-bar definition. Volume is compared with up to 10 previous bars of the session.",
    "IMPROVED: opening-range breakout; the first 1-minute high above the level decides (a stop-limit triggers once).",
    "Halts are approximated as 5+ minutes without a 1-minute bar during the regular session.",
    "Fills: a buy limit fills only when a minute's low reaches it, at the limit; a stop at the stop or at the open if "
    "gapped; stop first when a minute touches stop and target; on the fill minute a stop counts only when its side of "
    "the minute comes after the fill (bar path open-low-high-close if the minute closes at or above its open, else "
    "open-high-low-close).",
    "Stops act on pre-market minutes too (the intended protection; a plain IBKR STP would not trigger outside RTH).",
    "Costs: $0.0035 a share, $0.35 minimum per order, each side; half the SIP spread at the signal in and out; a "
    "locked quote (bid = ask) counts as a $0.01 spread.",
    "Risk $100 a trade; fewer than 10 shares is NO TICKET; results in R = P&L / (R x shares).",
    "Random benchmark: the same entries with a coin-flip direction (20 seeds), shorts mirrored around the fill; the "
    "short takes the long's conservative limit price, so it is credited what the long is charged.",
    "Universe B's average volume comes from Alpaca daily bars, which include extended hours (~14% more than the "
    "regular session), so its REGULAR entry bar of 2x is ~2.3x of regular-session volume.",
    "Periods: tuning 2024; verdict 2025-01-01 to the last complete session. GO needs >= 100 trades, mean net R > 0 "
    "with t >= 2, above the 95th percentile of the random means, and > 0 in 2025 and in 2026.",
    "Verdict basis: universe B; A and B pooled when B has fewer than 100 verdict trades.",
)
HE_RULES = {"ORIGINAL": "הכללים המקוריים", "IMPROVED": "הכללים המשופרים"}
HE_SESSIONS = {"REGULAR": "מסחר רגיל", "PREMARKET": "טרום מסחר"}


def _f(x, fmt="{:+.3f}"):
    return "—" if x is None else fmt.format(x)


def render(meta, results):
    """The Markdown report: a Hebrew summary (every line starts in Hebrew), the tables, the assumptions, the data."""
    L = [f"# Order-ticket backtest — {meta['run']}", "", "## סיכום", ""]
    for (r, s), res in results.items():
        st = res["by"][res["basis"]]["verdict"]
        L.append(f"- {HE_RULES[r]} · {HE_SESSIONS[s]}: {'GO ✅' if res['go'] else 'NO-GO ❌'} — עסקאות "
                 f"`{st['n']}`, ממוצע `{_f(st['mean'])}R`, סטטיסטי t `{_f(st['t'], '{:.2f}')}` (בסיס `{res['basis']}`)")
    L += ["", "תקופת הכוונון היא 2024 ותקופת ההכרעה מ־2025; כל ההנחות מפורטות למטה.", "",
          "## Verdict checks (verdict period)", "", "| Rules · session | Basis | Check | Pass |", "|---|---|---|---|"]
    for (r, s), res in results.items():
        L += [f"| {r} · {s} | {res['basis']} | {c} | {'pass' if ok else 'fail'} |" for c, ok in res["checks"]]
    L += ["", "## Results", "",
          "| Rules · session | Universe | Period | Tickets | Trades | Mean net R | t | Win | Median | Random p95 | "
          "2024 | 2025 | 2026 | <$5 | ≥$5 |", "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for (r, s), res in results.items():
        for u, b in res["by"].items():
            for period in ("tune", "verdict"):
                st = b[period]
                y = {k: _f(st["years"].get(k, {}).get("mean")) for k in ("2024", "2025", "2026")}
                bk = {k: _f(st["buckets"].get(k, {}).get("mean")) for k in ("<5", ">=5")}
                L.append(f"| {r} · {s} | {u} | {period} | {b['tickets'] if period == 'verdict' else ''} | {st['n']} | "
                         f"{_f(st['mean'])} | {_f(st['t'], '{:.2f}')} | {_f(st['win'], '{:.0%}')} | "
                         f"{_f(st['median'])} | {_f(b['p95']) if period == 'verdict' else ''} | {y['2024']} | "
                         f"{y['2025']} | {y['2026']} | {bk['<5']} | {bk['>=5']} |")
    L += ["", "## NO TICKET reasons and exits (whole range)", "", "| Rules · session | Universe | Tickets | Filled | "
          "NO TICKET reasons | Exits |", "|---|---|---:|---:|---|---|"]
    for (r, s), res in results.items():
        for u, b in res["by"].items():
            exits = collections.Counter(b["verdict"]["exits"]) + collections.Counter(b["tune"]["exits"])
            L.append(f"| {r} · {s} | {u} | {b['tickets']} | {b['fills']} | "
                     f"{', '.join(f'{k} {v}' for k, v in sorted(b['reasons'].items(), key=lambda kv: -kv[1]))} | "
                     f"{', '.join(f'{k} {v}' for k, v in sorted(exits.items()))} |")
    L += ["", "## Assumptions", ""] + [f"- {a}" for a in ASSUMPTIONS]
    L += ["", "## Data", "", f"- Range {meta['start']} … {meta['end']} · universes {meta['universes']} · "
          f"runtime {meta['runtime']:.0f}s · Alpaca requests {meta['requests']}"]
    L += [f"- {k}: {v}" for k, v in {**meta["counts"], **meta["gaps"]}.items()]
    L += ["", "`python -m bot.ticket_backtest` · data: Alpaca SIP (Basic plan), FINRA short interest, SEC shares", ""]
    return "\n".join(L)


# ---------- run ----------
def load_b(sessions):
    """FINRA reports usable per session, the SEC shares index and {ticker: cik} (the squeeze backtest's loaders)."""
    primary = {t: cik for cik, t in common.cik_tickers().items()}
    dates = [d for d in shorts.settlement_dates() if d >= LOOKBACK]
    raw = backtest.load_reports(dates)
    reports = [(u, shorts.remap(raw[d], primary)) for d in dates if raw.get(d) and (u := shorts.usable_from(d, sessions))]
    shares = shorts.shares_index(backtest.load_frames(int(LOOKBACK[:4]) - 1), backtest.load_filings(int(LOOKBACK[:4]) - 1))
    return reports, shares, primary


def _gate_tickers(days, reports, shares, primary):
    """Tickers that pass the short / shares gates on any of `days` (they need daily bars)."""
    usable, out = [u for u, _ in reports], set()
    for day in days:
        j = bisect.bisect_right(usable, day) - 1
        if j < 0:
            continue
        for t, row in reports[j][1].items():
            short, cik = row.get("si") or 0, primary.get(t)
            if cik and short > SHORT_AVG_MIN * AVG_VOL_MIN and t not in out:
                n = shorts.shares_at(shares, cik, day)
                if n and n < SHARES_MAX and short / n > SHORT_SHARES_MIN:
                    out.add(t)
    return out


TICK = 0.01  # a locked quote (bid = ask) still costs a tick


def _spread(t, day):
    def get(hhmm):
        q = alpaca.quote_at(t, alpaca.utc(day, hhmm))
        return None if q is None else round(max(q[1] - q[0], TICK), 4)
    return get


def _play(rules, session, bars, ctx, spread, day, t, universe):
    """One rule set on one ticker-session -> (NO TICKET reason, None) or (None, trade row)."""
    tk = ticket.signal(rules, session, bars, ctx, spread)
    if not tk["ok"]:
        return tk["reason"], None
    res = simulate(tk, bars, ctx)
    short = simulate(tk, bars, ctx, -1) if res["filled"] else res
    return None, {"universe": universe, "day": day, "t": t, "rules": rules, "session": session, "side": tk.get("side", 1),
                  **{k: tk[k] for k in ("at", "level", "entry_type", "entry", "limit", "stop", "R", "shares", "spread")},
                  "filled": res["filled"], "fill_at": res["fill_at"], "fill": res["fill"],
                  "kinds": "/".join(e["kind"] for e in res["exits"]), "gross_r": res["gross_r"],
                  "cost_r": res["cost_r"], "net_r": res["net_r"], "net_r_short": short["net_r"]}


def _day(day, hours, a_tickers, b_rows, daily, sessions, gaps, variants=None):
    """Every rule-set variant x session x universe on one session -> (trade rows, Counter of (universe, label,
    session, reason)). variants: [(label, rules, extra ctx)], by default the two rule sets as they are."""
    variants = variants or [(r, r, {}) for r in RULES]
    open_, close = hours
    syms = sorted(set(a_tickers) | set(b_rows))
    if not syms:
        return [], collections.Counter()
    mb = alpaca.minute_bars(syms, day)
    rows, reasons, orv = [], collections.Counter(), {}
    for universe, tickers in (("A", a_tickers), ("B", sorted(b_rows))):
        for t in tickers:
            bars = mb.get(t) or []
            if not bars:
                gaps["ticker-days without minute bars"] += 1
                continue
            row = b_rows.get(t) if universe == "B" else None
            atr = row["atr"] if row else ticket.atr14(daily.get(t, []), day)
            spread = _spread(t, day)
            for session in SESSIONS:
                start = open_ if session == "REGULAR" else ticket.PRE_OPEN
                if row:
                    start = eligible_from(session, bars, row["avg_vol"], open_)
                    if start is None:
                        reasons[(universe, None, session, "not in the list")] += 1
                        continue
                for label, rules, extra in variants:
                    ctx = {"day": day, "open": open_, "close": close, "atr": atr, "open_rel_vol": None, "from": start,
                           **extra}
                    if rules == "IMPROVED" and session == "REGULAR" and atr:
                        if t not in orv:
                            orv[t] = open_rel_vol(t, day, sessions)
                        ctx["open_rel_vol"] = orv[t]
                    reason, trade = _play(rules, session, bars, ctx, spread, day, t, universe)
                    if trade:
                        rows.append({**trade, "rules": label})
                    else:
                        reasons[(universe, label, session, reason)] += 1
    return rows, reasons


def _results(rows, reasons, labels=RULES):
    """Trade rows + NO TICKET counts -> {(label, session): {"basis", "checks", "go", "by": {universe: {...}}}}."""
    out = {}
    for r in labels:
        for s in SESSIONS:
            mine = [x for x in rows if x["rules"] == r and x["session"] == s]
            pooled = {(x["day"], x["t"]): x for x in sorted(mine, key=lambda x: x["universe"])}  # B over A
            by = {}
            for u, xs in (("A", [x for x in mine if x["universe"] == "A"]),
                          ("B", [x for x in mine if x["universe"] == "B"]), ("AB", list(pooled.values()))):
                filled = [x for x in xs if x["filled"]]
                tune = [x for x in filled if x["day"] <= TUNE_END]
                ver = [x for x in filled if x["day"] >= VERDICT_START]
                rm = random_means(ver)
                why = collections.Counter()
                for (uu, rr, ss, reason), n in reasons.items():
                    if ss == s and (rr == r or rr is None) and (uu == u or u == "AB"):
                        why[reason] += n
                by[u] = {"tune": stats(tune), "verdict": stats(ver), "random": rm, "p95": pctl(rm, PCTL) if ver else None,
                         "tickets": len(xs), "fills": len(filled), "reasons": dict(why), "_ver": ver}
            basis = "B" if by["B"]["verdict"]["n"] >= MIN_TRADES else "AB"
            checks, go = verdict(by[basis]["_ver"], by[basis]["random"])
            for b in by.values():
                del b["_ver"]
            out[(r, s)] = {"basis": basis, "checks": checks, "go": go, "by": by}
    return out


# ---------- second look: the IMPROVED spread gate (spec section 10) ----------
GRID = {"G0": 0.10, "G1": 0.25, "G2": 0.50, "G3": None}  # spread bound as a fraction of D (always <= 1% of price)
TUNE_MIN = 30  # trades in 2024 a gate needs to be selectable


def select(tune):
    """{gate: 2024 stats (A u B)} -> (the gate with the highest mean among those with >= TUNE_MIN trades, False), or
    ("G0", True) when none has enough trades (underpowered)."""
    ok = [(st["mean"], g) for g, st in tune.items() if st["n"] >= TUNE_MIN and st["mean"] is not None]
    return (max(ok)[1], False) if ok else ("G0", True)


def render_tune(meta, results):
    """The second-look report: per session, every gate's 2024 and verdict-period numbers, the pre-registered choice
    and its verdict."""
    L = [f"# Order-ticket backtest, second look: the IMPROVED spread gate — {meta['run']}", "", "## סיכום", ""]
    picks = {}
    for s in SESSIONS:
        picks[s] = select({g: results[(f"IMPROVED {g}", s)]["by"]["AB"]["tune"] for g in GRID})
        g, weak = picks[s]
        res = results[(f"IMPROVED {g}", s)]
        st = res["by"][res["basis"]]["verdict"]
        L.append(f"- {HE_SESSIONS[s]}: נבחר `{g}`{' (כוונון חלש: אין שער עם 30 עסקאות ב־2024)' if weak else ''} — "
                 f"{'GO ✅' if res['go'] else 'NO-GO ❌'}, עסקאות `{st['n']}`, ממוצע `{_f(st['mean'])}R`, "
                 f"סטטיסטי t `{_f(st['t'], '{:.2f}')}`")
    L += ["", "זהו מבט שני: תוצאות 2025–2026 של השער המקורי כבר נראו, ולכן GO כאן הוא סיבה למסחר נייר קדימה, "
          "לא למסחר אמיתי.", "", "## Method", "",
          "This is a second look (spec section 10): G0's 2025-2026 results were already seen, so this is not a clean "
          "out-of-sample test.", "",
          "Gates (spread always ≤ 1% of the price): " + ", ".join(f"{g} ≤ {v} × D" if v else f"{g} no D bound"
                                                                 for g, v in GRID.items()) + ".", "",
          "## Gates", "", "| Session | Gate | 2024 trades | 2024 mean | 2024 t | Verdict trades | Mean net R | t | "
          "Random p95 | 2025 | 2026 | Verdict |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for s in SESSIONS:
        for g in GRID:
            res = results[(f"IMPROVED {g}", s)]
            tu, b = res["by"]["AB"]["tune"], res["by"][res["basis"]]
            v = b["verdict"]
            mark = " ← chosen" if picks[s][0] == g else ""
            L.append(f"| {s} | {g}{mark} | {tu['n']} | {_f(tu['mean'])} | {_f(tu['t'], '{:.2f}')} | {v['n']} | "
                     f"{_f(v['mean'])} | {_f(v['t'], '{:.2f}')} | {_f(b['p95'])} | "
                     f"{_f(v['years'].get('2025', {}).get('mean'))} | {_f(v['years'].get('2026', {}).get('mean'))} | "
                     f"{'GO' if res['go'] else 'NO-GO'} ({res['basis']}) |")
    L += ["", "## Verdict checks of the chosen gates", "", "| Session | Gate | Check | Pass |", "|---|---|---|---|"]
    for s in SESSIONS:
        L += [f"| {s} | {picks[s][0]} | {c} | {'pass' if ok else 'fail'} |"
              for c, ok in results[(f"IMPROVED {picks[s][0]}", s)]["checks"]]
    L += ["", "## Assumptions", "", "- Everything else as in the first run's report (`ticket-2026-10-09.md`):"]
    L += [f"  - {a}" for a in ASSUMPTIONS]
    L += ["", "## Data", "", f"- Range {meta['start']} … {meta['end']} · universes {meta['universes']} · "
          f"runtime {meta['runtime']:.0f}s · Alpaca requests {meta['requests']}"]
    L += [f"- {k}: {v}" for k, v in {**meta["counts"], **meta["gaps"]}.items()]
    L += ["", "`python -m bot.ticket_backtest --tune`", ""]
    return "\n".join(L)


COLUMNS = ("universe", "day", "t", "rules", "session", "at", "level", "entry_type", "entry", "limit", "stop", "R",
           "shares", "spread", "filled", "fill_at", "fill", "kinds", "gross_r", "cost_r", "net_r", "net_r_short")


# ---------- the ZBA run (spec section 11) ----------
ZBA_START, ZBA_VERDICT, ZBA_YEARS = "2022-01-03", "2024-01-02", ("2024", "2025", "2026")
ZBA_GRID = {"K10": (0.10, None), "K25": (0.25, None), "K50": (0.50, None), "K100": (1.00, None),
            "K10s": (0.10, 0.25), "K25s": (0.25, 0.25), "K50s": (0.50, 0.25), "K100s": (1.00, 0.25)}  # spec section 12
ZBA_BASE, ZBA_TUNE_MIN, ZBA_TUNED_START = "K10", 300, "2024-02-01"
ZBA_COLUMNS = ("variant", "day", "t", "rank", "rel_vol", "side", "at", "level", "R", "shares", "spread", "filled", "fill_at",
               "fill", "kinds", "gross_r", "cost_r", "net_r", "net_r_short")
ZBA_ASSUMPTIONS = (
    "Universe: Alpaca assets on NYSE / NASDAQ, active and inactive (delisted names count), symbols of 1-5 capital "
    "letters, names without fund / ETF markers.",
    "Each session: 14-day average volume >= 1,000,000 and ATR14 > $0.50 from raw daily bars before the day, a bar on "
    "the session before, no close jump over 2x in the 15-bar window; open of the 09:30-09:35 bar > $5.",
    "Relative volume = the 09:30-09:35 volume / its mean over the 14 previous sessions (a missing bar counts 0); "
    ">= 1.0; the top 20 are traded.",
    "Side from the first 5-minute candle (close over open: buy stop at its high; under: sell stop at its low; equal: "
    "none); the stop order stays until the close and fills when a minute trades at it (at the stop, or the open if "
    "it gapped through).",
    "Stop loss 10% of ATR14 from the fill; no target; out at the last minute's close. One trade per ticker-day.",
    "Costs: $0.0035 a share, $0.35 minimum per order, each side; half the SIP spread at the trigger in and out (a "
    "locked quote counts $0.01; no quote: no trade). Stops fill at the stop or the gapped open; on the fill minute a "
    "stop counts only when its side of the minute comes after the fill (bar path).",
    "Random benchmark: the same fills with a coin-flip side (20 seeds).",
    "Verdict (pre-registered, 2024-01-02 onward, never seen for this universe): >= 100 trades, mean net R > 0 with "
    "t >= 2, above the random 95th percentile, mean net R > 0 in 2024, in 2025 and in 2026.",
    "Replication 2022-2023 (inside the paper's 2016-2023 sample): reported, not gated.",
    "Portfolio: each trade risks 1% of capital; a day's positions are scaled down together to at most 4x gross "
    "exposure, as if all were open at once.",
    "Section 12 grid: stop k x ATR14 (k 0.10 / 0.25 / 0.50 / 1.00), each with and without a spread gate (spread <= "
    "0.25 x the stop distance); chosen on 2022-2023 (>= 300 trades, highest mean); verdict on 2024-02-01 onward.",
)


def select_zba(tuning):
    """{variant: stats on 2022-2023} -> the variant with the highest mean among those with >= 300 trades, or None."""
    ok = [(st["mean"], v) for v, st in tuning.items() if st["n"] >= ZBA_TUNE_MIN and st["mean"] is not None]
    return max(ok)[1] if ok else None


def robustness(xs):
    """Not gated: the mean net R and t without the biggest 1% of trades, with extra slippage of $0.01 / $0.02 a share
    on each side, and the mean and count by quarter."""
    def mt(v):
        sd = statistics.stdev(v) if len(v) > 1 else 0.0
        return _mean(v), (_mean(v) / (sd / len(v) ** 0.5) if sd else None)
    net = sorted(x["net_r"] for x in xs)
    k = max(1, len(net) // 100)
    q = collections.defaultdict(list)
    for x in xs:
        q[f"{x['day'][:4]}Q{(int(x['day'][5:7]) - 1) // 3 + 1}"].append(x["net_r"])
    return {"drop top 1%": mt(net[:-k]),
            **{f"+${c:.2f} a share each side": mt([x["net_r"] - 2 * c / x["R"] for x in xs]) for c in (0.01, 0.02)},
            "quarters": {k_: (_mean(v), len(v)) for k_, v in sorted(q.items())}}


def _tuned(rows, variant, days):
    """The selected variant's verdict on 2024-02-01 onward (spec section 12)."""
    xs = [x for x in rows if x["variant"] == variant and x["filled"] and x["day"] >= ZBA_TUNED_START]
    rm = random_means(xs)
    checks, go = verdict(xs, rm, years=ZBA_YEARS)
    return {"variant": variant, "all": stats(xs), "long": stats([x for x in xs if x["side"] == 1]),
            "short": stats([x for x in xs if x["side"] == -1]), "p95": pctl(rm, PCTL) if xs else None,
            "book": portfolio(xs, [d for d in days if d >= ZBA_TUNED_START]), "checks": checks, "go": go,
            "robust": robustness(xs) if len(xs) > 1 else None}


def _zba_results(rows, days):
    """Trade rows -> {"replication" | "verdict": {"all", "long", "short", "random", "p95", "book"}} + the verdict."""
    filled = [x for x in rows if x["filled"]]
    out = {}
    for name, pick in (("replication", lambda d: d < ZBA_VERDICT), ("verdict", lambda d: d >= ZBA_VERDICT)):
        xs = [x for x in filled if pick(x["day"])]
        rm = random_means(xs)
        out[name] = {"all": stats(xs), "long": stats([x for x in xs if x["side"] == 1]),
                     "short": stats([x for x in xs if x["side"] == -1]), "random": rm,
                     "p95": pctl(rm, PCTL) if xs else None, "book": portfolio(xs, [d for d in days if pick(d)]),
                     "tickets": sum(1 for x in rows if pick(x["day"])), "_xs": xs}
    out["checks"], out["go"] = verdict(out["verdict"]["_xs"], out["verdict"]["random"], years=ZBA_YEARS)
    for name in ("replication", "verdict"):
        del out[name]["_xs"]
    return out


def render_zba(meta, res, grid=None, tuned=None):
    """The ZBA report: a Hebrew summary, the verdict checks, both periods by side, the book, the section-12 grid and
    the tuned variant's verdict, the assumptions."""
    v, r = res["verdict"], res["replication"]
    if grid is not None:
        tv = tuned["all"] if tuned else None
        line = (f"- גרסה מכוילת (סטופ רחב יותר, נבחרה על 2022–2023): `{tuned['variant']}` — "
                f"{'GO ✅' if tuned['go'] else 'NO-GO ❌'} על 2024-02-01 ואילך, עסקאות `{tv['n']}`, ממוצע נטו "
                f"`{_f(tv['mean'])}R`, סטטיסטי t `{_f(tv['t'], '{:.2f}')}`, תיק: שארפ "
                f"`{_f(tuned['book']['sharpe'], '{:.2f}')}`") if tuned else \
            "- גרסה מכוילת: אף גרסה לא הגיעה ל־300 עסקאות ב־2022–2023, ולכן אין בחירה (NO-GO)."
    L = [f"# ZBA 2024 \"Stocks in Play\" 5-minute ORB on 2024-2026 — {meta['run']}", "", "## סיכום", "",
         f"- הכרעה על {meta['verdict_range']}: {'GO ✅' if res['go'] else 'NO-GO ❌'} — עסקאות `{v['all']['n']}`, "
         f"ממוצע נטו `{_f(v['all']['mean'])}R`, סטטיסטי t `{_f(v['all']['t'], '{:.2f}')}`, תיק: תשואה שנתית "
         f"`{_f(v['book']['cagr'], '{:+.1%}')}`, שארפ `{_f(v['book']['sharpe'], '{:.2f}')}`",
         f"- שחזור על {meta['replication_range']} (בתוך תקופת המחקר): עסקאות `{r['all']['n']}`, ממוצע נטו "
         f"`{_f(r['all']['mean'])}R`, תיק: תשואה שנתית `{_f(r['book']['cagr'], '{:+.1%}')}`, שארפ "
         f"`{_f(r['book']['sharpe'], '{:.2f}')}`",
         *([line] if grid is not None else []),
         "", "הפרמטרים קבועים מהמאמר, בלי כוונון; כלל ההכרעה נרשם לפני ההרצה.", "",
         "## Verdict checks", "", "| Check | Pass |", "|---|---|"]
    L += [f"| {c} | {'pass' if ok else 'fail'} |" for c, ok in res["checks"]]
    L += ["", "## Results (net of costs)", "", "| Period | Side | Trades | Mean net R | t | Win | Median | Random p95 | "
          "2022 | 2023 | 2024 | 2025 | 2026 | <$5 | ≥$5 |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, b in (("replication", r), ("verdict", v)):
        for side in ("all", "long", "short"):
            st = b[side]
            y = {k: _f(st["years"].get(k, {}).get("mean")) for k in ("2022", "2023", "2024", "2025", "2026")}
            bk = {k: _f(st["buckets"].get(k, {}).get("mean")) for k in ("<5", ">=5")}
            L.append(f"| {name} | {side} | {st['n']} | {_f(st['mean'])} | {_f(st['t'], '{:.2f}')} | "
                     f"{_f(st['win'], '{:.0%}')} | {_f(st['median'])} | {_f(b['p95']) if side == 'all' else ''} | "
                     f"{y['2022']} | {y['2023']} | {y['2024']} | {y['2025']} | {y['2026']} | {bk['<5']} | {bk['>=5']} |")
    L += ["", "## Portfolio (1% risk a trade, at most 4x gross, after costs)", "",
          "| Period | Sessions | Total | Annual | Sharpe | Worst drawdown |", "|---|---:|---:|---:|---:|---:|"]
    for name, b in (("replication", r), ("verdict", v)):
        k = b["book"]
        L.append(f"| {name} | {k['days']} | {_f(k['total'], '{:+.1%}')} | {_f(k['cagr'], '{:+.1%}')} | "
                 f"{_f(k['sharpe'], '{:.2f}')} | {_f(k['max_dd'], '{:.1%}')} |")
    L += ["", "Paper (2016-2023, no spread or slippage): total 1,637%, IRR 41.6%, Sharpe 2.81, hit ratio 48.4%, "
          "max drawdown 12%.", ""]
    if grid is not None:
        L += ["## Cost-aware grid (spec section 12)", "", "Selected on 2022-2023 (≥ 300 trades, highest mean); "
              "verdict on 2024-02-01 onward (January 2024 was seen in a probe).", "",
              "| Variant | Stop × ATR | Spread gate | 2022-23 trades | 2022-23 mean | 2024+ trades | 2024+ mean | "
              "2024+ t | Cost R |", "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
        for name, g in grid.items():
            k, sd = ZBA_GRID[name]
            rp, vp = g["replication"]["all"], g["verdict"]["all"]
            mark = " ← chosen" if tuned and tuned["variant"] == name else ""
            L.append(f"| {name}{mark} | {k:g} | {'≤ ' + format(sd, 'g') + ' × stop' if sd else '—'} | {rp['n']} | "
                     f"{_f(rp['mean'])} | {vp['n']} | {_f(vp['mean'])} | {_f(vp['t'], '{:.2f}')} | "
                     f"{_f(g['cost'], '{:.3f}')} |")
        if tuned:
            L += ["", f"### {tuned['variant']} verdict (2024-02-01 onward)", "", "| Check | Pass |", "|---|---|"]
            L += [f"| {c} | {'pass' if ok else 'fail'} |" for c, ok in tuned["checks"]]
            k = tuned["book"]
            L += ["", f"Long {tuned['long']['n']} trades {_f(tuned['long']['mean'])}R · short {tuned['short']['n']} "
                  f"trades {_f(tuned['short']['mean'])}R · random p95 {_f(tuned['p95'])} · book: total "
                  f"{_f(k['total'], '{:+.1%}')}, annual {_f(k['cagr'], '{:+.1%}')}, Sharpe {_f(k['sharpe'], '{:.2f}')}, "
                  f"worst drawdown {_f(k['max_dd'], '{:.1%}')}", ""]
            rb = tuned["robust"]
            if rb:
                L += ["Robustness (not gated):", "", "| Test | Mean net R | t |", "|---|---:|---:|"]
                L += [f"| {name} | {_f(m)} | {_f(t, '{:.2f}')} |" for name, (m, t) in rb.items() if name != "quarters"]
                L += ["", "By quarter: " + " · ".join(f"{q} {_f(m)} ({n})" for q, (m, n) in rb["quarters"].items()), ""]
    L += ["## Exits and NO TICKET reasons", "",
          f"- Exits (verdict period): {', '.join(f'{k} {n}' for k, n in sorted(v['all']['exits'].items()))}",
          f"- NO TICKET: {', '.join(f'{k} {n}' for k, n in sorted(meta['reasons'].items(), key=lambda kv: -kv[1]))}",
          "", "## Assumptions", ""] + [f"- {a}" for a in ZBA_ASSUMPTIONS]
    L += ["", "## Data", "", f"- Range {meta['start']} … {meta['end']} · runtime {meta['runtime']:.0f}s · Alpaca "
          f"requests {meta['requests']}"]
    L += [f"- {k}: {n}" for k, n in {**meta["counts"], **meta["gaps"]}.items()]
    L += ["", "`python -m bot.ticket_backtest --zba` · data: Alpaca SIP (Basic plan)", ""]
    return "\n".join(L)


def _zba_main(a, t0, run):
    today = dt.datetime.now(NY).date().isoformat()
    start = a.start or ZBA_START
    look = (dt.date.fromisoformat(start) - dt.timedelta(60)).isoformat()
    hours = alpaca.calendar(look, a.end or today)
    sessions = sorted(hours)
    end = a.end or max(d for d in sessions if d < today)
    days = [d for d in sessions if start <= d <= end]
    prev = {d: sessions[k - 1] for k, d in enumerate(sessions) if k}
    syms = asset_universe(alpaca.assets())
    gaps, counts, reasons = collections.Counter(), collections.Counter(), collections.Counter()
    pre = collections.defaultdict(dict)  # day -> {ticker: {"atr", "avg_vol"}}
    for i in range(0, len(syms), 100):
        for t, bars in alpaca.daily(syms[i:i + 100], look, end).items():
            for d, info in prefilter_days(days, prev, bars).items():
                pre[d][t] = info
        if i % 2000 == 0:
            print(f"daily bars {i}/{len(syms)}, {alpaca.REQUESTS[0]} requests ({time.time() - t0:.0f}s)", flush=True)
    counts["symbols"] = len(syms)
    counts["mean prefiltered a session"] = round(_mean([len(pre[d]) for d in days]) or 0)
    k0 = sessions.index(days[0])
    span = sessions[max(0, k0 - Z_DAYS):sessions.index(days[-1]) + 1]
    need = collections.defaultdict(set)
    for k, d in enumerate(span):
        if d >= days[0]:
            for s_ in span[max(0, k - Z_DAYS):k + 1]:
                need[s_] |= set(pre[d])
    window, rows = collections.deque(maxlen=Z_DAYS), []
    for k, d in enumerate(span):
        ob = alpaca.opening_bars(sorted(need[d]), d) if need[d] else {}
        if d >= days[0] and len(window) == Z_DAYS:
            top = stocks_in_play({t: ob.get(t) for t in pre[d]}, list(window))
            counts["top ticker-days"] += len(top)
            mb = alpaca.minute_bars([t for t, _, _ in top], d) if top else {}
            for rank, (t, rv, _) in enumerate(top, 1):
                bars = mb.get(t) or []
                if not bars:
                    gaps["ticker-days without minute bars"] += 1
                    continue
                spread = _spread(t, d)
                for variant, (k_atr, sd) in ZBA_GRID.items():
                    ctx = {"day": d, "open": hours[d][0], "close": hours[d][1], "atr": pre[d][t]["atr"],
                           "open_rel_vol": rv, "from": hours[d][0], "stop_atr": k_atr, "spread_d": sd}
                    reason, trade = _play("ZBA", "REGULAR", bars, ctx, spread, d, t, "C")
                    if trade:
                        rows.append({**trade, "rank": rank, "rel_vol": rv, "variant": variant})
                    elif variant == ZBA_BASE:
                        reasons[reason] += 1
        window.append(ob)
        if k % 20 == 0 or k == len(span) - 1:
            print(f"{d}: {len(rows)} tickets, {alpaca.REQUESTS[0]} requests ({time.time() - t0:.0f}s)", flush=True)
    grid = {}
    for variant in ZBA_GRID:
        mine = [x for x in rows if x["variant"] == variant]
        grid[variant] = {**_zba_results(mine, days),
                         "cost": _mean([x["cost_r"] for x in mine if x["filled"] and x["day"] >= ZBA_VERDICT])}
    res = grid[ZBA_BASE]
    pick = select_zba({v: g["replication"]["all"] for v, g in grid.items()})
    tuned = _tuned(rows, pick, days) if pick else None
    meta = {"run": run, "start": start, "end": end, "runtime": time.time() - t0, "requests": alpaca.REQUESTS[0],
            "gaps": dict(gaps), "counts": dict(counts), "reasons": dict(reasons),
            "verdict_range": f"{max(start, ZBA_VERDICT)} → {end}", "replication_range": f"{start} → 2023-12-29"}
    os.makedirs(a.out, exist_ok=True)
    md, out_csv = os.path.join(a.out, f"ticket-zba-{run}.md"), os.path.join(a.out, f"ticket-zba-{run}-trades.csv")
    with open(md, "w") as f:
        f.write(render_zba(meta, res, grid, tuned))
    keep = [x for x in rows if (x["variant"] == ZBA_BASE and x["day"] >= ZBA_VERDICT)
            or (tuned and x["variant"] == pick != ZBA_BASE and x["day"] >= ZBA_TUNED_START)]
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(ZBA_COLUMNS)
        for x in keep:
            w.writerow([round(x[c], 4) if isinstance(x[c], float) else x[c] for c in ZBA_COLUMNS])
    st = res["verdict"]["all"]
    print(f"ZBA K10: {'GO' if res['go'] else 'NO-GO'} ({st['n']} trades, mean {_f(st['mean'])}R, "
          f"t {_f(st['t'], '{:.2f}')})")
    if tuned:
        print(f"ZBA {pick}: {'GO' if tuned['go'] else 'NO-GO'} ({tuned['all']['n']} trades, "
              f"mean {_f(tuned['all']['mean'])}R, t {_f(tuned['all']['t'], '{:.2f}')})")
    print(f"wrote {md} and {out_csv} ({time.time() - t0:.0f}s)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order-ticket backtest -> docs/backtest/ticket-<date>.md")
    ap.add_argument("--start", help="first session (default 2024-01-02; --zba: 2022-01-03)")
    ap.add_argument("--end", help="last session (default: the last complete one)")
    ap.add_argument("--universe", default="AB", choices=("A", "B", "AB"))
    ap.add_argument("--picks", default=PICKS)
    ap.add_argument("--out", default="docs/backtest")
    ap.add_argument("--tune", action="store_true", help="the IMPROVED spread-gate grid (spec section 10)")
    ap.add_argument("--zba", action="store_true", help='ZBA "Stocks in Play" replication (spec section 11)')
    a = ap.parse_args(argv)
    if a.zba:
        return _zba_main(a, time.time(), dt.date.today().isoformat())
    a.start = a.start or "2024-01-02"
    variants = [(f"IMPROVED {g}", "IMPROVED", {"spread_d": v}) for g, v in GRID.items()] if a.tune else None
    labels = [v[0] for v in variants] if variants else RULES
    name = "ticket-tune" if a.tune else "ticket"
    t0, run = time.time(), dt.date.today().isoformat()
    today = dt.datetime.now(NY).date().isoformat()
    hours = alpaca.calendar(LOOKBACK, a.end or today)
    sessions = sorted(hours)
    end = a.end or max(d for d in sessions if d < today)
    days = [d for d in sessions if a.start <= d <= end]
    gaps, counts = collections.Counter(), collections.Counter()
    picks = collections.defaultdict(list)
    if "A" in a.universe:
        for d, t in universe_a(a.picks):
            if a.start <= d <= end:
                picks[d].append(t)
    counts["A ticker-days"] = sum(map(len, picks.values()))
    reports = shares = primary = None
    gate = set()
    if "B" in a.universe:
        reports, shares, primary = load_b(sessions)
        gate = _gate_tickers(days, reports, shares, primary)
    a_syms = sorted({t for ts in picks.values() for t in ts})
    daily = alpaca.daily(sorted(gate | set(a_syms)), LOOKBACK, end)
    print(f"{len(days)} sessions, {counts['A ticker-days']} A ticker-days, {len(gate)} B tickers "
          f"({time.time() - t0:.0f}s)", flush=True)
    rows, reasons = [], collections.Counter()
    for k, day in enumerate(days):
        b_rows = fixed_b(day, reports, shares, primary, daily, counts) if reports is not None else {}
        counts["B ticker-days"] += len(b_rows)
        got, why = _day(day, hours[day], picks.get(day, []), b_rows, daily, sessions, gaps, variants)
        rows += got
        reasons += why
        if k % 20 == 0 or k == len(days) - 1:
            print(f"{day}: {len(rows)} tickets, {alpaca.REQUESTS[0]} requests ({time.time() - t0:.0f}s)", flush=True)
    results = _results(rows, reasons, labels)
    meta = {"run": run, "start": a.start, "end": end, "universes": a.universe, "runtime": time.time() - t0,
            "requests": alpaca.REQUESTS[0], "gaps": dict(gaps), "counts": dict(counts)}
    os.makedirs(a.out, exist_ok=True)
    md, out_csv = os.path.join(a.out, f"{name}-{run}.md"), os.path.join(a.out, f"{name}-{run}-trades.csv")
    with open(md, "w") as f:
        f.write(render_tune(meta, results) if a.tune else render(meta, results))
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for x in rows:
            w.writerow([round(x[c], 4) if isinstance(x[c], float) else x[c] for c in COLUMNS])
    for (r, s), res in results.items():
        st = res["by"][res["basis"]]["verdict"]
        print(f"{r} {s}: {'GO' if res['go'] else 'NO-GO'} (basis {res['basis']}, {st['n']} trades, "
              f"mean {_f(st['mean'])}R)")
    print(f"wrote {md} and {out_csv} ({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
