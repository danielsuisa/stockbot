"""S2 Stage 2: an independent re-simulation check of the swing evaluator (ADR 0020 section 4).

200 development H1 tickets and 200 of their controls (seed tag "s2-resim-v1") are re-simulated by code written
from the ADR 0019 section 5 text for this check only. It imports none of scalp_eval.swing's simulation functions
(entry, run_ticket, Quotes, SwingMarket methods) and none of scalp_eval.costs: the fee schedule is re-derived here.
It uses the evaluator's data loaders (verified sets, daily bars and corporate actions, quotes, minute bars). Every
fill, exit and net R must agree with the development run's per-ticket results within $0.01 per share.

    python -m scalp_eval.swing_resim --run 1     # appends {"phase": "S2", "kind": "resim_check", ...} to the ledger
"""
import argparse
import datetime as dt
import hashlib
import json
import random
import sys

from scalp_eval import swing_s2 as L  # loaders only: verified sets, market data, quotes, minute bars, ledger

TAG = "s2-resim-v1"
N = 200
RISK = 25.0
EXPIRY, HOLD = 3, 20
TAF_HOLIDAY = dt.date(2026, 10, 1)


def fee(side, q, px, day):
    """IBKR Pro Tiered with fees, re-derived from the published schedule (costs.py is not imported)."""
    if q <= 0:
        return 0.0
    comm = min(max(0.0035 * q, 0.35), 0.01 * q * px)
    t = comm + 0.000003 * q + 0.0030 * q + 0.00020 * q + comm * (0.000175 + 0.000565)
    if side == "sell":
        t += 0.0000206 * q * px
        if day < TAF_HOLIDAY:
            t += min(0.000166 * q, 8.30)
    return t


class Data:
    """Plain views over the loaders' outputs."""

    def __init__(self, mkt, quotes_by_tier, mins, dv):
        self.S = mkt.sessions
        self.ix = {d: i for i, d in enumerate(self.S)}
        self.raw = mkt.bars
        self.splits, self.divs, self.mergers = mkt.splits, mkt.divs, mkt.mergers
        self.open935 = quotes_by_tier["T1"].open935
        self.close5 = quotes_by_tier["T1"].close5
        self.mins, self.dv = mins, dv

    def ohlc(self, sym, i):
        b = self.raw.get(sym)
        if not b:
            return None
        lo, hi = 0, len(b[0])
        while lo < hi:  # binary search, written here
            mid = (lo + hi) // 2
            if b[0][mid] < i:
                lo = mid + 1
            else:
                hi = mid
        if lo < len(b[0]) and b[0][lo] == i:
            return b[1][lo], b[2][lo], b[3][lo], b[4][lo]
        return None

    def last_bar_at_or_before(self, sym, i=None):
        b = self.raw.get(sym)
        if not b:
            return None
        cands = [(k, c) for k, c in zip(b[0], b[4]) if i is None or k <= i]
        return cands[-1] if cands else None

    def next_bar_after(self, sym, i):
        b = self.raw.get(sym)
        for k, c in zip(b[0], b[4]):
            if k > i:
                return k, c
        return None

    def tier_frac(self, sym, i):
        d = self.dv.get(sym, {})
        v = sorted(d.get(j, 0.0) for j in range(i - 19, i + 1))
        med = (v[9] + v[10]) / 2.0
        return 0.00025 if med >= 50e6 else 0.000475


def half(q):
    if not q or q[0] is None or q[1] is None or q[0] <= 0 or q[1] < q[0]:
        return None
    return (q[1] - q[0]) / 2.0


