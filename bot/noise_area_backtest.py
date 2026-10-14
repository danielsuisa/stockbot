"""Pre-registered Noise Area replay on QQQ and SPY. Locked on 2026-10-14, before this run.

Nothing in this module places or transmits an order. A GO is a paper-trade result, not a merge and not permission
to trade. The listener is unchanged. Overnight momentum is not in this file.

Rules, from the 2026-10-14 research note, fixed before any result. SIP 1-minute bars, regular session only.
QQQ is the hypothesis. SPY is the same rule reported beside it, and it is not in the gate. In-sample is
2016-01-01 through 2022-12-31. Out-of-sample is 2023-01-01 through the last Alpaca session before the run's
New York date. Both are reported. Only the out-of-sample window is gated.

sigma at a decision minute is the mean, over the prior 14 sessions that have that minute, of the absolute move
from the 09:30 open to the close of the bar that ends at the decision minute. Today is not in the average.
Upper band = max(09:30 open, previous close) x (1 + sigma). Lower band = min(09:30 open, previous close) x
(1 - sigma). The multiplier is fixed at 1.

Decision times are 10:00, 10:30, ..., 15:30. The signal is that minute's close (the bar labeled one minute
earlier, because Alpaca labels a bar by its start). The fill is the next bar's open. Long when the close is
above the upper band, short when it is below the lower band. The stop, checked at the same times, takes a long
out when the close is below max(upper band, session VWAP) and a short out when the close is above min(lower
band, session VWAP). A missing sigma does not open a trade and does not move a stop. One entry per side per
day. Anything still open exits at the last regular minute's close (15:59, or the minute before an early close).
The long-only book never shorts. A cash account cannot short, so that book is part of the gate.

Costs: $0.0035 a share, $0.35 minimum per order, on the entry and on the exit, plus 1 cent of slippage against
the trade on each side. The gated size is $10,000 of notional, whole shares, no leverage. A $460 whole-share
book is reported and is not gated. Previous close is the prior session's SIP daily close. Prices are raw.

GO, all required, on QQQ out-of-sample, else NO-GO: at least 300 trades; net mean return per trade > 0 with
t > 2; annualized Sharpe > 0.8 (daily account returns, flat days included, 252 days, no risk-free rate); mean
daily account return beats the plain open-to-close return on the same sessions; profitable in at least 70% of
out-of-sample calendar years (compounded); the long-only book also has a positive mean trade return. An
incomplete download is NO-GO. In-sample numbers, SPY, and the $460 book cannot flip the verdict.
"""
import csv
import datetime as dt
import json
import statistics
from pathlib import Path

from bot import alpaca
from bot.noise_area import (ACCOUNT, DECISIONS, NOTIONAL, minute_before, regular_bars, simulate_day, trailing_sigma)

START, OOS = "2016-01-01", "2023-01-01"
WARM = "2015-11-01"  # sigma's 14 days. If SIP starts later, the first trades wait until 14 sessions exist.
SYMBOLS = ("QQQ", "SPY")
NY = alpaca.NY


def sharpe(rs):
    """Annualized Sharpe of daily account returns. None when the sample is too small or has no variation."""
    if len(rs) < 2:
        return None
    sd = statistics.stdev(rs)
    if sd == 0:
        return None
    return (sum(rs) / len(rs)) / sd * (252 ** 0.5)


def tstat(xs):
    if len(xs) < 2:
        return None
    sd = statistics.stdev(xs)
    if sd == 0:
        return None
    return (sum(xs) / len(xs)) / (sd / len(xs) ** 0.5)


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def year_profit(pairs):
    """{year: compounded return > 0} for the dated daily returns."""
    groups = {}
    for day, ret in pairs:
        groups.setdefault(day[:4], []).append(ret)
    out = {}
    for year, xs in groups.items():
        eq = 1.0
        for r in xs:
            eq *= 1 + r
        out[year] = eq - 1
    return out


def pack_stats(trades, daily):
    """`daily` is [(day, strategy return, benchmark or None)] inside one sample."""
    rs = [r for _, r, _ in daily]
    rets = [t["ret"] for t in trades]
    years = year_profit([(d, r) for d, r, _ in daily])
    benches = [b for _, _, b in daily if b is not None]
    eq, peak, worst = 1.0, 1.0, 0.0
    for r in rs:
        eq *= 1 + r
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1)
    prof = [y for y, total in years.items() if total > 0]
    return {"n": len(trades), "mean": _mean(rets), "t": tstat(rets),
            "win": (sum(r > 0 for r in rets) / len(rets)) if rets else None,
            "sharpe": sharpe(rs), "daily_mean": _mean(rs), "bench_mean": _mean(benches),
            "years": len(years), "years_ok": len(prof), "year_ret": years,
            "total": eq - 1, "max_dd": worst}


