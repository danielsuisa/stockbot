"""Faber 10-month filter on SPY. Pure functions. No orders.

The book is the T10 lock in docs/backtest/TRIALS.md. Cash earns nothing.
Commission is IBKR Pro Fixed, with no spread. A close equal to the average is cash.
"""
import statistics

from bot.queue_rules import fixed_fee
from bot.tom_exec import shares_for_fee

ACCOUNT = 460.0
LOOKBACK = 10


def month_ends(sessions):
    """The last session of each calendar month, in order."""
    last = {}
    for day in sessions:
        last[day[:7]] = day
    return [last[key] for key in sorted(last)]


def faber_signals(ends, prices, lookback=LOOKBACK):
    """`(date, hold)` once `lookback` month-end prices exist, including that month.

    Hold is true only when the month-end price is strictly above the average.
    """
    out = []
    for index, day in enumerate(ends):
        if index + 1 < lookback:
            continue
        window = ends[index + 1 - lookback:index + 1]
        if any(item not in prices for item in window):
            continue
        average = sum(prices[item] for item in window) / lookback
        out.append((day, prices[day] > average))
    return out


def drawdown_reduced(strategy_dd, spy_dd):
    """The T10 goal. Return is not an input. A tie is not a reduction."""
    if strategy_dd is None or spy_dd is None:
        return False
    return strategy_dd < spy_dd


def run_faber(days, signals, prices, account=ACCOUNT):
    """Close-to-close book. `signals` maps a session to True (SPY) or False (cash).

    The account starts in cash. A signal on a session trades at that close, so the
    next session's move belongs to the new holding. The last session liquidates.
    That final sale is the measurement exit. It is not an extra switch.
    """
    cash = float(account)
    shares = 0.0
    basis = 0.0
    holding = False
    fees = 0.0
    switches = 0
    realizations = []

    def mark(day):
        if not holding:
            return cash
        return cash + shares * prices[day]

    def enter(day):
        nonlocal cash, shares, basis, holding, fees
        bought, fee, left = shares_for_fee(cash, prices[day])
        cash = left
        shares = bought
        basis = bought * prices[day] + fee
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

    curve = []
    for day in days:
        if day in signals and bool(signals[day]) != holding:
            if signals[day]:
                enter(day)
            else:
                leave(day)
            switches += 1
        curve.append((day, mark(day)))
    if holding:
        leave(days[-1])
        curve[-1] = (days[-1], cash)
    returns = []
    for (_day0, equity0), (day1, equity1) in zip(curve, curve[1:]):
        if equity0 and equity0 > 0 and equity1 is not None:
            returns.append((day1, equity1 / equity0 - 1))
    values = [ret for _day, ret in returns]
    if len(values) < 2 or statistics.stdev(values) == 0:
        sharpe = None
    else:
        sharpe = (sum(values) / len(values) / statistics.stdev(values)) * (252 ** 0.5)
    return {
        "curve": curve,
        "returns": returns,
        "terminal": curve[-1][1],
        "fees": fees,
        "realizations": realizations,
        "switches": switches,
        "sharpe": sharpe,
    }
