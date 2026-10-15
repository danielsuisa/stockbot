"""Turn-of-month windows. Pure functions. No orders.

The quick screen and the full path are the T9 lock in docs/backtest/TRIALS.md.
Cash earns nothing. Commission is IBKR Pro Fixed, with no spread.
"""
from bot.queue_rules import fixed_fee

ACCOUNT = 460.0


def tom_windows(sessions):
    """One window per month that has a prior session and a next month with three sessions.

    The window is the month's last session plus the first three sessions of the next
    month. The entry is the close of the session before that last day. The exit is
    the close of the third session of the next month.
    """
    by_month = {}
    for day in sessions:
        by_month.setdefault(day[:7], []).append(day)
    months = sorted(by_month)
    index = {day: pos for pos, day in enumerate(sessions)}
    windows = []
    for pos, month in enumerate(months[:-1]):
        last = by_month[month][-1]
        entry_at = index[last] - 1
        if entry_at < 0:
            continue
        nxt = by_month[months[pos + 1]]
        if len(nxt) < 3:
            continue
        windows.append({
            "month": month,
            "entry": sessions[entry_at],
            "last": last,
            "exit": nxt[2],
        })
    return windows


def shares_for_fee(cash, price):
    """Fractional shares that spend at most `cash`, fee included, no spread."""
    if cash <= 0 or price <= 0:
        return 0.0, 0.0, cash
    lo, hi = 0.0, cash / price
    for _ in range(60):
        mid = (lo + hi) / 2
        if mid * price + fixed_fee(mid, price) <= cash + 1e-9:
            lo = mid
        else:
            hi = mid
    fee = fixed_fee(lo, price)
    return lo, fee, cash - lo * price - fee


def window_fee(raw_entry, raw_exit, account=ACCOUNT):
    """Two orders on a fresh `account` position. Returns the dollar fee."""
    if not raw_entry or raw_entry <= 0:
        return None
    shares = account / raw_entry
    exit_px = raw_exit if raw_exit and raw_exit > 0 else raw_entry
    return fixed_fee(shares, raw_entry) + fixed_fee(shares, exit_px)


def quick_screen(windows, gross, fees, account=ACCOUNT):
    """Mean window return times 12, minus the annual Fixed commission. No tax.

    `gross` and `fees` line up with `windows`. A partial year is not scaled up:
    it contributes the windows it has to the mean.
    """
    if not windows or len(gross) != len(windows) or len(fees) != len(windows):
        return None
    nets = [g - fee / account for g, fee in zip(gross, fees)]
    mean_gross = sum(gross) / len(gross)
    mean_fee = sum(fees) / len(fees)
    pretax = (mean_gross - mean_fee / account) * 12
    return {
        "n": len(windows),
        "mean_gross": mean_gross,
        "gross_annual": mean_gross * 12,
        "mean_fee": mean_fee,
        "commission_annual": mean_fee / account * 12,
        "pretax": pretax,
        "nets": nets,
    }


def close_enough(strategy, spy, margin=0.01):
    """True when the quick rate is within `margin` of SPY, or ahead of it."""
    if strategy is None or spy is None:
        return False
    return strategy >= spy - margin - 1e-12


def buy_hold(start_px, end_px, account=ACCOUNT):
    """One entry and one exit, Fixed commission, no spread."""
    shares, fee, left = shares_for_fee(account, start_px)
    exit_fee = fixed_fee(shares, end_px)
    terminal = left + shares * end_px - exit_fee
    return {"terminal": terminal, "shares": shares, "fees": fee + exit_fee}


def held_sessions(sessions, windows):
    """Sessions whose close-to-close move is inside a window: the day after entry through the exit."""
    index = {day: pos for pos, day in enumerate(sessions)}
    held = []
    for window in windows:
        start = index.get(window["entry"])
        end = index.get(window["exit"])
        if start is None or end is None:
            continue
        held.extend(sessions[start + 1:end + 1])
    return held


def run_tom_path(days, held, prices, account=ACCOUNT):
    """Compounded close-to-close path. Cash at 0%. No spread. The last close liquidates.

    `held` are the sessions that earn the price move. The switch is at the prior close.
    """
    held = set(held)
    cash = float(account)
    shares = 0.0
    basis = 0.0
    holding = False
    fees = 0.0
    realizations = []

    def mark(day):
        if not holding:
            return cash
        return cash + shares * prices[day]

    def enter(day):
        nonlocal cash, shares, basis, holding, fees
        if holding or day not in prices:
            return
        bought, fee, left = shares_for_fee(cash, prices[day])
        spent = cash - left
        cash = left
        shares = bought
        basis = spent
        holding = bought > 0
        fees += fee

    def leave(day):
        nonlocal cash, shares, basis, holding, fees
        if not holding:
            return
        fee = fixed_fee(shares, prices[day])
        proceeds = shares * prices[day] - fee
        realizations.append((day, proceeds - basis))
        cash += proceeds
        fees += fee
        shares = 0.0
        basis = 0.0
        holding = False

    def target(index):
        if index >= len(days) - 1:
            return False
        return days[index + 1] in held

    curve = []
    want = target(0)
    if want:
        enter(days[0])
    curve.append((days[0], mark(days[0])))
    for index in range(1, len(days)):
        day = days[index]
        want = target(index) if index < len(days) - 1 else False
        if want and not holding:
            enter(day)
        elif holding and not want:
            leave(day)
        curve.append((day, mark(day)))
    if holding:
        leave(days[-1])
        curve[-1] = (days[-1], cash)
    returns = []
    for (_day0, equity0), (day1, equity1) in zip(curve, curve[1:]):
        if equity0 and equity0 > 0 and equity1 is not None:
            returns.append((day1, equity1 / equity0 - 1))
    return {
        "curve": curve,
        "returns": returns,
        "terminal": curve[-1][1],
        "fees": fees,
        "realizations": realizations,
    }
