"""S2 real-set runs of the swing evaluator (ADR 0013 section 2, ADR 0019 sections 5-8, ADR 0020).

    python -m scalp_eval.swing_s2 run --window development     # reruns only for a documented defect
    python -m scalp_eval.swing_s2 run --window confirmation    # exactly once, refused a second time

Before simulating: the S trials ledger holds the S2 authorize line; every signed set verifies (signature, re-derived
digest, rules hash) and matches the digests in the authorize line; scalp_eval/ and scalp_engine/ have no uncommitted
change. A confirmation run also needs a logged development run, a passed re-simulation check recorded after it, and
no earlier confirmation start. No vendor request is made. Independent of scalp_engine (R4)."""
import argparse
import datetime as dt
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from scalp_eval import swing as W
from scalp_eval.costs import order_cost
from scalp_eval.data_access import day_bounds, read_frame
from scalp_eval.insider_study import MERGERS, clustered, data_root, iso_week

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "scalp_contracts" / "s_trials.jsonl"
TRUST_KEY = r"D:\scalp-env\keys\p3_signing.pub.pem"
SETS = ("H1", "S-a", "S-b", "controls")
WINDOWS = {"development": (dt.date(2022, 10, 1), dt.date(2024, 12, 31)),
           "confirmation": (dt.date(2025, 1, 1), dt.date(2026, 9, 17))}
ALL = (dt.datetime(2022, 1, 3, 5, tzinfo=UTC), dt.datetime(2026, 11, 2, 4, tzinfo=UTC))
CLOSE_RULE, OPEN_RULE = "close_minus_5m_v2", "open_plus_5m_v1"
TIER_HALF = {"T1": 0.00050 / 2, "T2": 0.00095 / 2}  # ADR 0019 section 5: half the G2 tier's median spread (S0 S6)
T1_DV = 50e6
o = pl.col


class Refused(RuntimeError):
    pass


# ---------------------------------------------------------------- ledger and preflight

def ledger_lines(ledger=LEDGER):
    p = Path(ledger)
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def append(rec, ledger=LEDGER):
    with open(ledger, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def authorize_line(lines):
    a = [r for r in lines if r.get("phase") == "S2" and r.get("kind") == "authorize"]
    return a[-1] if a else None


def git_state():
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    st = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "scalp_eval", "scalp_engine"],
                        capture_output=True, text=True, check=True).stdout
    return head, [x for x in st.splitlines() if x.strip()]


def preflight(window, lines, state=git_state):
    if window not in WINDOWS:
        raise Refused(f"unknown window {window}")
    auth = authorize_line(lines)
    if auth is None:
        raise Refused("refused: no S2 authorize line in scalp_contracts/s_trials.jsonl")
    head, dirty = state()
    if dirty:
        raise Refused(f"refused: uncommitted code under scalp_eval/ or scalp_engine/: {dirty[:5]}")
    if window == "confirmation":
        if any(r.get("phase") == "S2" and r.get("kind") == "confirmation_start" for r in lines):
            raise Refused("refused: the confirmation run has already started once (ADR 0019 section 8)")
        devs = [i for i, r in enumerate(lines) if r.get("phase") == "S2" and r.get("kind") == "result"
                and r.get("window") == "development"]
        if not devs:
            raise Refused("refused: no logged development run")
        if not any(r.get("phase") == "S2" and r.get("kind") == "resim_check" and r.get("pass") is True
                   for r in lines[devs[-1] + 1:]):
            raise Refused("refused: no passed re-simulation check after the last development run (S2 Stage 2)")
    return auth, head


# ---------------------------------------------------------------- inputs

def load_sets(window, auth, pubkey=TRUST_KEY):
    """Verified rows of the four sets (swing.verified_set: signature, re-derived digest, rules hash) whose digests
    equal the authorize line's."""
    out = {}
    for s in SETS:
        df, payload = W.verified_set(s, window, pubkey)
        if payload["sha256"] != auth["sets"][f"{s}|{window}"] or auth["rules_sha256"] != W.RULES_SHA256:
            raise Refused(f"{s}/{window}: digest or rules hash differs from the authorize line")
        out[s] = df.to_dicts()
    return out


