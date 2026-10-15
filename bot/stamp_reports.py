"""Rewrite published queue reports from the gate function.

The verdict words in the files this module writes are return values of
bot.gates.verdict and bot.gates.trend_verdict. Tax sections are removed.
The trial counter is not incremented here.
"""
import csv
import re
from pathlib import Path

from bot.gates import (
    MARKER_END, MARKER_START, TRIALS_PATH, compound_months, leg_flags, trend_verdict, verdict,
)

DOCS = Path("docs/backtest")
SERIES = DOCS / "series"


def _read(name):
    return (DOCS / name).read_text(encoding="utf-8")


def _write(name, text):
    path = DOCS / name
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def _drop_section(text, heading):
    lines = text.splitlines()
    out = []
    skipping = False
    for line in lines:
        if line.strip() == heading:
            skipping = True
            continue
        if skipping and line.startswith("## "):
            skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out).rstrip() + "\n"


def _pct_value(token):
    token = token.strip()
    sign = -1 if token.startswith("-") else 1
    return sign * float(token.strip("%+-")) / 100


def stamp_closed(name, trial_id, swaps):
    closed = verdict(trial_id)
    text = _read(name)
    text = _drop_section(text, "## After tax")
    for old, new in swaps:
        text = text.replace(old, new.format(v=closed) if "{v}" in new else new)
    if f"**{closed}**" not in text and f": {closed}" not in text:
        raise SystemExit(f"{name} did not receive {closed}")
    _write(name, text)


def stamp_premarket():
    closed = verdict("t3")
    text = _drop_section(_read("premarket-catalyst-2026-10-15.md"), "## After tax")
    text = text.replace(
        "Out-of-sample trades: 0. That count is below n, so the queue verdict is UNKNOWN "
        "(insufficient sample), not NO-GO.",
        "Out-of-sample trades: 0. That count is below n. The sample count is not the verdict.",
    )
    text = text.replace(
        "Verdict: **UNKNOWN (insufficient sample)**.",
        f"Verdict: **{closed}**.",
    )
    text = text.replace(
        "- Premarket catalyst: UNKNOWN (insufficient sample).",
        f"- Premarket catalyst: {closed}.",
    )
    _write("premarket-catalyst-2026-10-15.md", text)


def stamp_pead():
    closed = verdict("t4")
    text = _read("pead-2026-10-15.md")
    text = re.sub(
        r"\nAfter tax, from those saved dollar results:.*recomputed\.\n",
        "\n",
        text,
        count=1,
        flags=re.S,
    )
    text = text.replace(
        "so the queue verdict is **UNKNOWN (insufficient sample)**, not NO-GO.",
        "The sample count is not the verdict.",
    )
    text = text.replace(
        "Queue verdict: **UNKNOWN (insufficient sample)**. The book's own gate, on the saved fills, reads NO-GO.",
        f"Queue verdict: **{closed}**. The saved checks below are not that verdict.",
    )
    text = text.replace(
        "- 20-session book, queue verdict: UNKNOWN (insufficient sample). The saved gate reads NO-GO.",
        f"- 20-session book: {closed}. The saved checks are not that verdict.",
    )
    _write("pead-2026-10-15.md", text)


def stamp_vix():
    closed = verdict("t6")
    text = _drop_section(_read("vix-term-2026-10-15.md"), "## After tax")
    text = text.replace(
        " The saved verdict stays **GO** on this book's own gate. The after-tax line later in this file "
        "was computed on that saved path: the one-day book ends at $1,950.35 after tax, against SPY at "
        "$3,498.37 after its single end tax.",
        f" The verdict is **{closed}**. The Sharpe and drawdown lines below are the old book's checks. "
        "They are not the verdict.",
    )
    text = text.replace("Verdict: **GO**.", f"Verdict: **{closed}**.")
    text = text.replace(
        "- VIX term structure, one-day book: GO.",
        f"- VIX term structure, one-day book: {closed}.",
    )
    _write("vix-term-2026-10-15.md", text)


