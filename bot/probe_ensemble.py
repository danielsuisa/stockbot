"""Phase A of the recommendation algorithm (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md,
section 7 A; plan docs/superpowers/plans/2026-10-10-recommendation-algorithm-phase-a.md): the point-in-time universe,
the four signals as firing counters, item 2.02 coverage and the report.

Measurement only. No return of a ticket or a stock is computed: the signals' own inputs (S3's D-147 -> D-21 return
rank, S4's 5-session return, S2's close / previous close) use bars through D only, and no bar after D is read."""
import argparse
import bisect
import collections
import datetime as dt
import json
import math
import random
import time

from bot import alpaca, common, probe_ensemble_data as pd, probe_insider as pi

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


def _reacts(releases, sym, sessions, days):
    """Is one of the symbol's item 2.02 reaction sessions in `days`? releases: a pd.Releases (times fetched lazily) or,
    in tests, {symbol: {reaction sessions}}."""
    if isinstance(releases, pd.Releases):
        return releases.reacts_on(sym, sessions, days)
    return bool(set(releases.get(sym, ())) & days)


def s2_firings(day, series, releases, use_8k, sessions=None):
    """S2 on `day`: close / previous close >= 1.05 (split-adjusted), volume >= 2 x the mean of the 20 sessions before
    (split-adjusted), raw close in the upper half of the day's raw range, and (unless the fallback) `day` is the
    reaction session of an item 2.02 release -> {symbols}. The release is checked last (it may fetch a time)."""
    out = set()
    for sym, s in series.items():
        i = _i(s, day)
        if i is None or i - s["_seg_start"][i] < S2_AVG:
            continue
        c, prev = s["close"][i], s["close"][i - 1]
        avg = sum(s["volume"][i - S2_AVG:i]) / S2_AVG
        mid = (s["raw_high"][i] + s["raw_low"][i]) / 2
        if prev and c / prev >= S2_JUMP - 1e-9 and avg and s["volume"][i] >= S2_VOLUME * avg - 1e-9 \
                and s["raw_close"][i] >= mid - 1e-9 and (not use_8k or _reacts(releases, sym, sessions, {day})):
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
            and not _reacts(releases, sym, sessions, quiet)}


def transitions(states, min_outside):
    """[(day, {symbols in the state})] in session order, from the first session the state can be observed ->
    ([(day, symbol)] where a symbol enters the state after at least `min_outside` observed sessions outside it,
    the number of entries not counted in the warm-up). Sessions before the first one are unknown, not outside: the
    first `min_outside` sessions are the warm-up, and a first entry there cannot be shown to be a transition."""
    last_in, out, warm_up = {}, [], 0
    for k, (day, inside) in enumerate(states):
        for sym in sorted(inside):
            j = last_in.get(sym)
            if j is None and k < min_outside:
                warm_up += 1
            elif k - (-1 if j is None else j) - 1 >= min_outside:
                out.append((day, sym))
            last_in[sym] = k
    return out, warm_up


# ---------- coverage, the run, the report ----------
SIGNAL_START, SIGNAL_END, BARS_FROM = "2017-01-03", "2026-03-31", "2016-01-04"
COVERAGE_MIN = 0.90  # spec section 2: below it, S2 drops the 8-K condition
TZ_SAMPLE = ("320193", "19617", "789019")  # Apple and Microsoft (after the close), JPMorgan Chase (before the open)
TZ_YEARS = tuple(range(2017, 2027))  # one item 2.02 8-K per issuer and year
TZ_RULES = ("America/New_York", "UTC", "double")
REPORT_KEYS = ("acceptance_tz_check", "item_202_coverage", "fallback", "universe_per_year",
               "firings_per_signal_per_year", "warm_up", "data_gaps", "requests_and_runtime")
CHECK_KEYS = ("acceptance_tz_check", "cik_mapping_check", "price_rules_check", "look_ahead_check",
              "item_202_coverage_sample", "index_pages", "requests_and_runtime")


def item_202_coverage(stock_years, releases):
    """Universe stock-years {symbol: {years}} covered when the symbol has an item 2.02 release (point in time) filed in
    that year (releases: {symbol: [filing dates]}) -> {"per_year": {year: {"stock_years", "covered", "share"}}, "overall": {...}}."""
    per = collections.defaultdict(lambda: [0, 0])
    for sym, years in stock_years.items():
        have = {t[:4] for t in releases.get(sym, ())}
        for y in years:
            per[y][0] += 1
            per[y][1] += y in have
    tot = [sum(a for a, _ in per.values()), sum(b for _, b in per.values())]
    cell = lambda a, b: {"stock_years": a, "covered": b, "share": round(b / a, 4) if a else None}  # noqa: E731
    return {"per_year": {y: cell(*per[y]) for y in sorted(per)}, "overall": cell(*tot)}


