"""Pre-registered swing-pullback replay. Locked on 2026-10-14, before this run.

Nothing in this module places or transmits an order. A GO is a paper-trade result, not a merge and not
permission to trade. The listener is unchanged.

In-sample is 2016-01-01 through 2022-12-31. Out-of-sample is 2023-01-01 through the last Alpaca session
before the run's New York date. Only the out-of-sample window is gated, and each book is gated on its own:
the book with no earnings filter, and the book that uses the calendar proxy. In-sample numbers cannot flip
the verdict. An incomplete download is NO-GO.

The universe is Alpaca's active and inactive NYSE and NASDAQ names, one to five letters, excluding fund-like
names. Inactive names that still have bars are kept. Names Alpaca never listed are absent, so this is not a
CRSP delist tape. Prices are raw. A split can look like a gap, and a reused symbol is not split apart.
Averages use the sessions the name itself printed.
"""
import csv
import datetime as dt
import json
import traceback
from pathlib import Path

from bot import alpaca
from bot.desk_gate import YEARS, judge
from bot.swing_pullback import blackout_days, hold_exit, schedule, score, short_exit, signal
from bot.ticket_backtest import asset_universe

START, OOS, WARM = "2016-01-01", "2023-01-01", "2015-06-01"
NY = alpaca.NY


def _end():
    today = dt.datetime.now(NY).date().isoformat()
    days = [d for d in alpaca.calendar(WARM, today) if d < today]
    return today, days


def _fmt(x, spec="{:+.3f}"):
    return "—" if x is None else spec.format(x)


def _book_md(title, rows):
    oos = [r for r in rows if r["day"] >= OOS]
    ins = [r for r in rows if r["day"] < OOS]
    g = judge(oos, YEARS)
    lines = [f"### {title}", "", f"Verdict: **{'GO' if g['go'] else 'NO-GO'}**.", ""]
    for label, ok in g["checks"]:
        lines.append(f"- {'pass' if ok else 'fail'}: {label}")
    lines += ["", "| Sample | Trades | Mean net R | t | Random p95 | Win rate |", "|---|---:|---:|---:|---:|---:|"]
    for name, part, show_p in (("2023–2026", oos, g), ("2016–2022", ins, None)):
        xs = [r["net_r"] for r in part]
        m = sum(xs) / len(xs) if xs else None
        win = sum(r["net_r"] > 0 for r in part) / len(part) if part else None
        lines.append(f"| {name} | {len(part)} | {_fmt(m)} | {_fmt(judge(part)['t'] if part else None, '{:.2f}')} | "
                     f"{_fmt(show_p['p95'] if show_p else None)} | {_fmt(win, '{:.1%}')} |")
    lines += ["", "| Year | Trades | Mean net R |", "|---|---:|---:|"]
    for y in ("2016", "2017", "2018", "2019", "2020", "2021", "2022", *YEARS):
        part = [r["net_r"] for r in rows if r["day"][:4] == y]
        lines.append(f"| {y} | {len(part)} | {_fmt(sum(part) / len(part) if part else None)} |")
    inactive = [r for r in rows if r.get("listed") == "inactive"]
    lines += ["", f"Trades in names Alpaca now marks inactive: {len(inactive)} of {len(rows)}.", ""]
    return lines, g


def render(result):
    lines = [
        f"# Swing pullback — {result['run']}",
        "",
        "Long only, one position, $460, settled the session after the exit. No earnings calendar was available "
        "from Alpaca, so the proxy is the ten-day window from the second Monday of January, April, July, and "
        "October. The daily entry has no SIP quote, so every trade takes the 1% round-trip haircut plus the "
        "$0.35 minimum commission. This replay does not place or transmit an order.",
        "",
        f"Window asked for: in-sample {START} through 2022-12-31, out-of-sample {OOS} through {result['end']}. "
        f"First daily bar in the download: {result['first_bar']}. Universe {result['universe']} names, "
        f"{result['inactive_names']} of them inactive.",
        "",
        f"Candidates before the one-position rule: {result['candidates']}.",
        "",
    ]
    verdicts = []
    for key, title in (("plain", "No earnings filter"), ("proxy", "Calendar earnings proxy")):
        block, g = _book_md(title, result[key])
        lines += block
        verdicts.append((title, g["go"]))
    lines += ["## Verdict", ""]
    for title, go in verdicts:
        lines.append(f"- {title}: {'GO' if go else 'NO-GO'}.")
    lines.append("")
    return "\n".join(lines)


