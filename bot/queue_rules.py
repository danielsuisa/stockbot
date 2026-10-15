"""Queue rules for the pending books T3 through T10.

IBKR Pro Fixed commission, and the in-sample sample-size gate.
This module does not place or transmit an order.
"""
import statistics

FEE_PER_SHARE = 0.005
FEE_MIN = 1.0
FEE_CAP = 0.01
Z_95 = 1.645
UNKNOWN = "UNKNOWN (insufficient sample)"


def fixed_fee(shares, price):
    """One order. $0.005 a share, at least $1, at most 1% of that order's trade value."""
    notional = abs(shares) * abs(price)
    if shares == 0 or notional <= 0:
        return 0.0
    raw = max(FEE_MIN, FEE_PER_SHARE * abs(shares))
    return min(raw, FEE_CAP * notional)


def required_n(returns):
    """n = (1.645 * sigma / mu)^2 from in-sample per-trade returns.

    Returns `(n, mu, sigma, count)`. Sigma is the sample standard deviation.
    `n` is None when it cannot be computed: no trades, one trade, or a mean of
    zero (the ratio is infinite). A negative mean is used as written. The square
    keeps n positive.
    """
    xs = [float(value) for value in returns]
    count = len(xs)
    if count == 0:
        return None, None, None, 0
    mu = statistics.mean(xs)
    if count < 2:
        return None, mu, None, count
    sigma = statistics.stdev(xs)
    if mu == 0.0:
        return None, mu, sigma, count
    return (Z_95 * sigma / mu) ** 2, mu, sigma, count


def sample_unknown(oos_count, n):
    """True when the out-of-sample count does not meet n. An undefined n does not clear."""
    if n is None:
        return True
    return oos_count < n


def queue_verdict(oos_count, n, economic_go):
    """The sample gate is applied before a NO-GO."""
    if sample_unknown(oos_count, n):
        return UNKNOWN
    return "GO" if economic_go else "NO-GO"


def sample_lines(label, returns, oos_count, flag=None):
    """Prose placed before a report's results. Returns `(lines, n)`."""
    n, mu, sigma, count = required_n(returns)
    if count == 0:
        body = (
            f"{label}: the in-sample period has no trades, so mu and sigma are not defined. "
            "Required n is not defined."
        )
    elif sigma is None:
        body = (
            f"{label}: the in-sample period has {count} trade, so sigma is not defined. "
            f"mu is {mu:.6f}. Required n is not defined."
        )
    elif n is None:
        body = (
            f"{label}: in-sample mu is {mu:.6f} and sigma is {sigma:.6f} over {count} trades. "
            "mu is zero, so required n is infinite."
        )
    else:
        sign = " Mu is negative. The formula is applied as written." if mu < 0 else ""
        body = (
            f"{label}: in-sample per-trade mu {mu:.6f}, sigma {sigma:.6f}, {count} trades. "
            f"Required n = (1.645 × sigma / mu)² = {n:.2f}.{sign}"
        )
    lines = [body]
    if sample_unknown(oos_count, n):
        lines.append(
            f"Out-of-sample trades: {oos_count}. That count is below n, so the queue verdict "
            f"is {UNKNOWN}, not NO-GO."
        )
    else:
        lines.append(
            f"Out-of-sample trades: {oos_count}. That count meets n ({n:.2f}). "
            "The sample gate does not replace this book's own bar."
        )
    if flag:
        lines.append(flag)
    return lines, n
