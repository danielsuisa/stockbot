"""Pre-registered replay v2 of the breakout rule on Alpaca SIP minute bars.

Locked on 2026-10-14, before any v2 result. v1 (bot.breakout_backtest) stays as it was run. This module does not
change the listener and does not place or transmit an order. A GO is a paper-trade result, not permission to trade,
and it is not a merge.

Same as v1, unchanged: window 2024-01-02 through the last Alpaca session before the run's New York date; the same
universe, context_row, and passes screen (close within 3% of the 20-day high, and the contraction filter: a 10-day
range of at most 0.75 x ATR14, or an NR7); the same SIP minute tape; the same drop for a gap over 10% or a
pre-market close already through the level; the same trigger (one cent above yesterday's high or the high seen
today, and only after a print underneath); the same bracket, tick fill, stop-limit, and flat; the same costs
($100 of planned risk, 10-share minimum, commission, half the SIP spread each side).

The only change is the grade, so a sized grade can occur on a break that starts underneath. v1 required a 2–8%
gap as well as pre-market volume, and that gap is usually already through yesterday's high, so every v1 fill was
grade C.

v2 grade, on a filled trade only:
- The break has already started from underneath (a fill exists only after that).
- The name has already passed the contraction filter (it would not have been tracked otherwise).
- Adequate volume: cumulative SIP share volume from 04:00 through the trigger minute, divided by the 20-day
  average share volume, is at least 5%. Five percent is a liquidity floor, half of the live rule's 10%
  pre-market bar, counted through the cross rather than only before the open. Volume after the trigger minute
  is not used. The 5% was chosen before this run.
- C when that ratio is missing or under 5%.
- B when the ratio is at least 5%.
- A when the trade is B and the raw SIP spread at the fill is at most a quarter of the printed R. A missing
  spread stays B.

The gated book is v2 grades A and B. v2 grade C is reported and is not in the gate.

The v1 grade is still computed on each fill (the grade bot.breakout would have stored). The v1 artifact saved
counts only, not per-trade R, so this run reports the v1 grade-C book's trade count, win rate, mean net R, and t
from these same fills. That block is context. It is not gated and it cannot flip the v2 verdict.

Random control and the GO rule are v1's, applied to the v2 A+B book: at least 100 trades; mean net R > 0 with
t >= 2; mean net R above the 95th percentile of the 20 random-direction means; mean net R > 0 in 2024 and in 2025 and in 2026
(a year with no trade fails). An incomplete download is NO-GO.

Reported, not gated: win rate, median, mean cost, max drawdown of a 1% risk book capped at 4x gross, the same
book at A 1% and B 0.5%, the longest run of gated trades with net R <= 0, and the mean after an extra $0.01 a
share each side.
"""
import csv
import datetime as dt
import json
from collections import Counter
from pathlib import Path

from bot import alpaca, breakout, breakout_backtest as v1
from bot.ticket_backtest import stats

VOL_MIN = 0.05  # share volume from 04:00 through the trigger minute, as a fraction of the 20-day average
START, YEARS, GATED = v1.START, v1.YEARS, v1.GATED


def vol_ratio(name, minutes, minute):
    """Share volume from 04:00 through `minute`, divided by the 20-day average. None when the average is missing."""
    avg = name.get("avg_vol") or 0
    if avg <= 0:
        return None
    total = 0.0
    for bar in minutes:
        m = bar[0]
        if m > minute:
            break
        if "04:00" <= m <= minute:
            total += bar[5] or 0.0
    return total / avg


def grade_v2(spread, R, ratio):
    """C under 5% volume. B at or above it. A when that B also has a raw spread of at most a quarter of R.

    A missing spread stays B. The caller has already required a fill, which is a break from underneath on a name
    that passed the contraction filter."""
    if ratio is None or ratio < VOL_MIN:
        return "C"
    if spread is not None and R and R > 0 and spread <= breakout.SPREAD_R * R + 1e-9:
        return "A"
    return "B"


def _regrade(got, name, bars, event, feed, day):
    """Attach the v1 grade and replace `grade` with the v2 grade. The quote is the one play_fire already cached."""
    ratio = vol_ratio(name, bars, event["minute"])
    raw, _ = v1.quote_parts(feed.quote(name["t"], day, event["minute"]))
    got["grade_v1"] = got["grade"]
    got["vol_ratio"] = ratio
    got["grade"] = grade_v2(raw, got["R"], ratio)
    got["risk"] = {"A": 0.01, "B": 0.005, "C": 0.0}[got["grade"]]
    return got


def _v1_c(trades):
    """Stats for fills the v1 grade called C. Context only."""
    return stats([t for t in trades if t.get("filled") and t.get("grade_v1") == "C"])


def _pack(trades, days, counts, failed, start, end, complete):
    judged = v1.judge(trades, days)
    judged["go"] = bool(judged["go"] and complete)
    return {"trades": trades, "days": days, "counts": dict(counts), "failed": failed, "complete": complete,
            "start": start, "end": end, "judge": judged, "v1_c": _v1_c(trades)}


