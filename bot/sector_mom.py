"""Sector momentum across the nine original SPDR sectors. Locked 2026-10-15 before any price request.

Hold the top two, equal weight, for one month. This module does not place or transmit an order.

The formation return is the 12-1 shape with a six-month window: at month-end t, close(t-1) / close(t-6) - 1.
The month from t-1 to t is skipped. XLRE and XLC are not in the universe. If SPY's month-end adjusted
close is below its 200-day average, the next month is cash. Cash earns nothing.

Prices are split- and dividend-adjusted closes. In-sample months end in 2010 through 2019. Out-of-sample
months end in 2020 or later. The same rule is used on both sides. The account starts at $460, compounds,
and may hold fractional shares. IBKR fixed commission is $0.0035 a share with a $0.35 minimum per order.
The spread is 2 bps round trip: half of that is paid on the buy and half on the sell.
"""
import datetime as dt
import statistics

from bot.desk_gate import ACCOUNT, COMMISSION, MIN_ORDER

UNIVERSE = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")
EXCLUDED = ("XLC", "XLRE")
LOOKBACK, SKIP = 6, 1
TOP_N = 2
SMA_DAYS = 200
SPREAD = 0.0002
ASOF_DAYS = 5
IS_YEARS = tuple(str(y) for y in range(2010, 2020))
OOS_YEAR = "2020"


def order_fee(shares):
    return max(MIN_ORDER, COMMISSION * abs(shares))


def trade_cost(side, shares, price):
    """Cash change, commission dollars, and spread dollars. A buy returns negative cash."""
    half = SPREAD / 2
    notion = abs(shares) * price
    spread_dol = notion * half
    fee = order_fee(shares)
    if side == "buy":
        return -(notion + spread_dol + fee), fee, spread_dol
    return notion - spread_dol - fee, fee, spread_dol


def buy_shares(budget, price):
    """Fractional shares whose all-in buy cost fits in `budget`. Zero when the minimum commission does not fit."""
    if budget <= MIN_ORDER or price <= 0:
        return 0.0
    unit = price * (1 + SPREAD / 2)
    shares = (budget - MIN_ORDER) / unit
    if shares <= 0:
        return 0.0
    if COMMISSION * shares > MIN_ORDER:
        shares = budget / (unit + COMMISSION)
    return shares


def asof(rows, day, limit=ASOF_DAYS):
    """Last (date, close) on or before `day`, at most `limit` calendar days back."""
    lo, hi = 0, len(rows) - 1
    found = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if rows[mid][0] <= day:
            found = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if found < 0:
        return None
    gap = (dt.date.fromisoformat(day) - dt.date.fromisoformat(rows[found][0])).days
    if gap > limit:
        return None
    return rows[found]


def month_ends(rows):
    """Last session in each calendar month, in order."""
    last = {}
    for day, _px in rows:
        last[day[:7]] = day
    return [last[k] for k in sorted(last)]


def completed_month_ends(rows, today):
    """Drop the month that contains `today`. A month still in progress has no month-end yet."""
    ends = month_ends(rows)
    if ends and ends[-1][:7] == today[:7]:
        return ends[:-1]
    return ends


def sma(rows, day, n=SMA_DAYS):
    """Simple average of the `n` closes ending on `day`. The last close has to be that session."""
    point = asof(rows, day, limit=0)
    if point is None:
        return None
    idx = next(i for i, row in enumerate(rows) if row[0] == point[0])
    if idx + 1 < n:
        return None
    window = rows[idx + 1 - n:idx + 1]
    return sum(px for _d, px in window) / n


def formation_return(rows, ends, i):
    """close(t-1) / close(t-6) - 1. `i` indexes the rebalance month-end in `ends`."""
    if i < LOOKBACK:
        return None
    start = asof(rows, ends[i - LOOKBACK])
    end = asof(rows, ends[i - SKIP])
    if not start or not end or start[1] <= 0:
        return None
    return end[1] / start[1] - 1


def rank_top(returns):
    """Top two by formation return. A tie takes the symbol that sorts first."""
    ordered = sorted(returns.items(), key=lambda kv: (-kv[1], kv[0]))
    return [sym for sym, _ret in ordered[:TOP_N]]


def decision(prices, spy, ends, i):
    """('cash', []) or ('invest', [two symbols]). None when a price or the average is missing."""
    point = asof(spy, ends[i], limit=0)
    average = sma(spy, ends[i])
    if point is None or average is None:
        return None
    if point[1] < average:
        return ("cash", [])
    returns = {}
    for sym in UNIVERSE:
        ret = formation_return(prices[sym], ends, i)
        if ret is None:
            return None
        returns[sym] = ret
    return ("invest", rank_top(returns))