def swing_market(symbols):
    """SwingMarket of the ticket symbols (raw SIP daily OHLC; corporate actions deduplicated by vendor id) and
    {(symbol, date): close * volume} for the liquidity tier."""
    root = data_root()
    sessions = read_frame(root / "calendar", *ALL, columns=["trading_date"])["trading_date"].unique().sort().to_list()
    b = read_frame(root / "raw_bars_1d", *ALL, columns=["symbol", "feed", "open", "high", "low", "close", "volume",
                                                        "ingested_at"], symbols=sorted(symbols))
    b = b.filter(o("feed") == "sip").with_columns(d=o("event_at").dt.convert_time_zone("America/New_York").dt.date())
    b = b.sort("ingested_at").unique(subset=["symbol", "d"], keep="last")
    sess = pl.DataFrame({"d": sessions, "i": list(range(len(sessions)))}, schema={"d": pl.Date, "i": pl.Int32})
    b = b.join(sess, on="d", how="inner").sort(["symbol", "i"])
    bars, dv = {}, {}
    for sym, ii, oo, hh, ll, cc, vv in b.group_by("symbol", maintain_order=True).agg("i", "open", "high", "low", "close", "volume").iter_rows():
        bars[sym] = (list(ii), [float(x) for x in oo], [float(x) for x in hh], [float(x) for x in ll], [float(x) for x in cc])
        dv[sym] = dict(zip(ii, (float(c) * float(v) for c, v in zip(cc, vv))))
    acts = read_frame(root / "corporate_actions", *ALL).sort("ingested_at").unique(subset=["vendor_id"], keep="last")
    splits, divs, mergers = {}, {}, {}
    for r in acts.iter_rows(named=True):
        raw = json.loads(r["raw"])
        day = r["event_at"].astimezone(ET).date()
        t = r["action_type"]
        if t in ("forward_splits", "reverse_splits"):
            a, n = raw.get("old_rate"), raw.get("new_rate")
            if raw.get("symbol") and isinstance(a, (int, float)) and isinstance(n, (int, float)) and a > 0:
                splits.setdefault(raw["symbol"], []).append((day, float(n) / float(a)))
        elif t == "stock_dividends":
            rate = raw.get("rate")
            if raw.get("symbol") and isinstance(rate, (int, float)) and rate > 0:
                splits.setdefault(raw["symbol"], []).append((day, float(rate)))
        elif t == "cash_dividends":
            rate = raw.get("rate")
            if raw.get("symbol") and isinstance(rate, (int, float)) and rate > 0:
                divs.setdefault(raw["symbol"], []).append((day, float(rate)))
        elif t in MERGERS and raw.get("acquiree_symbol"):
            ar, tr = raw.get("acquirer_rate"), raw.get("acquiree_rate")
            ratio = float(ar) / float(tr) if isinstance(ar, (int, float)) and isinstance(tr, (int, float)) and tr else 0.0
            cash = raw.get("rate") if t == "cash_mergers" else raw.get("cash_rate")
            mergers.setdefault(raw["acquiree_symbol"], []).append(
                (day, t, ratio, float(cash) if isinstance(cash, (int, float)) else 0.0, raw.get("acquirer_symbol")))
    return W.SwingMarket(sessions, bars, splits, divs, mergers), dv


def dv20(dv, sym, i):
    """20-session median close * volume ending session i, a session without a bar counting 0 (ADR 0019 section 3)."""
    d = dv.get(sym, {})
    vals = sorted(d.get(j, 0.0) for j in range(i - 19, i + 1))
    return (vals[9] + vals[10]) / 2.0


def tier(dv, sym, i):
    return "T1" if dv20(dv, sym, i) >= T1_DV else "T2"


def quotes_frames(symbols):
    q = read_frame(data_root() / "raw_quote_samples", *ALL, columns=["symbol", "status", "bid_price", "ask_price",
                                                                     "sample_rule", "ingested_at"], symbols=sorted(symbols))
    q = q.filter(o("sample_rule").is_in([CLOSE_RULE, OPEN_RULE]) & (o("status") == "ok"))
    q = q.with_columns(d=o("event_at").dt.convert_time_zone("America/New_York").dt.date()).sort("ingested_at")
    q = q.unique(subset=["symbol", "d", "sample_rule"], keep="last")
    close5, open935 = {}, {}
    for s, d, bid, ask, r in q.select("symbol", "d", "bid_price", "ask_price", "sample_rule").iter_rows():
        (close5 if r == CLOSE_RULE else open935)[(s, d)] = (bid, ask)
    return {t: W.Quotes(open935, close5, h) for t, h in TIER_HALF.items()}


