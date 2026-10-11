"""The levels engine of the recommendation algorithm (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md
revision 7, sections 3 and 5): pure functions, no I/O. One code path for the backtest, the shadow journal and the live
`/ticket`; in phase B only `/ticket` uses it, without signals (`breakout`, H = 21).

Bars are `alpaca.daily` rows (date, o, h, l, c, v), ascending; rows after the signal day D are never read."""
import datetime as dt

from bot import alpaca, ticket

MODES = ("open", "breakout")
DEFAULT_MODE, DEFAULT_H = "breakout", 21
R_ATR, OPEN_LIMIT_ATR, BREAKOUT_LIMIT_ATR, TARGET_R = 2.0, 0.5, 0.25, 1.5
BREAKOUT_TICK, BREAKOUT_DAYS = 0.01, 3
MIN_BARS, MIN_SHARES, MAX_ADV_SHARE, ADV_SESSIONS = 15, 2, 0.01, 20
UNIVERSE_PRICE, UNIVERSE_ADV, UNIVERSE_HISTORY = 5.0, 10_000_000.0, 252  # spec section 2 (owner 2026-10-11: partial)
EPS = 1e-9


def px(x, ref=None):
    """An order price or amount: a cent for a stock from $1 up, $0.0001 below (plan decision 3). `ref` is the stock's
    price when x is an amount (a trail), so the amount follows the stock's scale (review m1)."""
    scale = x if ref is None else ref
    return round(x + (EPS if x >= 0 else -EPS), 2 if abs(scale) >= 1 else 4)


def _through(bars, day):
    return [b for b in bars if b[0] <= day]


def atr_through(bars, day):
    """`ticket.atr14` over the bars through `day` (it reads the bars strictly before the day it is given)."""
    nxt = (dt.date.fromisoformat(day) + dt.timedelta(1)).isoformat()
    return ticket.atr14(_through(bars, day), nxt)


def adv(bars):
    """The mean raw close x volume of the last 20 bars; None with fewer."""
    if len(bars) < ADV_SESSIONS:
        return None
    return sum(b[4] * b[5] for b in bars[-ADV_SESSIONS:]) / ADV_SESSIONS