def resim(tk, D):
    """One ticket from the ADR 0019 section 5 text. -> dict comparable with the evaluator's result."""
    sym, S = tk["symbol"], D.S
    i0 = D.ix[tk["signal_date"]]
    window = set(S[i0 + 1:i0 + 1 + EXPIRY])
    frac = D.tier_frac(sym, i0)

    def minutes(i):
        return D.mins.get((sym, S[i])) if S[i] in window else None

    def half_close(i, px):  # the session's sample, else one session back, else the tier median
        for j in (i, i - 1):
            if j >= 0:
                h = half(D.close5.get((sym, S[j])))
                if h is not None:
                    return h
        return px * frac

    def half_open(i, px):
        h = half(D.open935.get((sym, S[i])))
        return h if h is not None else half_close(i - 1, px)

    T, Lm = tk["trigger"], tk["limit"]
    fill = None  # (session, minute marker, price)
    resting, touched = False, False
    for i in range(i0 + 1, min(i0 + 1 + EXPIRY, len(S))):
        b = D.ohlc(sym, i)
        if b is None:
            continue
        op, hi, lo, cl = b
        mm = minutes(i)
        if resting:
            if op <= Lm:
                fill = (i, "open" if mm else None, min(op + half_open(i, op), Lm))
                break
            hit = [m for m in (mm or []) if m[3] <= Lm]
            if hit:
                fill = (i, hit[0][0], Lm)
                break
            if mm is None and lo <= Lm:
                fill = (i, None, Lm)
                break
            continue
        if hi < T:
            continue
        touched = True
        if op > Lm:
            resting = True
            hit = [m for m in (mm or []) if m[3] <= Lm]
            if hit:
                fill = (i, hit[0][0], Lm)
                break
            if mm is None and lo <= Lm:
                fill = (i, None, Lm)
                break
            continue
        if op >= T:
            fill = (i, "open" if mm else None, min(op + half_open(i, op), Lm))
        else:
            first = [m for m in (mm or []) if m[2] >= T]
            fill = (i, first[0][0] if first else None, min(T + half_open(i, T), Lm))
        break
    if fill is None:
        return {"filled": False}
    f, fmin, fpx = fill
    q = tk["qty"]
    shares, stop, target, cash = float(q), tk["stop"], tk["target"], 0.0
    last = D.last_bar_at_or_before(sym)
    end = f + HOLD - 1
    out = None  # (session, price, case, sell)
    for k in range(f, end + 1):
        if k >= len(S):
            break
        if k > f:
            for ex, m in D.splits.get(sym, ()):
                if S[k - 1] < ex <= S[k]:
                    shares, stop, target = shares * m, stop / m, target / m
            for ex, a in D.divs.get(sym, ()):
                if S[k - 1] < ex <= S[k]:
                    cash += shares * a
        b = D.ohlc(sym, k)
        if b is None:
            for eff, kind, ratio, mcash, acq in sorted(D.mergers.get(sym, ())):
                if S[f] < eff <= S[k]:
                    if kind == "cash_mergers":
                        out = (k, mcash, "merger_cash", False)
                    else:
                        ia = D.ix.get(eff)
                        ab = D.ohlc(acq, ia) if (acq and ia is not None) else None
                        out = (k, ratio * ab[3] + (mcash or 0.0), "merger_stock", False) if ab else \
                            (k, D.last_bar_at_or_before(sym, k)[1], "merger_no_acquirer", False)
                    break
            if out:
                break
            if last is not None and last[0] < k:
                out = (last[0], last[1] * 0.70, "delisting", False)
                break
            continue
        op, hi, lo, cl = b
        mm = minutes(k)
        if k == f:
            ev = None
            if mm is not None and fmin is not None:
                for m in mm:
                    if fmin != "open" and m[0] <= fmin:
                        continue
                    if m[3] <= stop:
                        ev = "stop"
                        break
                    if m[2] > target:
                        ev = "target"
                        break
            else:
                ev = "stop" if lo <= stop else ("target" if hi > target else None)
            if ev == "stop":
                out = (k, stop - half_close(k - 1, stop), "stop_fill_session", True)
                break
            if ev == "target":
                out = (k, target, "target", True)
                break
            continue
        if op <= stop:
            out = (k, op - half_close(k - 1, op), "gap_below_stop", True)
            break
        if op > target:
            out = (k, op, "gap_above_target", True)
            break
        s_hit, t_hit = lo <= stop, hi > target
        ev = None
        if s_hit and t_hit:
            ev = "stop"
            if mm is not None:
                for m in mm:
                    if m[3] <= stop:
                        ev = "stop"
                        break
                    if m[2] > target:
                        ev = "target"
                        break
        elif s_hit:
            ev = "stop"
        elif t_hit:
            ev = "target"
        if ev == "stop":
            out = (k, stop - half_close(k - 1, stop), "stop", True)
            break
        if ev == "target":
            out = (k, target, "target", True)
            break
    if out is None:
        b = D.ohlc(sym, end) if end < len(S) else None
        if b is not None:
            out = (end, b[3] - half_close(end, b[3]), "time", True)
        else:
            nb = D.next_bar_after(sym, end)
            if nb is None:
                lb = D.last_bar_at_or_before(sym)
                out = (lb[0], lb[1] * 0.70, "delisting", False)
            else:
                out = (nb[0], nb[1] - half_close(nb[0], nb[1]), "gap_time", True)
    xi, xpx, case, sell = out
    cost = fee("buy", q, fpx, S[f]) + (fee("sell", shares, xpx, S[xi]) if sell else 0.0)
    pnl = shares * xpx + cash - q * fpx - cost
    return {"filled": True, "fill_i": f, "fill_px": fpx, "exit_i": xi, "exit_px": xpx, "case": case, "net_r": pnl / RISK,
            "qty": q}


