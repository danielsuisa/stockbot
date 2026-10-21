"""T1 stage 3 (ADR 0016 sections 1-6): replay the live screener's stage 1 and 2 per session, build the H1 tickets,
the ORB 5 pipeline-check tickets, the SIP-signal variant and the controls, sign every set; parity with the live
bot's journal; the leakage checks. No outcome is computed here: nothing after a ticket's issue time is read.

    python -m scalp_engine.t1_replay build [--from D --to D]   # tickets + controls + counts, signed per window
    python -m scalp_engine.t1_replay leakage [--sample N]       # perturbation check on real sessions
    python -m scalp_engine.t1_replay parity --journal FILE --date D [--equity E --risk-share P]"""
import argparse
import copy
import datetime as dt
import hashlib
import json
import random
import re
import sys
from collections import Counter

import polars as pl

from scalp_engine import storage
from scalp_engine import intraday_rules as R
from scalp_engine import t1_data as T

H1_SETUPS = ("vwap_hammer", "vwap_engulf", "flag")
MORNING = R.scan_times("morning")
K_CONTROLS = 3
CONTROL_TAG = "t1-control-v1"
RISK_USD, EQUITY_USD, CAP20 = 25.0, 2500.0, 0.20
RULE_VERSION = "t1-v1"
KEY_PATH = r"D:\scalp-env\keys\p3_signing.pem"
EPS = 1e-9


def window_of(d):
    if T.DEV[0] <= d <= T.DEV[1]:
        return "development"
    if T.CONF[0] <= d <= T.CONF[1]:
        return "confirmation"
    if T.TOPUP_FIRST <= d <= T.TOPUP_CAP:
        return "topup"
    return None


# ---------------------------------------------------------------- sizing (ADR 0016 section 4)

def shares_for(trigger, stop, equity=EQUITY_USD, cap_share=1.0, risk=RISK_USD):
    """floor(min(risk / (trigger - stop), cap_share * equity / trigger)); 0 = a 0-share skip."""
    r = trigger - stop
    if r <= 0 or trigger <= 0:
        return 0
    return int(min(risk / r, cap_share * equity / trigger) + EPS)


# ---------------------------------------------------------------- the replay core (pure)

def replay_core(day, watch, bars, rel_fn, windows=("morning",), risk=R.STUDY_RISK, max_pos=None, audit=None):
    """The bot's run_scan loop over the scan closes of `windows`. Each scan sees only the minutes before its close
    (the bot reads them at close + 25 s; the minute starting at the close is still forming and is never in a
    completed candle). audit, if a list, receives (scan close, latest minute used) for the available_at check."""
    state = R.fresh_state(day)
    state["watch"] = copy.deepcopy(watch)
    for w in windows:
        for now in R.scan_times(w):
            seen = {s: [b for b in bs if b[0] < now] for s, bs in bars.items()}
            if audit is not None:
                last = max((b[0] for bs in seen.values() for b in bs), default=None)
                audit.append((now, last))
            R.run_scan_from(state, now, seen, rel_fn, risk, max_pos)
    return state


def signal_candles(state):
    """{symbol: set(scan closes)} at which any setup was detected (the controls never use them)."""
    out = {}
    for now, sym, _ in state["detected"]:
        out.setdefault(sym, set()).add(now)
    return out


def control_seed(ticket_id):
    return int(hashlib.sha256(f"{ticket_id}|{CONTROL_TAG}".encode()).hexdigest(), 16)


def draw_controls(ticket_id, eligible, k=K_CONTROLS):
    """K closes drawn uniformly without replacement from the sorted eligible closes (fewer when fewer exist)."""
    pool = sorted(eligible)
    return sorted(random.Random(control_seed(ticket_id)).sample(pool, min(k, len(pool))))


def candle_at(bars1, close):
    """The completed 5-minute candle ending at `close` from minutes before it, or None (an empty slot)."""
    b5 = R.candles([b for b in bars1 if b[0] < close], close)
    return b5[-1] if b5 and b5[-1]["end"] == close else None


ETP_NAME = re.compile(
    r"\b(ETFs?|ETNs?|ETP|Funds?|Index|iShares|SPDR|ProShares|Direxion|Select Sector|UltraPro|UltraShort|"
    r"2X|3X|Daily Target|Exchange[- ]Traded|Trust Units|Trust,? Series)\b", re.I)


