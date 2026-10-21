"""T2 real-set runs of the intraday ticket evaluator (ADR 0016 sections 3-7, ADR 0017).

    python -m scalp_eval.intraday_t2 run --window development     # reruns only for a documented defect
    python -m scalp_eval.intraday_t2 run --window confirmation    # exactly once, refused a second time

Before simulating: the trials ledger holds the T2 authorize line; every signed set verifies under the trusted key,
re-derives to its signed digest and matches the hashes in the authorize line; the rules file hashes to the authorized
rules hash; scalp_eval/ and scalp_engine/ have no uncommitted change. A confirmation run also needs a logged
development run whose ORB 5 net mean lies in its band (ADR 0017 item 2), and no earlier confirmation start.
Independent of scalp_engine (R4): files are read and hashed, never imported."""
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

from scalp_eval import intraday_eval as E
from scalp_eval.data_access import day_bounds, read_frame, read_ticket_set
from scalp_eval.insider_study import clustered
from scalp_eval.verify import verify_artifact

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "scalp_contracts" / "t_trials.jsonl"
RULES_FILE = REPO / "scalp_engine" / "intraday_rules.py"
TRUST_KEY = Path(r"D:\scalp-env\keys\p3_signing.pub.pem")
SETS = ("H1", "CONTROLS", "ORB5", "H1_SIPSIG")
WINDOWS = {"development": (dt.date(2022, 2, 1), dt.date(2024, 12, 31)),
           "confirmation": (dt.date(2025, 1, 2), dt.date(2026, 10, 16))}
ORB_BAND = (-0.36, 0.39)
H1_SETUPS = ("vwap_hammer", "vwap_engulf", "flag")
GRID_RULE = "t1_grid_v1"


def data_root():
    import os
    return Path(os.environ.get("SCALP_DATA_ROOT") or r"D:\scalp-data")


# ---------------------------------------------------------------- the ledger and the preflight

def ledger_lines(ledger=LEDGER):
    p = Path(ledger)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def append(rec, ledger=LEDGER):
    with open(ledger, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def authorize_line(lines):
    auth = [r for r in lines if r.get("phase") == "T2" and r.get("kind") == "authorize"]
    return auth[-1] if auth else None


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def git_state():
    """(HEAD commit, uncommitted paths under scalp_eval/ and scalp_engine/)."""
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    st = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "scalp_eval", "scalp_engine"],
                        capture_output=True, text=True, check=True).stdout
    return head, [x for x in st.splitlines() if x.strip()]


def preflight(window, lines, state=git_state):
    """-> (authorize line, HEAD) or raise E.Refused."""
    if window not in WINDOWS:
        raise E.Refused(f"unknown window {window}")
    auth = authorize_line(lines)
    if auth is None:
        raise E.Refused("refused: no T2 authorize line in scalp_contracts/t_trials.jsonl")
    head, dirty = state()
    if dirty:
        raise E.Refused(f"refused: uncommitted code under scalp_eval/ or scalp_engine/: {dirty[:5]}")
    if window == "confirmation":
        if any(r.get("phase") == "T2" and r.get("kind") == "confirmation_start" for r in lines):
            raise E.Refused("refused: the confirmation run has already started once (ADR 0016 section 7)")
        dev = [r for r in lines if r.get("phase") == "T2" and r.get("kind") == "result" and r.get("window") == "development"]
        if not dev:
            raise E.Refused("refused: no logged development run")
        if not dev[-1].get("orb5_in_band"):
            raise E.Refused("refused: the last development run's ORB 5 net mean is outside its band (ADR 0017 item 2)")
    return auth, head


def load_set(window, name, auth, key):
    """Verified rows of one signed set: signature, digest re-derived, hashes equal to the authorize line."""
    env = json.loads((data_root() / "derived" / "t1" / "sets" / window / f"{name}.signed.json").read_text(encoding="utf-8"))
    p = verify_artifact(env, [key])
    if (p["window"], p["set"]) != (window, name):
        raise E.Refused(f"set label mismatch: {p['window']}/{p['set']}")
    if p["sha256"] != auth["sets"][f"{window}|{name}"]:
        raise E.Refused(f"{window}/{name}: signed digest differs from the authorize line")
    if p["rules_sha256"] != auth["rules_sha256"]:
        raise E.Refused(f"{window}/{name}: rules hash differs from the authorize line")
    df = read_ticket_set(data_root() / p["table"])
    if canonical_sha256(df) != p["sha256"] or df.height != p["rows"]:
        raise E.Refused(f"{window}/{name}: rows do not re-derive to the signed digest")
    return df.to_dicts()


def canonical_sha256(df):
    """The set digest, re-derived here (R4): sorted canonical JSON lines."""
    lines = sorted(json.dumps({k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in sorted(r.items())},
                              sort_keys=True, separators=(",", ":")) for r in df.to_dicts())
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