def minute_pairs(tickets, mkt):
    """(symbol, session) pairs of every ticket's expiry sessions t + 1 .. t + 3 (A5 fallback)."""
    out = {}
    for t in tickets:
        i0 = mkt.idx[t["signal_date"]]
        for i in range(i0 + 1, min(i0 + 4, len(mkt.sessions))):
            out.setdefault(mkt.sessions[i], set()).add(t["symbol"])
    return out


def load_minutes(pairs):
    """{(symbol, date): [("HH:MM", o, h, l, c)]} SIP regular-session minutes of the given pairs."""
    out = {}
    for d, syms in sorted(pairs.items()):
        df = read_frame(data_root() / "raw_bars_1m", *day_bounds(d), columns=["symbol", "open", "high", "low", "close",
                                                                              "feed", "ingested_at"], symbols=sorted(syms))
        if df.height == 0:
            continue
        df = df.filter(o("feed") == "sip").with_columns(m=o("event_at").dt.convert_time_zone("America/New_York").dt.strftime("%H:%M"))
        df = df.filter((o("m") >= "09:30") & (o("m") <= "15:59"))
        df = df.sort("ingested_at").unique(subset=["symbol", "event_at"], keep="last").sort(["symbol", "event_at"])
        for s, m, op, hi, lo, cl in df.select("symbol", "m", "open", "high", "low", "close").iter_rows():
            out.setdefault((s, d), []).append((m, op, hi, lo, cl))
    return out


def minutes_for(ticket, mkt, mins):
    """A5 fallback: minute bars only on the ticket's own expiry sessions t + 1 .. t + 3; None elsewhere (stop first)."""
    i0 = mkt.idx[ticket["signal_date"]]
    allowed = set(mkt.sessions[i0 + 1:i0 + 4])
    return lambda sym, d: mins.get((sym, d)) if d in allowed else None


def late_8k_flags(h1, sessions):
    """Reported only: H1 tickets with an Item 2.02 8-K header acceptance in (t 20:00 ET, t + 4 sessions] (A2 leak)."""
    f = read_frame(data_root() / "issuer_filings", *ALL, columns=["cik", "form", "items", "accession"])
    f = f.filter(o("form").is_in(["8-K", "8-K/A"]) & o("items").fill_null("").str.contains(r"2\.02"))
    a = read_frame(data_root() / "filing_acceptance", *ALL, columns=["accession", "status", "method"])
    a = a.filter((o("method") == "header") & (o("status") == "ok")).select("accession", at=o("event_at"))
    times = {}
    for cik, t in f.join(a, on="accession").select("cik", "at").iter_rows():
        times.setdefault(cik, []).append(t)
    idx = {d: i for i, d in enumerate(sessions)}
    out = set()
    for t in h1:
        i = idx[t["signal_date"]]
        lo = dt.datetime.combine(t["signal_date"], dt.time(20), ET).astimezone(UTC)
        hi = dt.datetime.combine(sessions[min(i + 4, len(sessions) - 1)], dt.time(23, 59), ET).astimezone(UTC)
        if any(lo < x <= hi for x in times.get(t["cik"], [])):
            out.add(t["ticket_id"])
    return out


# ---------------------------------------------------------------- statistics

