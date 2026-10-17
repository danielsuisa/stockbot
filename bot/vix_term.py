"""VIX term structure. Locked 2026-10-15 before any price request.

Hold SPY when the prior session's VIX close is below the prior session's VIX3M close.
Otherwise hold the cash sleeve. The fill is the next session's open. This module does not place or transmit an order.

The cash sleeve is BIL before 2020-01-01 and before SGOV's first bar. From SGOV's first bar
it is SGOV. A change from BIL to SGOV is a switch. The initial buy is not a switch.

A second book requires VIX below VIX3M on two consecutive prior closes. It is reported
beside the one-day book. It is not the verdict.

In-sample execution dates fall in 2008 through 2019. Out-of-sample dates fall in 2020 through
the last session before the run date. Success, on the one-day book only, is an out-of-sample
Sharpe strictly above buy-and-hold SPY and an out-of-sample max drawdown strictly smaller in
magnitude, after costs. The risk-free rate is zero. Sharpe uses the sample standard deviation
of daily simple returns and scales by the square root of 252.

Maximum drawdown uses the out-of-sample close marks only. The first out-of-sample close
starts the peak. An earlier in-sample peak does not carry in.
"""
import csv
import datetime as dt
import io
import statistics
from zoneinfo import ZoneInfo

from bot.sector_mom import ACCOUNT, SPREAD, buy_shares, max_drawdown, trade_cost

NY = ZoneInfo("America/New_York")
IS_START = "2008-01-01"
OOS_START = "2020-01-01"
SLEEVE_CUT = "2020-01-01"
DROP_MAX = 0.01
CASH = ("BIL", "SGOV")
VERDICT_DAYS = 1


def sharpe(returns):
    """Annualized Sharpe of daily simple returns. The risk-free rate is zero."""
    if len(returns) < 2:
        return None
    sd = statistics.stdev(returns)
    if sd == 0:
        return None
    return (sum(returns) / len(returns) / sd) * (252 ** 0.5)


def sleeve(day, sgov_first):
    """BIL before 2020 and until SGOV prints. SGOV from its first bar onward."""
    if day < SLEEVE_CUT or not sgov_first or day < sgov_first:
        return "BIL"
    return "SGOV"


def parse_cboe(text):
    """CBOE daily history. DATE is MM/DD/YYYY. Returns `{date: close}`."""
    out = {}
    sample = text.lstrip("\ufeff")
    for row in csv.DictReader(io.StringIO(sample)):
        raw = (row.get("DATE") or row.get("Date") or "").strip()
        close = row.get("CLOSE") or row.get("Close")
        if not raw or close in (None, ""):
            continue
        month, day, year = raw.split("/")
        stamp = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        out[stamp] = float(close)
    return out


