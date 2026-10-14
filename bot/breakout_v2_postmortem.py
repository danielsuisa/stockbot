"""Post-mortem of the v2 A/B fills. The filters below were chosen from 2024 only.

2024, before any 2025–2026 number was read: the book mean was about −0.176R. The only split that
reached a non-negative mean with more than 100 trades was a charged spread of at most 3% of R
(178 trades, about +0.003R). Grade B was about −0.67R. A loose 10-day range (above 4 ATR) was about
−0.25R, two or more failed probes about −0.27R, and a fill already through the 20-day high was the
worst extension quartile. The 15:00 hour won only 27% of the time. SPY below its 20-day average was
less bad than SPY above it. Those are the six filters. They are not revised after 2025–2026.

Nothing here places or transmits an order.
"""
import csv
import statistics
from datetime import datetime
from pathlib import Path

from bot.ticket_backtest import SEEDS, pctl, random_means

TRADES = Path("docs/backtest/breakout-rule-v2-2026-10-14.csv")
FEATURES = Path("docs/backtest/breakout-v2-feature-slice-2026-10-14.csv")
OUT = Path("docs/backtest/breakout-v2-winners-postmortem-2026-10-14.md")
OOS = "2025-01-01"

# Locked from 2024. Do not edit these after reading 2025–2026.
FILTERS = (
    ("F1 spread ≤ 3% of R", lambda r: r["sr"] <= 0.03),
    ("F2 grade A, 10-day range ≤ 3.4 ATR, at most one failed probe",
     lambda r: r["grade"] == "A" and r["tight"] is not None and r["tight"] <= 3.4 and (r["probes"] or 0) <= 1),
    ("F3 grade A, spread ≤ 3% of R, entry before 15:00",
     lambda r: r["grade"] == "A" and r["sr"] <= 0.03 and r["hour"] < 15),
    ("F4 SPY prior close below its 20-day average", lambda r: r["spy_up20"] == 0),
    ("F5 fill not above the prior 20-day high", lambda r: r["ext"] is not None and r["ext"] <= 0),
    ("F6 grade A and QQQ prior close below its 50-day average",
     lambda r: r["grade"] == "A" and r["qqq_up50"] == 0),
)


def _f(v):
    if v is None or v == "":
        return None
    return float(v)


def load():
    feat = {}
    with FEATURES.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            feat[(r["day"], r["t"])] = r
    rows = []
    with TRADES.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            g = feat[(r["day"], r["t"])]
            for k in ("vol_ratio", "fill", "stop", "target", "R", "shares", "spread", "exit",
                      "gross_r", "cost_r", "net_r", "net_r_short"):
                r[k] = float(r[k])
            for k in ("gap", "dist_high", "high20", "prev_close", "atr", "atr_pct", "atr_rank",
                      "ret1", "ret2", "ret3", "ret5", "tight", "probes", "spy_ret20", "qqq_ret20"):
                r[k] = _f(g.get(k))
            for k in ("nr7", "spy_up20", "spy_up50", "qqq_up20", "qqq_up50"):
                r[k] = None if g.get(k) in (None, "") else int(float(g[k]))
            r["sr"] = r["spread"] / r["R"]
            r["hour"] = int(r["minute"][:2])
            r["dow"] = datetime.fromisoformat(r["day"]).strftime("%a")
            r["month"] = r["day"][5:7]
            r["year"] = r["day"][:4]
            r["ext"] = None if not r["high20"] else r["fill"] / r["high20"] - 1
            r["win"] = r["net_r"] > 0
            r["extra"] = r["net_r"] - 2 * 0.01 / r["R"]
            r["kinds"] = r["kind"]
            rows.append(r)
    return rows


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def tstat(xs):
    if len(xs) < 2:
        return None
    sd = statistics.stdev(xs)
    if sd == 0:
        return None
    return mean(xs) / (sd / len(xs) ** 0.5)


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    pos = q * (len(xs) - 1)
    lo = int(pos)
    if lo + 1 >= len(xs):
        return xs[lo]
    return xs[lo] + (xs[lo + 1] - xs[lo]) * (pos - lo)


def fmt(x, spec="{:+.3f}"):
    return "—" if x is None else spec.format(x)


def cell(xs):
    if not xs:
        return "—"
    nets = [x["net_r"] for x in xs]
    return f"{len(xs)} | {sum(x['win'] for x in xs) / len(xs):.1%} | {fmt(mean(nets))}"


