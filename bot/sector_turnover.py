"""Turnover of the locked sector book. Holdings only. No return is computed.

A single-fund replacement is a name in last month's held set that is not in this month's.
Cash is the empty set. The preregistered decision is used: top two by the 6-1 return, or cash
when SPY's month-end close is strictly below its 200-day average. This module does not place
or transmit an order.
"""
import datetime as dt

from bot.sector_mom import completed_month_ends, decision, single_fund_replacements

START = "2007-01-01"
GATE = 2.0


def held_set(choice):
    if choice[0] == "invest":
        return tuple(choice[1])
    return ()


def replacement_steps(prices, spy, today, start=START):
    """Changes into each completed month-end on or after `start`.

    The held set at the prior month-end is the baseline. It is not itself a step.
    """
    ends = completed_month_ends(spy, today)
    prev = None
    steps = []
    for i, end in enumerate(ends):
        choice = decision(prices, spy, ends, i)
        if choice is None:
            continue
        held = held_set(choice)
        if prev is None:
            prev = held
            continue
        if end >= start:
            steps.append({
                "end": end,
                "prior": prev,
                "held": held,
                "replacements": single_fund_replacements(prev, held),
                "cash": choice[0] == "cash",
            })
        prev = held
    return steps


def per_year(steps):
    """Total single-fund replacements divided by the year span of the steps."""
    if len(steps) < 2:
        return None
    first = dt.date.fromisoformat(steps[0]["end"])
    last = dt.date.fromisoformat(steps[-1]["end"])
    years = (last - first).days / 365.25
    if years <= 0:
        return None
    total = sum(step["replacements"] for step in steps)
    return {"total": total, "steps": len(steps), "years": years, "per_year": total / years}


def closes(steps):
    """True when the average replacement rate is above the pre-registered gate."""
    stats = per_year(steps)
    return stats is not None and stats["per_year"] > GATE
