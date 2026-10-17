"""Pre-registered replay of the breakout rule on Alpaca SIP minute bars.

Locked on 2026-10-14, before the run. The decision rule is bot.breakout (the code the listener runs). Nothing here
places or transmits an order. A GO is a paper-trade result, not permission to trade.

Window: 2024-01-02 through the last Alpaca session before the run's New York date (the K10s verdict window, more than
a year). A session is an Alpaca calendar day.

Who is tracked: the listener's universe (NYSE/NASDAQ, 1–5 letters, no fund/ETF name), then context_row + passes on
daily bars strictly before the session, ranked by distance to the 20-day high, at most 30 names. Same functions as
the live loop.

Tape: SIP 1-minute bars from 04:00 through the flat minute. A minute is used only after it closes. On a pre-market
minute the close and the cumulative pre-market volume are passed to apply_quote before that minute's bar path; a
"drop" skips the name for the day. The bar path is open-low-high-close when the minute closes at or above its open,
otherwise open-high-low-close. observe() is the trigger. The grade is whatever apply_quote has already stored (a
cross on the first regular minute with no pre-market quote stays C).

Order, the prices bracket() prints at the trigger: buy stop at the trigger, limit at trigger + 0.25 x ATR, stop and
target absolute. One target for the whole position (the "half at the target" line in the ping is text, not a second
order). Flat at 15:55 New York, or at the last minute before an earlier close.

Fills (K10s section 13): SIP round-lot trades of the trigger minute. The stop triggers on the first trade at or
through the level and fills at the next trade (at the trigger trade when none follows). A fill above the limit is
cancelled. The printed stop is then checked on the remaining trades of that minute and fills at the trade after the
one that hits it. From the next minute, the bar model: a stop or a target fills at the stop or the target, or at the
open when the open is already through it; a minute that touches both is the stop; anything still open exits at the
flat minute's close.

Costs (K10s section 6): $100 of planned risk, and fewer than 10 shares is not a trade. Commission $0.0035 a share,
$0.35 minimum, once on the entry and once on the exit. Half the SIP spread on the way in and half on the way out
(the full spread once), from the quote at the fill; a locked quote counts as $0.01; no usable quote (missing, a side
at or below zero, crossed, or older than 60 seconds) drops the trade. R in the denominator is the printed R, so a
fill worse than the trigger makes a full stop lose more than 1R. Results are net of these costs.

Grade at the fill quote: C stays C, B becomes A when the raw spread is at most a quarter of R (the live rule). The
gated book is grades A and B (the pings the live rule would size). Grade C is reported and is not in the gate.

Random control: 20 seeds, each ticker-day a coin flip, the short played on prices negated around zero with the same
distances from the fill (ticket_backtest.random_means).

GO, all four, on the gated book, else NO-GO: at least 100 trades; mean net R > 0 with t >= 2; mean net R above the
95th percentile of the 20 random means; mean net R > 0 in 2024 and in 2025 and in 2026 (a year with no trade fails).
Reported, not gated: win rate, median, mean cost, max drawdown of a 1% risk book capped at 4x gross (the K10s
portfolio), the same book at the live weights (A 1%, B 0.5%), the longest run of trades with net R <= 0, and the
mean after an extra $0.01 a share each side. An incomplete download is NO-GO.
"""
import bisect
import csv
import datetime as dt
import json
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

from bot import alpaca, breakout, levels, ticket_backtest
from bot.ticket_backtest import (COMMISSION, MIN_ORDER, SEEDS, portfolio, random_means, stats, verdict)

START = "2024-01-02"  # locked with the K10s verdict window; not chosen after seeing this rule's results
YEARS = ("2024", "2025", "2026")
RISK_USD, MIN_SHARES = 100.0, 10  # K10s: $100 of risk, fewer than 10 shares is not a trade
FLAT, OPEN = "15:55", "09:30"
LOOKBACK_DAYS = 120
EXTRA_SLIP = 0.01  # reported, not gated: an extra cent a share on the entry and on the exit
NY = ZoneInfo("America/New_York")
GATED = ("A", "B")


def bar_path(o, h, l, c):
    """A closed minute's prices in order. The close decides which extreme came first."""
    if c >= o:
        return (o, l, h, c)
    return (o, h, l, c)


