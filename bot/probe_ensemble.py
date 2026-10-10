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
