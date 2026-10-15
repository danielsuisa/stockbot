"""Pre-registered post-earnings drift replay. Locked on 2026-10-15, before this run.

Nothing in this module places or transmits an order. A GO is a paper-trade result, not a merge and not
permission to trade. The listener is unchanged. The 20-session IBKR Pro book is the only gated book.
"""
import csv
import datetime as dt
import json
import traceback
from collections import defaultdict
from pathlib import Path

from bot import alpaca
from bot.desk_gate import YEARS, judge
from bot.pead import (
    GATE_HOLD, HOLDS, beats_spy, event_ok, gated, headline_matches, in_prior_24h, mark, schedule, score,
    spy_return,
)
from bot.premarket_catalyst import GAPS, split_sane, universe
from bot.premarket_catalyst_backtest import CACHE, _end, _get, _probe, _spread, corporate_actions, daily

START, OOS, WARM = "2016-01-01", "2023-01-01", "2015-06-01"
NY = alpaca.NY
NEWS = "https://data.alpaca.markets/v1beta1/news"


def _candidates(adj, sessions, splits):
    index = {d: i for i, d in enumerate(sessions)}
    out, skipped_split = [], 0
    for sym, rows in adj.items():
        if len(rows) < 22:
            continue
        by = {r[0]: r for r in rows}
        banned = splits.get(sym) or set()
        for i in range(1, len(rows) - 1):
            day = rows[i][0]
            if day < START or day not in index:
                continue
            si = index[day]
            if si < 20 or si + 1 >= len(sessions):
                continue
            prev = by.get(sessions[si - 1])
            if not prev:
                continue
            entry_day = sessions[si + 1]
            if rows[i + 1][0] != entry_day:
                continue
            if day in banned or entry_day in banned:
                skipped_split += 1
                continue
            prior_v, prior_d = [], []
            for j in range(si - 20, si):
                b = by.get(sessions[j])
                if not b or b[4] is None or b[5] is None:
                    prior_v.append(None)
                    prior_d.append(None)
                else:
                    prior_v.append(b[5])
                    prior_d.append(b[4] * b[5])
            if not event_ok(prev[4], rows[i], prior_v, prior_d):
                continue
            entry, stop = rows[i + 1][1], rows[i][3]
            if entry is None or stop is None or entry <= stop or entry <= 0:
                continue
            adv = sum(x for x in prior_d if x) / max(1, sum(x is not None for x in prior_d))
            future = []
            for d in sessions[si + 1:si + 1 + max(HOLDS)]:
                b = by.get(d)
                future.append(None if b is None else (d, b[1], b[2], b[3], b[4]))
            holds = {}
            for n in HOLDS:
                long = mark(future, entry, stop, n, "long")
                short = mark(future, entry, stop, n, "short")
                if not long or not short:
                    continue
                holds[n] = {"exit": long[0], "kind": long[1], "exit_day": long[2], "short": short[0]}
            if GATE_HOLD not in holds:
                continue
            out.append({"t": sym, "event_day": day, "entry_day": entry_day, "day": entry_day,
                        "entry": entry, "stop": stop, "adv": adv, "holds": holds})
    return out, skipped_split


def _news(symbols, day):
    """Symbols whose headline matches in the prior 24 hours. A cut-off page excludes the day."""
    if not symbols:
        return set(), False
    path = CACHE / "pead-news" / f"{day}.json.gz"
    got = alpaca._read(path)
    if got is None:
        end = dt.datetime.fromisoformat(f"{day}T09:30").replace(tzinfo=NY)
        start = end - dt.timedelta(hours=24)
        rows, truncated = [], False
        for i in range(0, len(symbols), 30):
            part = symbols[i:i + 30]
            token, pages = None, 0
            while pages < 40:
                js = _get(NEWS, {"symbols": ",".join(part),
                                 "start": start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "end": end.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "limit": 50, "sort": "asc", **({"page_token": token} if token else {})})
                rows.extend(js.get("news") or [])
                token = js.get("next_page_token")
                pages += 1
                if not token:
                    break
            else:
                truncated = True
        got = {"news": rows, "truncated": truncated}
        alpaca._write(path, got)
    if got.get("truncated"):
        return set(), True
    hit = set()
    want = set(symbols)
    for item in got.get("news") or []:
        if not headline_matches(item.get("headline")) or not in_prior_24h(item.get("created_at"), day):
            continue
        hit |= set(item.get("symbols") or []) & want
    return hit, False