def plan(symbol, bars, day, mode=DEFAULT_MODE, horizon=DEFAULT_H, risk=100.0):
    """Section 3's ticket for `symbol` on signal day `day` -> {"ok": True, ...levels, size} or {"ok": False,
    "reason"}. Stop loss and target are from the planned entry P; the orders move them with the fill F."""
    upto = _through(bars, day)
    atr = atr_through(bars, day)
    if len(upto) < MIN_BARS or atr is None:
        return {"ok": False, "reason": "fewer than 15 bars"}
    if upto[-1][0] != day:  # halted, delisted or not yet published: never levels from an older session (review M1)
        return {"ok": False, "reason": "no bar on the signal day"}
    R = R_ATR * atr
    shares = int(risk / R + EPS) if R > 0 else 0
    if shares < MIN_SHARES:
        return {"ok": False, "reason": "shares below 2"}
    _, o, h, l, c, v = upto[-1]
    t = {"ok": True, "symbol": symbol, "day": day, "mode": mode, "horizon": horizon, "atr": atr, "R": R}
    if mode == "breakout":
        t["entry_stop"] = px(h + BREAKOUT_TICK)
        t["limit"] = px(t["entry_stop"] + BREAKOUT_LIMIT_ATR * atr)
        t["planned_entry"] = t["entry_stop"]
    elif mode == "open":
        t["limit"] = px(c + OPEN_LIMIT_ATR * atr)
        t["planned_entry"] = c
    else:
        raise ValueError(mode)
    recent = upto[-ADV_SESSIONS:]  # with 15-19 bars: the mean over the bars there are
    dollar = sum(b[4] * b[5] for b in recent) / len(recent)
    if shares * t["planned_entry"] > MAX_ADV_SHARE * dollar * (1 + EPS):
        return {"ok": False, "reason": "size above 1% of dollar volume"}
    P = t["planned_entry"]
    t.update(shares=shares, legs=(shares // 2, shares - shares // 2), stop_loss=px(P - R),
             target=px(P + TARGET_R * R), trail=px(R, ref=P))
    return t


def universe_mark(bars, day):
    """Phase B's partial universe test (owner 2026-10-11) -> the failed conditions ([] inside): raw close of D >= $5,
    20-session average dollar volume through D >= $10M, >= 252 sessions of bars before D. The insider-filing condition
    comes in phase C. The mark never refuses a ticket."""
    upto = _through(bars, day)
    out = []
    if not upto or upto[-1][0] != day or upto[-1][4] < UNIVERSE_PRICE - EPS:
        out.append("price below $5")
    dollar = adv(upto)
    if dollar is None or dollar < UNIVERSE_ADV * (1 - EPS):
        out.append("dollar volume below $10M")
    if len(upto) - 1 < UNIVERSE_HISTORY:
        out.append("fewer than 252 sessions")
    return out


# ---------- costs (spec section 5) ----------
COMMISSION, MIN_ORDER, TICK, QUOTE_AGE, COST_GUARD, STRESS = 0.0035, 0.35, 0.01, 60, 0.10, 1.5
QUOTE_OFFSET_MIN = 5  # owner 2026-10-11: 5 minutes before D's close / after the entry session's open


def _shift(hhmm, minutes):
    h, m = map(int, hhmm.split(":")[:2])
    t = h * 60 + m + minutes
    return f"{t // 60:02d}:{t % 60:02d}"


def guard_time(day, cal):
    """The guard quote's time: 5 minutes before `day`'s close in the exchange calendar ({date: (open, close)}),
    as UTC ISO (15:55 ET on a full session, 12:55 on a 13:00 close)."""
    return alpaca.utc(day, _shift(cal[day][1], -QUOTE_OFFSET_MIN))


def entry_time(day, cal):
    """The entry-session quote's time: 5 minutes after `day`'s open (09:35 ET on a full session), as UTC ISO."""
    return alpaca.utc(day, _shift(cal[day][0], QUOTE_OFFSET_MIN))


def spread(quote):
    """`alpaca.quote_full`'s (bid, ask, bid size, ask size, age s) -> the spread in dollars, or None when unusable
    (missing, a side <= 0, older than 60 s); a locked or crossed quote counts $0.01."""
    if quote is None:
        return None
    bid, ask, _, _, age = quote
    if bid is None or ask is None or bid <= 0 or ask <= 0 or (age is not None and age > QUOTE_AGE):
        return None
    return max(ask - bid, TICK)


def commission(legs):
    """4 orders (two entries, two exits) at $0.0035 a share, minimum $0.35 an order."""
    a, b = legs
    return sum(max(MIN_ORDER, q * COMMISSION) for q in (a, b, a, b))


def cost(t, spr, stress=False):
    """Section 5's dollars for a filled ticket: commissions + the full spread on every share."""
    return commission(t["legs"]) + spr * (STRESS if stress else 1.0) * t["shares"]


def cost_r(t, spr, stress=False):
    return cost(t, spr, stress) / (t["shares"] * t["R"])


def guard(t, quote):
    """The quote-based refusals of section 3 -> the ticket with "guard_spread" and "cost_r", or a refusal."""
    if not t.get("ok"):
        return t
    spr = spread(quote)
    if spr is None:
        return {"ok": False, "reason": "no usable quote"}
    c = cost_r(t, spr)
    if c > COST_GUARD + EPS:
        return {"ok": False, "reason": "cost above 10% of risk"}
    return {**t, "guard_spread": spr, "cost_r": c}


# ---------- the daily-bar fill model (spec section 3; synthetic tests only in phase B) ----------
def fill(t, after):
    """The entry: (index of the entry session in `after`, the sessions after D, fill price F), or None.
    `open`: the first session's open when it is at or below the limit. `breakout`: within 3 sessions, a high at or
    above the stop fills at max(stop, open) when that is within the limit; an open above the limit leaves the order
    resting at the limit, filled at the limit by that or a later valid session's low (plan decision 6)."""
    if not after:
        return None
    if t["mode"] == "open":
        o = after[0][1]
        return (0, o) if o <= t["limit"] + EPS else None
    stop, limit, resting = t["entry_stop"], t["limit"], False
    for k, (_, o, h, l, c, v) in enumerate(after[:BREAKOUT_DAYS]):
        if not resting and h >= stop - EPS:
            x = max(stop, o)
            if x <= limit + EPS:
                return k, x
            resting = True
        if resting and l <= limit + EPS:
            return k, limit
    return None


def exits(t, F, k, after):
    """Both legs' exits after a fill at F on session k -> [(type, price, session index)] for leg A then leg B, or
    None when the bars end with a leg still open. Rules 1-5 of section 3: a stop fills at the stop or at a lower open
    (never at an open before the fill: the entry session uses the stop); stop before target in one bar; the trail
    raises its running maximum by the session's high, then checks the low; a target fills at its price or a higher
    open; the time exit is the close of the H-th session after the entry session."""
    R = t["R"]
    S, T = px(F - R), px(F + TARGET_R * R)
    last = k + t["horizon"]
    out = [None, None]
    run_max, prev_level = F, None
    # rule 6: a resting order filled on a session that opened above the limit -- that session's high may have come
    # before the fill, so on it there is no target and the running maximum stays at F (the stop is still checked)
    rested = t["mode"] == "breakout" and k < len(after) and after[k][1] > t["limit"] + EPS
    for j in range(k, min(last, len(after) - 1) + 1):
        _, o, h, l, c, v = after[j]
        entry = j == k
        pre_fill_high = entry and rested
        if out[0] is None:
            if l <= S + EPS:
                out[0] = ("stop", S if entry or o >= S else o, j)
            elif h >= T - EPS and not pre_fill_high:
                out[0] = ("target", T if entry or o <= T else o, j)
        if out[1] is None:
            if not pre_fill_high:
                run_max = max(run_max, h)
            level = max(run_max - R, S)
            if l <= level + EPS:
                gap = not entry and prev_level is not None and o < prev_level
                out[1] = ("stop" if level <= S + EPS else "trail", px(o if gap else level), j)
            prev_level = level
        if j == last:
            out = [x or ("time", c, j) for x in out]
    return None if None in out else out


def gross_r(t, F, legs_exits):
    """Σ over legs of leg shares x (exit - F) / (shares x R)."""
    return sum(q * (x[1] - F) for q, x in zip(t["legs"], legs_exits)) / (t["shares"] * t["R"])


def simulate(t, after, spr):
    """A ticket's gross result and cost in R on the sessions after D; an unfilled ticket is 0 and 0; None while a
    leg is still open at the end of the bars. Never called on market data in phase B."""
    f = fill(t, after)
    if f is None:
        return {"filled": False, "gross_r": 0.0, "cost_r": 0.0}
    k, F = f
    ex = exits(t, F, k, after)
    if ex is None:
        return None
    return {"filled": True, "gross_r": gross_r(t, F, ex), "cost_r": cost_r(t, spr)}