def filled_stats(tickets, res, mkt, boot=True):
    filled = [(t, res[t["ticket_id"]]) for t in tickets if res[t["ticket_id"]]["filled"]]
    vals = [r["net_r"] for _, r in filled]
    weeks = [iso_week(mkt.sessions[r["fill_i"]]) for _, r in filled]
    s = W.summarize(vals, weeks) if boot and len(vals) > 1 else (clustered(vals, weeks) if vals else {"n": 0})
    gross = [(r["pnl"] + r["cost"]) / W.RISK for _, r in filled]
    cases = {}
    for _, r in filled:
        cases[r["case"]] = cases.get(r["case"], 0) + 1
    out = {"issued": len(tickets), "filled": len(filled), "fill_rate": len(filled) / len(tickets) if tickets else None,
           **{k: v for k, v in s.items() if k in ("n", "mean", "se", "t", "G", "ci95", "mde", "hit_rate")},
           "mean_gross_r": sum(gross) / len(gross) if gross else None,
           "mean_cost_r": sum(r["cost"] for _, r in filled) / W.RISK / len(filled) if filled else None,
           "exit_cases": cases, "unfilled_reasons": {}}
    for t in tickets:
        r = res[t["ticket_id"]]
        if not r["filled"]:
            out["unfilled_reasons"][r["reason"]] = out["unfilled_reasons"].get(r["reason"], 0) + 1
    by_year = {}
    for t, r in filled:
        by_year.setdefault(t["signal_date"].year, []).append(r["net_r"])
    out["by_year"] = {y: {"n": len(v), "mean": sum(v) / len(v)} for y, v in sorted(by_year.items())}
    if vals:
        k = math.ceil(0.01 * len(vals))
        keep = sorted(range(len(vals)), key=lambda i: -vals[i])[k:]
        c = clustered([vals[i] for i in keep], [weeks[i] for i in keep]) if len(keep) > 1 else {}
        out["without_top1pct"] = {"n": len(keep), "mean": c.get("mean"), "t": c.get("t")}
    return out


def issued_difference(h1, controls_of, res, mkt):
    """Gate (ii), ADR 0020: X = net R if filled else 0; C = mean over the ticket's controls of the same; clustered by
    the ISO week of t. -> (stats, tickets without controls)."""
    vals, weeks, none = [], [], 0
    for t in h1:
        cs = controls_of.get(t["ticket_id"], [])
        if not cs:
            none += 1
            continue
        vals.append(res[t["ticket_id"]]["net_r"] - sum(res[c["ticket_id"]]["net_r"] for c in cs) / len(cs))
        weeks.append(iso_week(t["signal_date"]))
    s = W.summarize(vals, weeks) if len(vals) > 1 else {"n": len(vals)}
    return {k: v for k, v in s.items() if k in ("n", "mean", "se", "t", "G", "ci95")}, none


def filled_difference(h1, controls_of, res):
    vals, weeks, dropped = [], [], 0
    for t in h1:
        r = res[t["ticket_id"]]
        if not r["filled"]:
            continue
        cs = [res[c["ticket_id"]]["net_r"] for c in controls_of.get(t["ticket_id"], []) if res[c["ticket_id"]]["filled"]]
        if not cs:
            dropped += 1
            continue
        vals.append(r["net_r"] - sum(cs) / len(cs))
        weeks.append(iso_week(t["signal_date"]))
    c = clustered(vals, weeks) if len(vals) > 1 else {}
    return {"n": len(vals), "mean": c.get("mean"), "t": c.get("t"), "dropped_no_filled_control": dropped}


def spy_total_return(mkt, i0, i1):
    """SPY close i0 -> close i1 with cash dividends added (not reinvested)."""
    a, b = mkt.bar("SPY", i0), mkt.bar("SPY", i1)
    if not (a and b):
        return None
    d0, d1 = mkt.sessions[i0], mkt.sessions[i1]
    div = sum(x for ex, x in mkt.divs.get("SPY", ()) if d0 < ex <= d1)
    return (b[1] + div) / a[1] - 1.0


# ---------------------------------------------------------------- the run

