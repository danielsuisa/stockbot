"""The levels engine of the recommendation algorithm (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md
revision 7, sections 3 and 5): pure functions, no I/O. One code path for the backtest, the shadow journal and the live
`/ticket`; in phase B only `/ticket` uses it, without signals (`breakout`, H = 21).

Bars are `alpaca.daily` rows (date, o, h, l, c, v), ascending; rows after the signal day D are never read."""
import datetime as dt

from bot import ticket

MODES = ("open", "breakout")
DEFAULT_MODE, DEFAULT_H = "breakout", 21
R_ATR, OPEN_LIMIT_ATR, BREAKOUT_LIMIT_ATR, TARGET_R = 2.0, 0.5, 0.25, 1.5
BREAKOUT_TICK, BREAKOUT_DAYS = 0.01, 3
MIN_BARS, MIN_SHARES, MAX_ADV_SHARE, ADV_SESSIONS = 15, 2, 0.01, 20
UNIVERSE_PRICE, UNIVERSE_ADV, UNIVERSE_HISTORY = 5.0, 10_000_000.0, 252  # spec section 2 (owner 2026-10-11: partial)
EPS = 1e-9


def px(x):
    """An order price: a cent from $1 up, $0.0001 below (plan decision 3)."""
    return round(x + (EPS if x >= 0 else -EPS), 2 if abs(x) >= 1 else 4)


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
             target=px(P + TARGET_R * R), trail=px(R))
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