def flat_minute(close_hhmm):
    """15:55, or the last minute of an earlier close (a 13:00 close flats at 12:59)."""
    h, m = (int(x) for x in close_hhmm.split(":")[:2])
    t = min(h * 60 + m - 1, 15 * 60 + 55)
    return f"{t // 60:02d}:{t % 60:02d}"


def track_names(daily, day):
    """daily: {symbol: [(date, o, h, l, c, v)]} -> the at most 30 names the listener would track."""
    found = []
    for sym, bars in daily.items():
        row = breakout.context_row(bars, day)
        if breakout.passes(row):
            found.append((row["dist"], sym, row))
    found.sort()
    return [breakout.track(row, sym) for _, sym, row in found[:breakout.TRACK_MAX]]


def session_events(name, minutes, day, flat=FLAT):
    """One tracked name through the closed minutes of `day` -> {"status": fire|drop|none, ...}.

    `minutes` are (HH:MM, o, h, l, c, v), in order. Pre-market quotes use that minute's close. The trigger uses
    the bar path after the quote, so a minute that closes already through the level is a drop and does not fire."""
    cum = 0.0
    for bar in minutes:
        m, o, h, l, c, v = bar[:6]
        if m > flat:
            break
        if m < OPEN:
            cum += v or 0.0
            if breakout.apply_quote(name, {"price": c, "volume": cum, "date": day}, day) == "drop":
                return {"status": "drop", "minute": m}
        for px in bar_path(o, h, l, c):
            level = breakout.observe(name, px, fire=True)
            if level is not None:
                return {"status": "fire", "level": level, "minute": m, "base": name.get("base"),
                        "order": breakout.bracket(name, level)}
    return {"status": "none"}


def fill_from_ticks(ticks, level, limit, stop):
    """Prices of the trigger minute, in order -> (fill, stop_fill or None, status).

    status is "no trigger", "limit", or "filled". A fill above the limit is cancelled. A stop on the trades after
    the fill fills at the following trade."""
    trig = next((i for i, p in enumerate(ticks) if p >= level - 1e-9), None)
    if trig is None:
        return None, None, "no trigger"
    fill = ticks[min(trig + 1, len(ticks) - 1)]
    if fill > limit + 1e-9:
        return None, None, "limit"
    if fill <= stop + 1e-9:
        return fill, fill, "filled"
    rest = ticks[min(trig + 1, len(ticks) - 1) + 1:]
    hit = next((i for i, p in enumerate(rest) if p <= stop + 1e-9), None)
    if hit is None:
        return fill, None, "filled"
    return fill, rest[min(hit + 1, len(rest) - 1)], "filled"


def exit_after(fill, stop, target, bars, flat):
    """Minutes after the fill -> (price, minute, kind). Stop and target are absolute. Both in one minute: the stop.
    Still open at `flat`: that minute's close."""
    for bar in bars:
        m, o, h, l, c = bar[:5]
        if o <= stop + 1e-9:
            return o, m, "stop"
        if o >= target - 1e-9:
            return o, m, "target"
        if l <= stop + 1e-9:
            return stop, m, "stop"
        if h >= target - 1e-9:
            return target, m, "target"
        if m >= flat:
            return c, m, "flat"
    if bars:
        return bars[-1][4], bars[-1][0], "flat"
    return fill, flat, "flat"


def share_count(R):
    """Shares for $100 of planned risk, or 0 when that is under 10 shares."""
    if not R or R <= 0:
        return 0
    n = int(RISK_USD / R + 1e-9)
    return n if n >= MIN_SHARES else 0


def net_of(fill, exit_px, R, shares, spread, extra=0.0):
    """(gross R, cost R, net R). `spread` is the full bid-ask; `extra` is added on each side, per share."""
    gross = (exit_px - fill) / R
    commission = max(MIN_ORDER, shares * COMMISSION) + max(MIN_ORDER, shares * COMMISSION)
    cost = (commission + shares * (spread + 2 * extra)) / (R * shares)
    return gross, cost, gross - cost


def _negate_bars(bars):
    return [(b[0], -b[1], -b[3], -b[2], -b[4], *b[5:]) for b in bars]


def resolve(fill, stop, target, rest, later, flat):
    """The exit price and kind for one side, in that side's own price space.

    A fill already through the stop exits at the fill. Otherwise a stop on the remaining trades of the entry minute
    fills at the next trade. After that minute, the bar model."""
    if fill <= stop + 1e-9:
        return fill, "stop"
    hit = next((i for i, p in enumerate(rest) if p <= stop + 1e-9), None)
    if hit is not None:
        return rest[min(hit + 1, len(rest) - 1)], "stop"
    px, _, kind = exit_after(fill, stop, target, later, flat)
    return px, kind


