"""Pre-move breakout levels. Pure functions: no network, no orders, no broker.

A name is in play when yesterday's close is near the 20-day high and the range has contracted.
The trigger sits one cent above the higher of yesterday's high and the high seen today, so a ping
can only happen when a later price trades through a level that was still above the market.
Nothing in this module places or transmits an order.
"""
import datetime as dt

from bot import common, levels, ticket
from bot.common import code, price

PRICE_MIN, ADV_MIN, NEAR_HIGH = 5.0, 10_000_000.0, 0.03
GAP_MIN, GAP_MAX, GAP_SKIP, PRE_VOL_MIN = 0.02, 0.08, 0.10, 0.10
CONTRACTION, ATR_DAYS, NEAR_DAYS, NR_DAYS = 0.75, 14, 20, 7
TICK, SPLIT = 0.01, 2.0
# Distances from the trigger. The pre-market low cannot pull the stop inside half an ATR (a one-tick low).
R_ATR, R_CAP, R_FLOOR_ATR, TARGET_R, LIMIT_ATR, SPREAD_R = 2.0, 0.10, 0.5, 1.5, 0.25, 0.25
SUGGESTED = {"C": 0.0, "B": 0.005, "A": 0.01}  # fraction of equity. His 5% / 15% are not in here.
OPTION = {"B": 0.05, "A": 0.15}  # printed on the ping, never used to count shares
HARD_CAP, DAILY_STOP, OPEN_RISK = 0.02, 0.02, 0.03
TRACK_MAX, WATCH_MAX, FOUND_MAX = 30, 8, 200
NY = levels.alpaca.NY


def pct_text(p):
    """0.005 -> '0.5%', 0.15 -> '15%'."""
    return f"{p * 100:.1f}".rstrip("0").rstrip(".") + "%"


def context_row(bars, day):
    """Daily bars (date, o, h, l, c, v) before `day` -> a context dict, or None when the name fails the screen.

    Needs 20 sessions, a close of at least $5 within 3% of the 20-day high, 20-day dollar volume of at least
    $10M, and either a 10-day range of at most 0.75 x ATR14 or an NR7. A close that doubled inside the window
    is treated as a split and dropped."""
    prior = [b for b in bars if b[0] < day]
    if len(prior) < NEAR_DAYS:
        return None
    window = prior[-NEAR_DAYS:]
    if any(a[4] and not 1 / SPLIT <= b[4] / a[4] <= SPLIT for a, b in zip(window, window[1:])):
        return None
    last = window[-1]
    high20 = max(b[2] for b in window)
    close, high, low = last[4], last[2], last[3]
    if close < PRICE_MIN or high20 <= 0 or close < high20 * (1 - NEAR_HIGH):
        return None
    adv = sum(b[4] * b[5] for b in window) / NEAR_DAYS
    avg_vol = sum(b[5] for b in window) / NEAR_DAYS
    if adv < ADV_MIN or not avg_vol:
        return None
    atr = ticket.atr14(prior, day)
    if not atr or atr <= 0:
        return None
    last10 = window[-10:]
    range10 = max(b[2] for b in last10) - min(b[3] for b in last10)
    ranges = [b[2] - b[3] for b in window[-NR_DAYS:]]
    return {"prior_high": high, "prior_close": close, "prior_low": low, "high20": high20, "atr": atr,
            "avg_vol": avg_vol, "adv": adv, "dist": (high20 - close) / high20,
            "contraction": range10 <= CONTRACTION * atr, "nr7": ranges[-1] <= min(ranges) + 1e-9}


def passes(row):
    return bool(row) and (row["contraction"] or row["nr7"])


def track(row, symbol):
    """A context row plus the live fields the trigger loop updates."""
    return {**row, "t": symbol, "base": "C", "gap": None, "pre_vol": None, "seen_high": 0.0, "seen_low": None,
            "seen_below": False, "pinged": False, "last": None}


def level_of(name):
    """The buy-stop, one cent above the higher of yesterday's high and the high seen so far today."""
    return max(name["prior_high"], name.get("seen_high") or 0.0) + TICK


