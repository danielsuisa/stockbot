"""The verdict run's numbers and files (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md 6.4, 6.6,
8): run once, in phase D, from the blind run's commit and inputs. Decided in phase C because 6.4 point 4 freezes the
commit at the blind run. Until then it runs on synthetic data only (its tests).

When check 1 or comparability fails, or b > 0.03 R, the verdict holds no return statistic: the driver never calls
`numbers` then, and `write` writes no ticket CSV."""
import collections
import csv
import datetime as dt
import json
import math
import os

from bot import blind, ensemble_backtest as bt, ensemble_stats as st

VERDICT_PATH = "ensemble-verdict-report.json"
TICKETS_PATH = "ensemble-tickets.csv"
CSV_FIELDS = ("day", "signal", "symbol", "bars_of", "block", "filled", "exit_a", "exit_b", "gross_r", "pool_mean_m",
              "cost_r", "cost_r_stress", "x")


def _x(t):
    return (t["g"] - t["m"]) - t["c"]


def _alone(xs, blocks):
    s10, s5 = st.ses(xs, blocks)
    return {"n": len(xs), "x_bar": st.mean(xs), "se_t10": s10, "se_t5": s5}


def numbers(kept, A_k, b, blocks10, refusals):
    """Every number of the verdict (6.4 gates 1-5 with the numbers behind them; reported, never gated: each signal
    alone, calendar years, filled tickets only, exit types, the cost distribution, refusals)."""
    x = [_x(t) for t in kept]
    xs = [(t["g"] - t["m"]) - t["cost_r_stress"] for t in kept]
    sig = [t["signal"] for t in kept]
    s10, s5 = st.ses(x, blocks10)
    A = bt.excess_mean([t["g"] for t in kept], [t["m"] for t in kept])
    a950 = sorted(A_k)[949]
    g1, g5 = st.gate1(x, b, blocks10), st.gate1(xs, b, blocks10)
    gates = {"1": g1, "2": {"pass": st.above_950th(A, A_k), "A": A, "A_950th": a950, "p": st.p_value(A, A_k)},
             "3": st.gate3(x, blocks10), "4": st.gate4(x, sig), "5": g5}
    by_sig, by_year = collections.defaultdict(list), collections.defaultdict(list)
    for v, t, blk in zip(x, kept, blocks10):
        by_sig[t["signal"]].append((v, blk))
        by_year[t["day"][:4]].append(v)
    filled = [v for v, t in zip(x, kept) if t["filled"]]
    exits = {"leg_a": collections.Counter(), "leg_b": collections.Counter()}
    costs = collections.defaultdict(list)
    for t in kept:
        if t["filled"]:
            exits["leg_a"][t["exits"][0]] += 1
            exits["leg_b"][t["exits"][1]] += 1
            costs[t["signal"]].append(t["c"])
            costs["all"].append(t["c"])
    reported = {
        "each_signal_alone": {s: _alone([v for v, _ in vb], [b_ for _, b_ in vb]) for s, vb in sorted(by_sig.items())},
        "calendar_years": {y: {"n": len(v), "x_bar": st.mean(v)} for y, v in sorted(by_year.items())},
        "filled_only": {"n": len(filled), "x_bar": st.mean(filled) if filled else None},
        "exit_types": {leg: dict(c) for leg, c in exits.items()},
        "cost_distribution": {s: {**blind.percentiles(v), "mean": st.mean(v)} for s, v in sorted(costs.items())},
        "refusals": refusals}
    return {"n": len(x), "x_bar": st.mean(x), "x_bar_minus_b": st.mean(x) - b, "se_t10": s10, "se_t5": s5, "A": A,
            "A_950th": a950, "p": gates["2"]["p"], "gates": gates, "reported": reported}


def summary_path(today):
    return f"docs/backtest/ensemble-{today}.md"