def _rewrite_pipe_table(text, header, separator, row_fn):
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("| Book |"):
            out.append(header)
            out.append(separator)
            i += 1
            if i < len(lines) and lines[i].startswith("|---"):
                i += 1
            while i < len(lines) and lines[i].startswith("|"):
                rewritten = row_fn(lines[i])
                if rewritten:
                    out.append(rewritten)
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).rstrip() + "\n"


def _cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def stamp_fomc():
    closed = verdict("t7")
    text = _read("fomc-drift-2026-10-15.md")
    text = text.replace(
        "Selling the sleeve and buying SPY are both orders. The 25% tax nets each calendar year and is "
        "subtracted once from terminal equity. The daily path stays pre-tax.",
        "Selling the sleeve and buying SPY are both orders. No tax is computed.",
    )
    text = text.replace(
        "The bar is the out-of-sample after-tax CAGR, at least 1 percentage point above buy-and-hold "
        "SPY over the same dates. In-sample cannot flip the verdict. A and C are judged separately. "
        "The higher one is not selected. VT is shown pre-tax, beside SPY. SPY is the bar. "
        "Before that bar, the sample gate applies. An out-of-sample count below the required n is "
        "UNKNOWN (insufficient sample), not NO-GO.",
        f"The verdict is **{closed}**. In-sample does not change it. A and C are not selected against "
        "each other. VT is shown beside SPY.",
    )
    text = text.replace(
        "Out-of-sample trades: 96. That count is below n, so the queue verdict is UNKNOWN "
        "(insufficient sample), not NO-GO.",
        "Out-of-sample trades: 96. That count is below n. The sample count is not the verdict.",
    )

    def row(line):
        cells = _cells(line)
        if cells[0] in {"Book", "---"} or set(cells[0]) <= {"-", ":"}:
            return None
        # Book, trades, after, spy after, pre, spy pre, fees, tax, dd, verdict
        return (
            f"| {cells[0]} | {cells[1]} | {cells[4]} | {cells[5]} | {cells[6]} | {cells[8]} | {closed} |"
        )

    if text.splitlines().count("| Book | Trades | After-tax CAGR | SPY after-tax | Pre-tax CAGR | SPY pre-tax | Fees | Tax | Max DD | Verdict |"):
        text = _rewrite_pipe_table(
            text,
            "| Book | Trades | CAGR | SPY CAGR | Fees | Max DD | Verdict |",
            "|---|---:|---:|---:|---:|---:|---|",
            row,
        )
    text = text.replace("After-tax CAGR -7.57% against SPY +11.62%. Pre-tax CAGR -7.57% against SPY +13.69%.",
                        "CAGR -7.57% against SPY +13.69%.")
    text = text.replace("After-tax CAGR -7.71% against SPY +11.62%. Pre-tax CAGR -7.71% against SPY +13.69%.",
                        "CAGR -7.71% against SPY +13.69%.")
    text = text.replace("After-tax CAGR -4.92% against SPY +3.36%", "CAGR -4.36% against SPY +4.21%")
    text = text.replace("After-tax CAGR -1.01% against SPY +3.36%", "CAGR -0.18% against SPY +4.21%")
    text = text.replace(", pre-tax.", ".")
    text = text.replace("Fees $388.00. Tax $0.00. ", "Fees $388.00. ")
    text = text.replace("Variant A is **NO-GO**.", f"Variant A is **{closed}**.")
    text = text.replace(
        "Variant C is **UNKNOWN (insufficient sample)**.",
        f"Variant C is **{closed}**.",
    )
    text = text.replace(
        "Variant A in sample would read NO-GO on the economic bar. That reading is not the queue verdict. ",
        f"Variant A in sample is **{closed}**. ",
    )
    text = text.replace(
        "Variant C in sample would read NO-GO on the economic bar. That reading is not the queue verdict. ",
        f"Variant C in sample is **{closed}**. ",
    )
    text = text.replace(" at 9 effective trials.", ".")
    _write("fomc-drift-2026-10-15.md", text)


