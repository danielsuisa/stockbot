"""GEM dual-momentum sleeve. The rule only. This module does not place or transmit an order.

At a month-end, compare 12-month total returns. If SPY beats BIL, hold whichever of SPY and VEU
has the higher 12-month total return. Otherwise hold the defensive asset. A tie is not a win.
There is no gap and no hysteresis. `defensive` is AGG for the original book and BIL for GEM-BIL.
The two books are reported separately. The better one is not chosen after the fact.
"""


def gem_sleeve(spy_12m, bil_12m, veu_12m, defensive):
    """The holding selected by one month-end signal. `defensive` is 'AGG' or 'BIL'."""
    if defensive not in ("AGG", "BIL"):
        raise ValueError(defensive)
    if spy_12m > bil_12m:
        if veu_12m > spy_12m:
            return "VEU"
        return "SPY"
    return defensive