ASSUMPTIONS = (
    "תקופה: ימי אות 3.1.2017 עד 31.3.2026; נרות עד 31.7.2026 לתוצאות בלבד (4.2, C17).",
    "יחידה: R לכל כרטיס שהונפק, מולא או לא; סיכון קבוע של 100$ לכל כרטיס (סעיף 3), בלי גודל לפי הון.",
    "מודל המילוי: נרות יומיים לפי כללים 1 עד 6 של סעיף 3; כרטיס שלא מולא שווה 0 בלי עלות.",
    "עלויות: עמלה 0.0035$ למניה ולפחות 0.35$ לפקודה על 4 פקודות; מרווח מלא מציטוט 5 דקות אחרי פתיחת יום הכניסה,"
    " ובלעדיו מרווח השמירה; תרחיש לחץ: מרווח כפול 1.5 (סעיף 5).",
    "היוניברס: נקודתי בזמן עם כלל שינוי השם (סעיף 2, 4.2); מחירי בעלי עניין לפי CIK.",
    "ההשוואה האקראית: מאגר של אותו יום, אותו עשירון נזילות, בלי כרטיס פתוח, לפחות 20 חברים; 1,000 הגרלות"
    " (6.2, C25).",
    "הסטטיסטיקה: SE לפי בלוקים, T10 ו-T5 (6.3); בדיקה 1: 4.1 כפול ה-SE הגדול ועוד b עד 0.10R (6.4).",
    "הטיית השרידות: b = K כפול 0.41% כפול 1R, עם K מחושב בריצה (6.4, 4.2).",
    "תכונות ידועות: חיפוש הסימול האחר של ה-CIK בכל ההגשות; ספירה לפי נרות הסימול; סימול אחר אחד לשני סימולים;"
    " סירובים לפי אות בלבד (4.2).",
    "שערים 3 ו-4 על x בלי b; b נכנס לשער 1, ולשער 5 דרך שער 1; 1% העליונים של שער 4 הם ceil(n/100) כרטיסים (4.2).",
    "כרטיס פתוח אחד לכל אות וסימול (C14); שבר בסדרה אחרי יום האות יוצא בסגירה האחרונה (C15); כרטיס שחורג מסוף"
    " הנתונים נשמט (C17).",
    "תרחיש הלחץ משתמש באותם כרטיסים ובאותם מאגרים, ורק העלויות גדלות (סעיף 5).",
    "היקף הטענה: GO מניח שאף אות לא נשאר בטובה או מחוצה לה יותר משנה בערך; תרחיש E מחוץ להיקף (6.3, 6.4).",
    "GO אינו המלצה סופית: הוא נשאר מותנה במעקב קדימה (6.5).",
)


def _f(v, fmt):
    """A number for the summary: None as a dash, an infinite t as the sign of infinity."""
    if v is None:
        return "—"
    if isinstance(v, float) and math.isinf(v):
        return "∞" if v > 0 else "-∞"
    return format(v, fmt)


def _finite(v):
    """The JSON report has no Infinity (not JSON): an infinite t is written as the string "inf"."""
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v