def short_levels(fill, stop, target):
    """The short's stop and target in negated space (a long played on prices multiplied by -1)."""
    return -fill, -2 * fill + stop, -2 * fill + target


def trade_result(fill, stop, target, rest, later, flat, R, shares, spread):
    """Long and short net R for one filled order. The short uses the same distances, on the negated tape."""
    long_px, kind = resolve(fill, stop, target, rest, later, flat)
    g, c, n = net_of(fill, long_px, R, shares, spread)
    fn, sn, tn = short_levels(fill, stop, target)
    short_px, _ = resolve(fn, sn, tn, [-p for p in rest], _negate_bars(later), flat)
    _, _, ns = net_of(fn, short_px, R, shares, spread)
    return {"exit": long_px, "kind": kind, "gross_r": g, "cost_r": c, "net_r": n, "net_r_short": ns}


def losing_streak(trades):
    """The longest run of gated trades with net R <= 0, in session then clock then ticker order."""
    best = cur = 0
    for x in sorted(trades, key=lambda t: (t["day"], t.get("minute") or "", t["t"])):
        if x["net_r"] <= 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def live_book(trades, days):
    """The K10s portfolio with each trade's own risk fraction (A 1%, B 0.5%) instead of a flat 1%."""
    tagged = [{**x, "net_r": x["net_r"] * x["risk"] / 0.01, "R": x["R"] * 0.01 / x["risk"]} for x in trades]
    # portfolio() risks 1% of equity and divides by R. Scaling R by 0.01/risk makes the dollar risk equal `risk`.
    return portfolio(tagged, days, risk=0.01)


def judge(trades, days):
    """The pre-registered verdict on the filled A and B trades. Incomplete callers force NO-GO themselves."""
    gated = [x for x in trades if x.get("filled") and x.get("grade") in GATED]
    rm = random_means(gated)
    checks, go = verdict(gated, rm, years=YEARS)
    st = stats(gated)
    return {"gated": gated, "stats": st, "checks": checks, "go": go,
            "p95": ticket_backtest.pctl(rm, 0.95) if gated else None,
            "book": portfolio(gated, days, risk=0.01),
            "live_book": live_book(gated, days) if gated else None,
            "streak": losing_streak(gated),
            "slip": _mean_slip(gated)}


def _mean_slip(trades, extra=EXTRA_SLIP):
    if not trades:
        return None
    xs = [x["net_r"] - 2 * extra / x["R"] for x in trades]
    return sum(xs) / len(xs)


def quote_parts(q):
    """quote_full's tuple -> (raw spread for the grade, spread charged in the cost), or (None, None)."""
    charged = levels.spread(q)
    if charged is None or q is None:
        return None, None
    raw = q[1] - q[0]
    return raw, charged


class AlpacaFeed:
    """SIP dailies, SIP minutes, SIP trades and SIP quotes. Reads only; never an order."""

    def __init__(self):
        self._universe = None
        self._daily = None

    def sessions(self, start, end):
        cal = alpaca.calendar(start, end)
        return [(d, cal[d][1]) for d in sorted(cal) if start <= d <= end]

    def universe(self):
        if self._universe is None:
            self._universe = ticket_backtest.asset_universe(alpaca.assets())
        return self._universe

    def daily(self, start, end):
        if self._daily is None:
            self._daily = alpaca.daily(self.universe(), start, end)
        return self._daily

    def minutes(self, symbols, day):
        return alpaca.minute_bars(symbols, day) if symbols else {}

    def trades(self, symbol, day, minute):
        return [p for _, p in alpaca.trades(symbol, day, minute)]

    def quote(self, symbol, day, minute):
        return alpaca.quote_full(symbol, alpaca.utc(day, f"{minute}:59"))


def index_daily(daily):
    """{symbol: (bars, dates)} so each session can slice without scanning the whole history twice."""
    return {sym: (bars, [b[0] for b in bars]) for sym, bars in daily.items()}


def screen_day(indexed, day):
    """The tracked names. A bar dated `day` or later is not in the slice."""
    view = {}
    for sym, (bars, dates) in indexed.items():
        i = bisect.bisect_left(dates, day)
        if i >= 20:
            view[sym] = bars[max(0, i - 40):i]
    return track_names(view, day)


