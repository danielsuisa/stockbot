"""The free step of the pre-registered plan (docs/research/intraday-audit-2026-10-17.md section 4, power gate check
1): count the H1 signals of the intraday screener on the 2024 sessions FROM THE EXISTING SIP CACHE ONLY. Nothing is
fetched: every Alpaca request is blocked and counted as a cache miss, so the count is also a coverage report.

The day list is the ZBA development run's (bot/zba_v2.py, same cache keys: the asset list, daily bars over the same
range, the opening 5-minute bars, the minute bars of each day's top 20 by opening relative volume). The detectors are the frozen pre-deck setups (bot/intraday_v1.py), replayed as the 5-minute scan loop saw them
on SIP minute bars. Live alerts have since moved to the study deck; this count stays the count that was published.
No fill is
simulated: "entry touched" only says a later minute's high reached the buy-stop level.

    python -m bot.intraday_count                 # writes docs/backtest/intraday-signal-count-2024.md and .csv
    python -m bot.intraday_count --year 2024"""
import argparse
import collections
import csv
import datetime as dt
import json
import os
import time

from bot import alpaca, intraday, intraday_v1, ticket, ticket_backtest as tb, zba_v2

YEAR = "2024"
SCANS = [ticket.hhmm(m) for a, b in (("09:40", "11:30"), ("15:00", "15:45"))
         for m in range(ticket.mins(a), ticket.mins(b) + 1, intraday.STEP)]
H1 = ("vwap_hammer", "vwap_engulf", "flag")  # the primary hypothesis's setups; the others are counted as secondary
POWER = {"sd": 1.47, "mu": 0.10, "z": 2.49, "years_max": 1.5}  # section 15 check 1 as pre-registered in the audit


class CacheMiss(RuntimeError):
    pass


MISSES = collections.Counter()


def _blocked(url, query):
    MISSES[url.split("markets/", 1)[-1][:40]] += 1
    raise CacheMiss(f"{url} {query.get('symbols', '')[:30]}")


def signals_of(bars1, atr, prev_close):
    """One ticker-day replayed through the scan loop -> [(scan time, setup, entry, stop ref, touched later)]."""
    out, seen = [], set()
    for now in SCANS:
        b5 = intraday.candles(bars1, now)
        if not b5:
            continue
        if now >= intraday.LATE_FROM and not (b5[-1]["c"] > b5[0]["o"] and b5[-1]["c"] > (b5[-1].get("vwap") or 0)):
            continue
        for s in intraday_v1.setups(b5, atr, prev_close):
            key = s["setup"]
            if key in seen:
                continue
            seen.add(key)
            entry = s["trigger_high"] + intraday_v1.TICK
            touched = any(b[2] >= entry for b in bars1 if now < b[0] < "16:00")
            out.append((now, key, round(entry, 2), s["stop_ref"], touched))
            break  # the screener issues one ticket per symbol per scan: its best setup
    return out


def count(year):
    t0 = time.time()
    look, hours, sessions, days, prev = zba_v2._calendar(zba_v2.DEV)
    year_days = [d for d in days if d.startswith(year)]
    syms = tb.asset_universe(alpaca.assets())
    tops, pre, _ = zba_v2.lists(days, sessions, hours, prev, syms, t0, need_news=False)
    rows, coverage = [], collections.Counter()
    for d in year_days:
        rel = tops.get(d, {}).get("rel", [])
        if not rel:
            coverage["sessions without a top-20 list"] += 1
            continue
        coverage["sessions with a top-20 list"] += 1
        try:
            mb = alpaca.minute_bars([t for t, _ in rel], d)
        except CacheMiss:
            coverage["sessions whose minute bars are not cached"] += 1
            continue
        coverage["ticker-days"] += len(rel)
        for rank, (t, rv) in enumerate(rel, 1):
            bars = mb.get(t) or []
            if not bars:
                coverage["ticker-days without minute bars"] += 1
                continue
            daily_close = None  # the previous close: the day's first regular minute open is not it; use the daily cache
            atr = pre[d][t][0]
            try:
                dbars = alpaca.daily([t], look, days[-1])[t]
                k = next(i for i, b in enumerate(dbars) if b[0] == prev[d])
                daily_close = dbars[k][4]
            except (CacheMiss, StopIteration, KeyError):
                coverage["ticker-days without a previous close"] += 1
                continue
            for now, setup, entry, stop_ref, touched in signals_of(bars, atr, daily_close):
                rows.append({"day": d, "t": t, "rank": rank, "rel_vol": round(rv, 2), "at": now, "setup": setup,
                             "entry": entry, "stop_ref": stop_ref, "touched": touched})
    return rows, coverage, year_days, time.time() - t0