def replay(feed, start=START, end=None, today=None, out=None):
    """v1's loop, with the v2 grade written onto each fill. Checkpoints every 20 sessions are complete=false."""
    today = today or dt.datetime.now(v1.NY).date().isoformat()
    sessions = [(d, c) for d, c in feed.sessions(start, end or today) if d < today]
    if not sessions:
        raise RuntimeError("no sessions in the window")
    end = sessions[-1][0]
    look = (dt.date.fromisoformat(start) - dt.timedelta(days=v1.LOOKBACK_DAYS)).isoformat()
    indexed = v1.index_daily(feed.daily(look, end))
    counts, trades, failed = Counter(), [], []
    days = [d for d, _ in sessions]
    for n, (day, close) in enumerate(sessions, 1):
        flat = v1.flat_minute(close)
        try:
            names = v1.screen_day(indexed, day)
            minutes = feed.minutes([nm["t"] for nm in names], day)
        except Exception as e:
            failed.append(day)
            print(f"{day}: screen failed ({type(e).__name__})", flush=True)
            continue
        for name in names:
            counts["tracked"] += 1
            bars = [b for b in (minutes.get(name["t"]) or []) if "04:00" <= b[0] <= flat]
            event = v1.session_events(name, bars, day, flat)
            counts[event["status"]] += 1
            if event["status"] != "fire":
                continue
            got = v1.play_fire(name, event, bars, day, flat, feed)
            counts[got["status"]] += 1
            if got["status"] == "download":
                failed.append(day)
            if not got.get("filled"):
                continue
            try:
                _regrade(got, name, bars, event, feed, day)
            except Exception as e:
                failed.append(day)
                print(f"{day} {name['t']}: regrade failed ({type(e).__name__})", flush=True)
                continue
            trades.append(got)
            counts[f"v2 {got['grade']}"] += 1
            counts[f"v1 {got['grade_v1']}"] += 1
        if n % 20 == 0 or n == len(sessions):
            print(f"{day}: {n}/{len(sessions)} sessions, filled {len(trades)}, failed {len(failed)}", flush=True)
            if out and n != len(sessions):
                write_report(_pack(trades, days, counts, failed, start, end, False), out)
    return _pack(trades, days, counts, list(dict.fromkeys(failed)), start, end, not failed)


def render(result):
    """v1's report, plus the v1 grade-C context block. The locked text is this module's docstring."""
    text = v1.render(result)
    head, _, tail = text.partition("## What was locked")
    head = head.replace("# Breakout rule, SIP minute replay —", "# Breakout rule v2, SIP minute replay —", 1)
    c = result.get("v1_c") or {}
    block = [
        "## v1 grade C (context, not gated)",
        "",
        "Same fills, graded by the v1 rule. The v1 artifact stored counts, not per-trade R, so these stats are",
        "recomputed here. They do not enter the verdict.",
        "",
        "| Trades | Win rate | Mean net R | t |",
        "|---:|---:|---:|---:|",
        f"| {c.get('n', 0)} | {v1._fmt(None if c.get('win') is None else c['win'], '{:.1%}')} | "
        f"{v1._fmt(c.get('mean'))} | {v1._fmt(c.get('t'), '{:.2f}')} |",
        "",
        "## What was locked",
    ]
    return head + "\n".join(block) + "\n\n" + __doc__.strip() + "\n"


def write_report(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.now(v1.NY).date().isoformat()
    stem = directory / f"breakout-rule-v2-{day}"
    stem.with_suffix(".md").write_text(render(result), encoding="utf-8")
    fields = ("day", "t", "grade", "grade_v1", "vol_ratio", "base", "minute", "fill", "stop", "target", "R",
              "shares", "spread", "exit", "kind", "gross_r", "cost_r", "net_r", "net_r_short")
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fields, extrasaction="ignore")
        w.writeheader()
        for x in result["judge"]["gated"]:
            w.writerow(x)
    payload = {k: result[k] for k in ("start", "end", "complete", "failed", "counts")}
    j = result["judge"]
    payload["go"] = j["go"]
    payload["checks"] = j["checks"]
    payload["stats"] = j["stats"]
    payload["p95"] = j["p95"]
    payload["streak"] = j["streak"]
    payload["slip"] = j["slip"]
    payload["book"] = {k: v for k, v in (j.get("book") or {}).items() if k != "daily"}
    payload["live_book"] = {k: v for k, v in (j.get("live_book") or {}).items() if k != "daily"}
    c = result.get("v1_c") or {}
    payload["v1_c"] = {k: c.get(k) for k in ("n", "win", "mean", "t")}
    stem.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"wrote {stem}.md  GO={j['go']}", flush=True)
    return stem.with_suffix(".md")


def main():
    try:
        alpaca._headers()
    except RuntimeError as e:
        raise SystemExit(str(e))
    result = replay(v1.AlpacaFeed(), out=Path("docs/backtest"))
    write_report(result, Path("docs/backtest"))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