def play_fire(name, event, minutes, day, flat, feed):
    """A fired name -> a trade dict, or a dict with filled False and a status. One name, one day."""
    order = event["order"]
    R = order["R"]
    shares = share_count(R)
    base = {"day": day, "t": name["t"], "minute": event["minute"], "base": event["base"], "R": R,
            "trigger": order["trigger"], "stop": order["stop"], "limit": order["limit"], "target": order["target"],
            "filled": False}
    if not shares:
        return {**base, "status": "few shares"}
    try:
        ticks = feed.trades(name["t"], day, event["minute"])
        quote = feed.quote(name["t"], day, event["minute"])
    except Exception:
        return {**base, "status": "download"}
    fill, _, status = fill_from_ticks(ticks, order["trigger"], order["limit"], order["stop"])
    if status != "filled":
        return {**base, "status": status}
    raw, charged = quote_parts(quote)
    if charged is None:
        return {**base, "status": "no quote", "fill": fill}
    grade = breakout.grade_for_cross(name, raw, R)
    trig = next(i for i, p in enumerate(ticks) if p >= order["trigger"] - 1e-9)
    rest = ticks[min(trig + 1, len(ticks) - 1) + 1:]
    later = [b for b in minutes if event["minute"] < b[0] <= flat]
    result = trade_result(fill, order["stop"], order["target"], rest, later, flat, R, shares, charged)
    return {**base, "filled": True, "status": "filled", "grade": grade, "fill": fill, "shares": shares,
            "spread": charged, "risk": {"A": 0.01, "B": 0.005, "C": 0.0}[grade], "kinds": result["kind"], **result}


def _pack(trades, days, counts, failed, start, end, complete):
    judged = judge(trades, days)
    judged["go"] = bool(judged["go"] and complete)
    return {"trades": trades, "days": days, "counts": dict(counts), "failed": failed, "complete": complete,
            "start": start, "end": end, "judge": judged}