def asset_class(exchange, name):
    """'etp' or 'stock' for the reported-only breakdown (ADR 0017 item 1): an NYSE Arca listing, or a name with
    an exchange-traded-product keyword (P1's classifier words; the bot's fund-word filter lets 'Fund,' and
    'ProShares' through). A name-less survivorship ticker counts as a stock."""
    return "etp" if exchange == "ARCA" or (name and ETP_NAME.search(name)) else "stock"


def asset_classes():
    """{symbol: 'etp' | 'stock'} for the universe, from the store's asset snapshot."""
    from scalp_engine import ingest
    recs = {}
    for a in ingest.latest_assets():
        recs.setdefault(a["symbol"], a)
    return {s: (asset_class(recs[s].get("exchange"), recs[s].get("name")) if s in recs else "stock")
            for s in T.universe_spans()}


CLASSES = {}  # symbol -> 'etp' | 'stock', filled by build(); 'stock' if unknown


def ticket_row(day, t, set_name):
    trigger, stop = t["entry_stop"], t["stop_loss"]
    tid = f"{day.isoformat()}|{t['symbol']}|{t['setup']}|{t['at']}"
    return {"ticket_id": tid, "set": set_name, "date": day, "symbol": t["symbol"], "setup": t["setup"],
            "issued_at": t["at"], "candle_start": t["candle"], "trigger": trigger, "limit": t["limit"], "stop": stop,
            "target": t["target"], "r_bot": t["R"], "r_floored": t["r_floored"], "atr": t["atr"],
            "shares": shares_for(trigger, stop), "shares_cap20": shares_for(trigger, stop, cap_share=CAP20),
            "score": t["score"], "gap": t["gap"], "pre_rel": t["pre_rel"], "open_relvol": t.get("open_relvol"),
            "last": t["last"], "vwap": t.get("vwap"), "parent_id": None, "draw_pool": None, "rules_sha256": R.rules_sha256(),
            "asset_class": CLASSES.get(t["symbol"], "stock")}


def control_rows(day, parent, bars1, signals):
    """Controls for one H1 ticket row (ADR 0016 section 5): same stock and day, a drawn morning close that is not a
    signal candle and has a candle; trigger = its high + 1 tick; the parent's R, target multiple and limit offset."""
    eligible = [c for c in MORNING if c not in signals and candle_at(bars1, c) is not None]
    out = []
    risk_d = round(parent["trigger"] - parent["stop"], 2)
    for k, close in enumerate(draw_controls(parent["ticket_id"], eligible)):
        cd = candle_at(bars1, close)
        trigger = R.levels.px(cd["h"] + R.TICK)
        stop, target = R.levels.px(trigger - risk_d), R.levels.px(trigger + R.TARGET_R * risk_d)
        limit = R.levels.px(trigger + R.LIMIT_ATR * parent["atr"])
        out.append({**parent, "ticket_id": f"{parent['ticket_id']}|c{k}", "set": "CONTROLS", "setup": "control",
                    "issued_at": close, "candle_start": cd["start"], "trigger": trigger, "limit": limit, "stop": stop,
                    "target": target, "r_bot": risk_d, "shares": shares_for(trigger, stop),
                    "shares_cap20": shares_for(trigger, stop, cap_share=CAP20), "last": cd["c"], "vwap": cd.get("vwap"),
                    "parent_id": parent["ticket_id"], "draw_pool": len(eligible)})
    return out, len(eligible)


def build_session(day, watch, iex, sip, rel_fn):
    """-> (rows, counts) for one session: H1, ORB5, H1_SIPSIG, CONTROLS; counts by setup."""
    st = replay_core(day, watch, iex, rel_fn)
    rows, counts = [], Counter()
    counts["in_play"] = len(watch)
    counts["in_play_reranked"] = len(st["watch"])
    sig = signal_candles(st)
    for now, sym, setup in st["detected"]:
        counts[f"signals:{setup}"] += 1
    for t in st["journal"]:
        if t["at"] not in MORNING:
            continue
        if t["setup"] in H1_SETUPS:
            r = ticket_row(day, t, "H1")
            rows.append(r)
            counts[f"tickets:H1:{t['setup']}"] += 1
            if r["shares"] == 0:
                counts[f"zero_share:H1:{t['setup']}"] += 1
            ctrl, pool = control_rows(day, r, iex.get(t["symbol"], []), sig.get(t["symbol"], set()))
            rows += ctrl
            counts["controls"] += len(ctrl)
            counts["controls_short"] += K_CONTROLS - len(ctrl)
        elif t["setup"] == "orb5":
            rows.append(ticket_row(day, t, "ORB5"))
            counts["tickets:ORB5:orb5"] += 1
    st2 = replay_core(day, watch, sip, rel_fn)
    for t in st2["journal"]:
        if t["at"] in MORNING and t["setup"] in H1_SETUPS:
            rows.append(ticket_row(day, t, "H1_SIPSIG"))
            counts[f"tickets:H1_SIPSIG:{t['setup']}"] += 1
    return rows, counts