def observe(name, px, fire=True):
    """Fold one price into `name`. Returns the level just traded through, or None.

    The first print above the level does not fire: the cross has to start from a price that was still under it.
    `fire=False` only arms that memory (the first pre-market quote of the day)."""
    if px is None or px <= 0 or name.get("pinged"):
        return None
    level = level_of(name)
    crossed = None
    if fire and name.get("seen_below") and px >= level - 1e-9:
        name["pinged"] = True
        crossed = level
    elif px < level - 1e-9:
        name["seen_below"] = True
    name["seen_high"] = max(name.get("seen_high") or 0.0, px)
    low = name.get("seen_low")
    name["seen_low"] = px if low is None else min(low, px)
    name["last"] = px
    return crossed


def apply_quote(name, quote, today):
    """A pre-market quote -> 'keep', 'drop' (gap already over 10%, or the level was already through), or 'skip'
    (no usable quote: the name stays, still grade C). A kept name is graded B or C and armed."""
    if name.get("pinged"):
        return "keep"
    if not quote or quote.get("date") != today or not quote.get("price"):
        return "skip"
    gap = quote["price"] / name["prior_close"] - 1
    vol = quote.get("volume")
    pre_vol = vol / name["avg_vol"] if vol is not None and name["avg_vol"] else None
    if gap > GAP_SKIP:
        return "drop"
    if quote["price"] >= name["prior_high"] + TICK - 1e-9 and not name.get("seen_below"):
        return "drop"  # the move was already through before we were underneath it
    name["gap"] = gap
    name["pre_vol"] = pre_vol
    name["base"] = "B" if GAP_MIN <= gap <= GAP_MAX and pre_vol is not None and pre_vol >= PRE_VOL_MIN else "C"
    return "keep"


def bracket(name, trigger):
    """Stop, limit and target off `trigger`. R is the tightest of 2 x ATR, 10% of the trigger, and the distance
    to just under the low seen today, and that last one cannot come inside half an ATR."""
    atr = name["atr"]
    distances = [R_ATR * atr, R_CAP * trigger]
    low = name.get("seen_low")
    if low is not None and low < trigger:
        structural = trigger - (low - TICK)
        if structural > 0:
            distances.append(max(structural, R_FLOOR_ATR * atr))
    R = max(min(distances), TICK)
    stop = levels.px(trigger - R)
    return {"trigger": levels.px(trigger), "stop": stop, "limit": levels.px(trigger + LIMIT_ATR * atr),
            "target": levels.px(trigger + TARGET_R * R), "R": R,
            "stop_pct": (trigger - stop) / trigger if trigger else 0.0}


def grade_for_cross(name, spread, R):
    """C stays C. B becomes A when the live spread is at most a quarter of R. The spread is IEX, not the national quote."""
    if name.get("base") != "B":
        return "C"
    if spread is not None and R > 0 and spread <= SPREAD_R * R + 1e-9:
        return "A"
    return "B"


def risk_pct(grade):
    """The percent that sizes the order: the suggestion, and never above the 2% hard cap."""
    return min(SUGGESTED.get(grade, 0.0), HARD_CAP)


def allocation(grade, E, R, allocated, open_risk):
    """Shares from the suggested percent of E. His 5% / 15% ride along as `option` and never enter the count.

    `block` is why there is no share count: grade C, no pinned equity, the 2% daily budget, the 3% open-risk
    budget, or a risk too small for one share. A block still gets a ping; it just has no size."""
    opt = OPTION.get(grade)
    pct = risk_pct(grade)
    out = {"grade": grade, "pct": pct, "option": opt, "shares": None, "dollars": None, "block": None}
    if pct <= 0:
        out["block"] = "grade"
        return out
    if not E:
        out["block"] = "equity"
        return out
    if allocated + pct > DAILY_STOP + 1e-9:
        out["block"] = "daily"
        return out
    if open_risk + pct > OPEN_RISK + 1e-9:
        out["block"] = "open"
        return out
    dollars = pct * E
    shares = int(dollars / R + 1e-9) if R and R > 0 else 0
    if shares < 1:
        out["block"] = "dust"
        return out
    out.update(shares=shares, dollars=dollars)
    return out


