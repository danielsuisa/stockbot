"""Pre-registered penny-stock replay. Locked on 2026-10-14, before this run.

Nothing in this module places or transmits an order. A GO is a paper-trade result, not a merge and not
permission to trade. The listener is unchanged.

In-sample is 2016-01-01 through 2022-12-31. Out-of-sample is 2023-01-01 through the last Alpaca session
before the run's New York date. The opening-range book and the VWAP book are separate $460 accounts. Only
the out-of-sample window of each is gated. In-sample numbers cannot flip the verdict. An incomplete
download is NO-GO.

The universe is Alpaca's active and inactive NYSE, NASDAQ, and NYSE American names, one to five letters,
excluding fund-like names. OTC is not in this tape. Inactive names that still have bars are kept. Names
Alpaca never listed are absent, so this is not a CRSP delist tape. Prices are raw. A split can look like a
gap, and a reused symbol is not split apart.

The headline filter is on only when the earliest AAPL headline Alpaca returns is dated on or before
2016-01-01. Otherwise both books run with no catalyst, and the report says so. That probe does not change
the gap, the relative volume, or the exits.
"""
import csv
import datetime as dt
import json
import re
import statistics
import traceback
from collections import defaultdict
from pathlib import Path

from bot import alpaca
from bot.desk_gate import YEARS, judge
from bot.penny_play import flat_minute, gap_ok, pick, play, rvol_ok, score
from bot.ticket_backtest import FUND

START, OOS, WARM = "2016-01-01", "2023-01-01", "2015-06-01"
EXCHANGES = ("NYSE", "NASDAQ", "AMEX")
NY = alpaca.NY


def names(assets):
    """Listed stocks, including inactive. AMEX is NYSE American, where a lot of sub-$5 names trade."""
    return sorted({a["symbol"] for a in assets if a.get("exchange") in EXCHANGES
                   and re.fullmatch(r"[A-Z]{1,5}", a.get("symbol") or "")
                   and not FUND.search(a.get("name") or "")})


def _end():
    today = dt.datetime.now(NY).date().isoformat()
    cal = alpaca.calendar(WARM, today)
    days = [d for d in sorted(cal) if d < today]
    return today, days, {d: cal[d] for d in days}


def _fmt(x, spec="{:+.3f}"):
    return "—" if x is None else spec.format(x)


