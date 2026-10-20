"""The intraday ticket evaluator (ADR 0016 sections 3-7): fills, exits, costs, liquidity, net R, the control
difference, the $2,500 cash book with T+1 settlement, CR1 inference by trading day and a day-block bootstrap.

T1 runs it on synthetic fixtures only. The CLI refuses a real (signed) set until the trials ledger
scalp_contracts/t_trials.jsonl holds a line {"phase": "T2", "kind": "authorize", ...} (ADR 0016 section 7).
Independent of scalp_engine (R4): the set's digest is re-derived here, never imported.

    python -m scalp_eval.intraday_eval run --set derived/t1/sets/<window>/H1.signed.json   # refused in T1"""
import argparse
import datetime as dt
import hashlib
import json
import math
import random
import sys
from pathlib import Path

from scalp_eval import costs
from scalp_eval.insider_study import clustered

TICK = 0.01
LIQ_SHARE = 0.01
PLAN = "tiered"
EQUITY = 2500.0
BOOT_TAG, BOOT_N = "t2-dayboot-v1", 10_000
LEDGER = Path(__file__).resolve().parents[1] / "scalp_contracts" / "t_trials.jsonl"


class Refused(RuntimeError):
    pass


def hm(t):
    return int(t[:2]) * 60 + int(t[3:5])


def hhmm(n):
    return f"{n // 60:02d}:{n % 60:02d}"


# ---------------------------------------------------------------- spreads

def spread_at(samples, t):
    """samples = {"HH:MM": (bid, ask) or None}: the dollar spread at t, else the nearest valid sample of the same
    symbol and session (ties: the earlier); None when no sample is valid (a "no quote" skip)."""
    def ok(q):
        return q is not None and q[0] > 0 and q[1] > 0 and q[1] >= q[0]
    if ok(samples.get(t)):
        return samples[t][1] - samples[t][0]
    valid = sorted((abs(hm(k) - hm(t)), hm(k), q) for k, q in samples.items() if ok(q))
    return None if not valid else valid[0][2][1] - valid[0][2][0]


# ---------------------------------------------------------------- one ticket

def simulate(tk, bars, samples, time_exit="15:55", day=dt.date(2024, 6, 12), shares=None):
    """One ticket against its session's SIP minute bars [("HH:MM", o, h, l, c, v)] ascending.

    tk: trigger, limit, stop, target, shares, issued_at ("HH:MM", the signal candle close). Returns a dict with
    status in {"zero_share", "no_quote", "not_filled", "rejected_liquidity", "filled"}; for "filled": entry, exit,
    exit_kind (stop / target / time), fill and exit minutes, costs, net $ and net R."""
    q = tk["shares"] if shares is None else shares
    if q <= 0:
        return {"status": "zero_share"}
    s_in, s_out = spread_at(samples, tk["issued_at"]), spread_at(samples, time_exit)
    if s_in is None or s_out is None:
        return {"status": "no_quote"}
    trig, lim, stop, tgt = tk["trigger"], tk["limit"], tk["stop"], tk["target"]
    c = hm(tk["issued_at"])
    pre = [b for b in bars if hm(b[0]) == c]
    pre_triggered = bool(pre) and pre[0][2] >= trig  # traded during [C, C + 60 s), before the order went live
    live = [b for b in bars if c + 1 <= hm(b[0]) < hm(time_exit)]
    fill = None
    resting = False
    for i, (m, o, h, l, cl, v) in enumerate(live):
        if fill is None:
            if resting:
                if o <= lim:
                    ref = o
                elif l <= lim:
                    ref = lim
                else:
                    continue
            elif pre_triggered and i == 0:
                if o <= lim:
                    ref = o
                else:
                    resting = True  # checked from the next minute
                    continue
            elif h >= trig:
                if o > lim:
                    resting = True
                    continue
                ref = o if o >= trig else trig
            else:
                continue
            if q > LIQ_SHARE * v:
                return {"status": "rejected_liquidity", "fill_minute": m, "minute_volume": v}
            fill = (i, m, ref + s_in / 2)
            if l <= stop:  # a stop counts in the fill minute; a target never does
                return _close(tk, q, fill, m, min(stop, o) - s_in / 2 - TICK, "stop", day)
            continue
        if l <= stop:  # the stop wins a minute that also trades through the target
            return _close(tk, q, fill, m, min(stop, o) - s_in / 2 - TICK, "stop", day)
        if h > tgt:
            return _close(tk, q, fill, m, tgt, "target", day)
    if fill is None:
        return {"status": "not_filled", "resting": resting}
    last = [b for b in bars if hm(b[0]) < hm(time_exit)]
    return _close(tk, q, fill, last[-1][0], last[-1][4] - s_out / 2, "time", day)


def _close(tk, q, fill, m, exit_px, kind, day):
    entry = fill[2]
    cost = costs.order_cost(PLAN, "buy", q, entry, day) + costs.order_cost(PLAN, "sell", q, exit_px, day)
    net = q * (exit_px - entry) - cost
    risk = q * (tk["trigger"] - tk["stop"])
    return {"status": "filled", "entry": entry, "exit": exit_px, "exit_kind": kind, "fill_minute": fill[1],
            "exit_minute": m, "shares": q, "costs": cost, "net": net, "net_r": net / risk}


