"""Index program: the driver and its reports (docs/superpowers/specs/2026-10-12-index-program-design.md 7, 8.2, 8.3,
with C2, B5, E2, E3, E4 and E5).

  --blind    8.2: counts only (the data fingerprint, trading days per year, each rule's entries per year and pi per
             window, TREND's order tickets per year). No equity is computed: the engine never runs.
  --verdict  8.3: refuses unless given the checked-out commit, the owner's go-ahead and the blind run's report,
             which it must reproduce (E5). Writes the JSON report and the Hebrew summary only.

Both read the Stooq file and the H.15 file only after their sha256 equals the manifest (A2). Daily equity stays in
memory (E2); the reports hold aggregated numbers only (B5). Prints one line."""
import argparse
import collections
import datetime as dt
import json
import os
import subprocess
import sys

from bot import index_data as idata, index_engine as ie, index_rules as rules, index_stats as ix

P = ("2009-01-02", "2026-09-30")  # C2: window P
FIT_P = ("2006-01-03", "2008-12-31")  # C2: the fitting period of P's scale
H = ("2006-01-03", "2026-09-30")  # C2: the operating scale's period
DEFAULT = {"t_months": rules.T_MONTHS, "d_rsi": rules.D_RSI_MAX, "budget": ie.BUDGET, "cap": ie.CAP}
BLIND_KEYS = ("fingerprint", "trading_days_per_year", "entries_per_year", "pi", "trend_order_tickets_per_year",
              "tom_days_held_p")
BLIND_PATH = "index-blind-report.json"
TOM_WIRING = 851  # 8.2: TOM holds SPY after the close on 851 days of P (F6: another count stops the run)
VERDICT_GLOB = "docs/backtest/index-verdict-*.json"  # F3: a verdict report on the branch refuses a second run
COSTS = {"optimistic": ie.OPTIMISTIC, "base": ie.BASE, "stress": ie.STRESS}


def _windows():
    return {"P": P, "FIT_P": FIT_P, "H": H}


def changes(rows, params):
    """Each rule's and policy's change points, and the days each rule holds SPY after the close."""
    days = [r[0] for r in rows]
    tom = rules.tom_positions(days)
    dip = rules.dip_positions(rows, rsi_max=params["d_rsi"])
    ch = {"STATIC": [("0000-00-00", "open", True)], "TREND": rules.trend_changes(rows, params["t_months"]),
          "TOM": [c for e, x in tom for c in ((e, "close", True),) + (((x, "close", False),) if x else ())],
          "D": [c for _s, e, _xs, x in dip for c in ((e, "open", True),) + (((x, "open", False),) if x else ())]}
    trend_on = ie._state(ch["TREND"])
    held = {"TOM": rules.held_after_close(tom, days), "D": rules.held_after_close([(e, x) for _s, e, _xs, x in dip], days),
            "TREND": {d for d in days if trend_on(d, "close")}}
    return ch, held


def _in(days, w):
    return [d for d in days if w[0] <= d <= w[1]]


# ---------- 8.2 ----------
def blind_report(rows, fingerprint, params=DEFAULT):
    """8.2: counts only. TREND's order tickets come from its change points (2 per switch, at H's first open too);
    the yearly rebalance needs the scale, which needs a drawdown, so it is not counted here."""
    days = [r[0] for r in rows]
    ch, held = changes(rows, params)
    entries = {"TREND": collections.Counter(d[:4] for d, _s, on in ch["TREND"] if on),
               "TOM": collections.Counter(d[:4] for d, _s, on in ch["TOM"] if on),
               "D": collections.Counter(d[:4] for d, _s, on in ch["D"] if on)}
    pi = {r: {n: round(len(held[r] & set(_in(days, w))) / len(_in(days, w)), 6) for n, w in _windows().items()}
          for r in ("TREND", "TOM", "D")}
    state, holding, tickets = ie._state(ch["TREND"]), False, collections.Counter()
    for d in _in(days, H):
        if state(d, "open") != holding:
            holding = not holding
            tickets[d[:4]] += 2
    rep = {"fingerprint": fingerprint, "trading_days_per_year": dict(collections.Counter(d[:4] for d in days)),
           "entries_per_year": {r: dict(sorted(c.items())) for r, c in entries.items()}, "pi": pi,
           "trend_order_tickets_per_year": dict(sorted(tickets.items())),
           "tom_days_held_p": sum(1 for d in held["TOM"] if P[0] <= d <= P[1])}
    assert tuple(rep) == BLIND_KEYS
    return rep


