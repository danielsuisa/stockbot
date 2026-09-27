"""Short-squeeze screener: features, eligibility, scoring and outcome - pure functions shared by the live run and
the backtest (docs/superpowers/specs/2026-09-27-squeeze-screener-design.md, sections 5-6)."""
import heapq

from bot import market

TOP = 10
HIT = 1.5  # "exploded": the highest price in the window reaches 1.5x the entry
WINDOW = 10  # sessions D .. D+9
MIN_PRICE = 1.0
MIN_DOLLAR = market.LOW_LIQUIDITY  # $2M a day
GRID = tuple({"w": w, "g_si": g, "g_rv": r}
             for w in (0.3, 0.5, 0.7) for g in (0.10, 0.15, 0.20) for r in (1.0, 1.5, 2.0))


def features(b, i):
    """Price/volume features known before session D, from a ticker's bars through index i (= session D-1):
    price; dollar = mean close x volume over the last 21 sessions; rvol = volume[D-1] / mean volume[D-21..D-2];
    ret5 = close[D-1] / close[D-6] - 1; brk = close[D-1] / max close[D-21..D-2]. None when the history is shorter
    than 21 sessions or a divisor is not positive."""
    if i < 20:
        return None
    c, v = b["c"], b["v"]
    avg = sum(v[i - 20:i]) / 20
    prior = max(c[i - 20:i])
    if not (avg > 0 and prior > 0 and c[i - 5] > 0):
        return None
    dollar = sum(c[k] * v[k] for k in range(i - 20, i + 1)) / 21
    return {"price": c[i], "dollar": dollar, "rvol": v[i] / avg, "ret5": c[i] / c[i - 5] - 1, "brk": c[i] / prior}


def make_row(ticker, si_row, shares, feat):
    """One eligible stock for the day, or None: needs a short-interest row, shares outstanding, features,
    price >= MIN_PRICE and dollar volume >= MIN_DOLLAR."""
    if not si_row or not shares or not feat or feat["price"] < MIN_PRICE or feat["dollar"] < MIN_DOLLAR:
        return None
    return {"t": ticker, "si_pct": si_row["si"] / shares, "dtc": si_row.get("dtc") or 0.0, "chg": si_row.get("chg"),
            "si_date": si_row.get("date"), "shares": shares, **feat}


def pct_ranks(xs):
    """Percentile rank of each value in [0, 1] (0 = lowest); ties share their mean rank; a single value -> 1.0."""
    n = len(xs)
    if n < 2:
        return [1.0] * n
    order = sorted(range(n), key=xs.__getitem__)
    out, i = [0.0] * n, 0
    while i < n:
        j = i
        while j + 1 < n and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2 / (n - 1)
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def scored(rows, g_si):
    """Candidate set C = rows with si_pct >= g_si, each with fuel = mean rank of si_pct and dtc and
    trigger = mean rank of rvol, ret5 and brk (ranks taken within C)."""
    c = [r for r in rows if r["si_pct"] >= g_si]
    if not c:
        return []
    rk = {k: pct_ranks([r[k] for r in c]) for k in ("si_pct", "dtc", "rvol", "ret5", "brk")}
    return [{**r, "fuel": (rk["si_pct"][n] + rk["dtc"][n]) / 2,
             "trigger": (rk["rvol"][n] + rk["ret5"][n] + rk["brk"][n]) / 3} for n, r in enumerate(c)]


def top(cands, w, g_rv, n=TOP):
    """The day's list from scored candidates: rvol >= g_rv, score = w x fuel + (1 - w) x trigger, best first
    (ties by ticker), at most n."""
    best = heapq.nsmallest(n, ((-(w * r["fuel"] + (1 - w) * r["trigger"]), r["t"], k)
                               for k, r in enumerate(cands) if r["rvol"] >= g_rv))
    return [{**cands[k], "score": -s} for s, _, k in best]


def rank(rows, v, n=TOP):
    """The day's list for variant v = {"w", "g_si", "g_rv"}."""
    return top(scored(rows, v["g_si"]), v["w"], v["g_rv"], n)


def outcome(b, i, end):
    """Buying at the open of bar i (session D) and holding through session `end` (ISO, = D+9): hit (max high >=
    HIT x open), maxup, dd (min low / open - 1), r10 (last close in the window / open - 1). None when bar i has no
    positive open."""
    o = b["o"][i]
    if o is None or not o > 0:
        return None
    j = i
    while j + 1 < len(b["d"]) and b["d"][j + 1] <= end:
        j += 1
    hi, lo = max(b["h"][i:j + 1]), min(b["l"][i:j + 1])
    return {"hit": hi >= HIT * o, "maxup": hi / o - 1, "dd": lo / o - 1, "r10": b["c"][j] / o - 1}