# ---------------------------------------------------------------- market inputs (SIP minutes, NBBO grid, calendar)

def session_info(first, last):
    """{date: time exit 'HH:MM'} (15:55, or close - 5 minutes on early closes) and the session list."""
    df = read_frame(data_root() / "calendar", *(day_bounds(first)[0], day_bounds(last)[1]))
    out = {}
    for r in df.select("trading_date", "close_at").iter_rows():
        c = r[1].astimezone(ET)
        ex = min(dt.time(15, 55), (dt.datetime.combine(r[0], c.time()) - dt.timedelta(minutes=5)).time())
        out[r[0]] = ex.strftime("%H:%M")
    return out


def day_market(day, symbols):
    """bars = {symbol: [("HH:MM", o, h, l, c, v)]} SIP 09:30-15:59; quotes = {symbol: {"HH:MM": (bid, ask) or None}}."""
    lo, hi = day_bounds(day)
    b = read_frame(data_root() / "raw_bars_1m", lo, hi, columns=["symbol", "open", "high", "low", "close", "volume",
                                                                 "feed", "ingested_at"], symbols=sorted(symbols))
    bars = {}
    if b.height:
        b = b.filter(pl.col("feed") == "sip").with_columns(
            m=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.strftime("%H:%M"))
        b = b.filter((pl.col("m") >= "09:30") & (pl.col("m") <= "15:59"))
        b = b.sort("ingested_at").unique(subset=["symbol", "event_at"], keep="last").sort(["symbol", "event_at"])
        for s, m, o, h, l, c, v in b.select("symbol", "m", "open", "high", "low", "close", "volume").iter_rows():
            bars.setdefault(s, []).append((m, o, h, l, c, v))
    q = read_frame(data_root() / "raw_quote_samples", lo, hi,
                   columns=["symbol", "status", "bid_price", "ask_price", "sample_rule"], symbols=sorted(symbols))
    quotes = {}
    if q.height:
        q = q.with_columns(m=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.strftime("%H:%M"),
                           pref=(pl.col("sample_rule") != GRID_RULE).cast(pl.Int8))
        for s, m, st, bid, ask, _ in q.sort(["symbol", "m", "pref"]).select(
                "symbol", "m", "status", "bid_price", "ask_price", "pref").iter_rows():
            d = quotes.setdefault(s, {})
            if m in d and d[m] is not None:
                continue  # the grid sample first, then any other rule's sample at the same time
            d[m] = (bid, ask) if st == "ok" and bid is not None and ask is not None else d.get(m)
    return bars, quotes


def simulate_rows(rows, sessions, shares_key="shares"):
    """{ticket_id: result} for set rows, day by day."""
    by_day = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    out = {}
    for d in sorted(by_day):
        rs = by_day[d]
        bars, quotes = day_market(d, {r["symbol"] for r in rs})
        for r in rs:
            tk = {k: r[k] for k in ("trigger", "limit", "stop", "target", "issued_at")}
            tk["shares"] = r[shares_key]
            out[r["ticket_id"]] = E.simulate(tk, bars.get(r["symbol"], []), quotes.get(r["symbol"], {}),
                                             time_exit=sessions[d], day=d)
    return out


# ---------------------------------------------------------------- statistics

def stats(values, days, boot=True):
    if not values:
        return {"n": 0}
    out = E.inference(values, days) if boot else clustered(values, days)
    out = {k: v for k, v in out.items()}
    if out.get("se") is not None:
        out["mde"] = 2.8416 * out["se"]
    return out


def without_top(values, days, share=0.01):
    k = math.ceil(share * len(values))
    order = sorted(range(len(values)), key=lambda i: -values[i])[k:]
    return clustered([values[i] for i in order], [days[i] for i in order])


def summarize(rows, res):
    """Filled-ticket statistics with gross and cost R, plus the status counts."""
    filled = [(r, res[r["ticket_id"]]) for r in rows if res[r["ticket_id"]]["status"] == "filled"]
    counts = {}
    for r in rows:
        counts[res[r["ticket_id"]]["status"]] = counts.get(res[r["ticket_id"]]["status"], 0) + 1
    nets = [x["net_r"] for _, x in filled]
    days = [r["date"].isoformat() for r, _ in filled]
    gross = [x["gross_r"] for _, x in filled]
    out = {"issued": len(rows), "status": counts, "fill_rate": (len(filled) / len(rows)) if rows else None,
           "net": stats(nets, days), "mean_gross_r": sum(gross) / len(gross) if gross else None,
           "mean_cost_r": (sum(gross) - sum(nets)) / len(nets) if nets else None,
           "win_rate": sum(1 for v in nets if v > 0) / len(nets) if nets else None,
           "without_top1pct": without_top(nets, days) if nets else None}
    by_year = {}
    for (r, x) in filled:
        by_year.setdefault(r["date"].year, []).append(x["net_r"])
    out["by_year"] = {y: {"n": len(v), "mean": sum(v) / len(v)} for y, v in sorted(by_year.items())}
    return out