def _book_md(title, rows):
    oos = [r for r in rows if r["day"] >= OOS]
    ins = [r for r in rows if r["day"] < OOS]
    g = judge(oos, YEARS)
    lines = [f"### {title}", "", f"Verdict: **{'GO' if g['go'] else 'NO-GO'}**.", ""]
    for label, ok in g["checks"]:
        lines.append(f"- {'pass' if ok else 'fail'}: {label}")
    lines += ["", "| Sample | Trades | Mean net R | t | Random p95 | Win rate | Mean R without commission |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for name, part, show in (("2023–2026", oos, g), ("2016–2022", ins, None)):
        xs = [r["net_r"] for r in part]
        bare = [r["net_r_no_commission"] for r in part if r.get("net_r_no_commission") is not None]
        m = sum(xs) / len(xs) if xs else None
        win = sum(r["net_r"] > 0 for r in part) / len(part) if part else None
        lines.append(
            f"| {name} | {len(part)} | {_fmt(m)} | {_fmt(judge(part)['t'] if part else None, '{:.2f}')} | "
            f"{_fmt(show['p95'] if show else None)} | {_fmt(win, '{:.1%}')} | "
            f"{_fmt(sum(bare) / len(bare) if bare else None)} |")
    lines += ["", "| Year | Trades | Mean net R |", "|---|---:|---:|"]
    for y in ("2016", "2017", "2018", "2019", "2020", "2021", "2022", *YEARS):
        part = [r["net_r"] for r in rows if r["day"][:4] == y]
        lines.append(f"| {y} | {len(part)} | {_fmt(sum(part) / len(part) if part else None)} |")
    hair = sum(r["cost_mode"] == "haircut" for r in rows)
    quote = sum(r["cost_mode"] == "quote" for r in rows)
    small = sum(r["shares"] < 100 for r in rows)
    inactive = sum(r.get("listed") == "inactive" for r in rows)
    lines += ["",
              f"Quotes: {quote} trades charged the SIP spread, {hair} took the 1% haircut. "
              f"The $0.35 minimum binds below 100 shares: {small} of {len(rows)}. "
              f"Inactive names: {inactive} of {len(rows)}.",
              ""]
    return lines, g


def render(result):
    news = result.get("news") or {}
    if news.get("used"):
        catalyst = (f"Headlines are on. The earliest AAPL headline Alpaca returned is {news.get('earliest')}. "
                    "A name needed one in the 24 hours ending 09:35 New York.")
    else:
        catalyst = (f"Headlines are off. The earliest AAPL headline Alpaca returned is {news.get('earliest')}. "
                    "That does not cover 2016-01-01, so neither book required a catalyst.")
    lines = [
        f"# Penny stocks in play — {result['run']}",
        "",
        "Two separate $460 books, long only, one name a day. The gap is the 09:30 five-minute open against the "
        "prior close. Relative volume is that bar against the median of the prior 20 opening bars. "
        "This replay does not place or transmit an order.",
        "",
        catalyst,
        "",
        f"Window asked for: in-sample {START} through 2022-12-31, out-of-sample {OOS} through {result.get('end')}. "
        f"First daily bar in the download: {result.get('first_bar')}. Universe {result.get('universe')} names, "
        f"{result.get('inactive_names')} of them inactive. Gap candidates {result.get('gap_candidates')}, "
        f"relative-volume passers {result.get('rvol_pass')}"
        + (f", of which {result.get('with_news')} had a headline." if news.get("used") else "."),
        "",
    ]
    verdicts = []
    for key, title in (("orb", "Opening-range breakout"), ("vwap", "VWAP pullback")):
        block, g = _book_md(title, result.get(key) or [])
        lines += block
        verdicts.append((title, g["go"]))
    lines += ["## Verdict", ""]
    for title, go in verdicts:
        lines.append(f"- {title}: {'GO' if go else 'NO-GO'}.")
    if not result.get("complete", True):
        lines += ["", "The download did not finish. The verdict is NO-GO either way."]
    lines.append("")
    return "\n".join(lines)


def _gap_candidates(daily, sessions):
    index = {d: i for i, d in enumerate(sessions)}
    need, cands = defaultdict(set), []
    for sym, rows in daily.items():
        by = {r[0]: r for r in rows}
        for day, i in index.items():
            if day < START or i < 20:
                continue
            prev, today = by.get(sessions[i - 1]), by.get(day)
            if not prev or not today or prev[4] <= 0:
                continue
            if not gap_ok(prev[4], today[1]):
                continue
            cands.append((day, sym, prev[4], i))
            for j in range(i - 20, i + 1):
                need[sessions[j]].add(sym)
    return cands, need


def _pass_rvol(cands, need, sessions):
    cached = {}

    def opening(day):
        if day not in cached:
            cached[day] = alpaca.opening_bars(sorted(need.get(day) or []), day)
        return cached[day]

    passed = []
    for n, day in enumerate(sorted(need)):
        opening(day)
        if n % 50 == 0:
            print(f"open5 {day} {n}/{len(need)} requests {alpaca.REQUESTS[0]}", flush=True)
    for day, sym, prev, i in cands:
        bar = opening(day).get(sym)
        if not bar or not gap_ok(prev, bar[0]):
            continue
        prior = []
        for j in range(i - 20, i):
            prev_bar = opening(sessions[j]).get(sym)
            prior.append(None if not prev_bar else prev_bar[4])
        if not rvol_ok(bar[4], prior):
            continue
        med = statistics.median([v for v in prior if v])
        passed.append({"day": day, "t": sym, "gap": bar[0] / prev - 1, "rvol": bar[4] / med, "i": i})
    return passed


def _spread(sym, day, minute):
    """The last SIP quote during the fill minute. The window ends as that minute ends."""
    h, m = (int(x) for x in minute.split(":")[:2])
    t = h * 60 + m + 1
    end = f"{t // 60:02d}:{t % 60:02d}"
    q = alpaca.quote_at(sym, alpaca.utc(day, end))
    if not q:
        return None
    return q[1] - q[0]


def _trade_days(passed, cal, listed, use_news):
    by_day = defaultdict(list)
    for row in passed:
        by_day[row["day"]].append(row)
    if use_news:
        kept = 0
        for day, rows in list(by_day.items()):
            prev = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
            hits = alpaca.news_hit([r["t"] for r in rows], alpaca.utc(prev, "09:35"), alpaca.utc(day, "09:35"),
                                   f"penny24_{day}")
            by_day[day] = [r for r in rows if r["t"] in hits]
            kept += len(by_day[day])
    else:
        kept = len(passed)
    books = {"orb": [], "vwap": []}
    for n, day in enumerate(sorted(by_day)):
        rows = by_day[day]
        if not rows:
            continue
        minutes = alpaca.minute_bars([r["t"] for r in rows], day)
        flat = flat_minute(cal[day][1])
        ready = []
        for row in rows:
            bars = [b for b in minutes.get(row["t"], []) if "09:30" <= b[0] <= flat]
            for variant in ("orb", "vwap"):
                setup = play(bars, flat, variant)
                if not setup:
                    continue
                scored = score(setup, None)
                if not scored or scored["net_r"] is None:
                    continue
                scored.update(day=day, t=row["t"], variant=variant, gap=row["gap"], rvol=row["rvol"],
                              listed=listed.get(row["t"]))
                ready.append(scored)
        for variant in ("orb", "vwap"):
            chosen = pick([r for r in ready if r["variant"] == variant])
            if not chosen:
                continue
            spread = _spread(chosen["t"], day, chosen["fill_minute"])
            rescored = score(chosen, spread)
            rescored.update(day=day, t=chosen["t"], variant=variant, gap=chosen["gap"], rvol=chosen["rvol"],
                            listed=chosen["listed"])
            books[variant].append(rescored)
        if n % 25 == 0:
            print(f"minutes {day} requests {alpaca.REQUESTS[0]}", flush=True)
    return books, kept


def run():
    run_day, sessions, cal = _end()
    end = sessions[-1]
    earliest = alpaca.news_earliest("AAPL")
    use_news = bool(earliest) and earliest[:10] <= START
    raw = alpaca.assets()
    symbols = names(raw)
    listed = {a["symbol"]: a.get("status") for a in raw}
    daily = {}
    for i in range(0, len(symbols), alpaca.CHUNK):
        part = symbols[i:i + alpaca.CHUNK]
        daily.update(alpaca.daily(part, WARM, end))
        print(f"daily {i + len(part)}/{len(symbols)} requests {alpaca.REQUESTS[0]}", flush=True)
    first = min((rows[0][0] for rows in daily.values() if rows), default=None)
    cands, need = _gap_candidates(daily, [s for s in sessions if s <= end])
    print(f"gap candidates {len(cands)} opening-bar days {len(need)}", flush=True)
    passed = _pass_rvol(cands, need, [s for s in sessions if s <= end])
    print(f"rvol pass {len(passed)} news {use_news} earliest {earliest}", flush=True)
    books, kept = _trade_days(passed, cal, listed, use_news)
    return {"run": run_day, "end": end, "first_bar": first, "universe": len(symbols),
            "inactive_names": sum(1 for s in symbols if listed.get(s) == "inactive"),
            "gap_candidates": len(cands), "rvol_pass": len(passed), "with_news": kept,
            "news": {"used": use_news, "earliest": earliest}, "complete": True,
            "requests": alpaca.REQUESTS[0], **books}


def _write(result):
    stem = Path(f"docs/backtest/penny-play-{result['run']}")
    stem.parent.mkdir(parents=True, exist_ok=True)
    text = render(result)
    if not result.get("complete", True):
        text = text.replace("**GO**", "**NO-GO**").replace(": GO.", ": NO-GO.")
    stem.with_suffix(".md").write_text(text, encoding="utf-8")
    fields = ["day", "t", "variant", "gap", "rvol", "entry", "stop", "exit", "kind_exit", "fill_minute",
              "shares", "net_r", "net_r_short", "net_r_no_commission", "pnl", "cost_mode", "listed"]
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fields, extrasaction="ignore")
        w.writeheader()
        for variant in ("orb", "vwap"):
            for r in result.get(variant) or []:
                w.writerow(r)
    slim = {k: v for k, v in result.items() if k not in ("orb", "vwap")}
    for variant in ("orb", "vwap"):
        slim[variant] = judge([r for r in result.get(variant) or [] if r["day"] >= OOS])
    stem.with_suffix(".json").write_text(json.dumps(slim, indent=1, default=str), encoding="utf-8")
    print(f"wrote {stem}.md", flush=True)


def main():
    try:
        result = run()
    except Exception:
        traceback.print_exc()
        run_day = dt.datetime.now(NY).date().isoformat()
        stub = {"run": run_day, "end": None, "first_bar": None, "universe": 0, "inactive_names": 0,
                "gap_candidates": 0, "rvol_pass": 0, "with_news": 0, "news": {"used": False, "earliest": None},
                "complete": False, "orb": [], "vwap": [], "requests": alpaca.REQUESTS[0]}
        _write(stub)
        return 1
    _write(result)
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