def _with_news(cands):
    by_day = defaultdict(list)
    for row in cands:
        by_day[row["event_day"]].append(row)
    kept, truncated = [], 0
    for n, day in enumerate(sorted(by_day)):
        hits, cut = _news([r["t"] for r in by_day[day]], day)
        if cut:
            truncated += 1
            continue
        kept.extend(r for r in by_day[day] if r["t"] in hits)
        if n % 25 == 0:
            print(f"news {day} kept {len(kept)} requests {alpaca.REQUESTS[0]}", flush=True)
    return kept, truncated


def _close_map(rows):
    return {r[0]: r for r in rows}


def _books(cands, listed, raw, adj, spy):
    """Schedule each hold, then price the spread once per taken trade."""
    books = {}
    spy_by = _close_map(spy)
    raw_by = {s: _close_map(rows) for s, rows in raw.items()}
    adj_by = {s: _close_map(rows) for s, rows in adj.items()}
    for n in HOLDS:
        rows = []
        for c in cands:
            h = c["holds"].get(n)
            if not h:
                continue
            rows.append({**c, "exit_day": h["exit_day"], "exit": h["exit"], "kind": h["kind"],
                         "short": h["short"]})
        taken = schedule(rows)
        priced = []
        for c in taken:
            raw_c = (raw_by.get(c["t"]) or {}).get(c["entry_day"])
            adj_c = (adj_by.get(c["t"]) or {}).get(c["entry_day"])
            spread = _spread(c["t"], c["entry_day"], "09:30", None if not raw_c else raw_c[4],
                             None if not adj_c else adj_c[4])
            scored = score(c["entry"], c["exit"], c["short"], c["stop"], spread)
            if not scored:
                continue
            spy_e = spy_by.get(c["entry_day"])
            spy_x = spy_by.get(c["exit_day"])
            scored.update(day=c["day"], t=c["t"], event_day=c["event_day"], exit_day=c["exit_day"],
                          entry=c["entry"], stop=c["stop"], exit=c["exit"], kind_exit=c["kind"],
                          hold=n, adv=c["adv"], listed=listed.get(c["t"]),
                          spy=spy_return(None if not spy_e else spy_e[1], None if not spy_x else spy_x[4]))
            priced.append(scored)
        books[n] = priced
        print(f"hold {n} trades {len(priced)}", flush=True)
    return books


def _fmt(x, spec="{:+.3f}"):
    return "—" if x is None else spec.format(x)


def _sample_line(name, rows, show):
    xs = [r["net_r"] for r in rows]
    m = sum(xs) / len(xs) if xs else None
    win = sum(r["net_r"] > 0 for r in rows) / len(rows) if rows else None
    lite = [r["lite_r"] for r in rows]
    lm = sum(lite) / len(lite) if lite else None
    ret = [r["net_return"] for r in rows]
    spy = [r["spy"] for r in rows if r.get("spy") is not None]
    rm = sum(ret) / len(ret) if ret else None
    sm = sum(spy) / len(spy) if spy else None
    return (f"| {name} | {len(rows)} | {_fmt(m)} | {_fmt(judge(rows)['t'] if rows else None, '{:.2f}')} | "
            f"{_fmt(show['p95'] if show else None)} | {_fmt(win, '{:.1%}')} | {_fmt(lm)} | {_fmt(rm, '{:+.2%}')} | "
            f"{_fmt(sm, '{:+.2%}')} |")