def draw(h1, controls):
    seed = int.from_bytes(hashlib.sha256(TAG.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    a = rng.sample(sorted(h1, key=lambda t: t["ticket_id"]), N)
    ids = {t["ticket_id"] for t in a}
    pool = sorted((c for c in controls if c["parent_id"] in ids), key=lambda t: t["ticket_id"])
    return a, rng.sample(pool, min(N, len(pool)))


def compare(mine, ev):
    """Agreement within $0.01 per share on fill and exit prices, the same sessions and fill state, and net R within
    $0.01 per share of the position (plus 1e-6 for float noise)."""
    if mine["filled"] != ev["filled"]:
        return "fill state"
    if not mine["filled"]:
        return None
    if mine["fill_i"] != ev["fill_i"] or abs(mine["fill_px"] - ev["fill_px"]) > 0.01 + 1e-9:
        return "fill"
    if mine["exit_i"] != ev["exit_i"] or abs(mine["exit_px"] - ev["exit_px"]) > 0.01 + 1e-9:
        return "exit"
    if abs(mine["net_r"] - ev["net_r"]) > 0.01 * mine["qty"] * 2 / RISK + 1e-6:
        return "net R"
    return None


def check(run=1, ledger=L.LEDGER):
    lines = L.ledger_lines(ledger)
    auth = L.authorize_line(lines)
    if auth is None:
        raise SystemExit("no S2 authorize line")
    sets = L.load_sets("development", auth)
    res = json.loads((L.data_root() / "derived" / "s2" / f"development_run{run}_tickets.json").read_text(encoding="utf-8"))
    a, c = draw(sets["H1"], sets["controls"])
    every = a + c
    mkt, dv = L.swing_market({t["symbol"] for t in every} | {"SPY"})
    quotes = L.quotes_frames({t["symbol"] for t in every})
    mins = L.load_minutes(L.minute_pairs(every, mkt))
    D = Data(mkt, quotes, mins, dv)
    bad = []
    for name, tks in (("H1", a), ("controls", c)):
        for t in tks:
            mine = resim(t, D)
            ev = res[f"{name}|{t['ticket_id']}"]
            why = compare(mine, ev)
            if why:
                bad.append({"set": name, "ticket_id": t["ticket_id"], "why": why, "mine": mine,
                            "evaluator": {k: ev.get(k) for k in ("filled", "fill_i", "fill_px", "exit_i", "exit_px", "case", "net_r")}})
    rec = {"phase": "S2", "kind": "resim_check", "run": run, "tag": TAG, "h1": len(a), "controls": len(c),
           "filled": sum(1 for t in every if res[f"{'H1' if t in a else 'controls'}|{t['ticket_id']}"]["filled"]),
           "disagreements": len(bad), "pass": not bad, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    out = L.data_root() / "derived" / "s2" / f"resim_run{run}.json"
    out.write_text(json.dumps({**rec, "details": bad}, indent=1, default=str) + "\n", encoding="utf-8")
    L.append(rec, ledger)
    return rec, bad


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_eval.swing_resim")
    ap.add_argument("--run", type=int, default=1)
    a = ap.parse_args(argv)
    rec, bad = check(a.run)
    print(json.dumps(rec, indent=1))
    for b in bad[:10]:
        print(json.dumps(b, default=str))
    return 0 if rec["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