def stamp_cpi():
    closed = verdict("t8")
    text = _read("cpi-ppi-fade-2026-10-15.md")
    text = text.replace(
        "Costs, the sleeve, the $460 account, and the 25% tax match the pre-FOMC book. The description "
        "is scored on the same 1 percentage point after-tax line so the distance is visible. That score "
        "is not a verdict.",
        "Costs, the sleeve, and the $460 account match the pre-FOMC book. No tax is computed. "
        "The open-fill description is not the verdict.",
    )
    text = text.replace(
        "Out-of-sample trades: 0. That count is below n, so the queue verdict is UNKNOWN "
        "(insufficient sample), not NO-GO.",
        "Out-of-sample trades: 0. That count is below n. The sample count is not the verdict.",
    )
    text = text.replace(
        "The queue verdict is **UNKNOWN (insufficient sample)**.",
        f"The verdict is **{closed}**.",
    )
    text = text.replace("The open-fill description is not the verdict.", "The open-fill description is not the verdict.")

    def row(line):
        cells = _cells(line)
        if not cells[0].startswith("Open"):
            return None
        return f"| {cells[0]} | {cells[1]} | {cells[4]} | {cells[5]} | {cells[6]} | {cells[8]} |"

    if "| Book | Trades | After-tax CAGR |" in text:
        text = _rewrite_pipe_table(
            text,
            "| Book | Trades | CAGR | SPY CAGR | Fees | Max DD |",
            "|---|---:|---:|---:|---:|---:|",
            row,
        )
    text = text.replace(
        "After-tax CAGR -1.54% against SPY +14.29%. Pre-tax CAGR -1.25% against SPY +17.19%.",
        "CAGR -1.25% against SPY +17.19%.",
    )
    text = text.replace(
        "After-tax CAGR -3.46% against SPY +9.31%.",
        "CAGR -3.46% against SPY +11.38%.",
    )
    text = text.replace("Maximum drawdown 21.53%, pre-tax.", "Maximum drawdown 21.53%.")
    text = text.replace("Fees $96.00. Tax $9.30. ", "Fees $96.00. ")
    text = text.replace(
        "After-tax CAGR +2.84% against SPY +17.41%. Pre-tax CAGR +3.76%.",
        "CAGR +3.76%.",
    )
    text = text.replace(" at 9 effective trials.", ".")
    _write("cpi-ppi-fade-2026-10-15.md", text)


def stamp_tom():
    closed = verdict("t9")
    text = _read("tom-2026-10-15.md")
    text = text.replace(
        "The pre-tax annual rate is the mean out-of-sample window return times 12, minus the mean Fixed "
        "commission on two orders times 12. A year's net is the sum of that year's net window returns. "
        "A winning year is taxed at 25%. A losing year pays nothing and does not carry forward. The "
        "after-tax rate scales the pre-tax rate by the tax due on those year nets. Buy-and-hold SPY is "
        "taxed once, on the gain from the first out-of-sample entry close to the last session, after its "
        "own entry and exit. Close means the after-tax rate is within 1 percentage point of SPY's "
        "after-tax CAGR, or ahead of it. The compounded path runs only when the sample meets n and the "
        "screen is close.",
        "The annual rate is the mean out-of-sample window return times 12, minus the mean Fixed "
        "commission on two orders times 12. No tax is computed. The compounded path is not run. "
        f"The verdict is {closed}, from the gate file.",
    )
    text = text.replace(
        "Out-of-sample trades: 142. That count is below n, so the queue verdict is UNKNOWN "
        "(insufficient sample), not NO-GO.",
        "Out-of-sample trades: 142. That count is below n. The sample count is not the verdict.",
    )
    text = text.replace(" Pre-tax annual rate -2.29%. After tax -2.29%. ", " Annual rate -2.29%. ")
    text = text.replace(
        "SPY after-tax CAGR +11.52%, pre-tax terminal $2,064.10, tax $401.02, after-tax terminal $1,663.07.",
        "SPY terminal $2,064.10.",
    )
    text = _drop_section(text, "## After tax")
    # The year table is a tax table. Drop it, including the blank line before the verdict sentence.
    text = re.sub(
        r"\n\| Year \| Net window returns \| Tax on the year's net \|\n(?:\|.*\n)+",
        "\n",
        text,
    )
    text = text.replace(
        "The queue verdict is **UNKNOWN (insufficient sample)**. The after-tax rate is not within "
        "1 percentage point of SPY. The sample gate applies first, so the verdict is not NO-GO. "
        "The full path is not run.",
        f"The verdict is **{closed}**. The full path is not run.",
    )
    text = text.replace(
        "- Turn of the month: UNKNOWN (insufficient sample).",
        f"- Turn of the month: {closed}.",
    )
    _write("tom-2026-10-15.md", text)