def judge(oos, long_oos, complete):
    """The locked QQQ out-of-sample checks. `oos` and `long_oos` are pack_stats results."""
    ratio = (oos["years_ok"] / oos["years"]) if oos["years"] else 0
    checks = [
        (f"at least 300 out-of-sample trades ({oos['n']})", oos["n"] >= 300),
        ("net mean return per trade > 0 with t > 2",
         oos["mean"] is not None and oos["mean"] > 0 and (oos["t"] or 0) > 2),
        ("annualized Sharpe > 0.8", oos["sharpe"] is not None and oos["sharpe"] > 0.8),
        ("beats the plain open-to-close return",
         oos["daily_mean"] is not None and oos["bench_mean"] is not None and oos["daily_mean"] > oos["bench_mean"]),
        (f"profitable in at least 70% of out-of-sample years ({oos['years_ok']}/{oos['years']})", ratio >= 0.70),
        ("long-only variant net positive", long_oos["mean"] is not None and long_oos["mean"] > 0),
    ]
    return checks, bool(complete and all(ok for _, ok in checks))


def _sample(day):
    return "oos" if day >= OOS else "is"


def _empty_books():
    books = {}
    for symbol in SYMBOLS:
        for side, notion in (("both", NOTIONAL), ("long", NOTIONAL), ("both", ACCOUNT), ("long", ACCOUNT)):
            if symbol == "SPY" and notion == ACCOUNT:
                continue
            books[(symbol, side, notion)] = {"trades": [], "daily": []}
    return books


def replay(feed, today=None, out=None):
    """The whole window. Checkpoints every 100 sessions are not a verdict (complete stays false)."""
    today = today or dt.datetime.now(NY).date().isoformat()
    sessions = [(d, c) for d, c in feed.sessions(WARM, today) if d < today]
    if not sessions:
        raise RuntimeError("no sessions in the window")
    end = [d for d, _ in sessions if d >= START][-1]
    daily = feed.daily(SYMBOLS, WARM, end)
    closes = {s: {b[0]: b[4] for b in daily.get(s) or []} for s in SYMBOLS}
    past = {s: {d: [] for d in DECISIONS} for s in SYMBOLS}
    books, failed = _empty_books(), []
    trade_days = [d for d, _ in sessions if d >= START]
    for n, (day, close) in enumerate(sessions, 1):
        try:
            minutes = feed.minutes(SYMBOLS, day)
        except Exception as e:
            if day >= START:
                failed.append(day)
                print(f"{day}: download failed ({type(e).__name__})", flush=True)
            continue
        prev_day = next((d for d, _ in reversed(sessions[:n - 1])), None)
        for symbol in SYMBOLS:
            bars = regular_bars(minutes.get(symbol) or [], close)
            if day >= START and not bars:
                failed.append(day)
                print(f"{day} {symbol}: no regular bars", flush=True)
                continue
            prev = closes[symbol].get(prev_day) if prev_day else None
            by = {b[0]: b for b in bars}
            opened = by.get("09:30")
            moves = {}
            if opened and opened[1] > 0:
                for decision in DECISIONS:
                    bar = by.get(minute_before(decision))
                    if bar is not None:
                        moves[decision] = bar[4] / opened[1] - 1
            if day >= START and bars:
                sigmas = {d: trailing_sigma(past[symbol][d]) for d in DECISIONS}
                for side, notion in (("both", NOTIONAL), ("long", NOTIONAL), ("both", ACCOUNT), ("long", ACCOUNT)):
                    if symbol == "SPY" and notion == ACCOUNT:
                        continue
                    got = simulate_day(bars, prev, sigmas, side=side, notional=notion)
                    slot = books[(symbol, side, notion)]
                    for trade in got["trades"]:
                        slot["trades"].append({"day": day, "symbol": symbol, "side_book": side,
                                               "notional": notion, **trade})
                    if got["status"] == "ok":
                        slot["daily"].append((day, got["daily"], got["benchmark"]))
            for decision, mv in moves.items():
                past[symbol][decision].append(mv)
        if day >= START and (n % 100 == 0 or day == end):
            done = sum(1 for d in trade_days if d <= day)
            print(f"{day}: {done}/{len(trade_days)} sessions, failed {len(set(failed))}", flush=True)
            if out and day != end:
                write_report(_result(books, failed, START, end, False), out)
    return _result(books, list(dict.fromkeys(failed)), START, end, not failed)


def _result(books, failed, start, end, complete):
    stats = {}
    for key, slot in books.items():
        symbol, side, notion = key
        label = f"{symbol}|{side}|{int(notion)}"
        stats[label] = {
            "is": pack_stats([t for t in slot["trades"] if _sample(t["day"]) == "is"],
                             [row for row in slot["daily"] if _sample(row[0]) == "is"]),
            "oos": pack_stats([t for t in slot["trades"] if _sample(t["day"]) == "oos"],
                              [row for row in slot["daily"] if _sample(row[0]) == "oos"]),
        }
    qqq, long = stats[f"QQQ|both|{int(NOTIONAL)}"]["oos"], stats[f"QQQ|long|{int(NOTIONAL)}"]["oos"]
    checks, go = judge(qqq, long, complete)
    return {"books": books, "stats": stats, "failed": failed, "complete": complete, "start": start, "end": end,
            "checks": checks, "go": go}