def issued_basis(h1, ctrl, res):
    """ADR 0017 item 2: for each issued H1 ticket with >= 1 control, X = net R if filled else 0, C = the mean over
    its controls of (net R if filled else 0); -> (diffs, days, tickets without controls)."""
    by_parent = {}
    for c in ctrl:
        by_parent.setdefault(c["parent_id"], []).append(c)

    def val(tid):
        x = res[tid]
        return x["net_r"] if x["status"] == "filled" else 0.0
    diffs, days, none = [], [], 0
    for t in h1:
        cs = by_parent.get(t["ticket_id"], [])
        if not cs:
            none += 1
            continue
        diffs.append(val(t["ticket_id"]) - sum(val(c["ticket_id"]) for c in cs) / len(cs))
        days.append(t["date"].isoformat())
    return diffs, days, none


def filled_basis(h1, ctrl, res):
    """The T1 version (reported only): filled H1 tickets with >= 1 filled control."""
    by_parent = {}
    for c in ctrl:
        by_parent.setdefault(c["parent_id"], []).append(c)
    diffs, days, dropped = [], [], 0
    for t in h1:
        x = res[t["ticket_id"]]
        if x["status"] != "filled":
            continue
        d = E.control_difference(x["net_r"], [res[c["ticket_id"]]["net_r"] if res[c["ticket_id"]]["status"] == "filled"
                                              else None for c in by_parent.get(t["ticket_id"], [])])
        if d is None:
            dropped += 1
            continue
        diffs.append(d)
        days.append(t["date"].isoformat())
    return diffs, days, dropped


def spy_return(first, last):
    df = read_frame(data_root() / "raw_bars_1d", day_bounds(first - dt.timedelta(days=7))[0], day_bounds(last)[1],
                    columns=["symbol", "close", "feed", "ingested_at"], symbols=["SPY"])
    df = df.filter(pl.col("feed") == "sip").with_columns(d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date())
    df = df.sort("ingested_at").unique(subset=["d"], keep="last").sort("d")
    before = df.filter(pl.col("d") < first)["close"]
    end = df.filter(pl.col("d") <= last)["close"]
    return float(end[-1]) / float(before[-1]) - 1


# ---------------------------------------------------------------- the run