def render(result):
    lines = [
        f"# Post-earnings drift — {result['run']}",
        "",
        "Long only, one position, $460, settled the session after the exit. The earnings day is an Alpaca "
        "headline in the prior 24 hours matching earnings, results, quarter, EPS, or revenue, plus the locked "
        "gap, volume, range, price, and ADV screen. Prices are split-adjusted. The gated book is the 20-session "
        "hold with IBKR Pro fees plus the spread. Holds of 10 and 40, and IBKR Lite, are reported. "
        "This replay does not place or transmit an order.",
        "",
        f"Window: in-sample {START} through 2022-12-31, out-of-sample entries from {OOS} through {result.get('end')}. "
        f"First split-adjusted daily bar: {result.get('first_bar')}.",
        "",
        f"Universe {result.get('universe')} names, {result.get('inactive_names')} inactive, "
        f"{result.get('inactive_with_bars')} inactive names with a daily bar.",
        "",
        f"AAPL split check: {result.get('aapl')}. Split sessions skipped: {result.get('split_days')}. "
        f"Price events {result.get('events')}. With a matching headline {result.get('with_news')}. "
        f"News days cut off {result.get('news_truncated')}.",
        "",
    ]
    verdict = result.get("verdict")
    lines.append(f"Verdict: **{'GO' if verdict and verdict.get('go') and result.get('complete') else 'NO-GO'}**.")
    lines.append("")
    if verdict:
        for label, ok in verdict["checks"]:
            lines.append(f"- {'pass' if ok else 'fail'}: {label}")
    if not result.get("complete"):
        lines.append("- fail: the download finished")
    lines += ["", "The gate is the 20-session IBKR Pro book. Mean return is the net dollar result divided by "
              "the dollars deployed. SPY is the split-adjusted open-to-close of the same window, with no cost.",
              ""]
    for n in HOLDS:
        rows = (result.get("books") or {}).get(n) or (result.get("books") or {}).get(str(n)) or []
        oos = [r for r in rows if r["day"] >= OOS]
        ins = [r for r in rows if r["day"] < OOS]
        g = gated(oos) if n == GATE_HOLD else judge(oos)
        title = f"Hold {n}" + (" — gated" if n == GATE_HOLD else " — reported")
        lines += [f"### {title}", ""]
        if n != GATE_HOLD:
            for label, ok in g["checks"]:
                lines.append(f"- {'pass' if ok else 'fail'}: {label}")
            spy_ok = beats_spy(oos)
            lines.append(f"- {'pass' if spy_ok else 'fail'}: mean net return above buy-and-hold SPY "
                         "(reported, not the verdict)")
            lines.append("")
        lines += ["| Sample | Trades | Mean net R | t | Random p95 | Win rate | Lite mean R | "
                  "Mean net return | SPY |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
                  _sample_line("2023–now", oos, g),
                  _sample_line("2016–2022", ins, None),
                  "",
                  "| Year | Trades | Mean net R |", "|---|---:|---:|"]
        for y in ("2016", "2017", "2018", "2019", "2020", "2021", "2022", *YEARS):
            part = [r["net_r"] for r in rows if r["day"][:4] == y]
            lines.append(f"| {y} | {len(part)} | {_fmt(sum(part) / len(part) if part else None)} |")
        inactive = sum(r.get("listed") == "inactive" for r in rows)
        quotes = sum(r.get("cost_mode") == "quote" for r in rows)
        lines += ["", f"Inactive names: {inactive} of {len(rows)}. Quotes: {quotes} scaled SIP spreads, "
                  f"{len(rows) - quotes} haircuts.", ""]
    lines += ["## Survivorship", ""]
    probe = result.get("probe") or []
    covered = sum(1 for r in probe if r.get("in_assets") and r.get("bars"))
    lines.append(f"Known 2016–2018 delistings in the asset file with bars: {covered} of {len(probe)}.")
    for r in probe:
        where = "in" if r.get("in_assets") else "NOT in"
        status = f", status {r['status']}" if r.get("status") else ""
        lines.append(f"- {r['symbol']} ({r['last']}): {where} the asset file{status}, {r.get('bars')} raw bars.")
    lines.append("")
    for gap in GAPS:
        lines.append(f"- {gap}")
    go = bool(verdict and verdict.get("go") and result.get("complete"))
    lines += ["", "## Verdict", "", f"- 20-session IBKR Pro: {'GO' if go else 'NO-GO'}.", ""]
    return "\n".join(lines)