def _fmt(x, spec="{:.4f}"):
    return "—" if x is None else spec.format(x)


def _he(x, digits=3):
    if x is None:
        return "חסר"
    if x == 0:
        return "אפס"
    return f"{'פלוס' if x > 0 else 'מינוס'} {abs(x):.{digits}f}"


def _row(st):
    return (f"| {st['n']} | {_fmt(None if st['win'] is None else st['win'], '{:.1%}')} | {_fmt(st['mean'])} | "
            f"{_fmt(st['t'], '{:.2f}')} | {_fmt(st['sharpe'], '{:.2f}')} | {_fmt(st['daily_mean'])} | "
            f"{_fmt(st['bench_mean'])} | {st['years_ok']}/{st['years']} | {_fmt(st['total'], '{:.1%}')} | "
            f"{_fmt(st['max_dd'], '{:.1%}')} |")


def render(result):
    go = "GO" if result["go"] else "NO-GO"
    q = result["stats"][f"QQQ|both|{int(NOTIONAL)}"]["oos"]
    lines = [
        f"# Noise Area, QQQ and SPY — {dt.date.today().isoformat()}",
        "",
        "## סיכום",
        "",
        f"- הכרעה: {go}. החלון מחוץ למדגם הוא 2023 ואילך. QQQ הוא ההשערה. SPY מדווח לידה.",
        f"- עסקאות QQQ מחוץ למדגם: {q['n']}. ממוצע לעסקה {_he(q['mean'])}. t {_he(q.get('t'), 2)}.",
        f"- שארפ שנתי {_he(q.get('sharpe'), 2)}. שנים רווחיות {q['years_ok']} מתוך {q['years']}.",
        "- המכפיל נעול על 1 לפני ההרצה. לא נשלחה פקודה. אין כאן מיזוג.",
        "",
        "## Verdict",
        "",
        f"**{go}.** QQQ, long and short, $10,000, out of sample {OOS} → {result['end']}. "
        f"Complete: {result['complete']}.",
        "",
        "| Check | Pass |",
        "|---|---|",
    ]
    for text, ok in result["checks"]:
        lines.append(f"| {text} | {'pass' if ok else 'fail'} |")
    if result["failed"]:
        lines.append(f"| every session downloaded ({len(result['failed'])} failed) | fail |")
    lines += [
        "",
        "SPY, the in-sample years, and the $460 book are below. They are not in the gate.",
        "",
        "| Book | Sample | Trades | Win | Mean trade | t | Sharpe | Mean day | Open-to-close | Years + | Total | Max DD |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, st in result["stats"].items():
        symbol, side, notion = label.split("|")
        name = f"{symbol} {side} ${notion}"
        lines.append(f"| {name} | in sample | " + _row(st["is"])[2:])
        lines.append(f"| {name} | out of sample | " + _row(st["oos"])[2:])
    lines += ["", "Mean trade, mean day, and the open-to-close column are simple returns on the account. "
              "Sharpe is annualized from every session, including flat days. A year is profitable when compounded "
              "equity for that year ends higher.",
              "", "## What was locked", "", __doc__.strip(), ""]
    return "\n".join(lines) + "\n"


def write_report(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.now(NY).date().isoformat()
    stem = directory / f"noise-qqq-{day}"
    stem.with_suffix(".md").write_text(render(result), encoding="utf-8")
    fields = ("day", "symbol", "side_book", "notional", "side", "entry_minute", "exit_minute", "entry", "exit",
              "shares", "pnl", "ret", "kind")
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fields, extrasaction="ignore")
        w.writeheader()
        for slot in result["books"].values():
            for trade in slot["trades"]:
                w.writerow(trade)
    payload = {k: result[k] for k in ("start", "end", "complete", "failed", "go", "checks", "stats")}
    stem.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"wrote {stem}.md  GO={result['go']}", flush=True)
    return stem.with_suffix(".md")


class AlpacaFeed:
    """SIP dailies and SIP minutes for QQQ and SPY. Reads only; never an order."""

    def sessions(self, start, end):
        cal = alpaca.calendar(start, end)
        return [(d, cal[d][1]) for d in sorted(cal) if start <= d <= end]

    def daily(self, symbols, start, end):
        return alpaca.daily(symbols, start, end)

    def minutes(self, symbols, day):
        return alpaca.minute_bars(symbols, day)


def main():
    try:
        alpaca._headers()
    except RuntimeError as e:
        raise SystemExit(str(e))
    result = replay(AlpacaFeed(), out=Path("docs/backtest"))
    write_report(result, Path("docs/backtest"))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
