"""The selection engine's universe and detectors (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md
sections 2, 4, 4.1 and 4.2): pure functions over `ensemble_data.Inputs`, point in time; nothing reads a bar after D.
Built on phase A's tested code (`probe_ensemble`), with 4.1/4.2: 8-K only; S2 needs a bar on the session before D;
S1 and S2 fire only on a universe symbol; the universe applies the rename rule; S1's issuer is the CIK and insider
prices are matched by CIK. Counts only: no outcome is computed here."""
import bisect
import collections
import datetime as dt

from bot import probe_ensemble as pe, probe_ensemble_data as pd

SIGNALS = ("S1", "S2", "S3", "S4")
MODES = {"S1": ("open", 21), "S2": ("breakout", 42), "S3": ("breakout", 21), "S4": ("open", 5)}
WINDOW_DAYS, RENAME_FACTOR = 365, 2.0


def _lo(day):
    return (dt.date.fromisoformat(day) - dt.timedelta(WINDOW_DAYS)).isoformat()


def _has_bar(inp, sym, day):
    s = inp.series.get(sym)
    return s is not None and pe._i(s, day) is not None


def cik_at(inp, sym, day):
    """The CIK of the symbol's latest Form 3/4/5 filed in [D - 365 days, D), or None."""
    items = inp.by_sym.get(sym, [])
    k = bisect.bisect_left(items, (day,))
    return items[k - 1][1] if k and items[k - 1][0] >= _lo(day) else None


def cik_price(inp, cik, day):
    """The CIK's latest market-price transaction filed in [D - 365 days, D) (C12) -> price, or None."""
    items = inp.prices_by_cik.get(cik, [])
    k = bisect.bisect_left(items, (day,))
    return items[k - 1][3] if k and items[k - 1][0] >= _lo(day) else None


def rename(sym, day, inp, first):
    """Section 2's rename rule for a symbol as filed without a bar on D -> the symbol whose bars it takes, or None:
    its CIK has exactly one other filed symbol with a bar on D, whose raw close is within a factor of 2 of the CIK's
    latest market-price transaction of the 365 days, and which is not itself in the first-pass universe."""
    if _has_bar(inp, sym, day):
        return None
    cik = cik_at(inp, sym, day)
    others = [o for o in inp.of_cik.get(cik, ()) if o != sym and _has_bar(inp, o, day)]
    if cik is None or len(others) != 1 or others[0] in first:
        return None
    price = cik_price(inp, cik, day)
    s = inp.series[others[0]]
    close = s["raw_close"][pe._i(s, day)]
    if price is None or not (price / RENAME_FACTOR <= close <= price * RENAME_FACTOR):
        return None
    return others[0]


def _qualifies(s, day):
    """The universe's bar conditions on one series (as `probe_ensemble.universe`) -> its dollar volume, or None."""
    i = pe._prepare(s)["_pos"].get(day)
    if i is None or s["raw_close"][i] < pe.MIN_PRICE or i - s["_seg_start"][i] < pe.MIN_HISTORY:
        return None
    dv = pe.dollar_volume(s, i)
    return dv if dv is not None and dv >= pe.MIN_DOLLAR_VOLUME else None


