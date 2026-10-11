"""The ensemble's backtest engine (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md sections 3, 6.2
and 4.2): issuing tickets from the firings (section 3's refusals at $100, one open or pending ticket per symbol and
signal), the daily-bar simulation through `levels`, the pools of the random comparison, m_i and the draws.

BLIND DISCIPLINE: until the blind run this module runs on synthetic bars only (its tests). Every result it computes
lives in memory: nothing here prints, logs, caches or writes a result."""
import array
import bisect
import random

from bot import ensemble_counts as ec, levels, probe_ensemble as pe, signals

RISK = 100.0
POOL_MIN = 20
QUOTE_SHARE_MAX, SMALL_POOL_SHARE_MAX = 0.05, 0.02
DRAWS, DRAW_SEED = 1000, 20261010
QUOTE_REASONS = ("no usable quote", "cost above 10% of risk")

plan_rows = ec.plan_rows


def _valid(mode):
    return 1 if mode == "open" else levels.BREAKOUT_DAYS


def rows_after(s, day, limit=None):
    """The bars after D in D's segment, in D's raw money (section 2: the split-adjusted series rescaled by
    raw close(D) / split-adjusted close(D)) -> [(date, o, h, l, c, 0)]. A bad-price break ends them (C15). `limit`
    caps the rows (a ticket never needs more than its validity + H + 1)."""
    i = pe._i(s, day)
    scale, seg, out = s["raw_close"][i] / s["close"][i], s["segment"][i], []
    for j in range(i + 1, len(s["dates"]) if limit is None else min(len(s["dates"]), i + 1 + limit)):
        if s["segment"][j] != seg:
            break
        f = s["close"][j] / s["raw_close"][j] * scale
        out.append((s["dates"][j], s["raw_open"][j] * f, s["raw_high"][j] * f, s["raw_low"][j] * f,
                    s["raw_close"][j] * f, 0))
    return out


def beyond_data(day, mode, horizon, sessions):
    """C17: the latest possible time-exit session (the last session the order is valid, plus H) is after the last
    loaded session -- a rule on dates, the same whatever happens to the ticket."""
    k = bisect.bisect_left(sessions, day)
    return k + _valid(mode) + horizon > len(sessions) - 1


def run_ticket(t, rows, guard_spread, entry_spread=None):
    """One ticket on the bars after D -> {"filled", "gross_r", "cost_r", "cost_r_stress", "end"}. Unfilled: 0 and 0,
    open or pending through its last valid session (D itself when no session of its segment follows: C15). Filled:
    section 3's exits (what is open when the bars end exits at the last close: section 2); the spread is the quote 5
    minutes after the entry session's open, else the guard spread (section 5)."""
    f = levels.fill(t, rows)
    if f is None:
        end = rows[min(_valid(t["mode"]), len(rows)) - 1][0] if rows else t["day"]
        return {"filled": False, "gross_r": 0.0, "cost_r": 0.0, "cost_r_stress": 0.0, "end": end, "exits": None}
    k, F = f
    ex = levels.exits(t, F, k, rows, close_at_end=True)
    spread = entry_spread(rows[k][0]) if entry_spread else None
    spread = guard_spread if spread is None else spread
    return {"filled": True, "gross_r": levels.gross_r(t, F, ex), "cost_r": levels.cost_r(t, spread),
            "cost_r_stress": levels.cost_r(t, spread, stress=True), "end": rows[max(x[2] for x in ex)][0],
            "exits": [x[0] for x in ex]}  # exit types, in memory for the verdict run only


def issue(fired, inp, renamed, quote):
    """Section 3's tickets from the firings in (day, signal, symbol) order -> (tickets, refusals {(signal, reason):
    n}). Order of the checks: an open or pending ticket of the signal on the symbol (C14); the quote-free refusals;
    beyond the data (C17); the quote-based refusals."""
    tickets, refusals, open_until = [], {}, {}

    def refuse(sig, reason):
        refusals[(sig, reason)] = refusals.get((sig, reason), 0) + 1
    for d, sig, sym in sorted(fired):
        if open_until.get((sig, sym), "") >= d:
            refuse(sig, "already open")
            continue
        bars_of = renamed.get((d, sym), sym)
        s = inp.series[bars_of]
        mode, horizon = signals.MODES[sig]
        t = levels.plan(sym, plan_rows(s, d), d, mode=mode, horizon=horizon, risk=RISK)
        if not t["ok"]:
            refuse(sig, t["reason"])
            continue
        if beyond_data(d, mode, horizon, inp.sessions):
            refuse(sig, "beyond the data")
            continue
        g = levels.guard(t, quote(bars_of, levels.guard_time(d, inp.cal)))
        if not g["ok"]:
            refuse(sig, g["reason"])
            continue
        res = run_ticket(g, rows_after(s, d, _valid(mode) + horizon + 1), g["guard_spread"],
                         lambda day, b=bars_of: levels.spread(quote(b, levels.entry_time(day, inp.cal))))
        tickets.append({"day": d, "signal": sig, "symbol": sym, "bars_of": bars_of, "mode": mode, "horizon": horizon,
                        **res})
        open_until[(sig, sym)] = res["end"]
    return tickets, refusals