def _candidates(daily, sessions, listed):
    index = {d: i for i, d in enumerate(sessions)}
    out = []
    for sym, rows in daily.items():
        if len(rows) < 61:
            continue
        by = {r[0]: r for r in rows}
        for i in range(60, len(rows) - 1):
            day = rows[i][0]
            if day < START or day not in index:
                continue
            entry_i = index[day] + 1
            if entry_i >= len(sessions):
                continue
            entry_day = sessions[entry_i]
            nxt = rows[i + 1]
            if nxt[0] != entry_day:
                continue
            sig = signal(rows[:i + 1], nxt[1])
            if not sig:
                continue
            future_days = sessions[entry_i:entry_i + 15]
            future = []
            for d in future_days:
                b = by.get(d)
                future.append(None if b is None else (d, b[1], b[2], b[3], b[4]))
            exited = hold_exit(future, sig["entry"], sig["stop"], sig["target"])
            if not exited:
                continue
            short_px = short_exit(future, sig["entry"], sig["stop"], sig["target"])
            scored = score(sig["entry"], exited[0], short_px, sig["stop"])
            if not scored or scored["net_r"] is None or scored["net_r_short"] is None:
                continue
            out.append({"day": entry_day, "entry_day": entry_day, "t": sym, "signal_day": day,
                        "exit_day": exited[2], "exit_kind": exited[1], "entry": sig["entry"],
                        "stop": sig["stop"], "target": sig["target"], "exit": exited[0],
                        "adv": sig["adv"], "hold": future_days, "listed": listed.get(sym), **scored})
    return out


def run():
    run_day, sessions = _end()
    end = sessions[-1]
    banned = blackout_days(2016, int(end[:4]))
    raw = alpaca.assets()
    symbols = asset_universe(raw)
    listed = {a["symbol"]: a.get("status") for a in raw}
    daily = {}
    for i in range(0, len(symbols), alpaca.CHUNK):
        part = symbols[i:i + alpaca.CHUNK]
        daily.update(alpaca.daily(part, WARM, end))
        print(f"daily {i + len(part)}/{len(symbols)} requests {alpaca.REQUESTS[0]}", flush=True)
    first = min((rows[0][0] for rows in daily.values() if rows), default=None)
    cands = _candidates(daily, [s for s in sessions if s <= end], listed)
    plain = schedule(cands, banned, False)
    proxy = schedule(cands, banned, True)
    return {"run": run_day, "end": end, "first_bar": first, "universe": len(symbols),
            "inactive_names": sum(1 for s in symbols if listed.get(s) == "inactive"),
            "candidates": len(cands), "plain": plain, "proxy": proxy, "complete": True,
            "requests": alpaca.REQUESTS[0]}


def _write(result):
    stem = Path(f"docs/backtest/swing-pullback-{result['run']}")
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".md").write_text(render(result), encoding="utf-8")
    fields = ["day", "t", "signal_day", "exit_day", "exit_kind", "entry", "stop", "target", "exit",
              "shares", "net_r", "net_r_short", "pnl", "adv", "listed", "book"]
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fields, extrasaction="ignore")
        w.writeheader()
        for book, rows in (("plain", result["plain"]), ("proxy", result["proxy"])):
            for r in rows:
                w.writerow({**r, "book": book})
    slim = {k: v for k, v in result.items() if k not in ("plain", "proxy")}
    slim["plain"] = judge([r for r in result["plain"] if r["day"] >= OOS])
    slim["proxy"] = judge([r for r in result["proxy"] if r["day"] >= OOS])
    stem.with_suffix(".json").write_text(json.dumps(slim, indent=1, default=str), encoding="utf-8")
    print(f"wrote {stem}.md", flush=True)


def main():
    try:
        result = run()
    except Exception:
        traceback.print_exc()
        run_day = dt.datetime.now(NY).date().isoformat()
        stub = {"run": run_day, "end": None, "first_bar": None, "universe": 0, "inactive_names": 0,
                "candidates": 0, "plain": [], "proxy": [], "complete": False, "requests": alpaca.REQUESTS[0]}
        _write(stub)
        return 1
    _write(result)
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