def tz_check(zip_path):
    """Compare submissions.zip's acceptanceDateTime with EDGAR's index page (Eastern time) for one item 2.02 8-K per
    issuer and year -> {"pairs" (each with the rule that reproduces the index time, and the offset in hours),
    "rules_matched", "tz"}: tz is the one rule that reproduces every pair, else None (the full run stops)."""
    pairs = []
    for cik in TZ_SAMPLE:
        for acc, field in pd.accepted_pairs(zip_path, cik, TZ_YEARS):
            idx = pd.index_accepted(cik, acc)
            off = None
            if idx:
                off = (dt.datetime.fromisoformat(field[:16]) - dt.datetime.fromisoformat(idx)).total_seconds() / 3600
            pairs.append({"cik": cik, "accession": acc, "field": field, "index": idx, "offset_h": off,
                          "rule": next((r for r in TZ_RULES if idx and pd._to_ny(field, r) == idx), None)})
    rules = sorted({p["rule"] or "none" for p in pairs})
    return {"pairs": pairs, "rules_matched": rules, "tz": rules[0] if len(rules) == 1 and rules[0] != "none" else None}


def load_insider(today):
    """Every Form 3/4/5 symbol row (2015 onward) and S1's purchase lines (2015Q4 onward, for the 30-day window)."""
    rows, buys, stats, quarters = [], [], {}, []
    for y in range(2015, today.year + 1):
        for q in range(1, 5):
            z = pi.quarter_zip(y, q)
            if z is None:
                continue
            quarters.append(f"{y}Q{q}")
            rows += pd.symbol_filings(z, stats)
            if (y, q) >= (2015, 4):
                buys += pd.purchases(z)
    return rows, buys, stats, quarters


def _calendar():
    cal = alpaca.calendar("2015-12-01", SIGNAL_END)
    return sorted(d for d in cal if BARS_FROM <= d <= SIGNAL_END)


def run(checks, sample=400, seed=7):
    t0, today = time.time(), dt.date.today()
    sessions = _calendar()
    rows, buys, stats, quarters = load_insider(today)
    filed, ciks = pd.index(rows)
    zip_path = pd.download(pd.SUBMISSIONS, common.ROOT / ".cache" / "sec" / "submissions.zip")
    tz = tz_check(zip_path)  # evidence only: the zip's acceptanceDateTime is not used (owner's decision 2026-10-10)
    symbols = sorted(filed)
    if checks:
        rng = random.Random(seed)
        symbols = sorted(set(rng.sample(symbols, min(sample, len(symbols)))) | {"AAPL", "GE"})
    found = pd.earnings_releases(zip_path, {c for s in symbols for c in ciks.get(s, ())}, "2016-12-01", SIGNAL_END)
    reactions = pd.Releases(found, rows)  # acceptance times from EDGAR's index pages, fetched only where they matter
    series = pd.load_series(symbols, BARS_FROM, SIGNAL_END, sessions)
    days = [d for d in sessions if d <= SIGNAL_END]
    if checks:
        return checks_report(t0, tz, ciks, series, filed, reactions, sessions)
    sizes, distinct, stock_years = collections.defaultdict(list), collections.defaultdict(set), collections.defaultdict(set)
    s2 = {k: collections.Counter() for k in ("8-K, in universe", "8-K, all", "fallback, in universe", "fallback, all")}
    s3_states, s4_states, members = [], [], {}
    # S3/S4 states are observed from the first session the universe can hold a symbol (MIN_HISTORY earlier bars from
    # BARS_FROM) and not before SIGNAL_START; earlier sessions are unknown (the warm-up in transitions())
    observed_from = max(SIGNAL_START, days[min(MIN_HISTORY, len(days) - 1)])
    for d in days:
        u = universe(d, series, filed)
        members[d] = set(u)
        if d >= SIGNAL_START:
            y = d[:4]
            sizes[y].append(len(u))
            distinct[y] |= set(u)
            for sym in u:
                stock_years[sym].add(y)
            for use_8k in (True, False):
                fired = s2_firings(d, series, reactions, use_8k, sessions)
                tag = "8-K" if use_8k else "fallback"
                s2[f"{tag}, all"][y] += len(fired)
                s2[f"{tag}, in universe"][y] += len(fired & set(u))
        if d >= observed_from:
            s3_states.append((d, s3_state(d, series, u)))
            s4_states.append((d, s4_state(d, series, u, reactions, sessions)))
    cov = item_202_coverage(stock_years, {s: reactions.filing_dates(s) for s in stock_years})
    tz["index pages read"], tz["index pages missing"] = len(reactions.times), reactions.missing
    keep = cov["overall"]["share"] is not None and cov["overall"]["share"] >= COVERAGE_MIN
    s1 = [(d, s) for d, s in s1_firings(buys, sessions) if SIGNAL_START <= d <= SIGNAL_END]
    s1_in = sum(1 for d, s in s1 if s in members.get(sessions[min(bisect.bisect_left(sessions, d), len(sessions) - 1)], ()))
    per_year = lambda xs: dict(sorted(collections.Counter(d[:4] for d, _ in xs).items()))  # noqa: E731
    s3, s3_warm = transitions(s3_states, S3_OUTSIDE)
    s4, s4_warm = transitions(s4_states, 1)
    warm = lambda n, k: {"sessions": n, "from": s3_states[0][0], "to": s3_states[n - 1][0],  # noqa: E731
                         "entries not counted": k}
    firings = {"S1 insider cluster (all)": per_year(s1), "S1 in universe on its session": s1_in,
               "S2 earnings drift (decided rule, in universe)": dict(sorted(s2["8-K, in universe" if keep else
                                                                            "fallback, in universe"].items())),
               "S2 for information": {k: dict(sorted(v.items())) for k, v in s2.items()},
               "S3 momentum near the high": per_year(s3), "S4 short-term reversal": per_year(s4)}
    return {"acceptance_tz_check": tz, "item_202_coverage": cov,
            "fallback": "8-K kept" if keep else "8-K dropped",
            "universe_per_year": {y: {"average daily size": round(sum(v) / len(v), 1), "distinct symbols": len(distinct[y]),
                                      "sessions": len(v)} for y, v in sorted(sizes.items())},
            "firings_per_signal_per_year": firings,
            "warm_up": {"states observed from": observed_from, "S3": warm(S3_OUTSIDE, s3_warm), "S4": warm(1, s4_warm)},
            "data_gaps": {"insider quarters": f"{quarters[0]}..{quarters[-1]} ({len(quarters)})",
                          "symbols as filed": len(filed), "symbols with bars": len(series),
                          "symbols without bars": len(filed) - len(series),
                          "series broken by the bad-price rule": sum(1 for s in series.values() if s["segment"][-1]),
                          "filings with an unusable symbol": stats.get("unusable symbol", 0),
                          "S1 purchase lines": len(buys)},
            "requests_and_runtime": {"alpaca": alpaca.REQUESTS[0], "sec zips": pi.SEC_REQUESTS[0],
                                     "runtime_s": round(time.time() - t0)}}