def power(n_year):
    n_needed = (POWER["z"] * POWER["sd"] / POWER["mu"]) ** 2
    years = n_needed / n_year if n_year else float("inf")
    return n_needed, years


def render(rows, coverage, year_days, secs, year, run):
    by = collections.Counter(r["setup"] for r in rows)
    touched = collections.Counter(r["setup"] for r in rows if r["touched"])
    h1 = [r for r in rows if r["setup"] in H1]
    h1_touched = sum(1 for r in h1 if r["touched"])
    covered = coverage["sessions with a top-20 list"] - coverage["sessions whose minute bars are not cached"]
    scale = 252 / covered if covered else 0
    n_needed, years = power(len(h1) * scale)
    n_needed_t, years_t = power(h1_touched * scale)
    lines = [f"# Intraday screener — H1 signal count on {year}, cache only (power gate check 1) — {dt.date.today()}", "",
             f"Run {run or 'local'} · {secs:.0f}s · Alpaca requests attempted and blocked: {sum(MISSES.values())} "
             f"({dict(MISSES) or 'none'}) · nothing downloaded", "",
             "## Coverage", "",
             f"- Sessions in {year}: {len(year_days)}; with a cached top-20 list: {coverage['sessions with a top-20 list']}; "
             f"of those with cached minute bars: {covered}",
             f"- Ticker-days replayed: {coverage['ticker-days']} · without minute bars: {coverage['ticker-days without minute bars']} "
             f"· without a previous close: {coverage['ticker-days without a previous close']}",
             "- The top-20 list is the ZBA development run's (prefilter + opening relative volume ≥ 1); the screener's "
             "pre-market stage is not replayed (its pre-market bars are not in the cache).",
             "", "## Signals (one per symbol, setup and day; the scan loop's first hit)", "",
             "| Setup | Signals | Entry level touched later | Per 252 sessions |", "|---|---:|---:|---:|"]
    for s in ("vwap_hammer", "vwap_engulf", "flag", "red_to_green", "orb5", "orb15"):
        lines.append(f"| {s}{' (H1)' if s in H1 else ''} | {by[s]} | {touched[s]} | {by[s] * scale:.0f} |")
    lines += ["", f"**H1 total: {len(h1)} signals ({len(h1) * scale:.0f} a year); entry touched: {h1_touched} "
              f"({h1_touched * scale:.0f} a year).** \"Touched\" is an upper bound on fills (no limit, no slippage, no "
              "halt check), not a fill simulation.", "",
              "## Power gate, check 1 (section 15; parameters as pre-registered, not changed)", "",
              f"- Required filled trades: n = ({POWER['z']} × {POWER['sd']}R / {POWER['mu']}R)² = {n_needed:.0f}",
              f"- With every H1 signal filled: {len(h1) * scale:.0f}/year → {years:.1f} years → "
              f"{'PASS' if years <= POWER['years_max'] else 'FAIL'} (limit {POWER['years_max']})",
              f"- With only the touched entries filled: {h1_touched * scale:.0f}/year → {years_t:.1f} years → "
              f"{'PASS' if years_t <= POWER['years_max'] else 'FAIL'}",
              "", "No backtest was run, no fill simulated, no parameter changed."]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default=YEAR)
    ap.add_argument("--out", default="docs/backtest")
    a = ap.parse_args(argv)
    alpaca._get = _blocked  # cache only: a miss raises instead of fetching
    os.environ.setdefault("ALPACA_KEY_ID", "offline")
    os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
    try:
        rows, coverage, year_days, secs = count(a.year)
    except CacheMiss as e:
        rows, coverage, year_days, secs = [], collections.Counter({"aborted: cache miss before the day loop": 1}), [], 0
        print(f"cache miss: {e}")
    run = os.environ.get("GITHUB_RUN_ID", "")
    os.makedirs(a.out, exist_ok=True)
    with open(f"{a.out}/intraday-signal-count-{a.year}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["day", "t", "rank", "rel_vol", "at", "setup", "entry", "stop_ref", "touched"])
        w.writeheader()
        w.writerows(rows)
    text = render(rows, coverage, year_days, secs, a.year, run)
    with open(f"{a.out}/intraday-signal-count-{a.year}.md", "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