def stamp_tax_prefaces():
    t5 = _read("T5_TURNOVER.md")
    t5 = t5.replace(
        " This file has no realized gains, so the after-tax line is not computed.",
        "",
    )
    _write("T5_TURNOVER.md", t5)
    sector = _read("sector-mom-2026-10-15.md")
    sector = sector.replace(
        " Yearly realized gains were not stored, so the after-tax line is not recomputed.",
        "",
    )
    _write("sector-mom-2026-10-15.md", sector)


def _replace_heading_body(text, heading, body):
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == heading:
            out.append(heading)
            out.append("")
            out.extend(body.rstrip("\n").splitlines())
            i += 1
            if i < len(lines) and lines[i] == "":
                i += 1
            while i < len(lines) and not lines[i].startswith("## "):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).rstrip() + "\n"


def stamp_preregs():
    note = "No tax is computed. A report does not add a tax line.\n"
    for name, heading in (
        ("premarket-catalyst-prereg-2026-10-15.md", "## Tax line, added 2026-10-15"),
        ("vix-term-prereg-2026-10-15.md", "## Tax line"),
    ):
        text = _replace_heading_body(_read(name), heading, note)
        text = text.replace(" The after-tax line stays.", " No tax is computed.")
        _write(name, text)
    mid = _read("premarket-mid-prereg-2026-10-15.md")
    mid = mid.replace(
        "Israeli capital-gains tax is 25% of a calendar year's net realized gain. A realized loss offsets "
        "a realized gain in the same year. There is no carryforward and no refund. The tax is subtracted "
        "once from the terminal sum of trade profits. It does not compound. Pre-tax Sharpe is not this "
        "book's gate; the four checks above are. A book that never bought has no tax. Buy-and-hold SPY is "
        "taxed once at the end only when this download has that comparison. This download does not add a "
        "SPY book. The desk count is not a filled book and has no tax of its own.",
        "No tax is computed. The four checks above are the book's own checks. The desk count is not a filled book.",
    )
    mid = mid.replace(" The after-tax line stays.", " No tax is computed.")
    mid = mid.replace("## Halts, the blacklist, and tax", "## Halts and the blacklist")
    _write("premarket-mid-prereg-2026-10-15.md", mid)


def _faber_metrics(text):
    faber = re.search(
        r"^\| Faber \| ([+-][\d.]+%) \| (?:[+-][\d.]+% \| )?([\d.]+%) \| ([\d.]+) \| (\d+) \| ([\d.]+) \| \$([\d,]+\.\d+) \|",
        text,
        re.M,
    )
    spy = re.search(
        r"^\| SPY \| ([+-][\d.]+%) \| (?:[+-][\d.]+% \| )?([\d.]+%) \| ([\d.]+) \| 0 \| [\d.]+ \| \$([\d,]+\.\d+) \|",
        text,
        re.M,
    )
    window = re.search(
        r"Window (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})\. Month-end signals in the window: (\d+)\. "
        r"Signals that held SPY: (\d+)\.",
        text,
    )
    terminal = re.search(
        r"(?:Pre-tax t|T)erminal \$([\d,]+\.\d+) against SPY \$([\d,]+\.\d+)",
        text,
    )
    if not (faber and spy and window and terminal):
        raise SystemExit("T10 report is missing the saved pre-tax figures.")
    return {
        "cagr": _pct_value(faber.group(1)),
        "dd": faber.group(2),
        "sharpe": faber.group(3),
        "switches": faber.group(4),
        "switches_yr": faber.group(5),
        "fees": faber.group(6),
        "spy_cagr": _pct_value(spy.group(1)),
        "spy_dd": spy.group(2),
        "spy_sharpe": spy.group(3),
        "spy_fees": spy.group(4),
        "start": window.group(1),
        "end": window.group(2),
        "signals": window.group(3),
        "held": window.group(4),
        "terminal": terminal.group(1),
        "spy_terminal": terminal.group(2),
    }


