"""Index program: the overlay test and the decisions (docs/superpowers/specs/2026-10-12-index-program-design.md 7.1,
7.2 and 7.3 with C2 and E3). Reused from bot.ensemble_stats, where the formula is the same: `se` (7.1.3), `block`
(7.1b's floor(10 x i / n)) and `gate3` (7.1b: positive in at least 7 of 10 blocks and in each half). Pure
functions; nothing printed."""
from bot import ensemble_stats as st

HOLM_FIRST, HOLM_SECOND = 1.9945, 1.6670  # 7.1a: one-sided 5% for the pair, 70 degrees of freedom
SIZE_MIN = 0.010  # 7.1c: 252 x x-bar at least 1.0% a year
MDD_MAX, GROWTH_EDGE, TACT_EDGE = 0.20, 0.010, 0.005  # 7.2 a, 7.2 b, 7.3 (C2)
SLOT = {"open": 0, "close": 1}


def quarter_blocks(days):
    """7.1.3: each day's calendar quarter, counted from the first day's quarter -> (blocks, G)."""
    q = lambda d: int(d[:4]) * 4 + (int(d[5:7]) - 1) // 3  # noqa: E731
    first = q(days[0])
    blocks = [q(d) - first for d in days]
    return blocks, q(days[-1]) - first + 1


def block_se(x, days):
    """7.1.3: SE with the calendar quarters as blocks, by bot.ensemble_stats.se."""
    blocks, g = quarter_blocks(days)
    return st.se(x, blocks, g)


def overlay_x(r, spy, cash, held, days):
    """7.1.2: pi = the share of the days on which the rule holds SPY after the close; b(t) = pi x SPY's
    close-to-close return + (1 - pi) x the cash leg's return; x(t) = r(t) - b(t) -> (pi, [x])."""
    pi = sum(1 for d in days if d in held) / len(days)
    return pi, [ri - (pi * si + (1 - pi) * ci) for ri, si, ci in zip(r, spy, cash)]


def holm(t):
    """7.1a for the pair {rule: t}: the larger t passes at 1.9945 or more; the smaller only if the larger passed and
    it is at least 1.6670. Unrounded."""
    (big, tb), (small, ts) = sorted(t.items(), key=lambda kv: kv[1], reverse=True)
    first = tb >= HOLM_FIRST
    return {big: first, small: first and ts >= HOLM_SECOND}


def size_gate(x_bar):
    return 252 * x_bar >= SIZE_MIN


def overlay_test(x, days, x_stress):
    """7.1.3 and gates b, c and d (gate a is `holm`, over the pair)."""
    n = len(x)
    x_bar = st.mean(x)
    se = block_se(x, days)
    cons = st.gate3(x, [st.block(i, n) for i in range(n)])
    return {"days": n, "x_bar": x_bar, "x_bar_per_year": 252 * x_bar, "se": se, "t": x_bar / se if se else 0.0,
            "consistency": cons, "block_means": cons["block_means"], "size": size_gate(x_bar),
            "stress": st.mean(x_stress) > 0}


def passes(gates, holm_pass):
    """7.1.4: an overlay passes only with all four gates."""
    return bool(holm_pass and gates["consistency"]["pass"] and gates["size"] and gates["stress"])


def round_trips(trades):
    """8.3: each round trip's result, the account's equity just after the exit over its equity just before the
    entry (costs included), from run_window's trades; a trip still open at the window's end is not counted."""
    out, entry = [], None
    for _d, _slot, kind, before, after in trades:
        if kind == "in":
            entry = before
        elif kind == "out" and entry is not None:
            out.append(after / entry - 1)
            entry = None
    return out


def round_trip_stats(trips):
    """8.3: round trips, the share of winning ones, mean win, mean loss, worst one, longest run of losses."""
    wins, losses = [v for v in trips if v > 0], [v for v in trips if v <= 0]
    run = best = 0
    for v in trips:
        run = run + 1 if v <= 0 else 0
        best = max(best, run)
    return {"round_trips": len(trips), "winning_share": len(wins) / len(trips) if trips else None,
            "mean_win": st.mean(wins) if wins else None, "mean_loss": st.mean(losses) if losses else None,
            "worst": min(trips) if trips else None, "longest_losing_run": best}


def eligible(pol, static):
    """7.2 with C2, each policy at its own scale over P: a, maximum drawdown at most 20%; b, growth above STATIC's
    by at least 1.0 point; d, growth above STATIC's with the stress costs. (c is removed.)"""
    out = {"a": pol["mdd_p"] <= MDD_MAX, "b": pol["growth_p"] - static["growth_p"] >= GROWTH_EDGE,
           "d": pol["growth_p_stress"] > static["growth_p_stress"]}
    out["eligible"] = all(out.values())
    return out


def outcome(elig, growth):
    """7.3 with C2 -> ("STATIC", None) or ("TIMING", the selected policy). With both eligible, TACT only if its growth
    over P is above TREND's by at least 0.5 point."""
    ok = [p for p in ("TREND", "TACT") if elig.get(p, {}).get("eligible")]
    if not ok:
        return "STATIC", None
    if len(ok) == 1:
        return "TIMING", ok[0]
    return "TIMING", "TACT" if growth["TACT"] - growth["TREND"] >= TACT_EDGE else "TREND"


def union_changes(rule_changes):
    """E3: TACT's change points, in SPY when any of the passing overlays holds a position; a change point only
    when the combined state changes."""
    events = sorted(((d, SLOT[s], k, v) for k, ch in enumerate(rule_changes) for d, s, v in ch))
    held, state, out = [False] * len(rule_changes), False, []
    k = 0
    while k < len(events):
        moment = events[k][:2]
        while k < len(events) and events[k][:2] == moment:
            held[events[k][2]] = events[k][3]
            k += 1
        if any(held) != state:
            state = any(held)
            out.append((moment[0], "open" if moment[1] == 0 else "close", state))
    return out