def _fit_buys(plans, cash):
    """Shrink a list of (symbol, shares, price) until the buys fit in cash."""
    shares = {sym: qty for sym, qty, _px in plans}
    prices = {sym: px for sym, _qty, px in plans}
    for _ in range(60):
        cost = 0.0
        for sym, qty in shares.items():
            if qty <= 1e-12:
                continue
            cash_change, _fee, _spread = trade_cost("buy", qty, prices[sym])
            cost += -cash_change
        if cost <= cash + 1e-8:
            return shares
        if cost <= 0:
            break
        scale = max(cash, 0.0) / cost
        shares = {sym: qty * scale for sym, qty in shares.items()}
    return {sym: 0.0 for sym in shares}


def rebalance(cash, shares, prices_today, targets):
    """Move to `targets` {symbol: shares}. Sells first. Buys are scaled to the cash that remains.

    `prices_today` is {symbol: price}. Returns cash, shares, commission, spread.
    """
    fee_sum = spread_sum = 0.0
    held = dict(shares)
    for sym in list(held):
        have = held.get(sym, 0.0)
        want = targets.get(sym, 0.0)
        if have - want <= 1e-9:
            continue
        cash_change, fee, spread_dol = trade_cost("sell", have - want, prices_today[sym])
        cash += cash_change
        fee_sum += fee
        spread_sum += spread_dol
        held[sym] = want
        if held[sym] <= 1e-12:
            held.pop(sym, None)
    plans = []
    for sym, want in targets.items():
        need = want - held.get(sym, 0.0)
        if need > 1e-9:
            plans.append((sym, need, prices_today[sym]))
    fitted = _fit_buys(plans, cash)
    for sym, qty in fitted.items():
        if qty <= 1e-12:
            continue
        cash_change, fee, spread_dol = trade_cost("buy", qty, prices_today[sym])
        if -cash_change > cash + 1e-6:
            continue
        cash += cash_change
        fee_sum += fee
        spread_sum += spread_dol
        held[sym] = held.get(sym, 0.0) + qty
    return cash, {s: q for s, q in held.items() if q > 1e-12}, fee_sum, spread_sum


def _prices_on(prices, day):
    out = {}
    for sym, rows in prices.items():
        point = asof(rows, day)
        if point is None:
            return None
        out[sym] = point[1]
    return out


def _equity(cash, shares, px):
    return cash + sum(qty * px[sym] for sym, qty in shares.items())


def sharpe(returns):
    """Annualized Sharpe of monthly simple returns. The risk-free rate is zero."""
    if len(returns) < 2:
        return None
    sd = statistics.stdev(returns)
    if sd == 0:
        return None
    return (sum(returns) / len(returns) / sd) * (12 ** 0.5)


def cagr(start_eq, end_eq, start, end):
    days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
    if days <= 0 or start_eq <= 0 or end_eq <= 0:
        return None
    return (end_eq / start_eq) ** (365.25 / days) - 1


def max_drawdown(equity):
    peak = None
    worst = 0.0
    for value in equity:
        if value is None or value <= 0:
            continue
        peak = value if peak is None else max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def compound(returns):
    wealth = 1.0
    for ret in returns:
        wealth *= 1 + ret
    return wealth - 1


def _sample(day):
    year = day[:4]
    if year in IS_YEARS:
        return "is"
    if year >= OOS_YEAR:
        return "oos"
    return None


def _targets(cash, shares, px, kind, names):
    if kind != "invest":
        return {}
    marked = _equity(cash, shares, px)
    out = {}
    for sym in names:
        out[sym] = (marked / TOP_N) / px[sym] if px[sym] > 0 else 0.0
    return out