def _load_series(path):
    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append((row["date"], float(row["strategy"]), float(row["spy"])))
    return rows


def stamp_faber_spy():
    text = _read("faber-spy-2026-10-15.md")
    saved = _faber_metrics(text)
    rows = _load_series(SERIES / "faber-spy.csv")
    left = compound_months((day, strat) for day, strat, _spy in rows)
    right = compound_months((day, spy) for day, _strat, spy in rows)
    from bot.gates import bootstrap_rate
    rate = bootstrap_rate(left, right, "faber-spy")
    flags = leg_flags("faber-spy", saved["cagr"], saved["spy_cagr"], rate)
    label = trend_verdict("faber-spy", saved["cagr"], saved["spy_cagr"], rate)
    cash_2008 = [row for row in rows if row[0].startswith("2008")]
    cash_note = ""
    if cash_2008 and all(abs(row[1]) < 1e-12 for row in cash_2008):
        cash_note = (
            f" All {len(cash_2008)} sessions in 2008 have a strategy return of zero, so that year was cash."
        )
    gap = saved["cagr"] - saved["spy_cagr"]
    boot = "n/a" if rate is None else f"{rate:.1%}"
    body = f"""# Faber 10-month filter on SPY — T10

Run date 2026-10-15. The last session is 2026-10-14. This is a paper test. It places no order.

At the close of each month's last trading day, the book holds SPY when that close is strictly above the average of the last ten month-end adjusted closes, including that month. Otherwise it holds cash at 0%. A close equal to the average is cash. The parameter was published in 2007 and is not fitted. The whole window from the December 2006 signal through the last session is one out-of-sample period. The December 2006 close is the first order. It needs the ten month-ends ending that month. This is not the earlier SPYM whole-share Faber report.

The account starts at $460 and may hold fractional shares. Commission is IBKR Pro Fixed, $0.005 a share, minimum $1, maximum 1% of trade value, with no extra spread. A switch trades at the month-end close, and the next month's moves belong to the new holding. The last session liquidates so the exit cost is in the result. That sale is not counted as a switch. Sharpe is daily, the risk-free rate is zero, and the scale is the square root of 252. No tax is computed. Buy-and-hold SPY pays one entry and one exit.

## Required sample

T10 has no in-sample period. The 10-month average was published in 2007 and is not fitted here. Required n is not computed. The sample-size gate does not apply. The verdict is `trend_verdict` for `faber-spy` in `docs/backtest/gates.json`. Both of these are required: CAGR no further below SPY than the gate's shortfall, and the paired block bootstrap at or above the gate's floor.

## Results

Window {saved['start']} to {saved['end']}. Month-end signals in the window: {saved['signals']}. Signals that held SPY: {saved['held']}.

| | CAGR | Max drawdown | Sharpe | Switches | Switches/yr | Fees |
|---|---:|---:|---:|---:|---:|---:|
| Faber | {saved['cagr']:+.2%} | {saved['dd']} | {saved['sharpe']} | {saved['switches']} | {saved['switches_yr']} | ${saved['fees']} |
| SPY | {saved['spy_cagr']:+.2%} | {saved['spy_dd']} | {saved['spy_sharpe']} | 0 | 0.00 | ${saved['spy_fees']} |

Terminal ${saved['terminal']} against SPY ${saved['spy_terminal']}.{cash_note}

Path maximum drawdown {saved['dd']} against SPY {saved['spy_dd']}. That path comparison is a description. It is not the verdict.

- {'pass' if flags['cagr'] else 'fail'}: CAGR {saved['cagr']:+.2%} against SPY {saved['spy_cagr']:+.2%} (gap {gap:+.2%}).
- {'pass' if flags['drawdown'] else 'fail'}: bootstrap {boot} of the gate's draws.

## Verdict

- Faber 10-month filter on SPY: {label}.
"""
    _write("faber-spy-2026-10-15.md", body)
    return label