# ---------- 7.1 to 7.3 ----------
def _daily(res):
    eq = [res["e0"]] + res["equity"]
    return [b / a - 1 for a, b in zip(eq, eq[1:])]


def _spy_cc(rows, days):
    close = {r[0]: r[4] for r in rows}
    idx = {r[0]: k for k, r in enumerate(rows)}
    return [close[d] / rows[idx[d] - 1][4] - 1 for d in days]


def _overlay(rows, cash, ch, held):
    base = ie.run_window(rows, cash, ch, 1.0, ie.BASE, *P)
    stress = ie.run_window(rows, cash, ch, 1.0, ie.STRESS, *P)
    days = base["days"]
    spy, c = _spy_cc(rows, days), [cash[d] for d in days]
    pi, x = ix.overlay_x(_daily(base), spy, c, held, days)
    _, xs = ix.overlay_x(_daily(stress), spy, c, held, days)
    test = ix.overlay_test(x, days, xs)
    by_year = collections.defaultdict(list)
    for d, v in zip(days, x):
        by_year[d[:4]].append(v)
    return {**test, "pi": pi, "x_bar_by_calendar_year": {y: sum(v) / len(v) for y, v in sorted(by_year.items())},
            "round_trips": ix.round_trip_stats(ix.round_trips(base["trades"]))}


def _p_numbers(rows, cash, ch, scale):
    out = {}
    for name, costs in (("base", ie.BASE), ("stress", ie.STRESS)):
        res = ie.run_window(rows, cash, ch, scale, costs, *P)
        out[name] = (ie.growth_rate(res["e0"], res["final"], res["days"][0], res["days"][-1]),
                     ie.max_drawdown(res["days"], res["equity"], res["e0"])[0])
    return {"growth_p": out["base"][0], "mdd_p": out["base"][1], "growth_p_stress": out["stress"][0]}


def decide(rows, cash, params=DEFAULT):
    """7.1 to 7.3 with C2 and E3: the overlay tests, TACT, the scales fitted on FIT_P, P at base and stress costs,
    eligibility and the outcome."""
    ch, held = changes(rows, params)
    overlays = {r: _overlay(rows, cash, ch[r], held[r]) for r in ("TOM", "D")}
    gate_a = ix.holm({r: o["t"] for r, o in overlays.items()})
    for r, o in overlays.items():
        o["holm"] = gate_a[r]
        o["passes"] = ix.passes(o, gate_a[r])
    passing = [r for r in ("TOM", "D") if overlays[r]["passes"]]
    if passing:
        ch["TACT"] = ix.union_changes([ch[r] for r in passing])
    pols = ["STATIC", "TREND"] + (["TACT"] if passing else [])
    scales, numbers = {}, {}
    for p in pols:
        fit = ie.fit_scale(rows, cash, ch[p], *FIT_P, budget=params["budget"], cap=params["cap"])
        scales[p] = fit
        numbers[p] = _p_numbers(rows, cash, ch[p], fit["scale"]) if fit["scale"] is not None else None
    elig = {}
    for p in pols[1:]:
        if numbers[p] is None or numbers["STATIC"] is None:
            elig[p] = {"eligible": False, "excluded": True}
        else:
            elig[p] = ix.eligible(numbers[p], numbers["STATIC"])
    out, sel = ix.outcome(elig, {p: n["growth_p"] for p, n in numbers.items() if n})
    # the spec does not cover a STATIC excluded by the scale search: flagged in the report, not settled here
    return {"overlays": overlays, "passing": passing, "policies": pols, "scales": scales, "p_numbers": numbers,
            "eligibility": elig, "outcome": out, "selected": sel, "static_excluded": scales["STATIC"]["excluded"],
            "_changes": ch, "_held": held}


# ---------- 8.3 ----------
def _metrics(res):
    years = ie.calendar_year_returns(res["e0"], res["days"], res["equity"])
    depth, peak, trough = ie.max_drawdown(res["days"], res["equity"], res["e0"])
    if depth and peak is None:
        peak = res["prev_close_day"]  # the fall starts at the close before the window
    return {"growth": ie.growth_rate(res["e0"], res["final"], res["days"][0], res["days"][-1]),
            "final_equity": res["final"], "max_drawdown": {"depth": depth, "peak": peak, "trough": trough},
            "worst_year": min(years.items(), key=lambda kv: kv[1]) if years else None,
            "positive_years": sum(1 for v in years.values() if v > 0) / len(years) if years else None,
            "order_tickets": res["tickets"], "mean_spy_weight": sum(res["weights"]) / len(res["weights"]),
            "calendar_year_returns": years}