# ---------------------------------------------------------------- controls, book, inference

def size(trigger, stop, equity=EQUITY, cap_share=1.0, risk=25.0):
    """ADR 0016 section 4, re-derived (R4): floor(min(risk / (trigger - stop), cap_share x equity / trigger))."""
    r = trigger - stop
    if r <= 0 or trigger <= 0:
        return 0
    return int(min(risk / r, cap_share * equity / trigger) + 1e-9)


def control_difference(ticket_r, control_rs):
    """ticket net R - mean net R of its filled controls; None when none filled (left out of gate (ii), counted)."""
    rs = [r for r in control_rs if r is not None]
    return None if not rs else ticket_r - sum(rs) / len(rs)


def book(trades, sessions, equity=EQUITY):
    """One cash account, one position at a time, T+1: trades = [{date, fill_minute, exit_minute, entry, exit,
    shares, costs}] (filled tickets). A sale's proceeds settle on the next session; a buy needs settled cash.
    -> {final, taken, blocked_settlement, blocked_position, path}."""
    nxt = {d: sessions[i + 1] for i, d in enumerate(sessions[:-1])}
    settled, pending, taken, b_settle, b_pos, path = equity, [], [], 0, 0, []
    by_day = {}
    for t in trades:
        by_day.setdefault(t["date"], []).append(t)
    for d in sessions:
        settled += sum(a for s, a in pending if s <= d)
        pending = [(s, a) for s, a in pending if s > d]
        busy_until = None
        for t in sorted(by_day.get(d, []), key=lambda x: (x["fill_minute"], x.get("ticket_id", ""))):
            if busy_until is not None and t["fill_minute"] <= busy_until:
                b_pos += 1
                continue
            q = min(t["shares"], int(settled / t["entry"] + 1e-9))
            if q < t["shares"]:
                b_settle += 1
                continue
            buy_cost = costs.order_cost(PLAN, "buy", q, t["entry"], d)
            sell_cost = costs.order_cost(PLAN, "sell", q, t["exit"], d)
            settled -= q * t["entry"] + buy_cost
            pending.append((nxt.get(d, d + dt.timedelta(days=1)), q * t["exit"] - sell_cost))
            busy_until = t["exit_minute"]
            taken.append(t.get("ticket_id"))
        path.append((d, settled + sum(a for _, a in pending)))
    final = settled + sum(a for _, a in pending)
    return {"final": final, "return": final / equity - 1, "taken": taken, "blocked_settlement": b_settle,
            "blocked_position": b_pos, "path": path}


def inference(values, days, tag=BOOT_TAG, n=BOOT_N):
    """Mean, CR1 SE clustered by trading day, t, and a day-block bootstrap 95% CI (seeded)."""
    out = clustered(values, days)
    by = {}
    for v, d in zip(values, days):
        s, k = by.get(d, (0.0, 0))
        by[d] = (s + v, k + 1)
    keys = sorted(by)
    if len(keys) >= 2:
        rng = random.Random(int.from_bytes(hashlib.sha256(tag.encode()).digest()[:8], "big"))
        means = []
        for _ in range(n):
            s = k = 0
            for _ in keys:
                a, b = by[keys[rng.randrange(len(keys))]]
                s += a
                k += b
            means.append(s / k)
        means.sort()
        out["boot_ci"] = (means[int(0.025 * n)], means[int(0.975 * n) - 1])
    return out


def gate(ticket_rs, ticket_days, diffs):
    """ADR 0016 section 6: (i) mean net R > 0 with CR1 t >= 2.0 by day; (ii) mean control difference > 0."""
    i = clustered(ticket_rs, ticket_days)
    d = [x for x in diffs if x is not None]
    ii = sum(d) / len(d) if d else None
    return {"i": i, "ii_mean": ii, "ii_n": len(d), "ii_dropped": len(diffs) - len(d),
            "pass_i": bool(i["mean"] is not None and i["mean"] > 0 and i["t"] is not None and i["t"] >= 2.0),
            "pass_ii": bool(ii is not None and ii > 0)}


def mde(n, sd=1.47):
    return 2.8416 * sd / math.sqrt(n) if n > 0 else None


# ---------------------------------------------------------------- the CLI guard

def authorized(ledger=LEDGER):
    if not Path(ledger).exists():
        return False
    for line in Path(ledger).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("phase") == "T2" and r.get("kind") == "authorize":
                return True
    return False


def run_real(set_path, ledger=LEDGER):
    if not authorized(ledger):
        raise Refused("refused: no T2 authorize entry in scalp_contracts/t_trials.jsonl (ADR 0016 section 7); "
                      "T1 runs the evaluator on synthetic fixtures only")
    raise NotImplementedError("the real-set loader is written in T2, under its prompt")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--set", required=True)
    a = ap.parse_args(argv)
    try:
        run_real(a.set)
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