def deciles(adv):
    """Liquidity deciles (C13): ranks ascending by dollar volume, ties by symbol; decile = floor(10 x rank / n)."""
    order = sorted(adv, key=lambda s: (adv[s], s))
    return {s: 10 * r // len(order) for r, s in enumerate(order)}


def universe(day, inp):
    """Section 2's universe on D -> {symbol: {"adv", "decile", "bars_of"}}: phase A's first pass, then the rename
    rule for the symbols filed in the 365 days that have no bar on D (their bars must meet the same conditions)."""
    first = pe.universe(day, inp.series, inp.filed)
    out = {s: {"adv": a, "bars_of": s} for s, a in first.items()}
    lo = _lo(day)
    for sym in inp.rename_pool:
        dates = inp.filed.get(sym, [])
        k = bisect.bisect_left(dates, lo)
        if sym in out or k == len(dates) or dates[k] >= day:
            continue
        other = rename(sym, day, inp, first)
        if other is not None:
            dv = _qualifies(inp.series[other], day)
            if dv is not None:
                out[sym] = {"adv": dv, "bars_of": other}
    dec = deciles({s: v["adv"] for s, v in out.items()})
    for s, v in out.items():
        v["decile"] = dec[s]
    return out


def _view(inp, u):
    return {s: inp.series[v["bars_of"]] for s, v in u.items()}, {s: v["adv"] for s, v in u.items()}


def s2(day, inp, u, diag=None):
    """S2 on D among the universe: a bar on the session before D (4.1), close / previous close >= 1.05, volume >= 2 x
    the average of the 20 sessions before D, raw close in the upper half of D's range, D the reaction session of an
    8-K item 2.02 release (checked last: it may fetch an acceptance time) -> {symbols}."""
    k = bisect.bisect_left(inp.sessions, day)
    prev = inp.sessions[k - 1] if k else None
    out = set()
    for sym, info in u.items():
        s = inp.series[info["bars_of"]]
        i = pe._i(s, day)
        if i is None or i - s["_seg_start"][i] < pe.S2_AVG:
            continue
        c, p = s["close"][i], s["close"][i - 1]
        avg = sum(s["volume"][i - pe.S2_AVG:i]) / pe.S2_AVG
        mid = (s["raw_high"][i] + s["raw_low"][i]) / 2
        if not (p and c / p >= pe.S2_JUMP - 1e-9 and avg and s["volume"][i] >= pe.S2_VOLUME * avg - 1e-9
                and s["raw_close"][i] >= mid - 1e-9 and pe._reacts(inp.releases, sym, inp.sessions, {day})):
            continue
        if s["dates"][i - 1] != prev:
            if diag is not None:
                diag["S2 not fired: no bar on the session before D"] += 1
            continue
        out.add(sym)
    return out


def s3_state(day, inp, u):
    view, adv = _view(inp, u)
    return pe.s3_state(day, view, adv)


def s4_state(day, inp, u):
    view, adv = _view(inp, u)
    return pe.s4_state(day, view, adv, inp.releases, inp.sessions)


def s1(inp, members, diag=None):
    """S1 per issuer CIK (phase A's rule: >= 3 people, >= $100,000, trade dates in the 30 days before, identical
    purchases once, no second event within 21 sessions), fired on the session on or after the filing date when the
    purchase lines' symbol, or else one of the CIK's symbols, is in the universe that session -> [(day, symbol)]."""
    by = collections.defaultdict(list)
    for x in inp.purchases:
        by[pd._cik(x["issuer"])].append(x)
    sessions, out = inp.sessions, []
    for cik, lines in by.items():
        lines.sort(key=lambda x: x["filed"])
        blocked = -1
        for F in sorted({x["filed"] for x in lines}):
            k = bisect.bisect_left(sessions, F)
            if k <= blocked or k >= len(sessions):
                continue
            since = (dt.date.fromisoformat(F) - dt.timedelta(pe.S1_WINDOW)).isoformat()
            live = [x for x in lines if x["filed"] <= F and since <= x["trade_date"] <= F]
            people, usd = pe._people(live)
            if people < pe.S1_PEOPLE or usd < pe.S1_USD - 1e-6:
                continue
            blocked = k + pe.S1_GAP
            day, members_day = sessions[k], members.get(sessions[k], frozenset())
            sym = next((x["symbol"] for x in reversed(live) if x["symbol"]), None)
            if sym not in members_day:
                alt = sorted(s for s in inp.of_cik.get(cik, ()) if s in members_day)
                if not alt:
                    continue
                sym = alt[0]
                if diag is not None:
                    diag["S1 fired on the CIK's other universe symbol"] += 1
            out.append((day, sym))
    return sorted(out)


def firings(inp, start, end):
    """Every test firing from `start` to `end` -> {"firings": [(day, signal, symbol)], "members": {day: frozenset},
    "renamed": {(day, symbol): bars_of}, "warm_up", "diagnostics"} (counts only)."""
    diag = collections.Counter()
    members, renamed, s2f, s3s, s4s = {}, {}, [], [], []
    for d in [d for d in inp.sessions if start <= d <= end]:
        u = universe(d, inp)
        members[d] = frozenset(u)
        for sym, v in u.items():
            if v["bars_of"] != sym:
                renamed[(d, sym)] = v["bars_of"]
        s2f += [(d, "S2", s) for s in sorted(s2(d, inp, u, diag))]
        s3s.append((d, s3_state(d, inp, u)))
        s4s.append((d, s4_state(d, inp, u)))
    s3, w3 = pe.transitions(s3s, pe.S3_OUTSIDE)
    s4, w4 = pe.transitions(s4s, 1)
    s1f = [(d, "S1", s) for d, s in s1(inp, members, diag) if start <= d <= end]
    fired = sorted(s1f + s2f + [(d, "S3", s) for d, s in s3] + [(d, "S4", s) for d, s in s4])
    for d, sig, sym in fired:
        if (d, sym) in renamed:
            diag[f"{sig} fired on a renamed symbol"] += 1
    return {"firings": fired, "members": members, "renamed": renamed, "warm_up": {"S3": w3, "S4": w4},
            "diagnostics": dict(diag)}
