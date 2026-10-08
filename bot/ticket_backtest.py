"""The order-ticket backtest (spec docs/superpowers/specs/2026-10-08-ticket-backtest-design.md): fills and exits on
1-minute bars with costs, the random-direction mirror, the candidate universes, statistics and the verdict.

Fills are conservative (spec section 6): a buy limit fills only when a minute's low reaches it, at the limit; a stop
fills at the stop, or at the minute's open when it opened beyond it; a minute that touches both stop and target counts
as the stop; on the fill minute only the stop is checked. Results are in R: P&L / (R x shares)."""
from bot import ticket
from bot.ticket import hhmm, mins

COMMISSION, MIN_ORDER = 0.0035, 0.35  # dollars per share, minimum per order (each side)
TIME_STOP, TRAIL_PAD = 60, 0.10  # ORIGINAL: minutes to target 1 after the fill; trail below the last higher low


def _fill(t, bars):
    """-> (index into bars, price) of the entry fill, or None."""
    at, until, limit = t["at"], t["valid_until"], t["limit"]
    triggered = t["entry_type"] == "LMT"
    for i, (m, o, h, l, c, v, vw) in enumerate(bars):
        if m < at:
            continue
        if m >= until:
            return None
        if not triggered:  # a stop-limit: the first minute whose high is above the level triggers it
            if not h > t["level"]:
                continue
            triggered = True
            px = max(o, t["level"])
            if px <= limit:
                return i, px
        if l <= limit:
            return i, limit
    return None


def _mirror(bars):
    return [(m, -o, -l, -h, -c, v, None if vw is None else -vw) for m, o, h, l, c, v, vw in bars]


def simulate(t, bars1, ctx, direction=1):
    """A ticket (ticket.signal) played on the day's 1-minute bars -> {"filled", "fill_at", "fill", "exits": [{"qty",
    "price", "at", "kind"}], "gross_r", "cost_r", "net_r"}. direction -1: the same entry moment and price as a short
    (stops and targets mirrored around the fill, same costs) - the random-direction benchmark."""
    t0 = ctx["open"] if t["session"] == "REGULAR" else ticket.PRE_OPEN
    bars = [b for b in bars1 if t0 <= b[0] < ctx["close"]]
    got = _fill(t, bars)
    if got is None:
        return {"filled": False, "fill_at": None, "fill": None, "exits": [], "gross_r": 0, "cost_r": 0, "net_r": 0}
    i, fill = got
    play = bars if direction == 1 else _mirror(bars)
    entry = fill * direction
    exits = (_original if t["rules"] == "ORIGINAL" else _improved)(t, play, i, entry, t0)
    risk = t["R"] * t["shares"]
    gross = sum((e["price"] - entry) * e["qty"] for e in exits) / risk
    orders = [t["shares"]] if t["rules"] == "ORIGINAL" else [g["qty"] for g in t["legs"]]  # IMPROVED: 2 brackets
    commission = sum(max(MIN_ORDER, q * COMMISSION) for q in orders + [e["qty"] for e in exits])
    cost = (commission + t["shares"] * t["spread"]) / risk  # half the spread in, half out
    for e in exits:
        e["price"] = round(e["price"] * direction, 4)
    return {"filled": True, "fill_at": bars[i][0], "fill": fill, "exits": exits, "gross_r": gross, "cost_r": cost,
            "net_r": gross - cost}


def _stop_px(o, l, stop):
    """The fill of a sell stop in a minute, or None: at the open when it opened at or below the stop."""
    return o if o <= stop else stop if l <= stop else None


def _original(t, bars, i, entry, t0):
    """The owner's management (long space): stop on all shares until target 1; then the rest's stop at entry, trailed
    to the last higher low - 0.10, exit at the next open after a 5-minute close under VWAP; time stop 60 minutes after
    the fill while target 1 is not hit; anything open exits at the last minute's close."""
    R = t["R"]
    stop, target = round(entry - R, 4), round(entry + 2 * R, 4)
    qty_a, qty_b = t["legs"][0]["qty"], t["legs"][1]["qty"]
    b5 = ticket.to5(bars)
    sw = ticket.swings(b5)
    k = next((j for j, b in enumerate(b5) if mins(b["end"]) > mins(bars[i][0])), len(b5))  # first bar not yet closed
    exits, t1, open_qty = [], False, qty_a + qty_b
    fill_m = mins(bars[i][0])

    def out(qty, px, at, kind):
        exits.append({"qty": qty, "price": px, "at": at, "kind": kind})

    m, o, h, l, c = bars[i][:5]
    if l <= stop:  # the fill minute: the stop only
        out(open_qty, stop, m, "stop")
        return exits
    for m, o, h, l, c, v, vw in bars[i + 1:]:
        now, vwap_exit = mins(m), False
        while k < len(b5) and mins(b5[k]["end"]) <= now:  # 5-minute bars that closed before this minute
            if t1:
                hl = ticket.last_higher_low(sw, k)
                if hl is not None:
                    stop = max(stop, round(hl - TRAIL_PAD, 4))
                w = ticket.vwap(bars, t0, b5[k]["end"])
                vwap_exit = vwap_exit or (w is not None and b5[k]["c"] < w)
            k += 1
        if vwap_exit:
            out(open_qty, o, m, "vwap")
            return exits
        if not t1 and now >= fill_m + TIME_STOP:
            out(open_qty, o, m, "time")
            return exits
        px = _stop_px(o, l, stop)
        if px is not None:
            out(open_qty, px, m, "stop" if not t1 else "breakeven" if stop == entry else "trail")
            return exits
        if not t1 and h >= target:
            out(qty_a, target, m, "target")
            t1, open_qty, stop = True, qty_b, max(stop, entry)
    out(open_qty, c, m, "eod")
    return exits


def _improved(t, bars, i, entry, t0):
    """Two legs (long space), each with its own stop at fill - D; leg A also a target at fill + 2D; both flat at
    their exit_at minute (the open), else at the last minute's close."""
    D = t["R"]
    stop = round(entry - D, 4)
    legs = [{"qty": g["qty"], "target": None if g["target"] is None else round(entry + 2 * D, 4),
             "exit_at": g["exit_at"]} for g in t["legs"]]
    exits = []

    def out(g, px, at, kind):
        exits.append({"qty": g["qty"], "price": px, "at": at, "kind": kind})
        legs.remove(g)

    m, o, h, l, c = bars[i][:5]
    if l <= stop:
        for g in list(legs):
            out(g, stop, m, "stop")
        return exits
    for m, o, h, l, c, v, vw in bars[i + 1:]:
        for g in list(legs):
            if g["exit_at"] and m >= g["exit_at"]:
                out(g, o, m, "eod")
        px = _stop_px(o, l, stop)
        for g in list(legs):
            if px is not None:
                out(g, px, m, "stop")
            elif g["target"] is not None and h >= g["target"]:
                out(g, g["target"], m, "target")
        if not legs:
            return exits
    for g in list(legs):
        out(g, c, m, "eod")
    return exits