# ---------------------------------------------------------------- inputs from the store

class Opening:
    """SIP 09:30-09:34 five-minute bars per session (the bot's opening_bars), cached per day."""

    def __init__(self):
        self.cache = {}

    def day(self, d, symbols):
        have = self.cache.setdefault(d, {})
        need = sorted(set(symbols) - set(have))
        if need:
            df = T.minute_frame(d, need, "sip", "09:30", "09:34")
            got = T.bars_by_symbol(df) if df.height else {}
            for s in need:
                b = got.get(s)
                have[s] = None if not b else (b[0][1], max(x[2] for x in b), min(x[3] for x in b), b[-1][4],
                                              sum(x[5] for x in b))
        return {s: have[s] for s in symbols}


def session_inputs(d, daily, opening, cal):
    stats = daily.stats(d)
    pre, opens = T.stage1_inputs(d, stats)
    watch = R.watchlist_from(stats, pre, opens)
    syms = [w["symbol"] for w in watch]
    iex_df = T.minute_frame(d, syms, "iex", "09:30", "15:59")
    sip_df = T.minute_frame(d, syms, "sip", "09:30", "15:59")
    iex = T.bars_by_symbol(iex_df) if iex_df.height else {}
    sip = T.bars_by_symbol(sip_df) if sip_df.height else {}
    prior = [x for x in cal if x < d][-R.RELVOL_SESSIONS:]

    def rel_fn(symbols):
        return R.open_relvol_from(symbols, opening.day(d, symbols), [opening.day(p, symbols) for p in prior])
    return {"stats": stats, "watch": watch, "iex": iex, "sip": sip, "rel_fn": rel_fn, "prefilter": len(stats)}


# ---------------------------------------------------------------- sets, signing

SCHEMA = {"ticket_id": pl.Utf8, "set": pl.Utf8, "date": pl.Date, "symbol": pl.Utf8, "setup": pl.Utf8,
          "issued_at": pl.Utf8, "candle_start": pl.Utf8, "trigger": pl.Float64, "limit": pl.Float64, "stop": pl.Float64,
          "target": pl.Float64, "r_bot": pl.Float64, "r_floored": pl.Boolean, "atr": pl.Float64, "shares": pl.Int64,
          "shares_cap20": pl.Int64, "score": pl.Float64, "gap": pl.Float64, "pre_rel": pl.Float64,
          "open_relvol": pl.Float64, "last": pl.Float64, "vwap": pl.Float64, "parent_id": pl.Utf8,
          "draw_pool": pl.Int64, "rules_sha256": pl.Utf8, "asset_class": pl.Utf8}


def frame(rows):
    return pl.DataFrame(rows, schema=SCHEMA, orient="row") if rows else pl.DataFrame(schema=SCHEMA)


def canonical_sha256(df):
    """SHA-256 of the rows as sorted canonical JSON lines (column order and row order do not matter)."""
    lines = sorted(json.dumps({k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in sorted(r.items())},
                              sort_keys=True, separators=(",", ":")) for r in df.to_dicts())
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def sign_set(df, set_name, window, inputs_sha, key_path=KEY_PATH):
    from scalp_engine.signing import load_private_key, sign_artifact
    path = T.derived(f"sets/{window}/{set_name}.parquet")
    df.write_parquet(path)
    payload = {"kind": "intraday_ticket_set", "table": f"derived/t1/sets/{window}/{set_name}.parquet",
               "rule_version": RULE_VERSION, "rows": df.height, "sha256": canonical_sha256(df), "set": set_name,
               "window": window, "rules_sha256": R.rules_sha256(), "inputs_sha256": inputs_sha}
    env = sign_artifact(payload, load_private_key(key_path))
    T.derived(f"sets/{window}/{set_name}.signed.json").write_text(json.dumps(env, indent=1) + "\n", encoding="utf-8")
    return payload