def checks_report(t0, tz, ciks, series, filed, reactions, sessions):
    """The pre-run checks (booleans and counts only; no price ratio is output)."""
    multi = sum(1 for v in ciks.values() if len(v) > 1)
    known = {"AAPL -> 320193": "320193" in ciks.get("AAPL", set()),
             "FB and META -> 1326801": "1326801" in ciks.get("FB", set()) and "1326801" in ciks.get("META", set())}
    rules = {}
    for sym, day in (("AAPL", "2020-08-31"), ("GE", "2021-08-02")):  # a 4:1 split, a 1:8 reverse split
        s = series.get(sym)
        i = s["dates"].index(day) if s and day in s["dates"] else None
        if i is None:
            rules[sym] = "no bars"
            continue
        raw_jump = s["raw_close"][i] / s["raw_close"][i - 1]
        adj = s["close"][i] / s["close"][i - 1]
        rules[sym] = {"raw close moved by the split factor": raw_jump < 0.3 or raw_jump > 3.5,
                      "split-adjusted close continuous (within a factor of 1.5)": 1 / 1.5 < adj < 1.5,
                      "same segment across the split": s["segment"][i] == s["segment"][i - 1]}
    breaks = sum(1 for s in series.values() if s["segment"][-1])
    days = [sessions[i] for i in (300, 900, 1500) if i < len(sessions)]
    look = True
    for d in days:
        u = universe(d, series, filed)
        cut = through(series, d)
        look &= (u == universe(d, cut, filed)
                 and s2_firings(d, series, reactions, True, sessions) == s2_firings(d, cut, reactions, True, sessions)
                 and s3_state(d, series, u) == s3_state(d, cut, u)
                 and s4_state(d, series, u, reactions, sessions) == s4_state(d, cut, u, reactions, sessions))
    stock_years = collections.defaultdict(set)
    for d in sessions:
        if d >= SIGNAL_START:
            for sym in universe(d, series, filed):
                stock_years[sym].add(d[:4])
    return {"acceptance_tz_check": tz,
            "cik_mapping_check": {"symbols": len(ciks), "symbols filed under more than one CIK": multi, **known},
            "price_rules_check": {**rules, "sample series broken by the bad-price rule": breaks,
                                  "sample symbols with bars": len(series)},
            "look_ahead_check": {"days compared": days, "identical with every bar after D removed": look},
            "item_202_coverage_sample": item_202_coverage(stock_years, {s: reactions.filing_dates(s) for s in stock_years}),
            "index_pages": {"read": len(reactions.times), "missing": reactions.missing},
            "requests_and_runtime": {"alpaca": alpaca.REQUESTS[0], "sec zips": pi.SEC_REQUESTS[0],
                                     "runtime_s": round(time.time() - t0)}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Recommendation algorithm phase A (no returns)")
    ap.add_argument("--checks", action="store_true", help="the pre-run checks on a sample")
    a = ap.parse_args(argv)
    out = run(a.checks)
    text = json.dumps(out, indent=1, default=sorted)
    print(text)
    common.summary(f"### Phase A {'checks' if a.checks else 'report'}\n\n```json\n{text}\n```")
    return 0 if "stopped" not in out else 1


if __name__ == "__main__":
    raise SystemExit(main())