def flat_clock(day):
    """15:55 New York as an Israel clock time ('22:55'), the year-round 7-hour gap."""
    t = dt.datetime.fromisoformat(f"{day}T15:55").replace(tzinfo=NY)
    return t.astimezone(common.IL).strftime("%H:%M")


def _size_line(sizing):
    opt = f" אפשרות {code(pct_text(sizing['option']))} אינה הפקודה" if sizing.get("option") else ""
    block = sizing.get("block")
    if block == "grade":
        return f"בלי כמות: ציון {code(sizing['grade'])} בלי גודל"
    if block == "daily":
        return f"בלי כמות: סיכון היום הגיע לתקרה של {code(pct_text(DAILY_STOP))}." + opt
    if block == "open":
        return f"בלי כמות: הסיכון הפתוח הגיע לתקרה של {code(pct_text(OPEN_RISK))}." + opt
    if block == "equity":
        return (f"בלי כמות: לא נקבע הון. סיכון {code(pct_text(sizing['pct']))} תקרה {code(pct_text(HARD_CAP))}."
                + opt)
    if block == "dust":
        return "בלי כמות: הסיכון יוצא פחות ממניה אחת." + opt
    half = f" ({code(sizing['shares'] // 2)} ביעד)" if sizing["shares"] >= 2 else ""
    return (f"מניות {code(sizing['shares'])}{half} סיכון {code(pct_text(sizing['pct']))} "
            f"({common.amount(sizing['dollars'])}) תקרה {code(pct_text(HARD_CAP))}." + opt)


def _reason_line(name, tape):
    bits = []
    if name.get("nr7"):
        bits.append("הנר הצר בשבעה ימים")
    if name.get("contraction"):
        bits.append("טווח עשרה ימים צר")
    bits.append(f"{code(pct_text(name['dist']))} מתחת לשיא עשרים יום")
    if name.get("gap") is not None:
        bits.append(common.change(name["gap"]))
    if name.get("pre_vol") is not None:
        bits.append(f"מחזור לפני הפתיחה {code(pct_text(name['pre_vol']))} מיום")
    bits.append(f"המחיר מ־{code(tape)}, לא כל הבורסות")
    return "רקע: " + ", ".join(bits)


def ping_text(name, order, sizing, day, tape):
    """Five Hebrew lines. No disclaimer and no question: the caller sends it with footer=False."""
    lines = [
        f"פינג {code(sizing['grade'])} לונג: {code(name['t'])}",
        f"טריגר {code(price(order['trigger']))} לימיט {code(price(order['limit']))} בתוקף היום",
        (f"סטופ {code(price(order['stop']))} ({code(pct_text(order['stop_pct']))} מתחת) "
         f"יעד {code(price(order['target']))} סגירה {code(flat_clock(day))}"),
        _size_line(sizing),
        _reason_line(name, tape),
    ]
    return "\n".join(lines)


def watch_text(names, now):
    """The 08:45 New York list: at most 8 names still under the trigger, grade B before C. No order."""
    il = now.astimezone(common.IL)
    waiting = [n for n in names if not n.get("pinged")]
    waiting.sort(key=lambda n: (0 if n.get("base") == "B" else 1, n["dist"], n["t"]))
    show = waiting[:WATCH_MAX]
    lines = [f"מעקב לפני הפתיחה {code(f'{il:%H:%M}')}"]
    if not show:
        lines.append("אין מניה שעומדת בתנאים.")
    for n in show:
        gap = common.change(n["gap"]) if n.get("gap") is not None else "בלי גאפ"
        vol = (f" מחזור {code(pct_text(n['pre_vol']))} מיום" if n.get("pre_vol") is not None else "")
        lines.append(f"מניה {code(n['t'])} ציון {code(n['base'])} טריגר {code(price(level_of(n)))} {gap}{vol}")
    lines.append("מחיר לפני הפתיחה מ־Nasdaq, לא כל הבורסות. בלי פקודה.")
    return "\n".join(lines)