def _write(result):
    stem = Path(f"docs/backtest/pead-{result['run']}")
    stem.parent.mkdir(parents=True, exist_ok=True)
    text = render(result)
    if not result.get("complete", True):
        text = text.replace("**GO**", "**NO-GO**").replace(": GO.", ": NO-GO.")
    stem.with_suffix(".md").write_text(text, encoding="utf-8")
    fields = ["hold", "day", "t", "event_day", "exit_day", "entry", "stop", "exit", "kind_exit", "shares",
              "net_r", "net_r_short", "net_return", "lite_r", "lite_return", "spy", "pnl", "cost_mode", "listed"]
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fields, extrasaction="ignore")
        w.writeheader()
        for n in HOLDS:
            for r in (result.get("books") or {}).get(n) or []:
                w.writerow(r)
    slim = {k: v for k, v in result.items() if k != "books"}
    slim["books"] = {}
    for n, rows in (result.get("books") or {}).items():
        oos = [r for r in rows if r["day"] >= OOS]
        slim["books"][n] = gated(oos) if n == GATE_HOLD else judge(oos)
        slim["books"][n]["spy_ok"] = beats_spy(oos)
    stem.with_suffix(".json").write_text(json.dumps(slim, indent=1, default=str), encoding="utf-8")
    print(f"wrote {stem}.md", flush=True)


def run():
    run_day, sessions, _cal = _end()
    end = sessions[-1]
    assets = alpaca.assets()
    symbols, indexed = universe(assets)
    inactive = {s for s in symbols if indexed[s].get("status") == "inactive"}
    print(f"universe {len(symbols)} inactive {len(inactive)}", flush=True)
    splits = corporate_actions(WARM, end, symbols)
    adj = daily(symbols, WARM, end, "split")
    raw = daily([s for s in symbols if adj.get(s)], WARM, end, "raw")
    spy = daily(["SPY"], WARM, end, "split").get("SPY", [])
    first = min((rows[0][0] for rows in adj.values() if rows), default=None)
    aapl_ok, aapl = split_sane({r[0]: r[4] for r in adj.get("AAPL", [])}, sessions)
    probe = _probe(end)
    from bot.premarket_catalyst import index_assets
    full = index_assets(assets)
    for row in probe:
        asset = full.get(row["symbol"])
        row["in_assets"] = bool(asset)
        row["status"] = asset.get("status") if asset else None
    with_bars = {s for s, rows in adj.items() if rows}
    if not aapl_ok:
        return _result(run_day, end, first, symbols, inactive, with_bars, aapl, 0, probe, 0, 0, 0, {}, False)
    cands, skipped = _candidates(adj, [s for s in sessions if s <= end], splits)
    print(f"events {len(cands)} split skips {skipped}", flush=True)
    kept, truncated = _with_news(cands)
    print(f"headlines {len(kept)} truncated days {truncated}", flush=True)
    listed = {s: indexed[s].get("status") for s in symbols}
    books = _books(kept, listed, raw, adj, spy)
    result = _result(run_day, end, first, symbols, inactive, with_bars, aapl, skipped, probe,
                     len(cands), len(kept), truncated, books, True)
    result["verdict"] = gated([r for r in books[GATE_HOLD] if r["day"] >= OOS])
    return result


def _result(run_day, end, first, symbols, inactive, with_bars, aapl, skipped, probe, events, with_news,
            truncated, books, complete):
    return {"run": run_day, "end": end, "first_bar": first, "universe": len(symbols),
            "inactive_names": len(inactive), "inactive_with_bars": len(inactive & with_bars),
            "aapl": aapl, "split_days": skipped, "probe": probe, "events": events, "with_news": with_news,
            "news_truncated": truncated, "books": books, "complete": complete,
            "requests": alpaca.REQUESTS[0], "verdict": None}


def main():
    try:
        result = run()
    except Exception:
        traceback.print_exc()
        run_day = dt.datetime.now(NY).date().isoformat()
        stub = {"run": run_day, "end": None, "first_bar": None, "universe": 0, "inactive_names": 0,
                "inactive_with_bars": 0, "aapl": None, "split_days": 0, "probe": [], "events": 0,
                "with_news": 0, "news_truncated": 0, "books": {}, "complete": False,
                "requests": alpaca.REQUESTS[0], "verdict": None}
        _write(stub)
        return 1
    _write(result)
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