def _window(rows, cash, ch, scale, w):
    return {"costs": {n: _metrics(ie.run_window(rows, cash, ch, scale, c, *w)) for n, c in COSTS.items()},
            "accounts_base": {str(int(a)): _metrics(ie.run_window(rows, cash, ch, scale, ie.BASE, *w, equity0=a))
                              for a in (5_000.0, 25_000.0)}}


def _brief(d):
    return {"outcome": d["outcome"], "selected": d["selected"], "passing_overlays": d["passing"],
            "scales": {p: {"fitted": s["fitted"], "scale": s["scale"]} for p, s in d["scales"].items()},
            "p_numbers": d["p_numbers"], "eligibility": d["eligibility"], "static_excluded": d["static_excluded"]}


def verdict_report(rows, cash, fingerprint, params=DEFAULT):
    """8.3 with C2, B5, E2 and E4: aggregated numbers only."""
    d = decide(rows, cash, params)
    ch = d["_changes"]
    pols = {}
    for p in d["policies"]:
        s_p = d["scales"][p]
        s_h = ie.fit_scale(rows, cash, ch[p], *H, budget=params["budget"], cap=params["cap"])
        entry = {"scale_p": {"fitted": s_p["fitted"], "scale": s_p["scale"], "excluded": s_p["excluded"]},
                 "scale_operating": {"fitted": s_h["fitted"], "scale": s_h["scale"], "excluded": s_h["excluded"]},
                 "windows": {}}
        if s_p["scale"] is not None:
            entry["windows"]["P"] = _window(rows, cash, ch[p], s_p["scale"], P)
            entry["windows"]["FIT_P"] = _window(rows, cash, ch[p], s_p["scale"], FIT_P)
        if s_h["scale"] is not None:
            entry["windows"]["H"] = _window(rows, cash, ch[p], s_h["scale"], H)
        pols[p] = entry
    refs = {"spy_s1": {n: _metrics(ie.run_window(rows, cash, ch["STATIC"], 1.0, ie.BASE, *w)) for n, w in
                       (("P", P), ("H", H))},
            "cash_leg": {n: _metrics(ie.run_window(rows, cash, [], 1.0, ie.BASE, *w)) for n, w in (("P", P), ("H", H))}}
    overlays = {r: {k: v for k, v in o.items()} for r, o in d["overlays"].items()}
    rep = {"fingerprint": fingerprint, "windows": _windows(), "overlays": overlays, "policies": pols,
           "references": refs, "eligibility": d["eligibility"], "p_numbers": d["p_numbers"],
           "outcome": d["outcome"], "selected": d["selected"], "static_excluded": d["static_excluded"]}
    # E4: the information items, after the decision, in their own section; they decide nothing
    info = {"no_crash_cap": _brief(decide(rows, cash, {**params, "cap": 1.0})),
            "budget_15": _brief(decide(rows, cash, {**params, "budget": 0.15})),
            "budget_17": _brief(decide(rows, cash, {**params, "budget": 0.17})),
            "t_8_months": {**_brief(decide(rows, cash, {**params, "t_months": 8})),
                           "first_trend_state": rules.trend_states(rows, 8)[0][0]},
            "t_12_months": {**_brief(decide(rows, cash, {**params, "t_months": 12})),
                            "first_trend_state": rules.trend_states(rows, 12)[0][0]},  # cold before this day
            "d_rsi_5": _brief(decide(rows, cash, {**params, "d_rsi": 5.0})),
            "d_rsi_15": _brief(decide(rows, cash, {**params, "d_rsi": 15.0})),
            "t_timing_statistic": _overlay(rows, cash, ch["TREND"], d["_held"]["TREND"])}
    rep["information, no decision"] = info
    return rep