def build(first, last, sign=True):
    days = T.window_sessions(first, last)
    cal = [d for d, _ in T.sessions()]
    daily, opening = T.Daily(max(days)), Opening()
    CLASSES.update(asset_classes())
    by_window, counts, watch_hash = {}, {}, {}
    for i, d in enumerate(days):
        inp = session_inputs(d, daily, opening, cal)
        cached = T.watch_of(d)
        if cached is not None and [w["symbol"] for w in cached] != [w["symbol"] for w in inp["watch"]]:
            raise RuntimeError(f"{d}: replayed watchlist differs from the one that set the fetch lists")
        rows, c = build_session(d, inp["watch"], inp["iex"], inp["sip"], inp["rel_fn"])
        w = window_of(d)
        by_window.setdefault(w, []).extend(rows)
        key = (w, d.year)
        counts.setdefault(key, Counter()).update(c)
        counts[key]["sessions"] += 1
        counts[key]["prefilter"] += inp["prefilter"]
        h = watch_hash.setdefault(w, hashlib.sha256())
        h.update(json.dumps([d.isoformat(), [x["symbol"] for x in inp["watch"]]]).encode())
        if (i + 1) % 50 == 0:
            T.log(f"build: {i + 1}/{len(days)} sessions")
        if len(opening.cache) > 40:
            for k in sorted(opening.cache)[:-20]:
                del opening.cache[k]
    out = {"counts": {f"{w}|{y}": dict(c) for (w, y), c in sorted(counts.items())}, "sets": {}}
    for w, rows in by_window.items():
        df = frame(rows)
        for s in ("H1", "ORB5", "H1_SIPSIG", "CONTROLS"):
            part = df.filter(pl.col("set") == s)
            out["sets"][f"{w}|{s}"] = sign_set(part, s, w, watch_hash[w].hexdigest()) if sign else {"rows": part.height}
    T.derived(f"build_{first}_{last}.json").write_text(json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8")
    return out


# ---------------------------------------------------------------- leakage on real sessions

def perturb(bars, cut):
    """Every minute at or after `cut` changed beyond recognition (prices x 1.7 and inverted ranges, volumes x 9)."""
    out = {}
    for s, bs in bars.items():
        out[s] = [b if b[0] < cut else (b[0], b[1] * 1.7, b[2] * 3.1, b[3] * 0.2, b[4] * 0.6, b[5] * 9 + 1, b[6])
                  for b in bs]
    return out


def leakage(sample=30, seed="t1-leakage-v1"):
    """For sampled sessions and every morning close C: perturbing every IEX minute at or after C leaves every
    ticket issued at or before C, and every detection at or before C, unchanged; every scan used only minutes
    before its close; stage-1 inputs end before 09:38 and the re-rank inputs before 09:55."""
    days = T.window_sessions(T.DEV[0], T.CONF[1])
    pick = sorted(random.Random(seed).sample(days, sample))
    cal = [d for d, _ in T.sessions()]
    daily, opening = T.Daily(max(pick)), Opening()
    res = {"sessions": [d.isoformat() for d in pick], "scans": 0, "violations": [], "tickets_checked": 0}
    for d in pick:
        inp = session_inputs(d, daily, opening, cal)
        audit = []
        base = replay_core(d, inp["watch"], inp["iex"], inp["rel_fn"], audit=audit)
        for now, last in audit:
            if last is not None and not last < now:
                res["violations"].append(f"{d} scan {now} used minute {last}")
        for cut in MORNING:
            alt = replay_core(d, inp["watch"], perturb(inp["iex"], cut), inp["rel_fn"])
            a = [t for t in base["journal"] if t["at"] <= cut]
            b = [t for t in alt["journal"] if t["at"] <= cut]
            res["scans"] += 1
            res["tickets_checked"] += len(a)
            if a != b or [x for x in base["detected"] if x[0] <= cut] != [x for x in alt["detected"] if x[0] <= cut]:
                res["violations"].append(f"{d} cut {cut}")
    res["stage1_inputs_end"] = {"sip_pre": "09:29 (available 09:30)", "iex_open": "09:34 (available 09:35)",
                                "decided": "09:38", "rerank_inputs_end": "09:34 SIP (available 09:35, +15 min delay 09:50)",
                                "rerank_decided": "09:55", "daily": "bars <= t-1"}
    res["pass"] = not res["violations"]
    T.derived("leakage.json").write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    return res


# ---------------------------------------------------------------- parity with the live journal

def parity(journal_path, day, equity=500.0, risk_share=0.005):
    """Replay `day` in parity mode (both windows; the bot's sizing test with equity E and risk share p) and match
    every journaled ticket and watchlist row of the live bot (data/intraday_state.json at that day's last commit)."""
    js = json.loads(open(journal_path, encoding="utf-8").read())
    if js["day"] != day.isoformat():
        raise ValueError(f"journal day {js['day']} != {day}")
    cal = [d for d, _ in T.sessions()]
    daily, opening = T.Daily(day), Opening()
    inp = session_inputs(day, daily, opening, cal)
    st = replay_core(day, inp["watch"], inp["iex"], inp["rel_fn"], windows=("morning", "power"),
                     risk=equity * risk_share, max_pos=0.2 * equity)  # levels.POSITION_CAP x E
    mine = {(t["symbol"], t["setup"], t["at"]): t for t in st["journal"]}
    rows = []
    for j in js["journal"]:
        k = (j["symbol"], j["setup"], j["at"])
        t = mine.get(k)
        fields = ("entry_stop", "limit", "stop_loss", "target", "R", "shares")
        diff = None if t is None else {f: (j.get(f), t.get(f)) for f in fields if j.get(f) != t.get(f)}
        rows.append({"ticket": "|".join(k), "matched": t is not None and not diff, "diff": diff,
                     "live": {f: j.get(f) for f in fields + ("last", "vwap")},
                     "replay": None if t is None else {f: t.get(f) for f in fields + ("last", "vwap")}})
    extra = [f"{t['symbol']}|{t['setup']}|{t['at']}" for t in st["journal"]
             if (t["symbol"], t["setup"], t["at"]) not in {(j["symbol"], j["setup"], j["at"]) for j in js["journal"]}]
    live_w = {w["symbol"]: w for w in js["watch"]}
    rep_w = {w["symbol"]: w for w in st["watch"]}
    pre_w = [w["symbol"] for w in inp["watch"]]
    watch_rows = []
    for s in sorted(set(live_w) | set(rep_w)):
        a, b = live_w.get(s), rep_w.get(s)
        watch_rows.append({"symbol": s, "live": a is not None, "replay": b is not None,
                           "fields": None if not (a and b) else {f: (a.get(f), b.get(f)) for f in
                                                                 ("gap", "pre_vol", "pre_rel", "open", "atr", "prev_close",
                                                                  "adv", "open_relvol")}})
    out = {"date": day.isoformat(), "equity": equity, "risk_share": risk_share, "tickets": rows,
           "replay_only": extra, "watch": watch_rows, "replay_pre_rerank_watch": pre_w,
           "matched": sum(r["matched"] for r in rows), "journaled": len(rows)}
    T.derived(f"parity_{day.isoformat()}.json").write_text(json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["build", "leakage", "parity"])
    ap.add_argument("--from", dest="first", default=T.DEV[0].isoformat())
    ap.add_argument("--to", dest="last", default=T.CONF[1].isoformat())
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--journal")
    ap.add_argument("--date")
    ap.add_argument("--equity", type=float, default=500.0)
    ap.add_argument("--risk-share", type=float, default=0.005)
    ap.add_argument("--no-sign", action="store_true")
    a = ap.parse_args(argv)
    if a.step == "build":
        out = build(dt.date.fromisoformat(a.first), dt.date.fromisoformat(a.last), sign=not a.no_sign)
        print(json.dumps(out["sets"], indent=1, default=str))
    elif a.step == "leakage":
        out = leakage(a.sample)
        print(json.dumps({k: out[k] for k in ("scans", "tickets_checked", "pass")}, indent=1))
        print(out["violations"][:10])
    else:
        out = parity(a.journal, dt.date.fromisoformat(a.date), a.equity, a.risk_share)
        print(json.dumps({k: out[k] for k in ("matched", "journaled", "replay_only")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