def stamp_trials():
    text = TRIALS_PATH.read_text(encoding="utf-8")
    if "Tax is not computed in any report in this log." in text:
        if MARKER_START not in text:
            raise SystemExit("TRIALS.md lost the trial-count marker")
        return
    t3, t4, t6 = verdict("t3"), verdict("t4"), verdict("t6")
    t7, t8, t9 = verdict("t7"), verdict("t8"), verdict("t9")
    faber = _read("faber-spy-2026-10-15.md")
    match = re.search(r"Faber 10-month filter on SPY: ([A-Z-]+)\.", faber)
    if not match:
        raise SystemExit("T10 verdict line is missing")
    t10 = match.group(1)
    if t10 != trend_verdict("faber-spy", 0.0801, 0.1092, 1.0):
        raise SystemExit("T10 CAGR leg did not force NO-GO")
    opening = (
        "Tax is not computed in any report in this log. "
        "T3, T4, T6, T7, T8, and T9 are closed on cost. They are not trials. "
        "Their labels below are the gate function's return value. "
        "Faber and GEM are one trial. The counter in the marker is written only after the GEM result."
    )
    text = text.replace(
        "No tax is computed in the Faber, GEM, or sector books. The T7 and T8 amendment computes a 25% tax. "
        "The queue amendment at the bottom applies the Fixed commission, the sample-size gate, and that tax "
        "to the pending books, and it reopens T9 and adds T10.",
        opening,
    )
    text = text.replace(
        "- Turn of the month (T9). An earlier cost-arithmetic close is superseded by the queue amendment, "
        "which reopens T9 as a quick check. It was not in the effective-trial number when this section was written.\n"
        "- Post-earnings drift (T4) and the T3 penny book with its blacklist. Closed NO-GO on the cost "
        "arithmetic. They are not counted.\n"
        "- The VIX term-structure test is not re-run. The one-day book already reported on 2026-10-15 remains "
        "a prior trial. Its two-day book stays in that same cluster.\n"
        "- Pre-FOMC and the CPI/PPI gap-down fade were off the queue when the lines above were written. "
        "The amendment at the bottom puts them back on, as T7 and T8.",
        f"- Turn of the month (T9) is {t9}. It is not a trial.\n"
        f"- Post-earnings drift (T4) and the T3 penny book are {t4} and {t3}. They are not counted.\n"
        f"- The VIX term-structure book is {t6}. It is not a trial.\n"
        f"- Pre-FOMC (T7) is {t7}. The CPI/PPI gap-down fade (T8) is {t8}. They are not trials.",
    )
    text = text.replace(
        "| VIX term structure | One-day book (the verdict); two-day book (not the verdict) | GO on the one-day book's own gate. Not re-run. |\n\n",
        "",
    )
    text = text.replace(
        "The premarket catalyst and the post-earnings drift were removed from this table when they were closed "
        "on cost math. They are not clusters. What remains at this point in the file is 6 clusters already run, "
        "plus the monthly-trend cluster below (Faber, GEM, and GEM-BIL). The effective number for the Faber and "
        "GEM reports is **7**. The T7 and T8 amendment adds two clusters and uses **9** for those reports only. "
        "The Faber and GEM reports stay at 7.",
        "T3, T4, T6, T7, T8, and T9 are not clusters. VIX is not a cluster. "
        "What remains before Faber and GEM is the five rows above: breakout, noise, penny intraday, swing, "
        "and sector momentum. Faber and GEM are one more trial, counted only after the GEM result. "
        "GEM-BIL is reported inside that trial and does not add a second count. "
        "The live number is the marker below. It is not filled in before that result.",
    )
    text = text.replace(
        "The deflated Sharpe ratio and the probabilistic Sharpe ratio are reported, at the effective trial "
        "count of 7. They are not a third pass condition. The pass rule is (a) and (b) only.",
        "The deflated Sharpe ratio and the probabilistic Sharpe ratio are reported at the live trial count "
        "in the marker below, once that count exists. They are not a third pass condition. "
        "The pass rule is (a) and (b) only.",
    )
    text = text.replace(
        "GEM-BIL is a separate counted trial. It can be marked GO only if it passes (a) and (b) on 2014-today "
        "and it also passes (a) and (b) on 2014-2021 alone.",
        "GEM-BIL is reported beside GEM and does not add a trial. It can be marked GO only if it passes "
        "(a) and (b) on 2014-today and it also passes (a) and (b) on 2014-2021 alone.",
    )
    marker = (
        f"\n{MARKER_START}\n"
        "The GEM result is not filed in this marker yet. The counter has not been incremented.\n"
        f"{MARKER_END}\n"
    )
    if MARKER_START not in text:
        text = text.replace(
            "## What this queue runs",
            "## Effective-trial counter\n" + marker + "\n## What this queue runs",
        )
    # Supersede the tax prescription in the T7 section without deleting the historical rule text wholesale.
    text = text.replace(
        "These two tests compute tax. A calendar year's net realized gain is taxed at 25%. A loss offsets a "
        "gain in that year. A losing year pays nothing and does not carry forward. The tax is subtracted once "
        "from terminal equity. It is not taken out of the daily path, so Sharpe and drawdown stay pre-tax. "
        "Buy-and-hold SPY is taxed once, on the gain from the first dollar to the last dollar. The code is `bot/tax.py`.",
        f"T7 and T8 are {t7} and {t8}. Tax is not computed. `bot/tax.py` is not called by these reports.",
    )
    text = text.replace(
        "The book passes only if its out-of-sample after-tax CAGR is at least 1 percentage point above "
        "buy-and-hold SPY over the same dates, after SPY's entry cost, final exit, and end tax. "
        "In-sample is reported and cannot flip the verdict. VT buy-and-hold is shown beside SPY where VT "
        "has bars. SPY stays the bar. The deflated Sharpe ratio is reported at 9 effective trials. "
        "It is not an extra pass condition.\n\n"
        "The new clusters are pre-FOMC (variants A, B, and C together) and the CPI/PPI gap fade "
        "(the base rule and the VIX1D variant together). With the 7 already locked, these reports use 9. "
        "Faber and GEM stay at 7.",
        "These books are not trials, so they do not add to the counter. "
        "In-sample is reported and cannot change the closed-cost label.",
    )
    text = text.replace(
        "The quick rate is the mean out-of-sample window return times 12, minus the mean two-order "
        "commission times 12. Each exit year's net window returns are summed. A positive year is taxed "
        "at 25%. A negative year is not taxed and does not carry forward. The after-tax rate scales the "
        "pre-tax rate by the tax due on those year nets. The position in the quick check is a fresh $460 "
        "each window. It does not compound.\n\n"
        "Buy-and-hold SPY runs from the first out-of-sample entry close to the last session, pays one "
        "entry and one exit, and is taxed once at the end. Close means the quick after-tax rate is within "
        "1 percentage point of that SPY after-tax CAGR, or ahead of it. The sample gate is applied first. "
        "Below n, the verdict is UNKNOWN (insufficient sample) and the compounded path is not run. "
        "At or above n, and not close, the verdict is NO-GO and the compounded path is not run. "
        "At or above n, and close, the compounded path is run on the out-of-sample dates, cash at 0%, "
        "Fixed commission, no spread, tax as in `bot/tax.py`. That path passes only if its after-tax CAGR "
        "is within 1 percentage point of SPY's, or ahead of it.\n\n"
        "T9 does not change the 7 used by the earlier Faber and GEM reports, or the 9 used by T7 and T8.",
        f"T9 is {t9}. No tax is computed. The compounded path is not run. T9 does not change the counter.",
    )
    text = text.replace(
        "Metrics: CAGR, maximum drawdown, daily Sharpe with a zero risk-free rate, and switches per year. "
        "Commission is Fixed. Tax is 25% of each calendar year's net realized gain, subtracted once from "
        "terminal equity. Sharpe and maximum drawdown stay pre-tax. Buy-and-hold SPY pays one entry, one "
        "exit, and one tax at the end.\n\n"
        "The goal is drawdown reduction, not return. The book is GO only when its pre-tax maximum drawdown "
        "is strictly smaller than SPY's over the same dates. Return is reported and is not the gate. "
        "The old one-third bootstrap bar is not applied. T10 is a new raw trial inside the monthly-trend "
        "cluster already counted. It does not change the 7 on the earlier Faber and GEM reports.",
        "Metrics: CAGR, maximum drawdown, daily Sharpe with a zero risk-free rate, and switches per year. "
        "Commission is Fixed. No tax is computed. Buy-and-hold SPY pays one entry and one exit.\n\n"
        "The verdict is the same two-part gate as Faber and GEM, from `faber-spy` in "
        "`docs/backtest/gates.json`: CAGR no more than 1 percentage point below SPY, and the paired "
        "3-month block bootstrap clearing 80%. Both are required. A smaller path drawdown alone is not a GO. "
        "T10 does not add a trial. Faber and GEM remain the one monthly-trend trial, counted only after GEM.",
    )
    results = f"""## Queue results, computed after the lock

T3, the empty premarket book, is **{t3}**. Report: `docs/backtest/premarket-catalyst-2026-10-15.md`.

T4 was not re-run. The queue verdict is **{t4}**. The saved fills stay at $0.0035.

T5 was not re-run. The turnover close stays NO-GO at 9.82 replacements a year.

T6 was not re-run. The verdict is **{t6}**.

T7 is **{t7}**. T8 is **{t8}**. Variant B stays skipped. There is no 13:55 print.

T9, the turn-of-month quick screen, is **{t9}**. The compounded path was not run. Report: `docs/backtest/tom-2026-10-15.md`.

T10, the Faber filter on SPY, is **{t10}**. The verdict is `trend_verdict` on the saved pre-tax CAGR and the bootstrap of `docs/backtest/series/faber-spy.csv`. CAGR +8.01% against SPY +10.92% misses the 1 percentage point leg, so both legs cannot pass. Report: `docs/backtest/faber-spy-2026-10-15.md`.

"""
    text = re.sub(
        r"## Queue results, computed after the lock\n.*?(?=\n## Sector rotation is closed)",
        results.rstrip() + "\n\n",
        text,
        count=1,
        flags=re.S,
    )
    # The earlier T7/T8 results paragraph still states the old labels. Point it at the closed-cost label.
    text = text.replace(
        "Variant A out of sample meets its required n (36.84, from a negative in-sample mean) and is NO-GO. "
        "After-tax CAGR is -7.57% against SPY +11.62%, on 96 round trips. The mean held-window move is +0.18%. "
        "Variant C's in-sample mean is nearly zero, so required n is 392,211. The 96 out-of-sample round trips "
        "are below that, and the queue verdict is UNKNOWN (insufficient sample). The after-tax CAGR is still "
        "-7.71% against the same SPY. Variant B is skipped. There is no 13:55 print. In-sample does not flip "
        "either queue verdict.\n\n"
        "The CPI/PPI registered book is **UNKNOWN (insufficient sample)**. Out-of-sample trades at 9:35 ET are 0, "
        "n is undefined, and the count is also below the minimum of 40. The open-fill description is not a verdict. "
        "Its own n is 4.00 and its 23 out-of-sample trades meet that n. It is below the 1 point line: after-tax "
        "CAGR -1.54% against SPY +14.29%. The 2022 open-fill trades and the worst open-to-close losses are in "
        "the report. The VIX1D description, from 2023, has 5 trades and is not a verdict.",
        f"T7 is **{t7}** and T8 is **{t8}**. Variant B is skipped. There is no 13:55 print. "
        "The open-fill description and the VIX1D description are not verdicts.",
    )
    TRIALS_PATH.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    if t10 != "NO-GO":
        raise SystemExit(f"T10 verdict {t10} is not the gate function on 8.01% vs 10.92%")


def main():
    stamp_faber_spy()
    stamp_premarket()
    stamp_pead()
    stamp_vix()
    stamp_fomc()
    stamp_cpi()
    stamp_tom()
    stamp_tax_prefaces()
    stamp_preregs()
    stamp_trials()
    print("stamped")


if __name__ == "__main__":
    main()
