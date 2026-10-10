"""Phase A.1 of the recommendation algorithm (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md
revision 6, section 7 A.1; plan docs/superpowers/plans/2026-10-10-recommendation-algorithm-phase-a1-claude.md):
item 2.02 coverage with 8-K only, universe-like stock-months defined without bars (FINRA average daily share volume x
the latest Form 4/5 transaction price), how many the rename rule recovers, f and b = f x 1 R.

Counts and coverage only. No return of a ticket or a stock is computed; the only files written are caches of raw
data (FINRA reports, Alpaca bars, SEC zips)."""
import argparse
import bisect
import calendar
import collections
import datetime as dt
import json
import random
import time

from bot import alpaca, common, probe_ensemble as pe, probe_ensemble_data as pd, probe_insider as pi, shorts

CACHE = common.ROOT / ".cache" / "finra"
MONTHS = ("2018-01", "2026-03")
MIN_PRICE, MIN_DOLLAR_VOLUME, WINDOW_DAYS = 5.0, 10_000_000.0, 365
RENAME_SHARE, B_MAX = 1 / 3, 0.03  # spec section 2 (the rename rule) and section 6.4 (b)


# ---------- FINRA ----------
FINRA_FETCHED = [0]


def finra_report(date):
    """FINRA's consolidated short-interest report settled on `date` (exchange-listed classes) -> {FINRA symbol: slim
    row}, cached as gzip JSON."""
    path = CACHE / f"{date}.json.gz"
    got = alpaca._read(path)
    if got is None:
        got = shorts.report(date)
        FINRA_FETCHED[0] += 1
        alpaca._write(path, got)
    return got


def finra_spellings(symbol):
    """An insider-data symbol (BRK.B) -> its two FINRA spellings: shorts.norm's (BRK-B) and FINRA's own class-share
    form without a separator (BRKB)."""
    return symbol.replace(".", "-"), symbol.replace(".", "")


def finra_row(report, symbol):
    """The report's row for an insider-data symbol, or None."""
    return next((report[s] for s in finra_spellings(symbol) if s in report), None)


def volume_check(reports):
    """{date: report} -> how many rows carry FINRA's average daily share volume, and the reports where none does (the
    spec's stop condition: no volume field)."""
    rows = [r for rep in reports.values() for r in rep.values()]
    return {"reports": len(reports), "rows": len(rows), "rows with volume": sum(1 for r in rows if r.get("adv")),
            "reports without any volume": sorted(d for d, rep in reports.items()
                                                 if not any(r.get("adv") for r in rep.values()))}