def summary_md(rep, today):
    """The Hebrew summary of section 8 (the gates table and every assumption). Without a return statistic when
    check 1, comparability or the data check failed."""
    label = st.LABELS["GO"] if rep["outcome"] == "GO" else st.LABELS["NO-GO"]
    c1 = rep["check_1"]
    lines = [f"# אלגוריתם ההמלצות: ריצת הפסיקה ({today})", "",
             f"**תוצאה:** `{rep['outcome']}` · תווית: {label}", "",
             f"קלטים: `{rep['inputs']}` · קומיט: `{rep['commit']}`", "",
             "## בדיקה 1 והשוואות", "",
             "| מדד | ערך |", "|---|---|",
             f"| μ_min | `{_f(c1['mu_min'], '.4f')}` |", f"| b | `{_f(c1['b_R'], '.4f')}` |",
             f"| SE של T10 / T5 | `{_f(c1['se_t10'], '.4f')}` / `{_f(c1['se_t5'], '.4f')}` |",
             f"| ציטוטים שנדחו | `{rep['comparability']['quote_share']:.2%}` |",
             f"| מאגרים קטנים | `{rep['comparability']['small_pool_share']:.2%}` |", ""]
    if "gates" in rep:
        g = rep["gates"]
        ok = lambda v: "עבר" if v else "נכשל"  # noqa: E731
        lines += ["## השערים (6.4)", "",
                  "| שער | מה נבדק | ערך | תוצאה |", "|---|---|---|---|",
                  f"| שער 1 | x̄ פחות b, t10, t5 | `{_f(g['1']['x_bar_minus_b'], '.4f')}`, "
                  f"`{_f(g['1']['t10'], '.2f')}`, `{_f(g['1']['t5'], '.2f')}` | {ok(g['1']['pass'])} |",
                  f"| שער 2 | A מול ה-950 מתוך 1,000 | `{_f(g['2']['A'], '.4f')}` מול `{_f(g['2']['A_950th'], '.4f')}`, p "
                  f"`{_f(g['2']['p'], '.4f')}` | {ok(g['2']['pass'])} |",
                  f"| שער 3 | בלוקים חיוביים, חצאים | `{g['3']['blocks_positive']}` מתוך `10`; חצאים "
                  f"`{_f(g['3']['half_means'][0], '.4f')}`, `{_f(g['3']['half_means'][1], '.4f')}` "
                  f"| {ok(g['3']['pass'])} |",
                  f"| שער 4 | בלי 1% העליונים, בלי כל אות | `{_f(g['4']['mean_without_top_1pct'], '.4f')}`; "
                  + ", ".join(f"{s}: `{_f(v, '.4f')}`" for s, v in sorted(g['4']['mean_each_signal_removed'].items()))
                  + f" | {ok(g['4']['pass'])} |",
                  f"| שער 5 | שער 1 בתרחיש לחץ | `{_f(g['5']['x_bar_minus_b'], '.4f')}`, `{_f(g['5']['t10'], '.2f')}`, "
                  f"`{_f(g['5']['t5'], '.2f')}` | {ok(g['5']['pass'])} |", "",
                  f"מספר כרטיסים: `{rep['n']}` · x̄: `{_f(rep['x_bar'], '.4f')}` · x̄ פחות b: "
                  f"`{_f(rep['x_bar_minus_b'], '.4f')}`", "",
                  "הדיווחים שאינם שערים (כל אות לבד, שנים, כרטיסים שמולאו, סוגי יציאה, עלויות, סירובים) נמצאים"
                  f" ב-`{VERDICT_PATH}`, והכרטיסים ב-`{TICKETS_PATH}`.", ""]
    else:
        lines += ["לא חושב שום נתון תשואה: הריצה נעצרה לפני השערים (6.4).", ""]
    lines += ["## הנחות", ""] + [f"- {a}" for a in ASSUMPTIONS] + [""]
    return "\n".join(lines)


def write(rep, kept, today):
    """The verdict's files: the JSON report, the ticket CSV (only when the gates were computed) and the Hebrew
    summary -> [paths]."""
    paths = [VERDICT_PATH]
    with open(VERDICT_PATH, "w", encoding="utf-8") as fh:
        json.dump(_finite(rep), fh, indent=1, ensure_ascii=False, allow_nan=False)
    if "gates" in rep and kept is not None:
        with open(TICKETS_PATH, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(CSV_FIELDS)
            for t in kept:
                ex = t["exits"] or [None, None]
                w.writerow([t["day"], t["signal"], t["symbol"], t["bars_of"], t["block"], t["filled"], ex[0], ex[1],
                            t["g"], t["m"], t["c"], t["cost_r_stress"], _x(t)])
        paths.append(TICKETS_PATH)
    md = summary_path(today)
    os.makedirs(os.path.dirname(md), exist_ok=True)
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(summary_md(rep, today))
    paths.append(md)
    return paths


def today():
    return dt.date.today().isoformat()
