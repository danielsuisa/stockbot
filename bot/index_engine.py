"""Index program: execution, accounting, costs and the scale (docs/superpowers/specs/2026-10-12-index-program-design.md
sections 5, 6.2 and 6.3, with A4, C2 and E4). Pure functions; daily equity stays in memory (E2); nothing printed.

A policy is a list of change points [(day, "open" or "close", in_spy)]: the state at a moment is the last change
at or before it (OFF before the first). The account holds s x equity in SPY while in SPY and the rest in the cash
leg; every switch trades both legs and pays both legs' costs; costs come out of the cash leg on the trade's day."""
import bisect
import collections

Costs = collections.namedtuple("Costs", "commission bps")
OPTIMISTIC, BASE, STRESS = Costs(0.35, 0.0), Costs(0.35, 1.0), Costs(0.35, 3.0)  # 5: $0.35 an order; 0, 1, 3 bp
EQUITY0 = 10_000.0  # 5: the model account
BAND = 0.05  # 6.2: the yearly rebalance band
BUDGET, CAP = 0.16, 0.60  # 6.2: the drawdown budget and the crash cap (A4: applied after the search)
STEPS = [round(k / 100, 2) for k in range(1, 101)]  # A4: s = 0.01 .. 1.00
SLOT = {"open": 0, "close": 1}


def _state(changes):
    keys = [(d, SLOT[s]) for d, s, _ in changes]
    vals = [v for _, _, v in changes]

    def at(day, slot):
        k = bisect.bisect_right(keys, (day, SLOT[slot])) - 1
        return vals[k] if k >= 0 else False
    return at


def run_window(rows, cash_r, changes, scale, costs, start, end, equity0=EQUITY0):
    """One fresh account over [start, end] (5): it starts in the cash leg at the close before `start`, takes the
    policy's position at the first open, marks at every close, and is marked at the last close at the end. A close
    execution on the window's last day is not executed (E4). -> {"days", "equity", "weights", "tickets",
    "tickets_by_year", "costs", "trades", "final", "e0"}; "trades" are (day, slot, "in"/"out"/"rebalance",
    equity before, equity after)."""
    state = _state(sorted(changes, key=lambda c: (c[0], SLOT[c[1]])))
    k0 = next(k for k, r in enumerate(rows) if r[0] >= start)
    win = [r for r in rows[k0:] if r[0] <= end]
    prev = rows[k0 - 1] if k0 > 0 else None
    cash, shares, holding = equity0, 0.0, False
    out = {"days": [], "equity": [], "weights": [], "tickets": 0, "tickets_by_year": collections.Counter(),
           "costs": 0.0, "trades": [], "e0": equity0}

    def pay(day, amount):
        nonlocal cash
        c = 2 * costs.commission + 2 * costs.bps / 10_000 * abs(amount)  # both legs: two orders, bp on each leg
        cash -= c
        out["costs"] += c
        out["tickets"] += 2
        out["tickets_by_year"][day[:4]] += 2

    def switch(day, slot, price, into):
        nonlocal cash, shares, holding
        before = shares * price + cash
        if into:
            amount = scale * before
            shares, cash = amount / price, cash - amount
        else:
            amount = shares * price
            shares, cash = 0.0, cash + amount
        pay(day, amount)
        holding = into
        out["trades"].append((day, slot, "in" if into else "out", before, shares * price + cash))

    for i, (day, o, _h, _l, c) in enumerate(win):
        cash *= 1 + cash_r[day]  # day t's cash return, on the money in the cash leg at the close of t-1
        target = state(day, "open")
        if target != holding:
            switch(day, "open", o, target)
        elif holding and i > 0 and day[:4] != win[i - 1][0][:4]:  # the year's first trading day, still in SPY
            e_prev = out["equity"][-1]
            w_prev = shares * win[i - 1][4] / e_prev
            if abs(w_prev - scale) > BAND:
                amount = scale * e_prev - shares * win[i - 1][4]  # the dollar amount that restored s at that close
                before = shares * o + cash
                shares, cash = shares + amount / o, cash - amount
                pay(day, amount)
                out["trades"].append((day, "open", "rebalance", before, shares * o + cash))
        if i < len(win) - 1:  # E4: a signal on the window's last day is not executed
            target = state(day, "close")
            if target != holding:
                switch(day, "close", c, target)
        e = shares * c + cash
        out["days"].append(day)
        out["equity"].append(e)
        out["weights"].append(shares * c / e if e else 0.0)
    out["final"] = out["equity"][-1] if out["equity"] else equity0
    out["prev_close_day"] = prev[0] if prev else None
    return out


# ---------- 6.3 ----------
def growth_rate(e0, e_end, first_day, last_day):
    """6.3: (final / initial) ^ (365.25 / calendar days from the window's first day to its last day) - 1."""
    import datetime as dt
    days = (dt.date.fromisoformat(last_day) - dt.date.fromisoformat(first_day)).days
    return (e_end / e0) ** (365.25 / days) - 1


def max_drawdown(days, equity):
    """6.3: the largest fall of the closing equity from its highest earlier close in the window -> (depth, peak
    day, trough day); (0.0, None, None) when it never falls."""
    best, peak, peak_day = (0.0, None, None), float("-inf"), None
    for d, e in zip(days, equity):
        if e > peak:
            peak, peak_day = e, d
        elif peak > 0 and (peak - e) / peak > best[0]:
            best = ((peak - e) / peak, peak_day, d)
    return best


def calendar_year_returns(e0, days, equity):
    """B5: each calendar year's return of the account: its last close over the previous year's last close (the
    initial equity for the window's first year), less 1."""
    out, prev = {}, e0
    for k, d in enumerate(days):
        if k == len(days) - 1 or days[k + 1][:4] != d[:4]:
            out[d[:4]] = equity[k] / prev - 1
            prev = equity[k]
    return out


# ---------- 6.2 with A4 and C2 ----------
def scale_from(mdds, budget=BUDGET, cap=CAP):
    """6.2 and A4 from the drawdowns of s = 0.01 .. 1.00 in rising order: the last s before the first one above
    the budget (1.00 when none is); excluded when 0.01 is already above it; the crash cap after the search."""
    fitted = None
    for s, m in zip(STEPS, mdds):
        if m > budget:
            break
        fitted = s
    return {"fitted": fitted, "scale": None if fitted is None else min(fitted, cap), "excluded": fitted is None}


def fit_scale(rows, cash_r, changes, fit_start, fit_end, budget=BUDGET, cap=CAP):
    """6.2: the policy over the fitting period, a fresh $10,000 account at base costs, for every s -> the fitted
    scale, the scale after the cap, and the drawdowns (kept so that the information budgets of 8.3 reuse them)."""
    mdds = []
    for s in STEPS:
        res = run_window(rows, cash_r, changes, s, BASE, fit_start, fit_end)
        mdds.append(max_drawdown(res["days"], res["equity"])[0])
    return {**scale_from(mdds, budget, cap), "mdds": mdds}