def simulate(prices, spy, today):
    """Walk month by month. `prices` maps each sector symbol to [(date, adj_close), ...].

    A holding month is labeled by the month-end on which it finishes. The position was chosen at the
    previous month-end, and that rebalance's cost sits in the month it opens. The last month sells to
    cash, so its exit cost is in that month too.
    """
    missing = [sym for sym in UNIVERSE if sym not in prices or not prices[sym]]
    if missing or not spy:
        return {"complete": False, "reason": "missing series", "months": [], "daily": []}
    ends = completed_month_ends(spy, today)
    if len(ends) < LOOKBACK + 2:
        return {"complete": False, "reason": "not enough months", "months": [], "daily": []}
    pairs = []
    for i in range(LOOKBACK, len(ends) - 1):
        if _sample(ends[i + 1]) is None:
            continue
        pairs.append(i)
    if not pairs:
        return {"complete": False, "reason": "no months in sample", "months": [], "daily": []}
    end_of = {}
    schedule = {}
    for i in pairs:
        choice = decision(prices, spy, ends, i)
        if choice is None:
            return {"complete": False, "reason": f"missing price at {ends[i]}", "months": [], "daily": []}
        schedule[ends[i]] = choice
        end_of[ends[i]] = ends[i + 1]
    final = ends[pairs[-1] + 1]
    spy_by = {day: px for day, px in spy}
    first = ends[pairs[0]]
    dates = [day for day, _px in spy if first <= day <= final]
    cash, shares = ACCOUNT, {}
    daily, months, opens = [], [], {}
    total_fee = total_spread = 0.0
    prev_after = prev_date = None
    open_fee = open_spread = 0.0
    open_names, open_regime, open_sample = "", "", None
    for day in dates:
        px = _prices_on(prices, day)
        if px is None or day not in spy_by:
            return {"complete": False, "reason": f"missing close {day}", "months": [], "daily": []}
        if prev_after is not None and (day in schedule or day == final):
            before = _equity(cash, shares, px)
            daily.append((day, before, open_sample))
            if day == final:
                cash, shares, fee, spread_dol = rebalance(cash, shares, px, {})
                total_fee += fee
                total_spread += spread_dol
                equity = _equity(cash, shares, px)
                daily.append((day, equity, open_sample))
                months.append({"end": day, "sample": open_sample, "ret": equity / prev_after - 1,
                               "spy": spy_by[day] / spy_by[prev_date] - 1, "regime": open_regime,
                               "names": open_names, "equity": equity,
                               "fee": open_fee + fee, "spread": open_spread + spread_dol})
                break
            months.append({"end": day, "sample": open_sample, "ret": before / prev_after - 1,
                           "spy": spy_by[day] / spy_by[prev_date] - 1, "regime": open_regime,
                           "names": open_names, "equity": before, "fee": open_fee, "spread": open_spread})
        if day in schedule:
            kind, names = schedule[day]
            targets = _targets(cash, shares, px, kind, names)
            cash, shares, fee, spread_dol = rebalance(cash, shares, px, targets)
            total_fee += fee
            total_spread += spread_dol
            equity = _equity(cash, shares, px)
            open_fee, open_spread = fee, spread_dol
            open_names = ",".join(names)
            open_regime = kind
            open_sample = _sample(end_of[day])
            prev_after, prev_date = equity, day
            opens.setdefault(open_sample, (day, equity))
            daily.append((day, equity, open_sample))
            continue
        daily.append((day, _equity(cash, shares, px), open_sample))
    months = [m for m in months if m["sample"] in ("is", "oos")]
    return {"complete": True, "reason": "", "months": months, "daily": daily, "opens": opens,
            "fee": total_fee, "spread": total_spread, "final_equity": cash,
            "start": first, "end": final}


def slice_stats(months, daily, start, end, start_eq, end_eq, spy_start, spy_end):
    rets = [m["ret"] for m in months]
    spy = [m["spy"] for m in months]
    mean = sum(rets) / len(rets) if rets else None
    out = {"n": len(months), "mean": mean, "sharpe": sharpe(rets), "spy_sharpe": sharpe(spy),
           "cagr": cagr(start_eq, end_eq, start, end) if start and end else None,
           "spy_cagr": cagr(spy_start, spy_end, start, end) if start and end and spy_start and spy_end else None,
           "maxdd": max_drawdown([row[1] for row in daily]),
           "total": compound(rets) if rets else None, "spy_total": compound(spy) if spy else None,
           "cash_months": sum(m["regime"] == "cash" for m in months),
           "years": {}}
    for year in sorted({m["end"][:4] for m in months}):
        part = [m for m in months if m["end"][:4] == year]
        out["years"][year] = {"n": len(part), "ret": compound([m["ret"] for m in part]),
                              "spy": compound([m["spy"] for m in part]),
                              "cash": sum(m["regime"] == "cash" for m in part)}
    return out


def goes(stats, complete):
    """Out-of-sample success. In-sample numbers are not an input."""
    mean_ok = stats["mean"] is not None and stats["mean"] > 0
    sharpe_ok = (stats["sharpe"] is not None and stats["spy_sharpe"] is not None
                 and stats["sharpe"] > stats["spy_sharpe"])
    checks = [("positive mean monthly net return after costs", mean_ok and complete),
              ("Sharpe above buy-and-hold SPY", sharpe_ok and complete)]
    return {"checks": checks, "go": all(ok for _label, ok in checks)}