def exclusions(day, fired_by_day, tickets):
    """Symbols with a ticket of any signal firing or open on D (6.2): that day's firings and the issued tickets
    whose window (signal day through end) covers D."""
    return set(fired_by_day.get(day, ())) | {t["symbol"] for t in tickets if t["day"] <= day <= t["end"]}


def open_by_day(tickets, sessions):
    """{day: symbols with an issued ticket open or pending that day} -- exclusions() for every day at once (the
    blind run's scale: built once, not scanned per pool)."""
    out = {}
    for t in tickets:
        k = bisect.bisect_left(sessions, t["day"])
        while k < len(sessions) and sessions[k] <= t["end"]:
            out.setdefault(sessions[k], set()).add(t["symbol"])
            k += 1
    return out


def pool(day, decile, mode, horizon, inp, day_members, renamed, excl):
    """6.2's pool: D's universe symbols of the decile, not excluded, passing the quote-free refusals at $100 with
    the ticket's mode and H -> sorted symbols."""
    syms, decs = day_members[day]
    out = []
    for sym, dec in zip(syms, decs):
        if dec != decile or sym in excl:
            continue
        s = inp.series[renamed.get((day, sym), sym)]
        if levels.plan(sym, plan_rows(s, day), day, mode=mode, horizon=horizon, risk=RISK)["ok"]:
            out.append(sym)
    return sorted(out)


def pool_ok(members):
    return len(members) >= POOL_MIN


def pool_results(day, decile, mode, horizon, members, inp, renamed):
    """Each member's gross result in its own R, with the ticket's mode and H on the same day (no costs: 6.2) ->
    array('d'), held in memory only (C23)."""
    out = array.array("d")
    for sym in members:
        s = inp.series[renamed.get((day, sym), sym)]
        t = levels.plan(sym, plan_rows(s, day), day, mode=mode, horizon=horizon, risk=RISK)
        out.append(run_ticket(t, rows_after(s, day, _valid(mode) + horizon + 1), 0.0)["gross_r"])
    return out


def m(results):
    return sum(results) / len(results)


def excess_mean(values, ms):
    """The mean over tickets of (value - m_i): A for the tickets' own results, A_k for a draw (one function)."""
    return sum(v - mi for v, mi in zip(values, ms)) / len(values)


def A(kept):
    return excess_mean([t["g"] for t in kept], [t["m"] for t in kept])


def draws(kept, pools, n=DRAWS, seed=DRAW_SEED):
    """6.2's draws: draw k uses random.Random(seed + k) and replaces every ticket, in order, by one uniformly chosen
    member of its pool (sorted by symbol) -> [A_k]."""
    ms = [t["m"] for t in kept]
    out = []
    for k in range(n):
        rng = random.Random(seed + k)
        out.append(excess_mean([pools[t["pool"]][rng.randrange(len(pools[t["pool"]]))] for t in kept], ms))
    return out


def comparability(quote_refused, passing, small_pool, issued):
    """6.2's two shares against 5% and 2% (unrounded)."""
    q = quote_refused / passing if passing else 0.0
    p = small_pool / issued if issued else 0.0
    return {"quote_share": q, "small_pool_share": p,
            "comparable": q <= QUOTE_SHARE_MAX and p <= SMALL_POOL_SHARE_MAX}


def compare(tickets, inp, renamed, day_members, fired_by_day):
    """The pools of every issued ticket (shared per key (D, decile, mode, H)) -> (kept tickets with "pool" and "m",
    pools {key: results}, the number dropped for a pool under 20)."""
    pools, kept, small = {}, [], 0
    dec_of, open_map = {}, open_by_day(tickets, inp.sessions)
    for t in tickets:
        syms, decs = day_members[t["day"]]
        if t["day"] not in dec_of:
            dec_of[t["day"]] = dict(zip(syms, decs))
        key = (t["day"], dec_of[t["day"]].get(t["symbol"]), t["mode"], t["horizon"])
        if key not in pools:
            excl = set(fired_by_day.get(t["day"], ())) | open_map.get(t["day"], set())
            members = pool(*key, inp, day_members, renamed, excl)
            pools[key] = pool_results(*key, members, inp, renamed) if pool_ok(members) else None
        if pools[key] is None:
            small += 1
            continue
        kept.append({**t, "pool": key, "m": m(pools[key]), "g": t["gross_r"], "c": t["cost_r"]})
    kept.sort(key=lambda t: (t["day"], t["symbol"], t["signal"]))  # C25: the draws' order
    return kept, {k: v for k, v in pools.items() if v is not None}, small