# ---------- universe-like stock-months, the rename rule, f and b ----------
def month_ends(first, last):
    """["YYYY-MM" from `first` to `last`] -> [(month, its last calendar day ISO)]."""
    y, m = map(int, first.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= last:
        out.append((f"{y:04d}-{m:02d}", dt.date(y, m, calendar.monthrange(y, m)[1]).isoformat()))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def latest_before(items, end):
    """The last of `items` (tuples sorted by their first element, an ISO date) dated on or before `end`, or None."""
    k = bisect.bisect_right(items, end, key=lambda t: t[0])
    return items[k - 1] if k else None


def _window(month_end):
    return (dt.date.fromisoformat(month_end) - dt.timedelta(WINDOW_DAYS)).isoformat()


def universe_like(month_end, symbols_filed, prices, finra, stats=None):
    """Spec section 7 A.1 item 2 for the month ending `month_end` -> {symbol: {"V", "P"}}: an issuer symbol on a Form
    3/4/5 filed in the 365 days through the month's end ({symbol: sorted filing dates}); in the month's FINRA report
    (`finra`, None when there is none) with average daily share volume V; the latest Form 4/5 transaction price P of
    those 365 days ({symbol: sorted [(filed, trade date, accession, price)]}: latest by filing date, then trade date,
    then accession) >= $5; V x P >= $10M. `stats` counts the FINRA gaps."""
    stats = {} if stats is None else stats
    lo, out = _window(month_end), {}
    if finra is None:
        return out
    for sym, dates in symbols_filed.items():
        k = bisect.bisect_left(dates, lo)
        if k == len(dates) or dates[k] > month_end:
            continue
        row = finra_row(finra, sym)
        if row is None:
            stats["not in the FINRA report"] = stats.get("not in the FINRA report", 0) + 1
            continue
        if not row.get("adv"):
            stats["FINRA row without volume"] = stats.get("FINRA row without volume", 0) + 1
            continue
        last = latest_before(prices.get(sym, []), month_end)
        if last is None or last[0] < lo or last[3] < MIN_PRICE:
            continue
        if row["adv"] * last[3] >= MIN_DOLLAR_VOLUME:
            out[sym] = {"V": row["adv"], "P": last[3]}
    return out


def cik_at(filings_by_sym, sym, month_end):
    """The CIK of the symbol's latest Form 3/4/5 filing in the 365 days through the month's end ({symbol: sorted
    [(filed, cik)]}), or None."""
    last = latest_before(filings_by_sym.get(sym, []), month_end)
    return last[1] if last and last[0] >= _window(month_end) else None


def recovered(sym, month, cik, symbols_of_cik, bar_months):
    """The rename rule (spec section 2) for a symbol without bars in `month`: its CIK has exactly one other filed
    symbol with bars ({cik: {symbols}}, {symbol: {months with a bar}}), and that symbol has a bar in the month -> that
    symbol, else None."""
    others = [s for s in symbols_of_cik.get(cik, ()) if s != sym and bar_months.get(s)]
    return others[0] if len(others) == 1 and month in bar_months[others[0]] else None


CELLS = ("universe-like", "with bars", "without bars", "recovered by the rename rule",
         "recovered, duplicating a stock-month with bars")


def survivorship_counts(per_month):
    """{month: {"like": set, "with bars": set, "recovered": {symbol: other}, "duplicates": int}} -> {"per_year": {year:
    cells}, "overall": cells}."""
    per = collections.defaultdict(lambda: dict.fromkeys(CELLS, 0))
    for month, m in per_month.items():
        for key in (month[:4], "overall"):
            c = per[key]
            c["universe-like"] += len(m["like"])
            c["with bars"] += len(m["with bars"])
            c["without bars"] += len(m["like"]) - len(m["with bars"])
            c["recovered by the rename rule"] += len(m["recovered"])
            c["recovered, duplicating a stock-month with bars"] += m["duplicates"]
    overall = per.pop("overall", dict.fromkeys(CELLS, 0))
    return {"per_year": {y: per[y] for y in sorted(per)}, "overall": overall}


def decide(overall):
    """The rename rule's one-third condition, f under both readings and b = f x 1 R against 0.03 R. b uses the rule as
    decided: when the rule is not used nothing counts as recovered (spec section 7 A.1 item 3). A stock-month
    recovered as a symbol that is itself universe-like with bars that month is the same company counted twice: it
    leaves the numerator and the denominator (owner's decision 2026-10-10)."""
    dup = overall.get("recovered, duplicating a stock-month with bars", 0)
    n, without = overall["universe-like"] - dup, overall["without bars"] - dup
    rec = overall["recovered by the rename rule"] - dup
    used = 3 * rec >= without
    f = (without - (rec if used else 0)) / n if n else None
    return {"rename rule used": used, "f (rule as decided)": f,
            "f (literal, recovered always subtracted)": (without - rec) / n if n else None,
            "b_R": f, "b within 0.03 R": f is not None and f <= B_MAX}


# ---------- the run and its report ----------
REPORT_KEYS = ("finra_volume_check", "item_202_coverage_8k_only", "item_202_coverage_with_8k_a", "s2_decision",
               "stock_months", "rename_rule", "allowance", "data_gaps", "requests_and_runtime")
CHECK_KEYS = ("finra_volume_check", "spelling_check", "rename_check", "sample_stock_months", "item_202_coverage_sample",
              "requests_and_runtime")
CHECK_SYMBOLS = ("BRK.B", "META", "FB", "AAPL")
STALE_DAYS = 31  # a month whose chosen FINRA report settled longer ago than this is listed as a gap


def load_filings(today):
    """Every Form 3/4/5 symbol row (filed, cik, symbol) from 2015Q1 and, per symbol, the Form 4/5 transaction prices
    reduced to the latest one per filing month (enough for a month-end query) -> (rows, {symbol: sorted [(filed,
    trade date, accession, price)]}, stats, quarters)."""
    rows, best, stats, quarters = [], {}, {}, []
    for y in range(2015, today.year + 1):
        for q in range(1, 5):
            z = pi.quarter_zip(y, q)
            if z is None:
                continue
            quarters.append(f"{y}Q{q}")
            rows += pd.symbol_filings(z, stats)
            for filed, sym, trade, acc, px in pd.transaction_prices(z):
                key, val = (sym, filed[:7]), (filed, trade or "", acc, px)
                if key not in best or val > best[key]:
                    best[key] = val
    prices = collections.defaultdict(list)
    for (sym, _), val in best.items():
        prices[sym].append(val)
    return rows, {s: sorted(v) for s, v in prices.items()}, stats, quarters


def _rename_check(sym, filed, by_sym, of_cik, bar_months, month="2021-06", end="2021-06-30"):
    cik = cik_at(by_sym, sym, end)
    has = month in bar_months.get(sym, ())
    return {"filed": sym in filed, f"bar in {month}": has, f"CIK at {month}": cik,
            "recovered as": None if has else recovered(sym, month, cik, of_cik, bar_months)}


def run(checks, sample=300, seed=7):
    t0 = time.time()
    sessions = pe._calendar()
    rows, prices, stats, quarters = load_filings(dt.date.today())
    filed, ciks = pd.index(rows)
    by_sym, of_cik = collections.defaultdict(list), collections.defaultdict(set)
    for f, c, sym in rows:
        by_sym[sym].append((f, pd._cik(c)))
        of_cik[pd._cik(c)].add(sym)
    for v in by_sym.values():
        v.sort()
    months = month_ends(*MONTHS)
    settles = [(d,) for d in sorted(shorts.settlement_dates())]
    chosen = {m: (latest_before(settles, end) or (None,))[0] for m, end in months}
    reports = {d: finra_report(d) for d in sorted({d for d in chosen.values() if d})}
    vc = volume_check(reports)
    if not reports or vc["reports without any volume"]:
        return {"finra_volume_check": vc, "stopped": "FINRA's reports carry no volume field"}

    counted = sorted(filed)
    if checks:
        rng = random.Random(seed)
        counted = sorted(set(rng.sample(counted, min(sample, len(counted)))) | (set(CHECK_SYMBOLS) & set(filed)))
    siblings = {o for s in counted for c in ciks.get(s, ()) for o in of_cik.get(pd._cik(c), ())}
    series = pd.load_series(sorted(set(counted) | siblings), pe.BARS_FROM, pe.SIGNAL_END, sessions)
    bar_months = {}  # decision 5: a session in the raw Alpaca bars (read from the cache load_series just filled)
    wanted_bars = sorted(set(counted) | siblings)
    for i in range(0, len(wanted_bars), alpaca.CHUNK):
        for sym, bars in alpaca.daily(wanted_bars[i:i + alpaca.CHUNK], pe.BARS_FROM, pe.SIGNAL_END).items():
            if bars:
                bar_months[sym] = {b[0][:7] for b in bars}

    # items 2-3: universe-like stock-months, bars, the rename rule
    filed_counted = {s: filed[s] for s in counted}
    per_month, fstats = {}, {}
    for m, end in months:
        like = universe_like(end, filed_counted, prices, reports.get(chosen[m]), fstats)
        with_bars = {s for s in like if m in bar_months.get(s, ())}
        rec = {}
        for s in sorted(set(like) - with_bars):
            other = recovered(s, m, cik_at(by_sym, s, end), of_cik, bar_months)
            if other:
                rec[s] = other
        per_month[m] = {"like": set(like), "with bars": with_bars, "recovered": rec,
                        "duplicates": sum(1 for o in rec.values() if o in with_bars)}
    counts = survivorship_counts(per_month)
    decision = decide(counts["overall"])

    # item 1: item 2.02 coverage of universe stock-years (phase A's universe), 8-K only and with 8-K/A
    universe_series = {s: series[s] for s in counted if s in series}
    stock_years = collections.defaultdict(set)
    for d in sessions:
        if pe.SIGNAL_START <= d <= pe.SIGNAL_END:
            for sym in pe.universe(d, universe_series, filed):
                stock_years[sym].add(d[:4])
    zip_path = pd.download(pd.SUBMISSIONS, common.ROOT / ".cache" / "sec" / "submissions.zip")
    wanted = {c for s in stock_years for c in ciks.get(s, ())}
    cov = {}
    for key, forms in (("8-K", ("8-K",)), ("8-K/A", ("8-K", "8-K/A"))):
        rel = pd.Releases(pd.earnings_releases(zip_path, wanted, "2016-12-01", pe.SIGNAL_END, forms=forms), rows)
        cov[key] = pe.item_202_coverage(stock_years, {s: rel.filing_dates(s) for s in stock_years})
    requests = {"alpaca": alpaca.REQUESTS[0], "sec zips": pi.SEC_REQUESTS[0], "finra reports fetched": FINRA_FETCHED[0],
                "runtime_s": round(time.time() - t0)}

    if checks:
        rep = reports.get(chosen.get("2024-06"))
        return {"finra_volume_check": vc,
                "spelling_check": {"BRK.B in the FINRA report for 2024-06": rep is not None
                                   and finra_row(rep, "BRK.B") is not None},
                "rename_check": {s: _rename_check(s, filed, by_sym, of_cik, bar_months) for s in ("FB", "META")},
                "sample_stock_months": {"symbols counted": len(counted), **counts, "decision": decision},
                "item_202_coverage_sample": {"8-K only": cov["8-K"], "with 8-K/A": cov["8-K/A"]},
                "requests_and_runtime": requests}
    keep = pe.keeps_8k(cov["8-K"])
    stale = [m for m, end in months if chosen[m] is None
             or (dt.date.fromisoformat(end) - dt.date.fromisoformat(chosen[m])).days > STALE_DAYS]
    allowance = {k: decision[k] for k in ("f (rule as decided)", "f (literal, recovered always subtracted)", "b_R",
                                          "b within 0.03 R")}
    allowance["limit_R"] = B_MAX
    return {"finra_volume_check": vc, "item_202_coverage_8k_only": cov["8-K"],
            "item_202_coverage_with_8k_a": cov["8-K/A"],
            "s2_decision": {"threshold": pe.COVERAGE_MIN, "overall share, 8-K only": cov["8-K"]["overall"]["share"],
                            "decision": "8-K kept" if keep else "8-K dropped"},
            "stock_months": counts,
            "rename_rule": {"rename rule used": decision["rename rule used"],
                            "recovered": counts["overall"]["recovered by the rename rule"],
                            "without bars": counts["overall"]["without bars"],
                            "duplicates (left out of f)": counts["overall"]["recovered, duplicating a stock-month with bars"]},
            "allowance": allowance,
            "data_gaps": {"months with no or a stale FINRA report": stale, **fstats,
                          "insider quarters": f"{quarters[0]}..{quarters[-1]} ({len(quarters)})",
                          "symbols as filed": len(filed), "symbols with raw bars": len(bar_months),
                          "filings with an unusable symbol": stats.get("unusable symbol", 0)},
            "requests_and_runtime": requests}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Recommendation algorithm phase A.1 (counts only, no returns)")
    ap.add_argument("--checks", action="store_true", help="the pre-run checks on a sample")
    a = ap.parse_args(argv)
    out = run(a.checks)
    text = json.dumps(out, indent=1, default=sorted)
    print(text)
    common.summary(f"### Phase A.1 {'checks' if a.checks else 'report'}\n\n```json\n{text}\n```")
    return 0 if "stopped" not in out else 1


if __name__ == "__main__":
    raise SystemExit(main())
