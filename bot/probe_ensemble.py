"""Phase A of the recommendation algorithm (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md,
section 7 A; plan docs/superpowers/plans/2026-10-10-recommendation-algorithm-phase-a.md): the point-in-time universe,
the four signals as firing counters, item 2.02 coverage and the report.

Measurement only. No return of a ticket or a stock is computed: the signals' own inputs (S3's D-147 -> D-21 return
rank, S4's 5-session return, S2's close / previous close) use bars through D only, and no bar after D is read."""
import bisect
import datetime as dt
import math

from bot import probe_ensemble_data as pd

MIN_PRICE, MIN_DOLLAR_VOLUME, MIN_HISTORY, DV_SESSIONS, FILED_DAYS = 5.0, 10_000_000.0, 252, 20, 365


def _prepare(s):
    """Index and prefix sums a series needs for O(1) daily checks (added once, in place)."""
    if "_pos" not in s:
        s["_pos"] = {d: i for i, d in enumerate(s["dates"])}
        acc = [0.0]
        for c, v in zip(s["raw_close"], s["raw_volume"]):
            acc.append(acc[-1] + c * v)
        s["_dv"] = acc
        starts, first = [], {}
        for i, g in enumerate(s["segment"]):
            first.setdefault(g, i)
            starts.append(first[g])
        s["_seg_start"] = starts
    return s


def dollar_volume(s, i):
    """The 20-session average dollar volume (raw close x raw volume) through bar i; None with fewer bars in the
    segment."""
    _prepare(s)
    if i - s["_seg_start"][i] + 1 < DV_SESSIONS:
        return None
    return (s["_dv"][i + 1] - s["_dv"][i + 1 - DV_SESSIONS]) / DV_SESSIONS


def universe(day, series, filed):
    """Spec section 2's universe on `day` -> {symbol: its 20-session average dollar volume}: a Form 3/4/5 filed in the
    365 days before `day` with that trading symbol, a bar on `day`, raw close >= $5, average dollar volume >= $10M,
    and >= 252 earlier bars in the current segment (the bad-price rule)."""
    lo = (dt.date.fromisoformat(day) - dt.timedelta(FILED_DAYS)).isoformat()
    out = {}
    for sym, s in series.items():
        dates = filed.get(sym)
        if not dates:
            continue
        k = bisect.bisect_left(dates, lo)
        if k == len(dates) or dates[k] >= day:
            continue
        i = _prepare(s)["_pos"].get(day)
        if i is None or s["raw_close"][i] < MIN_PRICE or i - s["_seg_start"][i] < MIN_HISTORY:
            continue
        dv = dollar_volume(s, i)
        if dv is not None and dv >= MIN_DOLLAR_VOLUME:
            out[sym] = dv
    return out


def rank_cut(values, share, top):
    """The top (or bottom) ceil(share x n) symbols by value; ties broken by symbol -> set."""
    order = sorted(values, key=lambda k: ((-values[k] if top else values[k]), k))
    return set(order[:math.ceil(share * len(order))])


def liquidity_top_half(u):
    return rank_cut(u, 0.5, True)


# ---------- the four signals, as firing counters ----------
S1_WINDOW, S1_PEOPLE, S1_USD, S1_GAP = 30, 3, 100_000.0, 21
S2_JUMP, S2_VOLUME, S2_AVG = 1.05, 2.0, 20
S3_NEAR, S3_HIGH, S3_FROM, S3_TO, S3_TOP, S3_OUTSIDE = 0.95, 252, 147, 21, 0.20, 21
S4_DAYS, S4_BOTTOM, S4_MAX, S4_QUIET = 5, 0.05, -0.10, 5


def through(series, day):
    """Every series cut after `day` (copies): what a detector may see on `day`."""
    out = {}
    for sym, s in series.items():
        k = bisect.bisect_right(s["dates"], day)
        x = {key: s[key][:k] for key in ("dates", "close", "volume", "raw_close", "raw_volume", "raw_high", "raw_low",
                                         "segment")}
        if k:
            out[sym] = x
    return out


def _people(lines):
    """Distinct buyers when identical purchases (same trade date, shares, price) count as one person, and the dollars
    with each identical purchase once -> (people, dollars)."""
    parent = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            a = parent[a]
        return a
    seen = {}
    for x in lines:
        key = (x["trade_date"], x["shares"], x["price"])
        find(x["owner"])
        if key in seen:
            parent[find(x["owner"])] = find(seen[key][0])
        else:
            seen[key] = (x["owner"], x["value"])
    return len({find(o) for o in parent}), sum(v for _, v in seen.values())


