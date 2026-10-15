"""$3–$20 parallel of the premarket catalyst. Locked 2026-10-15 before any data request.

The screen, the entry, the stop, and the gate are the sub-$5 rule. The prior close is $3 through $20,
both ends inclusive. This module does not place or transmit an order.

A print strictly under $5 still fills at the ask and the bid, or at 1% on each side when a quote is
missing. A print at $5 or above pays the full quoted spread once, or the 1% round-trip haircut when
the entry quote is missing. The desk size beside the gate uses a 10% LULD band on every name, including
a print of exactly $3.
"""
from bot.desk_gate import ACCOUNT, friction, shares_for
from bot.premarket_catalyst import CHEAP_SLIP, score

MID_LO, MID_HI = 3.0, 20.0
MID_LULD = 0.10


def mid_price_ok(prev_close):
    """Prior split-adjusted close from $3 through $20, both ends inclusive."""
    return prev_close is not None and MID_LO <= prev_close <= MID_HI


def _priced_side(entry, exit_px, stop, shares, spread, side, pro):
    """One side at $5 or above. The spread is charged once. `pro` False zeros the commission."""
    slip, fee, mode = friction(entry, exit_px, shares, spread)
    if not pro:
        fee = 0.0
    gross = (exit_px - entry) if side == "long" else (entry - exit_px)
    risk = abs(entry - stop) * shares
    if shares < 1 or risk <= 0 or entry <= 0 or exit_px <= 0:
        return None
    pnl = gross * shares - slip - fee
    return {"pnl": pnl, "net_r": pnl / risk, "cost_mode": mode}


def score_priced(row, entry_quote, exit_quote, slip=CHEAP_SLIP):
    """Cost for one $3–$20 signal. `slip` changes only a print strictly under $5.

    At $5 and above the entry quote's bid-ask is the spread, charged once. A missing entry quote uses
    the 1% round-trip haircut. The exit quote is not a second spread. The share count is the whole
    shares of $460 at the printed entry. Under $5 this is the sub-$5 scorer, including `slip`.
    """
    entry = row.get("entry")
    if entry is None:
        return None
    if entry < 5.0:
        return score(row, entry_quote, exit_quote, slip)
    if row.get("short_exit") is None or row.get("stop") is None or entry <= row["stop"]:
        return None
    shares = shares_for(entry, ACCOUNT)
    if shares < 1:
        return None
    spread = None
    if entry_quote and entry_quote[0] is not None and entry_quote[1] is not None and entry_quote[1] >= entry_quote[0]:
        spread = entry_quote[1] - entry_quote[0]
    long = _priced_side(entry, row["exit"], row["stop"], shares, spread, "long", True)
    short = _priced_side(entry, row["short_exit"], row["stop"], shares, spread, "short", True)
    bare = _priced_side(entry, row["exit"], row["stop"], shares, spread, "long", False)
    if not long or not short or not bare or long["net_r"] is None or short["net_r"] is None:
        return None
    out = dict(row)
    out.update(shares=shares, pnl=long["pnl"], net_r=long["net_r"], net_r_short=short["net_r"],
               short_pnl=short["pnl"], cost_mode=long["cost_mode"], net_r_no_commission=bare["net_r"],
               fill_entry=entry, fill_exit=row["exit"], slip=None)
    return out