def run(window, ledger=LEDGER):
    lines = ledger_lines(ledger)
    auth, head = preflight(window, lines)
    sets = load_sets(window, auth)
    k = 1 + sum(1 for r in lines if r.get("phase") == "S2" and r.get("kind") == "start" and r.get("window") == window)
    start = {"phase": "S2", "kind": "start", "window": window, "run": k, "code_commit": head,
             "at": dt.datetime.now(UTC).isoformat()}
    if window == "confirmation":
        append({**start, "kind": "confirmation_start"}, ledger)
    append(start, ledger)
    every = [t for s in SETS for t in sets[s]]
    mkt, dv = swing_market({t["symbol"] for t in every} | {"SPY"})
    quotes = quotes_frames({t["symbol"] for t in every})
    mins = load_minutes(minute_pairs([t for t in every if t["family"] == "gaphold"], mkt))

    def run_set(tickets):
        out = {}
        for t in tickets:
            q = quotes[tier(dv, t["symbol"], mkt.idx[t["signal_date"]])]
            out[t["ticket_id"]] = W.run_ticket(t, mkt, minutes_for(t, mkt, mins), q,
                                               stop_first_only=t["family"] == "nday")
        return out
    res = {s: run_set(sets[s]) for s in SETS}
    h1 = sets["H1"]
    ctrl_res = res["controls"]
    controls_of = {}
    for c in sets["controls"]:
        controls_of.setdefault(c["parent_id"], []).append(c)
    both = {**res["H1"], **ctrl_res}
    out = {"window": window, "run": k, "code_commit": head, "authorize": auth.get("adr0020_commit"),
           "H1": filled_stats(h1, res["H1"], mkt), "S-a": filled_stats(sets["S-a"], res["S-a"], mkt),
           "S-b": filled_stats(sets["S-b"], res["S-b"], mkt), "controls": filled_stats(sets["controls"], ctrl_res, mkt, boot=False)}
    gi, none = issued_difference(h1, controls_of, both, mkt)
    out["gate_ii_issued"] = {**gi, "tickets_without_controls": none}
    out["gate_ii_filled_basis"] = filled_difference(h1, controls_of, both)
    out["control_shortfall"] = sum(1 for t in h1 if len(controls_of.get(t["ticket_id"], [])) < 3)
    late = late_8k_flags(h1, mkt.sessions)
    out["late_8k_tickets"] = len(late)
    out["H1_without_late_8k"] = filled_stats([t for t in h1 if t["ticket_id"] not in late], res["H1"], mkt, boot=False)
    tiers = {t["ticket_id"]: tier(dv, t["symbol"], mkt.idx[t["signal_date"]]) for t in h1}
    out["H1_by_tier"] = {g: filled_stats([t for t in h1 if tiers[t["ticket_id"]] == g], res["H1"], mkt, boot=False)
                         for g in ("T1", "T2")}
    bk = W.simulate_book(h1, mkt, None, None, minutes_for=lambda t: minutes_for(t, mkt, mins),
                         quotes_for=lambda t: quotes[tier(dv, t["symbol"], mkt.idx[t["signal_date"]])])
    if bk.get("first_i") is not None:
        bk["spy_total_return"] = spy_total_return(mkt, bk["first_i"], bk["last_i"])
        bk["first_fill"], bk["last_exit"] = mkt.sessions[bk["first_i"]].isoformat(), mkt.sessions[bk["last_i"]].isoformat()
    out["book"] = bk
    if window == "confirmation":
        g = out["H1"]
        pass_i = bool(g.get("mean") is not None and g["mean"] > 0 and g.get("t") is not None and g["t"] >= 2.0)
        pass_ii = bool(gi.get("mean") is not None and gi["mean"] > 0)
        out["gate"] = {"pass_i": pass_i, "pass_ii": pass_ii}
        out["verdict"] = "provisional GO" if pass_i and pass_ii else ("NO-GO (a)" if not pass_i else "NO-GO (b)")
    path = data_root() / "derived" / "s2" / f"{window}_run{k}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8")
    per = {f"{s}|{tid}": r for s in SETS for tid, r in res[s].items()}
    (path.parent / f"{window}_run{k}_tickets.json").write_text(json.dumps(per, default=str) + "\n", encoding="utf-8")
    append({"phase": "S2", "kind": "result", "window": window, "run": k, "code_commit": head,
            "at": dt.datetime.now(UTC).isoformat(), "results": str(path.relative_to(data_root())),
            "results_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "h1_filled": out["H1"]["filled"],
            "h1_mean_net_r": out["H1"].get("mean"), "verdict": out.get("verdict")}, ledger)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_eval.swing_s2")
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--window", required=True, choices=sorted(WINDOWS))
    a = ap.parse_args(argv)
    try:
        out = run(a.window)
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps({"window": out["window"], "run": out["run"], "h1": {k: out["H1"].get(k) for k in ("filled", "mean", "t")},
                      "verdict": out.get("verdict")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