def parse_yahoo(payload):
    """Yahoo chart v8. Adjusted open is the print times adjclose/close. Returns `{date: (open, close)}`."""
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return {}
    block = result[0]
    stamps = block.get("timestamp") or []
    quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((block.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    opens = quote.get("open") or []
    closes = quote.get("close") or []
    rows = {}
    for ts, opx, cpx, apx in zip(stamps, opens, closes, adj):
        if ts is None or opx is None or cpx is None or apx is None or cpx <= 0 or opx <= 0 or apx <= 0:
            continue
        day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).astimezone(NY).date().isoformat()
        rows[day] = (float(opx) * (float(apx) / float(cpx)), float(apx))
    return rows


def _before(series, today):
    return {day: value for day, value in (series or {}).items() if day < today}


def _first(series):
    days = [day for day in series if day >= IS_START]
    return min(days) if days else None


def align(prices, vix, vix3m, today):
    """SPY sessions that have the prints the rule needs. A later bar than `today` is ignored.

    A session missing VIX, VIX3M, SPY, or BIL is removed. On and after SGOV's first bar, a
    session missing SGOV is removed too. The next kept session uses the previous kept close.
    """
    spy = _before(prices.get("SPY"), today)
    bil = _before(prices.get("BIL"), today)
    sgov = _before(prices.get("SGOV"), today)
    vix = _before(vix, today)
    vix3m = _before(vix3m, today)
    firsts = {"SPY": _first(spy), "BIL": _first(bil), "VIX": _first(vix), "VIX3M": _first(vix3m)}
    if any(value is None for value in firsts.values()):
        missing = [name for name, value in firsts.items() if value is None]
        return {"complete": False, "reason": "missing " + ", ".join(missing), "usable": [],
                "candidates": 0, "dropped": 0, "drop_rate": None, "window_start": None,
                "sgov_first": None, "gap": None, "end": None}
    window = max(firsts.values())
    gap = None
    if window > IS_START:
        late = [f"{name} {day}" for name, day in sorted(firsts.items()) if day == window]
        gap = (f"In-sample was asked to start on {IS_START}. The overlap starts on {window} "
               f"({', '.join(late)}).")
    sgov_first = min(sgov) if sgov else None
    candidates = sorted(day for day in spy if window <= day < today and _bar_ok(spy[day]))
    if any(day >= OOS_START for day in candidates) and not sgov_first:
        return {"complete": False, "reason": "SGOV has no bars", "usable": [],
                "candidates": len(candidates), "dropped": len(candidates), "drop_rate": 1.0,
                "window_start": window, "sgov_first": None, "gap": gap, "end": None}
    usable = []
    for day in candidates:
        if day not in bil or day not in vix or day not in vix3m or not _bar_ok(bil[day]):
            continue
        if sgov_first and day >= sgov_first and (day not in sgov or not _bar_ok(sgov[day])):
            continue
        usable.append(day)
    dropped = len(candidates) - len(usable)
    rate = dropped / len(candidates) if candidates else None
    reason = None
    complete = True
    if not candidates:
        complete, reason = False, "no SPY sessions"
    elif rate is None or rate > DROP_MAX:
        complete, reason = False, f"dropped {dropped} of {len(candidates)} sessions"
    elif not usable or usable[-1] < OOS_START:
        complete, reason = False, "out-of-sample has no sessions"
    elif len(usable) < 3:
        complete, reason = False, "fewer than three usable sessions"
    return {"complete": complete, "reason": reason, "usable": usable, "candidates": len(candidates),
            "dropped": dropped, "drop_rate": rate, "window_start": window, "sgov_first": sgov_first,
            "gap": gap, "end": usable[-1] if usable else None, "firsts": firsts}


def _bar_ok(bar):
    return (isinstance(bar, (tuple, list)) and len(bar) >= 2 and bar[0] is not None and bar[1] is not None
            and bar[0] > 0 and bar[1] > 0)


def targets(usable, vix, vix3m, consecutive, sgov_first):
    """Execution dates and the symbol to hold. The signal is the prior `consecutive` kept closes."""
    dates, symbols = [], []
    for idx in range(consecutive, len(usable)):
        day = usable[idx]
        on = all(vix[usable[j]] < vix3m[usable[j]] for j in range(idx - consecutive, idx))
        dates.append(day)
        symbols.append("SPY" if on else sleeve(day, sgov_first))
    return dates, symbols


def _path(dates, symbols, prices):
    """Buy at the open when the symbol changes. Mark at the close. The first buy is not a switch."""
    cash, shares, held, basis = ACCOUNT, 0.0, None, 0.0
    fee = spread = 0.0
    rows, realizations, switches = [], [], []
    prev_eq = ACCOUNT
    for day, target in zip(dates, symbols):
        opx = prices[target][day][0]
        switched = False
        if held is not None and target != held:
            sale, f, s = trade_cost("sell", shares, prices[held][day][0])
            cash += sale
            fee += f
            spread += s
            realizations.append((day, sale - basis))
            switches.append({"day": day, "from": held, "to": target})
            held, shares, basis = None, 0.0, 0.0
            switched = True
        if held is None:
            qty = buy_shares(cash, opx)
            delta, f, s = trade_cost("buy", qty, opx)
            cash += delta
            fee += f
            spread += s
            if qty > 0:
                shares, basis, held = qty, -delta, target
        equity = cash + shares * prices[held][day][1] if held else cash
        ret = equity / prev_eq - 1 if prev_eq > 0 else None
        prev_eq = equity
        rows.append({"day": day, "symbol": held or "CASH", "equity": equity, "ret": ret,
                     "switch": switched, "sample": "oos" if day >= OOS_START else "is"})
    final_mark = None
    if held and rows:
        final_mark = (rows[-1]["day"], shares * prices[held][rows[-1]["day"]][1] - basis)
    return {"rows": rows, "switches": switches, "fee": fee, "spread": spread,
            "final_equity": rows[-1]["equity"] if rows else ACCOUNT,
            "realizations": realizations, "final_mark": final_mark}


def _slice(rows):
    rets = [row["ret"] for row in rows if row["ret"] is not None]
    equity = [row["equity"] for row in rows]
    return {"n": len(rows), "sharpe": sharpe(rets),
            "maxdd": max_drawdown(equity) if equity else None,
            "final": equity[-1] if equity else None,
            "switches": sum(1 for row in rows if row["switch"])}


def _book(path, spy_path, dates):
    out = {}
    for sample in ("oos", "is"):
        stats = _slice([row for row in path["rows"] if row["sample"] == sample])
        spy = _slice([row for row in spy_path["rows"] if row["sample"] == sample])
        stats["spy_sharpe"] = spy["sharpe"]
        stats["spy_maxdd"] = spy["maxdd"]
        stats["spy_final"] = spy["final"]
        out[sample] = stats
    years = sorted({day[:4] for day in dates})
    per_year = {year: 0 for year in years}
    for switch in path["switches"]:
        per_year[switch["day"][:4]] = per_year.get(switch["day"][:4], 0) + 1
    out.update(rows=path["rows"], switches=path["switches"], per_year=per_year,
               switches_per_year=(len(path["switches"]) / len(years) if years else None),
               fee=path["fee"], spread=path["spread"], final_equity=path["final_equity"],
               realizations=path["realizations"], final_mark=path["final_mark"],
               spy_final=spy_path["final_equity"], start=dates[0] if dates else None,
               end=dates[-1] if dates else None)
    return out


def goes(stats, complete):
    """The one-day out-of-sample book. Both checks are required. In-sample cannot flip this."""
    sharpe_ok = (stats.get("sharpe") is not None and stats.get("spy_sharpe") is not None
                 and stats["sharpe"] > stats["spy_sharpe"])
    dd_ok = (stats.get("maxdd") is not None and stats.get("spy_maxdd") is not None
             and abs(stats["maxdd"]) < abs(stats["spy_maxdd"]))
    checks = [
        ("OOS Sharpe strictly above buy-and-hold SPY", sharpe_ok),
        ("OOS max drawdown strictly smaller than buy-and-hold SPY", dd_ok),
        ("the download finished", bool(complete)),
    ]
    return {"go": bool(complete) and sharpe_ok and dd_ok, "checks": checks}


def simulate(prices, vix, vix3m, today):
    """One-day book and two-day book. `today` is the New York run date and is not a session."""
    meta = align(prices, vix, vix3m, today)
    books = {}
    if meta["usable"]:
        px = {name: _before(prices.get(name), today) for name in ("SPY", "BIL", "SGOV")}
        for consecutive in (1, 2):
            dates, symbols = targets(meta["usable"], vix, vix3m, consecutive, meta["sgov_first"])
            if not dates:
                continue
            path = _path(dates, symbols, px)
            spy = _path(dates, ["SPY"] * len(dates), px)
            books[str(consecutive)] = _book(path, spy, dates)
    meta["books"] = books
    verdict = goes(books.get("1", {}).get("oos", {}), meta["complete"])
    meta["verdict"] = verdict
    return meta
