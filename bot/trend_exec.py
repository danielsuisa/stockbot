"""Faber and GEM execution. Whole shares, market-on-open, leftover cash at zero.

Signals are computed elsewhere. This module turns a month-end target into fills.
It does not place or transmit an order.

The account buys whole shares of the fund named in the order, at that fund's open.
A SPY signal is executed as SPYM or as SPY by the caller; this module does not
choose. A switch sells at the next open and buys at the open after that.
"""
import math
import random
import statistics

from bot.gem import gem_sleeve

ACCOUNT = 460.0
HALF_SPREAD = 0.0001
CASH = "CASH"
N_EFF = 7
BOOT_DRAWS = 1000
BOOT_BLOCK = 3
BOOT_SEED = 20261015
BOOT_PASS = 0.80
CAGR_GAP = 0.01

_EULER = 0.5772156649015329


def fixed_commission(shares, price):
    """IBKR Pro Fixed: $0.005 a share, minimum $1, maximum 1% of trade value."""
    from bot.queue_rules import fixed_fee
    return fixed_fee(shares, price)


def whole_shares(cash, open_price):
    """Whole shares bought at `open_price`, paying the half-spread and the commission."""
    if open_price <= 0 or cash <= 0:
        return 0
    fill = open_price * (1 + HALF_SPREAD)
    count = int(cash // fill)
    while count > 0:
        if count * fill + fixed_commission(count, open_price) <= cash + 1e-8:
            return count
        count -= 1
    return 0


def actual_prints(bars, splits):
    """Undo split adjustment. `splits` maps the split date to new shares per old share.

    Yahoo's stored open is the post-split scale. A print before the split is multiplied
    by each later ratio, which puts it back at the price that traded.
    """
    later = sorted(splits.items())
    out = {}
    for day, (opx, cpx) in bars.items():
        factor = 1.0
        for split_day, ratio in later:
            if day < split_day:
                factor *= ratio
        out[day] = (opx * factor, cpx * factor)
    return out


def actual_dividends(amounts, splits):
    """Dividend cash per share actually held. Amounts follow the same split scale as prices."""
    later = sorted(splits.items())
    out = {}
    for day, amount in amounts.items():
        factor = 1.0
        for split_day, ratio in later:
            if day < split_day:
                factor *= ratio
        out[day] = amount * factor
    return out


def _tradable(sessions, prices, symbol, after):
    book = prices.get(symbol) or {}
    for day in sessions:
        if day > after and day in book:
            return day
    return None


def schedule(held, target, sessions, prices, signal_date):
    """Market-on-open orders after `signal_date`. A switch buys one session after the sale."""
    if target == held:
        return []
    if held == CASH:
        buy_day = _tradable(sessions, prices, target, signal_date)
        return [("buy", buy_day, target)] if buy_day else []
    sell_day = _tradable(sessions, prices, held, signal_date)
    if sell_day is None:
        return []
    if target == CASH:
        return [("sell", sell_day, held)]
    buy_day = _tradable(sessions, prices, target, sell_day)
    orders = [("sell", sell_day, held)]
    if buy_day:
        orders.append(("buy", buy_day, target))
    return orders


def simulate(prices, splits, dividends, sessions, signals, start, end, account=ACCOUNT):
    """Run one account from the close of `start` through the close of `end`.

    `signals` maps a session to an execution symbol or CASH. The account is cash at
    the start close. Orders from that close fill on later opens. Shares still held
    at `end` are sold at that close so the exit cost is in the final equity.
    """
    cash = float(account)
    shares = 0
    held = CASH
    orders = []
    curve = []
    trades = []
    for day in sessions:
        if day < start or day > end:
            continue
        div_symbol = held
        div_shares = shares
        if held != CASH and day in splits.get(held, {}):
            ratio = splits[held][day]
            opx = prices[held][day][0]
            grown = shares * ratio
            whole = int(math.floor(grown + 1e-9))
            fraction = grown - whole
            if fraction > 1e-8:
                cash += fraction * opx
            shares = whole
            div_shares = shares
            if shares == 0:
                held = CASH
                div_symbol = CASH
        due = [order for order in orders if order[1] == day]
        orders = [order for order in orders if order[1] != day]
        for side, _when, symbol in due:
            if side == "sell" and held == symbol and shares > 0 and day in prices.get(symbol, {}):
                opx = prices[symbol][day][0]
                fill = opx * (1 - HALF_SPREAD)
                fee = fixed_commission(shares, opx)
                cash += shares * fill - fee
                trades.append({"date": day, "side": "sell", "symbol": symbol, "shares": shares,
                               "open": opx, "fee": fee})
                shares = 0
                held = CASH
            elif side == "buy" and held == CASH and day in prices.get(symbol, {}):
                opx = prices[symbol][day][0]
                count = whole_shares(cash, opx)
                if count > 0:
                    fill = opx * (1 + HALF_SPREAD)
                    fee = fixed_commission(count, opx)
                    cash -= count * fill + fee
                    shares = count
                    held = symbol
                    trades.append({"date": day, "side": "buy", "symbol": symbol, "shares": count,
                                   "open": opx, "fee": fee, "cash": cash})
        if div_symbol != CASH and div_shares > 0 and day in dividends.get(div_symbol, {}):
            cash += dividends[div_symbol][day] * div_shares
        if held == CASH:
            marked = cash
        elif day in prices.get(held, {}):
            marked = cash + shares * prices[held][day][1]
        elif curve:
            marked = curve[-1][1]
        else:
            marked = cash
        if day == end and shares > 0 and day in prices.get(held, {}):
            cpx = prices[held][day][1]
            fill = cpx * (1 - HALF_SPREAD)
            fee = fixed_commission(shares, cpx)
            cash += shares * fill - fee
            trades.append({"date": day, "side": "sell", "symbol": held, "shares": shares,
                           "open": cpx, "fee": fee, "liquidation": True})
            shares = 0
            held = CASH
            marked = cash
        curve.append((day, marked))
        if day in signals and start <= day <= end and day != end:
            target = signals[day]
            live = held if shares > 0 else CASH
            orders = schedule(live, target, sessions, prices, day)
    final = curve[-1][1] if curve else account
    return {"curve": curve, "final": final, "trades": trades, "fees": sum(t["fee"] for t in trades)}


def month_ends(dates):
    last = {}
    for day in dates:
        last[day[:7]] = day
    return [last[key] for key in sorted(last)]


def faber_signal(adj_closes, exec_symbol):
    """SPY month-end adjusted close strictly above its 10-month average. Else cash.

    `adj_closes` is a sorted list of (date, adjusted close). The average uses ten
    month-end closes ending on the signal month. `exec_symbol` is the fund bought
    when the signal is long.
    """
    ends = month_ends([day for day, _px in adj_closes])
    px = dict(adj_closes)
    level = [px[day] for day in ends if day in px]
    ends = [day for day in ends if day in px]
    out = {}
    for i, day in enumerate(ends):
        if i < 9:
            continue
        average = sum(level[i - 9:i + 1]) / 10
        out[day] = exec_symbol if level[i] > average else CASH
    return out


def gem_signals(spy, bil, veu, defensive, equity_symbol):
    """Month-end GEM targets. A tie is not a win. `equity_symbol` is used when the sleeve is SPY."""
    spy_px, bil_px, veu_px = dict(spy), dict(bil), dict(veu)
    ends = [day for day in month_ends(spy_px) if day in spy_px]
    out = {}
    for i, day in enumerate(ends):
        if i < 12:
            continue
        prev = ends[i - 12]
        if prev not in bil_px or day not in bil_px or prev not in veu_px or day not in veu_px:
            continue
        if prev not in spy_px or spy_px[prev] <= 0 or bil_px[prev] <= 0 or veu_px[prev] <= 0:
            continue
        choice = gem_sleeve(
            spy_px[day] / spy_px[prev] - 1,
            bil_px[day] / bil_px[prev] - 1,
            veu_px[day] / veu_px[prev] - 1,
            defensive,
        )
        if choice == "SPY":
            out[day] = equity_symbol
        elif choice == "VEU":
            out[day] = "VEU"
        else:
            out[day] = defensive
    return out


def month_end_equity(curve):
    last = {}
    for day, equity in curve:
        last[day[:7]] = (day, equity)
    return [last[key] for key in sorted(last)]


def monthly_returns(curve, final=None, end=None):
    points = month_end_equity(curve)
    if final is not None and end is not None and points and points[-1][0] == end:
        points[-1] = (end, final)
    out = []
    for (_d0, e0), (d1, e1) in zip(points, points[1:]):
        if e0 <= 0:
            continue
        out.append((d1, e1 / e0 - 1))
    return out


def cagr(start_equity, end_equity, start, end):
    import datetime as dt
    years = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days / 365.25
    if years <= 0 or start_equity <= 0 or end_equity <= 0:
        return None
    return (end_equity / start_equity) ** (1 / years) - 1


def max_drawdown(curve):
    if not curve:
        return None
    peak = curve[0][1]
    worst = 0.0
    for _day, equity in curve:
        if equity > peak:
            peak = equity
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def year_drawdown(curve, year):
    rows = [(day, equity) for day, equity in curve if day[:4] == year]
    if len(rows) < 2:
        return None
    return max_drawdown(rows)


def _compound_drawdown(returns):
    wealth = 1.0
    peak = 1.0
    worst = 0.0
    for ret in returns:
        wealth *= 1 + ret
        if wealth > peak:
            peak = wealth
        if peak > 0:
            worst = max(worst, (peak - wealth) / peak)
    return worst


def drawdown_is_smaller(strategy, spy, fraction=2 / 3):
    """True when the strategy drawdown is at most `fraction` of SPY's."""
    if spy <= 0:
        return strategy <= 0
    return strategy <= spy * fraction + 1e-15


def block_bootstrap(strategy, spy, draws=BOOT_DRAWS, block=BOOT_BLOCK, seed=BOOT_SEED, fraction=2 / 3):
    """Paired circular block bootstrap. Returns the fraction of draws that pass."""
    if len(strategy) != len(spy) or len(strategy) < block:
        return None
    rng = random.Random(seed)
    length = len(strategy)
    blocks = math.ceil(length / block)
    passed = 0
    for _ in range(draws):
        path_s = []
        path_b = []
        for _block in range(blocks):
            start = rng.randrange(length)
            for step in range(block):
                i = (start + step) % length
                path_s.append(strategy[i])
                path_b.append(spy[i])
        path_s = path_s[:length]
        path_b = path_b[:length]
        if drawdown_is_smaller(_compound_drawdown(path_s), _compound_drawdown(path_b), fraction):
            passed += 1
    return passed / draws


def trend_passes(growth, spy_growth, boot_rate):
    """(a) CAGR no more than 1 point below SPY, and (b) the bootstrap clears 80%."""
    if growth is None or spy_growth is None or boot_rate is None:
        return False
    annual_ok = growth >= spy_growth - CAGR_GAP - 1e-12
    drawdown_ok = boot_rate + 1e-15 >= BOOT_PASS
    return annual_ok and drawdown_ok


def gem_bil_passes(full, early):
    """GEM-BIL needs the decision window and 2014-2021, each on its own."""
    return bool(full) and bool(early)


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p):
    """Acklam's inverse normal. Used by the deflated Sharpe ratio."""
    if p <= 0 or p >= 1:
        raise ValueError(p)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def sharpe_stats(returns):
    """Monthly Sharpe, skew, and raw kurtosis. Sharpe uses the sample standard deviation."""
    n = len(returns)
    if n < 3:
        return None
    mu = statistics.fmean(returns)
    sd = statistics.stdev(returns)
    if sd <= 0:
        return None
    m3 = sum((x - mu) ** 3 for x in returns) / n
    m4 = sum((x - mu) ** 4 for x in returns) / n
    skew = m3 / sd ** 3
    kurt = m4 / sd ** 4
    return {"n": n, "mean": mu, "sr": mu / sd, "skew": skew, "kurt": kurt}


def deflated_sharpe(returns, trials=N_EFF):
    """PSR against zero, and DSR against the expected maximum Sharpe given `trials` clusters."""
    stats = sharpe_stats(returns)
    if stats is None:
        return None
    sr, n, skew, kurt = stats["sr"], stats["n"], stats["skew"], stats["kurt"]
    variance_factor = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if variance_factor <= 0 or trials < 1:
        return {**stats, "psr": None, "dsr": None, "sr_benchmark": None}
    sr_var = variance_factor / (n - 1)
    if trials == 1:
        benchmark = 0.0
    else:
        z1 = _norm_ppf(1 - 1 / trials)
        z2 = _norm_ppf(1 - math.exp(-1) / trials)
        benchmark = math.sqrt(sr_var) * ((1 - _EULER) * z1 + _EULER * z2)

    def _psr(hurdle):
        z = (sr - hurdle) * math.sqrt(n - 1) / math.sqrt(variance_factor)
        return _norm_cdf(z)

    return {**stats, "psr": _psr(0.0), "dsr": _psr(benchmark), "sr_benchmark": benchmark}