def summary_md(rep, today):
    """The Hebrew summary of 8.3: the outcome stated plainly (also when it is STATIC), then the numbers."""
    out = rep["outcome"]
    if out == "STATIC":
        op = rep["policies"]["STATIC"]["scale_operating"]["scale"]
        head = (f"**תוצאה: STATIC.** אף מדיניות תזמון אינה זכאית לפי 7.2. ההמלצה היא התמהיל הקבוע בסקאלה התפעולית "
                f"{op}, שאינו צריך אלגוריתם, והמחקר על הכללים האלה מסתיים.")
    else:
        sel = rep["selected"]
        op = rep["policies"][sel]["scale_operating"]["scale"]
        head = (f"**תוצאה: TIMING.** המדיניות שנבחרה: {sel}, בסקאלה תפעולית {op}. התוכנית ממשיכה לשלב 5 "
                "(שלב נייר, בלי כסף אמיתי).")
    lines = [f"# תוכנית המדד: ריצת הפסיקה ({today})", "", head, "",
             f"טביעת הנתונים: `{rep['fingerprint']}` · חלון P: {rep['windows']['P'][0]} עד {rep['windows']['P'][1]}", "",
             "## מבחן ה-overlays (7.1)", "", "| כלל | t | Holm | עקביות | גודל | לחץ | עובר |", "|---|---|---|---|---|---|---|"]
    for r, o in rep["overlays"].items():
        lines.append(f"| {r} | {o['t']:.3f} | {o['holm']} | {o['consistency']['pass']} | {o['size']} | {o['stress']} | "
                     f"{o['passes']} |")
    lines += ["", "## המדיניות ב-P (עלויות בסיס)", "", "| מדיניות | סקאלה | צמיחה בשנה | ירידה מקסימלית | זכאית |",
              "|---|---|---|---|---|"]
    for p, n in rep["p_numbers"].items():
        if not n:
            lines.append(f"| {p} | הוחרגה | — | — | — |")
            continue
        el = rep["eligibility"].get(p, {}).get("eligible", "—") if p != "STATIC" else "—"
        lines.append(f"| {p} | {rep['policies'][p]['scale_p']['scale']} | {n['growth_p']:.2%} | {n['mdd_p']:.2%} | "
                     f"{el} |")
    lines += ["", "הפרטים המלאים, כולל סעיף 'information, no decision', נמצאים ב-`docs/backtest/index-verdict-<date>.json`. "
              "אין בדוח סדרה יומית או חודשית ואין מחיר (B5, E2).", ""]
    return "\n".join(lines)


# ---------- main ----------
def head():
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def summary_path(today):
    return f"docs/backtest/index-{today}.md"


def verdict_path(today):
    return f"docs/backtest/index-verdict-{today}.json"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Index program runs (blind, verdict)")
    ap.add_argument("--blind", action="store_true")
    ap.add_argument("--verdict", action="store_true")
    ap.add_argument("--stooq", required=True)
    ap.add_argument("--h15", default="data/index/h15-3m-tbill.csv")
    ap.add_argument("--manifest", default="data/index/manifest.json")
    ap.add_argument("--commit", default="")
    ap.add_argument("--owner-go-ahead", action="store_true")
    ap.add_argument("--blind-report", default="")
    ap.add_argument("--blind-run-id", default="", help="the blind run's id, recorded in the report (F3)")
    a = ap.parse_args(argv)
    try:
        rows = idata.load_prices(a.stooq, a.manifest)
        h15 = idata.load_h15(a.h15, a.manifest)
    except idata.DataMismatch:
        print("refused: a file's sha256 differs from the manifest; nothing was computed")
        return 2
    fp = idata.fingerprint(a.stooq, a.h15)
    if a.blind:
        with open(BLIND_PATH, "w", encoding="utf-8") as fh:
            json.dump(blind_report(rows, fp), fh, indent=1)
        print(f"blind report written: {BLIND_PATH}; data {fp}")
        return 0
    if a.verdict:
        import glob
        if glob.glob(VERDICT_GLOB):
            print("verdict refused: a verdict report already exists on the branch (F3: one verdict run)")
            return 2
        if not (a.owner_go_ahead and a.commit and a.commit == head()):
            print("verdict refused: it needs --commit <the checked-out commit> and --owner-go-ahead")
            return 2
        if not a.blind_report:
            print("verdict refused: it needs --blind-report <the blind run's report> (E5)")
            return 2
        with open(a.blind_report, encoding="utf-8") as fh:
            blind = json.load(fh)
        if blind.get("tom_days_held_p") != TOM_WIRING:
            print(f"verdict refused: the blind report's TOM count is not {TOM_WIRING} (8.2, F6)")
            return 2
        if blind != json.loads(json.dumps(blind_report(rows, fp))):
            print("verdict refused: the recomputed blind report differs from the blind run's (F3)")
            return 2
        cash = idata.cash_returns([r[0] for r in rows], h15)
        rep = verdict_report(rows, cash, fp)
        rep["record"] = {"commit": a.commit, "blind_run_id": a.blind_run_id,
                         "verdict_run_id": os.environ.get("GITHUB_RUN_ID", "")}  # rule 0.3, F3: the record of the run
        today = dt.date.today().isoformat()
        os.makedirs("docs/backtest", exist_ok=True)
        with open(verdict_path(today), "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1, ensure_ascii=False)
        with open(summary_path(today), "w", encoding="utf-8") as fh:
            fh.write(summary_md(rep, today))
        print(f"verdict written: {verdict_path(today)}, {summary_path(today)}")
        return 0
    ap.print_usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