def s1_firings(purchases, sessions):
    """S1: per issuer, the first filing date on which >= 3 people (identical purchases counted once) bought >= $100k
    with trade dates in the 30 days before; no second firing within 21 sessions -> [(filing date, symbol)]."""
    by = {}
    for x in purchases:
        by.setdefault(x["issuer"], []).append(x)
    out = []
    for lines in by.values():
        lines.sort(key=lambda x: x["filed"])
        blocked = -1
        for F in sorted({x["filed"] for x in lines}):
            k = bisect.bisect_left(sessions, F)
            if k <= blocked:
                continue
            since = (dt.date.fromisoformat(F) - dt.timedelta(S1_WINDOW)).isoformat()
            live = [x for x in lines if x["filed"] <= F and since <= x["trade_date"] <= F]
            people, usd = _people(live)
            if people >= S1_PEOPLE and usd >= S1_USD - 1e-6:
                sym = next((x["symbol"] for x in reversed(live) if x["symbol"]), None)
                out.append((F, sym))
                blocked = k + S1_GAP
    return sorted(out)


def _i(s, day):
    return _prepare(s)["_pos"].get(day)


def s2_firings(day, series, releases, use_8k):
    """S2 on `day`: close / previous close >= 1.05 (split-adjusted), volume >= 2 x the mean of the 20 sessions before
    (split-adjusted), raw close in the upper half of the day's raw range, and (unless the fallback) `day` is the
    reaction session of an item 2.02 release (releases: {symbol: {reaction sessions}}) -> {symbols}."""
    out = set()
    for sym, s in series.items():
        i = _i(s, day)
        if i is None or i - s["_seg_start"][i] < S2_AVG:
            continue
        if use_8k and day not in releases.get(sym, ()):
            continue
        c, prev = s["close"][i], s["close"][i - 1]
        avg = sum(s["volume"][i - S2_AVG:i]) / S2_AVG
        mid = (s["raw_high"][i] + s["raw_low"][i]) / 2
        if prev and c / prev >= S2_JUMP - 1e-9 and avg and s["volume"][i] >= S2_VOLUME * avg - 1e-9 \
                and s["raw_close"][i] >= mid - 1e-9:
            out.add(sym)
    return out


def s3_state(day, series, u):
    """S3's state on `day` among the universe u: close >= 0.95 x the highest close of the last 252 sessions (through
    D) and the D-147 -> D-21 return in the top 20% of the universe (split-adjusted closes) -> {symbols}."""
    near, ret = set(), {}
    for sym in u:
        s = series[sym]
        i = _i(s, day)
        if i is None or i - s["_seg_start"][i] < S3_FROM:
            continue
        c = s["close"]
        if c[i] >= S3_NEAR * max(c[max(s["_seg_start"][i], i - S3_HIGH + 1):i + 1]) - 1e-9:
            near.add(sym)
        if c[i - S3_FROM]:
            ret[sym] = c[i - S3_TO] / c[i - S3_FROM] - 1
    return near & rank_cut(ret, S3_TOP, True)


def s4_state(day, series, u, releases, sessions):
    """S4's state on `day` among the universe u: the 5-session return (split-adjusted) in the bottom 5% of the
    universe and <= -10%, in the top half by liquidity, and no item 2.02 reaction session in the last 5 sessions
    (D-4 .. D) -> {symbols}."""
    ret = {}
    for sym in u:
        s = series[sym]
        i = _i(s, day)
        if i is None or i - s["_seg_start"][i] < S4_DAYS or not s["close"][i - S4_DAYS]:
            continue
        ret[sym] = s["close"][i] / s["close"][i - S4_DAYS] - 1
    k = bisect.bisect_left(sessions, day)
    quiet = set(sessions[max(0, k - S4_QUIET + 1):k + 1])
    top = liquidity_top_half(u)
    return {sym for sym in rank_cut(ret, S4_BOTTOM, False) if ret[sym] <= S4_MAX + 1e-9 and sym in top
            and not quiet & set(releases.get(sym, ()))}


def transitions(states, min_outside):
    """[(day, {symbols in the state})] in session order -> [(day, symbol)] where a symbol enters the state after at
    least `min_outside` sessions outside it (sessions before the first one count as outside)."""
    last_in, out = {}, []
    for k, (day, inside) in enumerate(states):
        for sym in sorted(inside):
            j = last_in.get(sym)
            if j is None or k - j - 1 >= min_outside:
                out.append((day, sym))
            last_in[sym] = k
    return out