def bucket(r):
    if r["kind"] == "target":
        return "target, the 1.5R order"
    if r["kind"] == "stop":
        return "stop"
    if r["gross_r"] >= 2:
        return "time stop, gross at least 2R"
    if r["gross_r"] >= 1:
        return "time stop, gross at least 1R"
    if r["gross_r"] > 0:
        return "time stop, gross under 1R"
    return "time stop, gross at or below 0"


def loser_how(r):
    if r["win"]:
        return None
    if r["kind"] == "stop":
        return "stop"
    if r["kind"] == "target":
        return "target, still a net loss"
    if r["gross_r"] > 0:
        return "time stop, gross positive, costs took it"
    return "time stop, closed down"


def group_table(rows, key, order=None):
    groups = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    keys = order if order is not None else sorted(groups, key=lambda z: str(z))
    lines = ["| Group | Trades | Win rate | Mean net R |", "|---|---:|---:|---:|"]
    for k in keys:
        g = groups.get(k) or []
        if not g:
            continue
        lines.append(f"| {k} | {cell(g)} |")
    return lines


def quartile_table(rows, fn, label):
    xs = [r for r in rows if fn(r) is not None]
    xs.sort(key=fn)
    n = len(xs)
    lines = [f"| {label} quartile | Range | Trades | Win rate | Mean net R |", "|---|---|---:|---:|---:|"]
    for i in range(4):
        g = xs[i * n // 4:(i + 1) * n // 4]
        if not g:
            continue
        lines.append(f"| Q{i + 1} | {fn(g[0]):.4g} to {fn(g[-1]):.4g} | {cell(g)} |")
    return lines


def side_means(rows, fn, label):
    w = [fn(r) for r in rows if r["win"] and fn(r) is not None]
    l = [fn(r) for r in rows if not r["win"] and fn(r) is not None]
    return f"| {label} | {fmt(mean(w), '{:.4f}')} | {fmt(mean(l), '{:.4f}')} |"


def gate(rows, years):
    """The v2 gate on this subset. t > 2 is the bar Daniel named; t >= 2 is the original code."""
    xs = [r["net_r"] for r in rows]
    m, t = mean(xs), tstat(xs)
    rm = random_means(rows) if rows else []
    p95 = pctl(rm, 0.95) if rm else None
    by = {}
    for r in rows:
        by.setdefault(r["year"], []).append(r["net_r"])
    year_means = {y: mean(by.get(y) or []) for y in years}
    checks = {
        "n": len(rows),
        "mean": m,
        "t": t,
        "p95": p95,
        "years": year_means,
        "n_ok": len(rows) >= 100,
        "t_ok": m is not None and m > 0 and (t or 0) > 2,
        "rand_ok": m is not None and p95 is not None and m > p95,
        "years_ok": all(year_means[y] is not None and year_means[y] > 0 for y in years),
    }
    checks["go"] = checks["n_ok"] and checks["t_ok"] and checks["rand_ok"] and checks["years_ok"]
    return checks


def _he(x, digits=3):
    if x is None:
        return "חסר"
    if x == 0:
        return "אפס"
    return f"{'פלוס' if x > 0 else 'מינוס'} {abs(x):.{digits}f}"


def render(rows):
    wins = [r for r in rows if r["win"]]
    losses = [r for r in rows if not r["win"]]
    nets = [r["net_r"] for r in rows]
    gross = [r["gross_r"] for r in rows]
    base_oos = [r for r in rows if r["day"] >= OOS]
    lines = [
        "# Breakout v2, why the winners won — 2026-10-14",
        "",
        f"The A/B book is {len(rows)} fills, win rate {sum(r['win'] for r in rows) / len(rows):.1%}, "
        f"mean net R {fmt(mean(nets))}. A winner is a fill with net R above zero. "
        "No minute bars were downloaded again. Gap, the prior path, and the index regime come from a SIP daily "
        "slice for these names plus SPY and QQQ. Float is not in the data. Intraday failed probes are not in "
        "the trade file; the probe count is the daily proxy defined before the slice was read against 2025–2026.",
        "",
        "The six filters were locked on 2024 alone. Their 2025–2026 numbers were computed after that lock.",
        "",
        "## 1. Outcome anatomy",
        "",
        f"Net R percentiles: 10th {fmt(pct(nets, 0.10))}, 25th {fmt(pct(nets, 0.25))}, "
        f"median {fmt(statistics.median(nets))}, 75th {fmt(pct(nets, 0.75))}, 90th {fmt(pct(nets, 0.90))}.",
        f"Gross R mean {fmt(mean(gross))}, median {fmt(statistics.median(gross))}. "
        f"Mean cost {fmt(mean([r['cost_r'] for r in rows]))} R.",
        "",
        "The order's target is 1.5R, not 2R. A fill reaches 2R only when the exit price is that far through "
        "the printed R, which the target order does not wait for. A time stop is the 15:55 flat.",
        "",
        f"Winners: {len(wins)}. Losers: {len(losses)}.",
        "",
        "| Exit | Winners | Losers |",
        "|---|---:|---:|",
    ]
    labels = ["target, the 1.5R order", "time stop, gross at least 2R", "time stop, gross at least 1R",
              "time stop, gross under 1R", "time stop, gross at or below 0", "stop"]
    for lab in labels:
        lines.append(f"| {lab} | {sum(bucket(r) == lab for r in wins)} | {sum(bucket(r) == lab for r in losses)} |")
    lines += ["", "How the losers ended:", ""]
    lines += group_table(losses, loser_how)
    lines += ["", "Mean net R by exit, full book:", ""]
    lines += group_table(rows, lambda r: r["kind"], ["target", "flat", "stop"])
    lines += [
        "",
        f"Winners' mean gross R {fmt(mean([r['gross_r'] for r in wins]))}, mean net {fmt(mean([r['net_r'] for r in wins]))}. "
        f"Losers' mean gross {fmt(mean([r['gross_r'] for r in losses]))}, mean net {fmt(mean([r['net_r'] for r in losses]))}. "
        f"A stop's mean net R is {fmt(mean([r['net_r'] for r in rows if r['kind'] == 'stop']))}, past −1R because the fill "
        "is worse than the trigger and the denominator is the printed R.",
        "",
        "## 2. Timing",
        "",
        "### Hour",
        "",
    ]
    lines += group_table(rows, lambda r: f"{r['hour']:02d}", [f"{h:02d}" for h in range(9, 16)])
    lines += ["", "### Day of week", ""]
    lines += group_table(rows, lambda r: r["dow"], ["Mon", "Tue", "Wed", "Thu", "Fri"])
    lines += ["", "### Month", ""]
    lines += group_table(rows, lambda r: r["month"])
    lines += ["", "### Index regime, prior close versus the average. Known before the session.", ""]
    lines += group_table(rows, lambda r: f"SPY 20-day {'above' if r['spy_up20'] else 'below'}")
    lines += [""]
    lines += group_table(rows, lambda r: f"SPY 50-day {'above' if r['spy_up50'] else 'below'}")
    lines += [""]
    lines += group_table(rows, lambda r: f"QQQ 20-day {'above' if r['qqq_up20'] else 'below'}")
    lines += [""]
    lines += group_table(rows, lambda r: f"QQQ 50-day {'above' if r['qqq_up50'] else 'below'}")
    lines += ["", "## 3. Setup at the entry", ""]
    lines += group_table(rows, lambda r: f"grade {r['grade']}", ["grade A", "grade B"])
    lines += ["", "Price bucket, from the fill. Float is not available.", ""]

    def price_bucket(r):
        if r["fill"] < 20:
            return "under $20"
        if r["fill"] < 50:
            return "$20 to $50"
        if r["fill"] < 100:
            return "$50 to $100"
        return "$100 and up"

    lines += group_table(rows, price_bucket, ["under $20", "$20 to $50", "$50 to $100", "$100 and up"])
    lines += ["", "Quartiles. Q1 is the low end.", ""]
    for label, fn in (("Gap at the open", lambda r: r["gap"]),
                      ("Distance of the prior close under the 20-day high", lambda r: r["dist_high"]),
                      ("Fill versus the prior 20-day high", lambda r: r["ext"]),
                      ("ATR as a fraction of price", lambda r: r["atr_pct"]),
                      ("ATR percentile versus the prior 60 sessions", lambda r: r["atr_rank"]),
                      ("Volume through the trigger, fraction of the 20-day average", lambda r: r["vol_ratio"]),
                      ("Charged spread divided by R", lambda r: r["sr"])):
        lines += quartile_table(rows, fn, label)
        lines.append("")
    lines += [
        "Winners versus losers, mean of the feature:",
        "",
        "| Feature | Winners | Losers |",
        "|---|---:|---:|",
        side_means(rows, lambda r: r["gap"], "Gap"),
        side_means(rows, lambda r: r["dist_high"], "Distance under the 20-day high"),
        side_means(rows, lambda r: r["ext"], "Fill versus the 20-day high"),
        side_means(rows, lambda r: r["atr_pct"], "ATR / price"),
        side_means(rows, lambda r: r["atr_rank"], "ATR percentile"),
        side_means(rows, lambda r: r["vol_ratio"], "Volume ratio"),
        side_means(rows, lambda r: r["sr"], "Spread / R"),
        side_means(rows, lambda r: r["fill"], "Fill price"),
        "",
        "## 4. Path before the entry",
        "",
        "Returns are close to close, ending at the prior close. Tightness is the prior 10-day range divided by ATR. "
        "A failed probe is a prior day whose high traded through the then-prior 20-day high and whose close finished "
        "back under that high. The count is over the five sessions before the trade.",
        "",
    ]
    for label, fn in (("Prior 1-day return", lambda r: r["ret1"]),
                      ("Prior 5-day return", lambda r: r["ret5"]),
                      ("10-day range / ATR", lambda r: r["tight"]),
                      ("Failed probes in the prior 5 days", lambda r: r["probes"])):
        lines += quartile_table(rows, fn, label)
        lines.append("")
    lines += [
        "| Feature | Winners | Losers |",
        "|---|---:|---:|",
        side_means(rows, lambda r: r["ret1"], "Prior 1-day return"),
        side_means(rows, lambda r: r["ret5"], "Prior 5-day return"),
        side_means(rows, lambda r: r["tight"], "10-day range / ATR"),
        side_means(rows, lambda r: r["probes"], "Failed probes"),
        "",
        f"NR7 on the prior day is {sum(r['nr7'] == 1 for r in rows)} of {len(rows)}. It does not vary inside this "
        "book, so it cannot separate winners. The screen already required a contraction or an NR7.",
        "",
        "## 5. Filters locked on 2024, then checked on 2025–2026",
        "",
        "Each filter's 2024 row was the reason it was locked. The 2025–2026 row was not used to choose it. "
        "A filter is ranked later by its out-of-sample mean, and only if it has at least 50 out-of-sample trades.",
        "",
        "| Filter | 2024 trades | 2024 win | 2024 mean | 2025–26 trades | 2025–26 win | 2025–26 mean | Lift vs 2025–26 book |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    oos_base = mean([r["net_r"] for r in base_oos])
    scored = []
    for name, fn in FILTERS:
        ins = [r for r in rows if r["day"] < OOS and fn(r)]
        oos = [r for r in rows if r["day"] >= OOS and fn(r)]
        lift = None if not oos else mean([r["net_r"] for r in oos]) - oos_base
        lines.append(
            f"| {name} | {len(ins)} | {fmt(None if not ins else sum(r['win'] for r in ins) / len(ins), '{:.1%}')} | "
            f"{fmt(mean([r['net_r'] for r in ins]))} | {len(oos)} | "
            f"{fmt(None if not oos else sum(r['win'] for r in oos) / len(oos), '{:.1%}')} | "
            f"{fmt(mean([r['net_r'] for r in oos]))} | {fmt(lift)} |")
        scored.append((name, fn, oos, lift))
    ranked = sorted([s for s in scored if len(s[2]) >= 50], key=lambda s: s[3], reverse=True)
    lines += [
        "",
        f"The unfiltered 2025–2026 book mean is {fmt(oos_base)} across {len(base_oos)} trades.",
        "",
        "### The filters that lift out-of-sample mean R the most",
        "",
    ]
    top = ranked[:3]
    if not top:
        lines.append("No locked filter has 50 out-of-sample trades.")
    for name, fn, oos, lift in top:
        kept = [r for r in rows if fn(r)]
        lines.append(
            f"- **{name}.** Out of sample: {len(oos)} trades, win rate "
            f"{sum(r['win'] for r in oos) / len(oos):.1%}, mean net R {fmt(mean([r['net_r'] for r in oos]))}, "
            f"t {fmt(tstat([r['net_r'] for r in oos]), '{:.2f}')}, lift {fmt(lift)} versus the unfiltered "
            f"2025–2026 mean. Full sample, same rule: {len(kept)} trades, mean {fmt(mean([r['net_r'] for r in kept]))}.")
    lines += [
        "",
        "### Does any filtered subset clear the gate?",
        "",
        "The gate on a filtered book, applied to 2025–2026: at least 100 trades, mean net R above 0 with t above 2, "
        f"mean above the 95th percentile of {SEEDS} random-direction means, and a positive mean in 2025 and in 2026. "
        "2024 is the year the filter was chosen, so it is shown and not counted as confirmation.",
        "",
        "| Filter | Sample | Trades | Mean | t | Random p95 | 2024 | 2025 | 2026 | Gate |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    any_go = False
    for name, fn, oos, _ in scored:
        sample = [r for r in rows if r["day"] >= OOS and fn(r)]
        g = gate(sample, ("2025", "2026"))
        y2024 = mean([r["net_r"] for r in rows if r["year"] == "2024" and fn(r)])
        any_go = any_go or g["go"]
        lines.append(
            f"| {name} | 2025–2026 | {g['n']} | {fmt(g['mean'])} | {fmt(g['t'], '{:.2f}')} | {fmt(g['p95'])} | "
            f"{fmt(y2024)} | {fmt(g['years'].get('2025'))} | {fmt(g['years'].get('2026'))} | "
            f"{'GO' if g['go'] else 'NO-GO'} |")
    whole = gate(base_oos, ("2025", "2026"))
    lines.append(
        f"| unfiltered A/B | 2025–2026 | {whole['n']} | {fmt(whole['mean'])} | {fmt(whole['t'], '{:.2f}')} | "
        f"{fmt(whole['p95'])} | {fmt(mean([r['net_r'] for r in rows if r['year'] == '2024']))} | "
        f"{fmt(whole['years'].get('2025'))} | {fmt(whole['years'].get('2026'))} | "
        f"{'GO' if whole['go'] else 'NO-GO'} |")
    lines += [
        "",
        "No filtered subset clears that gate." if not any_go else "At least one filtered subset clears the gate.",
        "",
        "## 6. Costs and the winner label",
        "",
        f"Mean cost is {fmt(mean([r['cost_r'] for r in wins]))} R on winners and "
        f"{fmt(mean([r['cost_r'] for r in losses]))} R on losers. Losers pay more because a stop is a full R "
        "on a spread that is large relative to that R, and because cheap names need more shares.",
        f"{sum(r['gross_r'] > 0 and r['net_r'] <= 0 for r in rows)} fills were gross winners and net losers. "
        f"{sum(r['net_r'] > 0 and r['gross_r'] <= 0 for r in rows)} net winners were gross losers, so the cost model "
        "does not create the win.",
        f"Of {len(wins)} net winners, {sum(r['extra'] > 0 for r in wins)} stay winners after another cent a share "
        "on each side. The book's mean gross R is {fmt(mean(gross))}, already below zero, so a cheaper cost model "
        "does not turn the book into a win. The same is true inside F1: "
        f"gross mean {fmt(mean([r['gross_r'] for r in rows if r['sr'] <= 0.03]))}, "
        f"net mean {fmt(mean([r['net_r'] for r in rows if r['sr'] <= 0.03]))}.",
        "",
        "## What to keep",
        "",
    ]
    if top:
        best = top[0]
        lines.append(
            f"The largest out-of-sample lift among the locked filters with at least 50 trades is {best[0]}, "
            f"lift {fmt(best[3])} R. It is a description of which fills lost less. It is not a passing book.")
    lines += [
        "",
        "## למה ה־36% זכו",
        "",
        _hebrew(rows, wins, top, any_go),
        "",
    ]
    return "\n".join(lines) + "\n"


def _hebrew(rows, wins, top, any_go):
    n_target = sum(bucket(r) == "target, the 1.5R order" for r in wins)
    n_small = sum(bucket(r) == "time stop, gross under 1R" for r in wins)
    f1 = [r for r in rows if r["sr"] <= 0.03]
    b = [r for r in rows if r["grade"] == "B"]
    names = ", ".join(name.split(" ", 1)[0] for name, _, _, _ in top[:3]) or "אין"
    verdict = "אף סינון לא עבר את השער" if not any_go else "יש סינון שעבר את השער"
    return (
        f"הזוכות, {len(wins)} מתוך {len(rows)}, כמעט לא הגיעו ליעד. {n_small} מהן נסגרו בשעה 15:55 ברווח גולמי "
        f"קטן מ־R אחד, ורק {n_target} נגעו ביעד של אחד וחצי R. אף זוכה לא נבנתה על מהלך של 2R, כי הפקודה יוצאת "
        f"ביעד ולא מחכה לו. מה שחזר ב־2024, לפני שנפתחו 2025 ו־2026: מרווח שגובהו לכל היותר 3% מהמרחק לסטופ "
        f"({len(f1)} עסקאות, ממוצע כמעט אפס), וציון א׳ ולא ב׳. ציון ב׳, {len(b)} עסקאות, הפסיד בממוצע "
        f"{_he(mean([r['net_r'] for r in b]))} R. בסיס רפוי, שתי נגיעות כושלות בשיא, וכניסה שכבר מעל שיא 20 הימים "
        f"הפסידו יותר. המדד מעל הממוצע לא עזר. הסינונים שננעלו ואז נמדדו מחוץ למדגם הם {names}. {verdict}. "
        "העלות לא המציאה את הזוכות: הממוצע הגולמי של כל הספר כבר שלילי."
    )


def main():
    text = render(load())
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