def run(window, ledger=LEDGER):
    lines = ledger_lines(ledger)
    auth, head = preflight(window, lines)
    if file_sha256(RULES_FILE) != auth["rules_sha256"]:
        raise E.Refused("refused: scalp_engine/intraday_rules.py does not hash to the authorized rules hash")
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_public_key(TRUST_KEY.read_bytes())
    sets = {n: load_set(window, n, auth, key) for n in SETS}
    k = 1 + sum(1 for r in lines if r.get("phase") == "T2" and r.get("kind") == "start" and r.get("window") == window)
    start = {"phase": "T2", "kind": "start", "window": window, "run": k, "code_commit": head,
             "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if window == "confirmation":
        append({**start, "kind": "confirmation_start"}, ledger)
    append(start, ledger)
    first, last = WINDOWS[window]
    sessions = session_info(first, last)
    rs = {n: simulate_rows(sets[n], sessions) for n in SETS}  # per set: H1_SIPSIG ids repeat H1 ids
    cap20 = simulate_rows(sets["H1"], sessions, shares_key="shares_cap20")
    h1, ctrl, orb, sip = sets["H1"], sets["CONTROLS"], sets["ORB5"], sets["H1_SIPSIG"]
    res = {**rs["H1"], **rs["CONTROLS"]}  # H1 and control ids are distinct
    out = {"window": window, "run": k, "code_commit": head, "authorize_commit": auth.get("adr0017_commit"),
           "H1": summarize(h1, rs["H1"]), "CONTROLS": summarize(ctrl, rs["CONTROLS"]), "ORB5": summarize(orb, rs["ORB5"]),
           "H1_SIPSIG": summarize(sip, rs["H1_SIPSIG"]), "H1_cap20": summarize(h1, cap20)}
    out["per_setup"] = {s: summarize([r for r in h1 if r["setup"] == s], res) for s in H1_SETUPS}
    out["per_class"] = {c: summarize([r for r in h1 if r.get("asset_class") == c], res) for c in ("stock", "etp")}
    diffs, days, none = issued_basis(h1, ctrl, res)
    out["gate_ii_issued"] = {**stats(diffs, days), "tickets_without_controls": none}
    fd, fdays, dropped = filled_basis(h1, ctrl, res)
    out["gate_ii_filled_basis"] = {**stats(fd, fdays), "dropped_no_filled_control": dropped}
    n_ctrl = {}
    for c in ctrl:
        n_ctrl[c["parent_id"]] = n_ctrl.get(c["parent_id"], 0) + 1
    shortfall = sum(1 for t in h1 if n_ctrl.get(t["ticket_id"], 0) < 3)
    out["control_shortfall_tickets"] = shortfall
    orb_net = out["ORB5"]["net"].get("mean")
    out["orb5_in_band"] = orb_net is not None and ORB_BAND[0] <= orb_net <= ORB_BAND[1]
    trades = [{"ticket_id": t["ticket_id"], "date": t["date"], "fill_minute": res[t["ticket_id"]]["fill_minute"],
               "exit_minute": res[t["ticket_id"]]["exit_minute"], "entry": res[t["ticket_id"]]["entry"],
               "exit": res[t["ticket_id"]]["exit"], "shares": res[t["ticket_id"]]["shares"]}
              for t in h1 if res[t["ticket_id"]]["status"] == "filled"]
    bk = E.book(trades, sorted(sessions))
    out["book"] = {"final": bk["final"], "return": bk["return"], "taken": len(bk["taken"]),
                   "blocked_settlement": bk["blocked_settlement"], "blocked_position": bk["blocked_position"],
                   "spy_return": spy_return(first, last)}
    if window == "confirmation":
        out["gate"] = E.gate([res[t["ticket_id"]]["net_r"] for t in h1 if res[t["ticket_id"]]["status"] == "filled"],
                             [t["date"].isoformat() for t in h1 if res[t["ticket_id"]]["status"] == "filled"], [])
        gi = out["gate"]["pass_i"]
        gii = bool(out["gate_ii_issued"].get("mean") is not None and out["gate_ii_issued"]["mean"] > 0)
        out["gate"]["pass_ii_issued"] = gii
        out["verdict"] = "provisional GO" if gi and gii else ("NO-GO (a)" if not gi else "NO-GO (b)")
    path = data_root() / "derived" / "t2" / f"{window}_run{k}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8")
    per = {f"{n}|{t['ticket_id']}": rs[n][t["ticket_id"]] for n in SETS for t in sets[n]}
    (path.parent / f"{window}_run{k}_tickets.json").write_text(json.dumps(per, default=str) + "\n", encoding="utf-8")
    append({"phase": "T2", "kind": "result", "window": window, "run": k, "code_commit": head,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(), "results": str(path.relative_to(data_root())),
            "results_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "orb5_in_band": out["orb5_in_band"],
            "h1_filled": out["H1"]["net"].get("n"), "h1_mean_net_r": out["H1"]["net"].get("mean"),
            "verdict": out.get("verdict")}, ledger)
    return out


def check(window, sample=3):
    """No outcome: verify every signed set of the window under the trusted key and its own signed digest, and load
    the market inputs (SIP minutes, NBBO grid, time exits) of a few sessions. Nothing is simulated."""
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_public_key(TRUST_KEY.read_bytes())
    out, rows = {"rules_file_sha256": file_sha256(RULES_FILE)}, {}
    for n in SETS:
        env = json.loads((data_root() / "derived" / "t1" / "sets" / window / f"{n}.signed.json").read_text(encoding="utf-8"))
        p = verify_artifact(env, [key])
        df = read_ticket_set(data_root() / p["table"])
        out[n] = {"rows": df.height, "sha256": p["sha256"], "rederived": canonical_sha256(df) == p["sha256"],
                  "rules_sha256": p["rules_sha256"]}
        rows[n] = df
    first, last = WINDOWS[window]
    sessions = session_info(first, last)
    days = sorted(set(rows["H1"]["date"].to_list()))
    pick = [days[0], days[len(days) // 2], days[-1]][:sample]
    for d in pick:
        syms = set(rows["H1"].filter(pl.col("date") == d)["symbol"].to_list())
        bars, quotes = day_market(d, syms)
        out[f"market {d}"] = {"symbols": len(syms), "with_bars": sum(1 for s in syms if bars.get(s)),
                              "minutes": sum(len(v) for v in bars.values()),
                              "grid_times_ok": sum(1 for s in syms for v in quotes.get(s, {}).values() if v),
                              "time_exit": sessions[d]}
    out["spy_return_checked"] = spy_return(first, last) is not None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "check"])
    ap.add_argument("--window", required=True, choices=sorted(WINDOWS))
    a = ap.parse_args(argv)
    if a.cmd == "check":
        print(json.dumps(check(a.window), indent=1, default=str))
        return 0
    try:
        out = run(a.window)
    except E.Refused as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps({k: out[k] for k in ("window", "run", "orb5_in_band")} | {"verdict": out.get("verdict")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
