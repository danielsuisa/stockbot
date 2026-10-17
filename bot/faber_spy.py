"""Faber 10-month filter on SPY. Pure functions. No orders.

The book is the T10 lock in docs/backtest/TRIALS.md. Cash earns nothing.
Commission is IBKR Pro Fixed, with no spread. A close equal to the average is cash.
The recorded book holds cash when it is out of the market. A sleeve price map is
a separate holding, and entering or leaving it pays a commission.
"""
import math
import statistics

from bot.queue_rules import fixed_fee
from bot.tom_exec import shares_for_fee, whole_shares_for_fee

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


def run_faber(days, signals, prices, account=ACCOUNT, whole=False, sleeve=None,
              dividends=None, sleeve_dividends=None, splits=None, sleeve_splits=None,
              opens=None, sleeve_opens=None):
    """Close-to-close book. `signals` maps a session to True (the risk fund) or False.

    The account starts in cash. A signal on a session trades at that close, so the
    next session's move belongs to the new holding. False is cash at 0% unless
    `sleeve` has a price that session, in which case the account holds the sleeve.
    Entering and leaving the sleeve each pay a commission. The last session
    liquidates. That final sale is the measurement exit. It is not an extra switch.

    `whole` buys an integer share count. The default is fractional. `splits` maps
    a session to new shares per old share and is applied to shares already held.
    Dividend maps pay cash to shares held coming into that session.
    """
    cash = float(account)
    shares = 0.0
    basis = 0.0
    mode = "cash"
    intent = "out"
    fees = 0.0
    switches = 0
    realizations = []
    cash_extras = 0.0

    def price_of(day, which):
        book = prices if which == "risk" else sleeve
        if not book or day not in book:
            return None
        px = book[day]
        return px if px and px > 0 else None

    def mark(day):
        if mode == "cash":
            return cash
        px = price_of(day, mode)
        if px is None:
            return cash
        return cash + shares * px

    def size(day_cash, px):
        if whole:
            return whole_shares_for_fee(day_cash, px)
        return shares_for_fee(day_cash, px)

    def sell(day):
        nonlocal cash, shares, basis, mode, fees
        px = price_of(day, mode)
        if mode == "cash" or shares <= 0 or px is None:
            shares = 0.0
            basis = 0.0
            mode = "cash"
            return
        fee = fixed_fee(shares, px)
        proceeds = shares * px - fee
        realizations.append((day, proceeds - basis))
        cash += proceeds
        fees += fee
        shares = 0.0
        basis = 0.0
        mode = "cash"

    def buy(day, which):
        nonlocal cash, shares, basis, mode, fees
        px = price_of(day, which)
        if px is None:
            return
        bought, fee, left = size(cash, px)
        cash = left
        shares = bought
        basis = bought * px + fee
        mode = which if bought > 0 else "cash"
        fees += fee

    def apply_split(day):
        nonlocal cash, shares, basis, mode, cash_extras
        if mode == "cash" or shares <= 0:
            return
        table = splits if mode == "risk" else sleeve_splits
        if not table or day not in table:
            return
        grown = shares * table[day]
        paid = 0.0
        if whole:
            kept = int(math.floor(grown + 1e-9))
            fraction = grown - kept
            open_book = opens if mode == "risk" else sleeve_opens
            opx = None if not open_book else open_book.get(day)
            if fraction > 1e-8 and opx and opx > 0:
                paid = fraction * opx
                shares = float(kept)
            elif fraction > 1e-8:
                shares = grown
            else:
                shares = float(kept)
        else:
            shares = grown
        if shares <= 0:
            realizations.append((day, paid - basis))
            cash += paid
            basis = 0.0
            mode = "cash"
            return
        if paid:
            cash += paid
            cash_extras += paid

    def credit_dividend(day):
        nonlocal cash, cash_extras
        if mode == "cash" or shares <= 0:
            return
        table = dividends if mode == "risk" else sleeve_dividends
        if not table or day not in table:
            return
        amount = table[day] * shares
        if amount:
            cash += amount
            cash_extras += amount

    curve = []
    for day in days:
        apply_split(day)
        credit_dividend(day)
        signaled = day in signals
        if signaled:
            intent = "risk" if bool(signals[day]) else "out"
        if intent == "risk":
            desired = "risk"
        elif price_of(day, "sleeve") is not None:
            desired = "sleeve"
        else:
            desired = "cash"
        listing = (not signaled) and desired == "sleeve" and mode == "cash"
        if (signaled and desired != mode) or listing:
            if mode != "cash":
                sell(day)
            if desired != "cash":
                buy(day, desired)
            switches += 1
        curve.append((day, mark(day)))
    if mode != "cash":
        sell(days[-1])
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
        "cash_extras": cash_extras,
    }
