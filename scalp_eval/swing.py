"""Swing bracket ticket evaluator (ADR 0013 section 2, ADR 0019 sections 5-8). Evaluator side (R4): reads contract
tables through scalp_eval.data_access, verifies the engine's signed swing ticket digests, imports nothing from
scalp_engine. In S1 it runs only on synthetic data: the CLI refuses real ticket sets until the trials ledger
scalp_contracts/s_trials.jsonl holds a {"phase": "S2", "kind": "authorize"} record.

    python -m scalp_eval.swing --window development|confirmation --pubkey PATH
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import random
import sys
from pathlib import Path

from scalp_eval.costs import order_cost
import bisect

from scalp_eval.insider_study import bootstrap_ci, clustered, data_root, iso_week

UTC = dt.timezone.utc
RULES_SHA256 = "9f0475fad96c4f7c102d62ad450609b14ee6336f44f78105b0ff9ed950e7b80e"  # ADR 0019 (ADR 0013 section 1 text)
EXPIRY, HOLD, RISK, PLAN = 3, 20, 25.0, "tiered"
BOOK_CASH, BOOK_MAX_POS, BOOK_CAP = 2500.0, 4, 0.40
MDE_MULT = 2.0 + 0.8416


# ---------------------------------------------------------------- market (OHLC + corporate actions)

class SwingMarket:
    """Sessions, daily OHLC and corporate actions (the I2 Market with highs and lows).
    bars: {symbol: (sorted session indices, opens, highs, lows, closes)}; splits: {symbol: [(ex_date, share
    multiplier)]}; divs: {symbol: [(ex_date, cash per share)]}; mergers: {symbol: [(effective, kind, ratio, cash,
    acquirer)]}."""

    def __init__(self, sessions, bars, splits=None, divs=None, mergers=None):
        self.sessions = list(sessions)
        self.idx = {d: i for i, d in enumerate(self.sessions)}
        self.bars = bars
        self.splits, self.divs, self.mergers = splits or {}, divs or {}, mergers or {}

    def _j(self, sym, i):
        b = self.bars.get(sym)
        if b is None:
            return None, None
        j = bisect.bisect_left(b[0], i)
        return (b, j) if j < len(b[0]) and b[0][j] == i else (b, None)

    def bar_full(self, sym, i):
        b, j = self._j(sym, i)
        return None if j is None else (b[1][j], b[2][j], b[3][j], b[4][j])

    def bar(self, sym, i):
        x = self.bar_full(sym, i)
        return None if x is None else (x[0], x[3])

    def last_bar(self, sym, upto=None):
        b = self.bars.get(sym)
        if not b or not b[0]:
            return None
        j = len(b[0]) if upto is None else bisect.bisect_right(b[0], upto)
        return (b[0][j - 1], b[4][j - 1]) if j else None

    def first_close_after(self, sym, i):
        b = self.bars.get(sym)
        if not b:
            return None
        j = bisect.bisect_right(b[0], i)
        return (b[0][j], b[4][j]) if j < len(b[0]) else None


# ---------------------------------------------------------------- spreads

class Quotes:
    """open935 / close5: {(symbol, date): (bid, ask)}; tier: fallback half-spread fraction of price (ADR 0014 5)."""

    def __init__(self, open935=None, close5=None, tier_half_frac=0.0005):
        self.open935, self.close5, self.tier = open935 or {}, close5 or {}, tier_half_frac

    @staticmethod
    def _half(q):
        if not q:
            return None
        bid, ask = q
        if bid is None or ask is None or bid <= 0 or ask < bid:
            return None
        return (ask - bid) / 2.0

    def half_close(self, sym, sessions, i, price, back=2):
        for j in range(i, max(-1, i - back), -1):
            h = self._half(self.close5.get((sym, sessions[j])))
            if h is not None:
                return h
        return price * self.tier

    def half_fill(self, sym, sessions, i, price):
        h = self._half(self.open935.get((sym, sessions[i])))
        return h if h is not None else self.half_close(sym, sessions, i - 1, price)


# ---------------------------------------------------------------- entry (A1)

def entry(tk, mkt, minutes, quotes):
    """-> {"filled": False, "reason"} or {"filled": True, "i": fill session index, "minute": fill minute or None,
    "px": fill price, "kind"} (ADR 0014 section 5)."""
    sym, S = tk["symbol"], mkt.sessions
    i0 = mkt.idx[tk["signal_date"]]
    trigger, limit = tk["trigger"], tk["limit"]
    resting, touched = False, False
    for i in range(i0 + 1, min(i0 + 1 + EXPIRY, len(S))):
        bar = mkt.bar_full(sym, i)
        if bar is None:
            continue
        op, hi, lo, cl = bar
        mins = minutes(sym, S[i])
        if not resting:
            if hi < trigger:
                continue
            touched = True
            if op > limit:  # gapped above the limit: the triggered order rests at the limit (A1)
                resting = True
                m = next((m for m in (mins or []) if m[3] <= limit), None)
                if m is not None:
                    return {"filled": True, "i": i, "minute": m[0], "px": limit, "kind": "resting_limit"}
                if mins is None and lo <= limit:
                    return {"filled": True, "i": i, "minute": None, "px": limit, "kind": "resting_limit"}
                continue
            if op >= trigger:
                hs = quotes.half_fill(sym, S, i, op)
                return {"filled": True, "i": i, "minute": "open" if mins else None, "px": min(op + hs, limit),
                        "kind": "open"}
            hs = quotes.half_fill(sym, S, i, trigger)
            m = next((m for m in (mins or []) if m[2] >= trigger), None)
            return {"filled": True, "i": i, "minute": m[0] if m else None, "px": min(trigger + hs, limit), "kind": "trigger"}
        if op <= limit:
            hs = quotes.half_fill(sym, S, i, op)
            return {"filled": True, "i": i, "minute": "open" if mins else None, "px": min(op + hs, limit), "kind": "open"}
        m = next((m for m in (mins or []) if m[3] <= limit), None)
        if m is not None:
            return {"filled": True, "i": i, "minute": m[0], "px": limit, "kind": "resting_limit"}
        if mins is None and lo <= limit:
            return {"filled": True, "i": i, "minute": None, "px": limit, "kind": "resting_limit"}
    return {"filled": False, "reason": "gapped_above_limit" if touched else "no_touch"}


# ---------------------------------------------------------------- exits (A4)

def _scan_minutes(mins, after, stop, target):
    """First stop/target event in minutes strictly after `after` (None = all). Same minute: stop first."""
    if after == "open":  # an open fill: every minute of the session counts, the first included
        after = None
    for m in mins:
        if after is not None and m[0] <= after:
            continue
        if m[3] <= stop:
            return "stop"
        if m[2] > target:
            return "target"
    return None


def run_ticket(tk, mkt, minutes, quotes, qty=None, stop_first_only=False):
    """One bracket ticket -> result dict (ADR 0014 section 5). minutes(sym, date) -> [(ts, o, h, l, c)] or None.
    stop_first_only: S-a (daily bars, stop first throughout)."""
    sym, S = tk["symbol"], mkt.sessions
    mins_fn = (lambda s, d: None) if stop_first_only else minutes
    e = entry(tk, mkt, mins_fn, quotes)
    if not e["filled"]:
        return {"ticket_id": tk["ticket_id"], "filled": False, "reason": e["reason"], "net_r": 0.0}
    q = tk["qty"] if qty is None else qty
    f, fill_px = e["i"], e["px"]
    stop, target, shares, div_cash = tk["stop"], tk["target"], float(q), 0.0
    d_f = S[f]
    iH = f + HOLD - 1
    last = mkt.last_bar(sym)
    case, exit_px, xi, sell = None, None, None, True

    def actions_through(k):
        nonlocal stop, target, shares, div_cash
        d0, d1 = S[k - 1], S[k]
        for ex, fct in mkt.splits.get(sym, ()):
            if d0 < ex <= d1:
                shares *= fct
                stop, target = stop / fct, target / fct
        for ex, amt in mkt.divs.get(sym, ()):
            if d0 < ex <= d1:
                div_cash += shares * amt

    for k in range(f, iH + 1):
        if k >= len(S):
            break
        if k > f:
            actions_through(k)
        bar = mkt.bar_full(sym, k)
        if bar is None:
            for eff, kind, ratio, cash, acq in sorted(mkt.mergers.get(sym, ())):
                if d_f < eff <= S[k]:
                    if kind == "cash_mergers":
                        case, exit_px = "merger_cash", cash
                    else:
                        ia = mkt.idx.get(eff)
                        ab = mkt.bar(acq, ia) if (acq and ia is not None) else None
                        case, exit_px = ("merger_stock", ratio * ab[1] + (cash or 0.0)) if ab else \
                            ("merger_no_acquirer", mkt.last_bar(sym, k)[1])
                    xi, sell = k, False
                    break
            if case:
                break
            if last is not None and last[0] < k:
                case, exit_px, xi, sell = "delisting", last[1] * 0.70, last[0], False
                break
            continue
        op, hi, lo, cl = bar
        mins = mins_fn(sym, S[k])
        if k == f:
            if mins is not None and e["minute"] is not None:
                ev = _scan_minutes(mins, e["minute"], stop, target)
            else:
                ev = "stop" if lo <= stop else ("target" if hi > target else None)
            if ev == "stop":
                case, exit_px, xi = "stop_fill_session", stop - quotes.half_close(sym, S, k - 1, stop), k
            elif ev == "target":
                case, exit_px, xi = "target", target, k
            if case:
                break
            continue
        if op <= stop:
            case, exit_px, xi = "gap_below_stop", op - quotes.half_close(sym, S, k - 1, op), k
            break
        if op > target:
            case, exit_px, xi = "gap_above_target", op, k
            break
        hit_s, hit_t = lo <= stop, hi > target
        if hit_s and hit_t:
            ev = _scan_minutes(mins, None, stop, target) if mins is not None else "stop"
            ev = ev or "stop"
        else:
            ev = "stop" if hit_s else ("target" if hit_t else None)
        if ev == "stop":
            case, exit_px, xi = "stop", stop - quotes.half_close(sym, S, k - 1, stop), k
            break
        if ev == "target":
            case, exit_px, xi = "target", target, k
            break
    if case is None:
        if iH < len(S) and mkt.bar_full(sym, iH) is not None:
            cl = mkt.bar_full(sym, iH)[3]
            case, exit_px, xi = "time", cl - quotes.half_close(sym, S, iH, cl), iH
        else:
            nxt = mkt.first_close_after(sym, iH)
            if nxt is None:
                lb = mkt.last_bar(sym)
                case, exit_px, xi, sell = "delisting", lb[1] * 0.70, lb[0], False
            else:
                case, exit_px, xi = "gap_time", nxt[1] - quotes.half_close(sym, S, nxt[0], nxt[1]), nxt[0]
    cost = order_cost(PLAN, "buy", q, fill_px, d_f)
    if sell:
        cost += order_cost(PLAN, "sell", shares, exit_px, S[xi])
    pnl = shares * exit_px + div_cash - q * fill_px - cost
    return {"ticket_id": tk["ticket_id"], "filled": True, "fill_i": f, "fill_kind": e["kind"], "fill_px": fill_px,
            "exit_i": xi, "exit_px": exit_px, "case": case, "qty": q, "shares_exit": shares, "dividends": div_cash,
            "cost": cost, "pnl": pnl, "net_r": pnl / RISK}


# ---------------------------------------------------------------- book (ADR 0014 section 5)

def book_order(ticket_id):
    return hashlib.sha256(f"{ticket_id}|s2-book-v1".encode()).hexdigest()


def simulate_book(tickets, mkt, minutes, quotes, minutes_for=None, quotes_for=None):
    """$2,500 cash, T+1, <= 4 positions, a position <= 40% of equity (scaled down), seeded same-session order, idle
    cash at 0%. -> dict with trades, skips by reason, final equity. minutes_for(ticket) / quotes_for(ticket), when
    given, supply each ticket's own minute window and spread tier (S2 driver)."""
    mins_of = minutes_for or (lambda t: minutes)
    q_of = quotes_for or (lambda t: quotes)
    first = {t["ticket_id"]: run_ticket(t, mkt, mins_of(t), q_of(t)) for t in tickets}
    fills = sorted((r["fill_i"], book_order(tid), tid) for tid, r in first.items() if r["filled"])
    by_id = {t["ticket_id"]: t for t in tickets}
    settled, pending, open_pos, equity_cost = BOOK_CASH, [], [], 0.0
    trades, skips = [], {"t1_cash": 0, "max_positions": 0, "zero_qty": 0}
    for fi, _, tid in fills:
        settled += sum(c for d, c in pending if d < fi)
        pending = [(d, c) for d, c in pending if d >= fi]
        for p in [p for p in open_pos if p["exit_i"] < fi]:
            open_pos.remove(p)
        if len(open_pos) >= BOOK_MAX_POS:
            skips["max_positions"] += 1
            continue
        px = first[tid]["fill_px"]
        equity = settled + sum(c for _, c in pending) + sum(p["cost_basis"] for p in open_pos)
        q = min(by_id[tid]["qty"], int(BOOK_CAP * equity // px))
        if q <= 0:
            skips["zero_qty"] += 1
            continue
        r = run_ticket(by_id[tid], mkt, mins_of(by_id[tid]), q_of(by_id[tid]), qty=q)
        need = q * r["fill_px"] + order_cost(PLAN, "buy", q, r["fill_px"], mkt.sessions[r["fill_i"]])
        if need > settled:
            skips["t1_cash"] += 1
            continue
        settled -= need
        proceeds = need + r["pnl"]
        pending.append((r["exit_i"], proceeds))
        open_pos.append({"exit_i": r["exit_i"], "cost_basis": need})
        trades.append(r)
    final = settled + sum(c for _, c in pending)
    if not trades:
        return {"trades": 0, "skips": skips, "final_equity": final, "return": final / BOOK_CASH - 1.0, "first_i": None,
                "last_i": None, "fills": len(fills)}
    return {"trades": len(trades), "skips": skips, "final_equity": final, "return": final / BOOK_CASH - 1.0,
            "first_i": trades[0]["fill_i"] if trades else None, "last_i": max((t["exit_i"] for t in trades), default=None)}


# ---------------------------------------------------------------- inference

def summarize(values, weeks):
    c = clustered(values, weeks)
    return {**c, "ci95": bootstrap_ci(values, weeks, tag="s2-boot-v1") if len(values) > 1 else None,
            "mde": MDE_MULT * c["se"] if c.get("se") else None,
            "hit_rate": sum(v > 0 for v in values) / len(values) if values else None}


def control_differences(results, controls_of):
    """Gate (ii): per issued ticket, net R (0 if unfilled) minus the mean of its controls' net R (0 if unfilled)."""
    out = []
    for tid, r in results.items():
        cs = [c["net_r"] for c in controls_of.get(tid, [])]
        if cs:
            out.append((tid, r["net_r"] - sum(cs) / len(cs)))
    return out


# ---------------------------------------------------------------- inputs and the S2 guard

def canonical_sha256(rows_df):
    df = rows_df.drop([c for c in ("ingested_at",) if c in rows_df.columns])
    df = df.select(sorted(df.columns))
    rows = df.sort(df.columns).to_dicts()
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


LEDGER = Path(__file__).resolve().parents[1] / "scalp_contracts" / "s_trials.jsonl"


def s2_open(ledger=LEDGER):
    """True when the S trials ledger holds {"phase": "S2", "kind": "authorize"} (ADR 0019 section 8)."""
    p = Path(ledger)
    if not p.exists():
        return False
    for x in p.read_text(encoding="utf-8").splitlines():
        if x.strip():
            r = json.loads(x)
            if r.get("phase") == "S2" and r.get("kind") == "authorize":
                return True
    return False


def verified_set(setname, window, pubkey_path):
    from scalp_eval.data_access import read_frame
    from scalp_eval.verify import load_public_key, verify_artifact
    import polars as pl
    name = f"s1_{setname.replace('-', '').lower()}_{window}.signed.json"
    env = json.loads((data_root() / "derived" / name).read_text(encoding="utf-8"))
    payload = verify_artifact(env, [load_public_key(pubkey_path)])
    all_ = (dt.datetime(2022, 1, 3, 5, tzinfo=UTC), dt.datetime(2026, 11, 2, 4, tzinfo=UTC))
    df = read_frame(data_root() / "swing_tickets", *all_)
    sub = df.filter((pl.col("set") == setname) & (pl.col("window") == window))
    if payload["sha256"] != canonical_sha256(sub) or payload.get("rules_sha256") != RULES_SHA256:
        raise ValueError(f"{name}: rows or rules hash do not match the signed digest")
    return sub, payload


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_eval.swing")
    ap.add_argument("--window", required=True, choices=["development", "confirmation"])
    ap.add_argument("--pubkey", required=True)
    a = ap.parse_args(argv)
    if not s2_open():
        raise SystemExit("refused: no S2 authorize record in scalp_contracts/s_trials.jsonl; "
                         "S1 evaluates synthetic data only (ADR 0019 section 8)")
    raise SystemExit("refused: real sets run only through python -m scalp_eval.swing_s2 (ADR 0020), which verifies "
                     "every set against the authorize line and allows one confirmation")


if __name__ == "__main__":
    sys.exit(main())
