"""SYNTHETIC price paths for the index program's tests, on the exchange calendar's dates (index_probe.nyse_days, less
2011-02-17 under D1). No real price is read."""
import math

from bot import index_data, index_probe


def calendar(start, end):
    return [d for d in index_probe.nyse_days(start, end)[0] if d != "2011-02-17"]


def rows(start, end, close_of):
    """close_of(k, day) -> the close of trading day k; the open is the previous close (no gap), high and low
    1% around."""
    out, prev = [], None
    for k, d in enumerate(calendar(start, end)):
        c = close_of(k, d)
        o = c if prev is None else prev
        out.append((d, o, max(o, c) * 1.01, min(o, c) * 0.99, c))
        prev = c
    return out


def cash(rws, rate=2.0):
    days = [r[0] for r in rws]
    return index_data.cash_returns(days, {"1993-12-01": rate})


def rising(g=0.0004):
    return lambda k, d: 100.0 * math.exp(g * k)


def flat():
    return lambda k, d: 100.0


def crash(at, length, depth, g=0.0004):
    """A steady rise, then a fall of `depth` over `length` days from day `at`, then the rise again from the low."""
    def f(k, d):
        base = 100.0 * math.exp(g * k)
        if k < at:
            return base
        if k < at + length:
            return base * (1 - depth * (k - at) / length)
        return base * (1 - depth)
    return f