def replay(feed, start=START, end=None, today=None, out=None):
    """The whole window -> {"trades", "days", "counts", "complete", "end", "judge"}.

    When `out` is set, the report is rewritten every 20 sessions so a killed run still leaves an incomplete file."""
    today = today or dt.datetime.now(NY).date().isoformat()
    sessions = [(d, c) for d, c in feed.sessions(start, end or today) if d < today]
    if not sessions:
        raise RuntimeError("no sessions in the window")
    end = sessions[-1][0]
    look = (dt.date.fromisoformat(start) - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    indexed = index_daily(feed.daily(look, end))
    counts, trades, failed = Counter(), [], []
    days = [d for d, _ in sessions]
    for n, (day, close) in enumerate(sessions, 1):
        flat = flat_minute(close)
        try:
            names = screen_day(indexed, day)
            minutes = feed.minutes([nm["t"] for nm in names], day)
        except Exception as e:
            failed.append(day)
            print(f"{day}: screen failed ({type(e).__name__})", flush=True)
            continue
        fires = 0
        for name in names:
            counts["tracked"] += 1
            bars = [b for b in (minutes.get(name["t"]) or []) if "04:00" <= b[0] <= flat]
            event = session_events(name, bars, day, flat)
            counts[event["status"]] += 1
            if event["status"] != "fire":
                continue
            fires += 1
            got = play_fire(name, event, bars, day, flat, feed)
            counts[got["status"]] += 1
            if got["status"] == "download":
                failed.append(day)
            if got.get("filled"):
                trades.append(got)
                counts[f"grade {got['grade']}"] += 1
        if n % 20 == 0 or n == len(sessions):
            print(f"{day}: {n}/{len(sessions)} sessions, filled {len(trades)}, failed {len(failed)}", flush=True)
            if out and n != len(sessions):
                write_report(_pack(trades, days, counts, failed, start, end, False), out)
    return _pack(trades, days, counts, list(dict.fromkeys(failed)), start, end, not failed)


def _fmt(x, spec="{:+.3f}"):
    return "—" if x is None else spec.format(x)


def _he(x, digits=3):
    """A signed number as words, so a minus never sits against a digit."""
    if x is None:
        return "חסר"
    if x == 0:
        return "אפס"
    return f"{'פלוס' if x > 0 else 'מינוס'} {abs(x):.{digits}f}"


def render(result):
    """Markdown report. The Hebrew lines state the verdict; the tables hold the numbers."""
    j, st = result["judge"], result["judge"]["stats"]
    go = j["go"]
    title = "GO" if go else "NO-GO"
    dd = None if not j.get("book") else j["book"]["max_dd"] * 100
    lines = [
        f"# Breakout rule, SIP minute replay — {dt.date.today().isoformat()}",
        "",
        "## סיכום",
        "",
        f"- הכרעה: {title}. החלון והמספרים בטבלאות שמתחת.",
        f"- עסקאות מדורגות א׳ ו־ב׳: {st['n']}. ממוצע נטו {_he(st['mean'])} R. סטטיסטי t {_he(st.get('t'), 2)}.",
        f"- אחוז זוכות {_he(None if st.get('win') is None else st['win'] * 100, 1)} אחוז. "
        f"רצף הפסדים ארוך {j['streak']}.",
        f"- שפל מרבי בסיכון של אחוז אחד לעסקה: {_he(dd, 1)} אחוז.",
        "- הכלל ננעל לפני ההרצה. לא נשלחה פקודה.",
        "",
        "## Verdict",
        "",
        f"**{title}.** Window {result['start']} → {result['end']}. Complete: {result['complete']}.",
        "",
        "| Check | Pass |",
        "|---|---|",
    ]
    for text, ok in j["checks"]:
        lines.append(f"| {text} | {'pass' if ok else 'fail'} |")
    if result["failed"]:
        lines.append(f"| every session downloaded ({len(result['failed'])} failed) | fail |")
    book = j.get("book") or {}
    live = j.get("live_book") or {}
    lines += [
        "",
        "## Numbers (grades A and B, after costs)",
        "",
        "| Trades | Win rate | Avg net R | Expectancy (net R) | t | Median | Cost R | Random p95 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| {st['n']} | {_fmt(None if st.get('win') is None else st['win'], '{:.1%}')} | {_fmt(st['mean'])} | "
        f"{_fmt(st['mean'])} | {_fmt(st.get('t'), '{:.2f}')} | {_fmt(st.get('median'))} | "
        f"{_fmt(_mean_cost(j['gated']))} | {_fmt(j.get('p95'))} |",
        "",
        "| 1% risk, 4x gross cap | Max drawdown | Total | CAGR | Sharpe | Longest losing streak | Extra 1 cent each side |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| K10s book | {_fmt(book.get('max_dd'), '{:.1%}')} | {_fmt(book.get('total'), '{:.1%}')} | "
        f"{_fmt(book.get('cagr'), '{:.1%}')} | {_fmt(book.get('sharpe'), '{:.2f}')} | {j['streak']} | "
        f"{_fmt(j.get('slip'))} |",
        "",
        f"Live weights (A 1%, B 0.5%, same 4x cap), max drawdown {_fmt(live.get('max_dd'), '{:.1%}')}, "
        f"total {_fmt(live.get('total'), '{:.1%}')}. Not gated.",
        "",
        "## By year",
        "",
        "| Year | Trades | Mean net R |",
        "|---|---:|---:|",
    ]
    for year in YEARS:
        y = st.get("years", {}).get(year, {})
        lines.append(f"| {year} | {y.get('n', 0)} | {_fmt(y.get('mean'))} |")
    lines += ["", "## Counts", "", "| Status | N |", "|---|---:|"]
    for k, v in sorted(result["counts"].items()):
        lines.append(f"| {k} | {v} |")
    lines += ["", "## What was locked", "", __doc__.strip(), ""]
    return "\n".join(lines) + "\n"


def _mean_cost(trades):
    xs = [x["cost_r"] for x in trades if x.get("cost_r") is not None]
    return sum(xs) / len(xs) if xs else None


def write_report(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.now(NY).date().isoformat()
    stem = directory / f"breakout-rule-{day}"
    stem.with_suffix(".md").write_text(render(result), encoding="utf-8")
    fields = ("day", "t", "grade", "base", "minute", "fill", "stop", "target", "R", "shares", "spread", "exit",
              "kind", "gross_r", "cost_r", "net_r", "net_r_short")
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
    stem.with_suffix(".json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"wrote {stem}.md  GO={j['go']}", flush=True)
    return stem.with_suffix(".md")


def main():
    try:
        alpaca._headers()
    except RuntimeError as e:
        raise SystemExit(str(e))
    result = replay(AlpacaFeed(), out=Path("docs/backtest"))
    write_report(result, Path("docs/backtest"))
    # A finished NO-GO is a successful run. A hole in the download is not.
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
