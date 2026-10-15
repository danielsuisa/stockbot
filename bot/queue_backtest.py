"""T9 turn-of-month quick screen, then T10 Faber on SPY. Research only.

The rules are the queue amendment in docs/backtest/TRIALS.md. Nothing here places
or transmits an order. T9 runs the compounded path only when the quick screen is
close and the out-of-sample count meets n.
"""
import datetime as dt
import statistics
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.faber_spy import LOOKBACK, drawdown_reduced, faber_signals, month_ends, run_faber
from bot.macro_backtest import _pct, _yahoo
from bot.queue_rules import UNKNOWN, queue_verdict, sample_lines
from bot.tax import after_tax_terminal
from bot.tom_exec import (
    ACCOUNT, buy_hold, close_enough, held_sessions, quick_screen, run_tom_path, tom_windows, window_fee,
)
from bot.trend_exec import cagr, max_drawdown

NY = ZoneInfo("America/New_York")
DOCS = Path("docs/backtest")
SERIES = DOCS / "series"
MARGIN = 0.01


def _num(value, digits=2):
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _dd(value):
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def _load():
    spy = _yahoo("SPY")
    today = dt.datetime.now(NY).date().isoformat()
    sessions = sorted(day for day in spy["adj"] if day < today and day in spy["close"])
    return spy, sessions, sessions[-1], today


def _t9(spy, sessions, last, today):
    windows = []
    gross = []
    fees = []
    for window in tom_windows(sessions):
        entry, exit_day = window["entry"], window["exit"]
        if entry not in spy["adj"] or exit_day not in spy["adj"]:
            continue
        raw_entry = spy["close"].get(entry) or spy["adj"][entry]
        raw_exit = spy["close"].get(exit_day) or spy["adj"][exit_day]
        fee = window_fee(raw_entry, raw_exit)
        if fee is None:
            continue
        windows.append(window)
        gross.append(spy["adj"][exit_day] / spy["adj"][entry] - 1)
        fees.append(fee)
    ins = [(w, g, f) for w, g, f in zip(windows, gross, fees) if "2000-01-01" <= w["exit"] <= "2014-12-31"]
    oos = [(w, g, f) for w, g, f in zip(windows, gross, fees) if "2015-01-01" <= w["exit"] <= last]
    is_windows, is_gross, is_fees = (list(part) for part in zip(*ins)) if ins else ([], [], [])
    oos_windows, oos_gross, oos_fees = (list(part) for part in zip(*oos)) if oos else ([], [], [])
    is_nets = [g - f / ACCOUNT for g, f in zip(is_gross, is_fees)]
    screen = quick_screen(oos_windows, oos_gross, oos_fees) if oos_windows else None
    start = oos_windows[0]["entry"] if oos_windows else None
    spy_book = None
    spy_cagr = None
    if start and last in spy["adj"] and start in spy["adj"]:
        spy_book = buy_hold(spy["adj"][start], spy["adj"][last])
        spy_cagr = cagr(ACCOUNT, spy_book["after"], start, last)
    block, n = sample_lines("T9 turn of the month", is_nets, len(oos_windows))
    economic = bool(screen) and close_enough(screen["after"], spy_cagr, MARGIN)
    verdict = queue_verdict(len(oos_windows), n, economic)
    full = None
    if verdict == "GO":
        days = [day for day in sessions if start <= day <= last]
        held = held_sessions(sessions, oos_windows)
        path = run_tom_path(days, held, spy["adj"])
        after, tax, years = after_tax_terminal(path["terminal"], path["realizations"])
        net = sum(pnl for _day, pnl in path["realizations"])
        if abs(net - (path["terminal"] - ACCOUNT)) > 0.05:
            raise SystemExit(f"T9 realizations ${net:,.2f} do not meet the terminal.")
        full = {
            "path": path, "after": after, "tax": tax, "years": years,
            "post": cagr(ACCOUNT, after, days[0], days[-1]),
            "pre": cagr(ACCOUNT, path["terminal"], days[0], days[-1]),
            "dd": max_drawdown(path["curve"]),
        }
        full_spy = buy_hold(spy["adj"][days[0]], spy["adj"][days[-1]])
        full["spy_post"] = cagr(ACCOUNT, full_spy["after"], days[0], days[-1])
        full["spy_pre"] = cagr(ACCOUNT, full_spy["terminal"], days[0], days[-1])
        if not close_enough(full["post"], full["spy_post"], MARGIN):
            verdict = "NO-GO"
            full["verdict_note"] = (
                "The quick screen was within 1 percentage point. The compounded path is not, "
                "so the verdict is NO-GO."
            )
        else:
            full["verdict_note"] = "The compounded path stays within 1 percentage point of SPY, or ahead of it."
    lines = [
        "# Turn of the month — T9",
        "",
        f"Run date {today}. The last session is {last}. This is a paper test. It places no order.",
        "",
        "The window is the last trading day of the month plus the first three trading days of the next "
        "month. The book buys SPY at the close of the session before that last day and sells at the close "
        "of the third session of the next month. Every other session is cash at 0%. Prices are Yahoo "
        "Finance chart v8 adjusted closes. The commission uses the official close when it exists. "
        "In-sample exits fall in 2000 through 2014. Out-of-sample exits fall in 2015 through the last session.",
        "",
        "This is the quick screen. Each window is a fresh $460, not a compounded path. The pre-tax annual "
        "rate is the mean out-of-sample window return times 12, minus the mean Fixed commission on two "
        "orders times 12. A year's net is the sum of that year's net window returns. A winning year is "
        "taxed at 25%. A losing year pays nothing and does not carry forward. The after-tax rate scales "
        "the pre-tax rate by the tax due on those year nets. Buy-and-hold SPY is taxed once, on the gain "
        "from the first out-of-sample entry close to the last session, after its own entry and exit. "
        "Close means the after-tax rate is within 1 percentage point of SPY's after-tax CAGR, or ahead of it. "
        "The compounded path runs only when the sample meets n and the screen is close.",
        "",
        "## Required sample",
        "",
        *block,
        "",
        "## Quick screen",
        "",
    ]
    if screen is None or spy_book is None:
        lines.append("The out-of-sample window list is empty. The verdict is **UNKNOWN (insufficient sample)**.")
    else:
        lines.append(
            f"Out-of-sample windows: {screen['n']}, from {oos_windows[0]['entry']} to {oos_windows[-1]['exit']}. "
            f"Mean window return {_pct(screen['mean_gross'])}. Mean window return times 12: "
            f"{_pct(screen['gross_annual'])}. Annual commission {_pct(screen['commission_annual'])} "
            f"(mean fee ${screen['mean_fee']:.2f} on ${ACCOUNT:.0f}). Pre-tax annual rate {_pct(screen['pretax'])}. "
            f"After tax {_pct(screen['after'])}. SPY after-tax CAGR {_pct(spy_cagr)}, "
            f"pre-tax terminal ${spy_book['terminal']:,.2f}, tax ${spy_book['tax']:,.2f}, "
            f"after-tax terminal ${spy_book['after']:,.2f}."
        )
        lines.append("")
        lines.append("| Year | Net window returns | Tax on the year's net |")
        lines.append("|---|---:|---:|")
        for row in screen["year_nets"]:
            lines.append(f"| {row['year']} | {_pct(row['net'])} | {_pct(row['tax'])} |")
        lines.append("")
        if verdict == UNKNOWN:
            extra = ""
            if not close_enough(screen["after"], spy_cagr, MARGIN):
                extra = (
                    " The after-tax rate is not within 1 percentage point of SPY. "
                    "The sample gate applies first, so the verdict is not NO-GO."
                )
            lines.append(
                f"The queue verdict is **{UNKNOWN}**.{extra} The full path is not run."
            )
        elif verdict == "NO-GO" and full is None:
            lines.append(
                "The out-of-sample count meets n, and the after-tax rate is not within 1 percentage point "
                "of SPY. The verdict is **NO-GO**. The full path is not run."
            )
        elif full is not None:
            lines.append(full["verdict_note"])
            lines.append("")
            lines.append(
                f"Compounded path, {full['path']['curve'][0][0]} to {full['path']['curve'][-1][0]}. "
                f"After-tax CAGR {_pct(full['post'])} against SPY {_pct(full['spy_post'])}. "
                f"Pre-tax CAGR {_pct(full['pre'])} against SPY {_pct(full['spy_pre'])}. "
                f"Maximum drawdown {_dd(full['dd'])}, pre-tax. Fees ${full['path']['fees']:,.2f}. "
                f"Tax ${full['tax']:,.2f}. The verdict is **{verdict}**."
            )
            path = SERIES / "tom.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="") as handle:
                handle.write("date,strategy\n")
                for day, ret in full["path"]["returns"]:
                    handle.write(f"{day},{ret:.8f}\n")
    lines.extend(["", "## After tax", ""])
    if screen is not None:
        lines.append(
            f"The quick screen's after-tax annual rate is {_pct(screen['after'])}. "
            "SPY is taxed once at the end. The 25% rate nets each calendar year of windows. "
            "A losing year pays nothing."
        )
    else:
        lines.append("There is no out-of-sample window, so the tax line is not computed.")
    lines.extend(["", "## Verdict", "", f"- Turn of the month: {verdict}.", ""])
    out = DOCS / "tom-2026-10-15.md"
    out.write_text("\n".join(lines))
    print(out, verdict)
    return verdict


def _spy_curve(days, prices):
    from bot.queue_rules import fixed_fee
    from bot.tom_exec import shares_for_fee
    shares, _fee, left = shares_for_fee(ACCOUNT, prices[days[0]])
    curve = []
    for index, day in enumerate(days):
        if index == len(days) - 1:
            equity = left + shares * prices[day] - fixed_fee(shares, prices[day])
        else:
            equity = left + shares * prices[day]
        curve.append((day, equity))
    returns = []
    for (_day0, equity0), (day1, equity1) in zip(curve, curve[1:]):
        if equity0 > 0:
            returns.append((day1, equity1 / equity0 - 1))
    return curve, returns


def _t10(spy, sessions, last, today):
    ends = month_ends(sessions)
    signals = faber_signals(ends, spy["adj"], LOOKBACK)
    opening = [row for row in signals if row[0][:7] == "2006-12"]
    if not opening:
        raise SystemExit("December 2006 does not have ten month-end closes.")
    start = opening[0][0]
    days = [day for day in sessions if start <= day <= last]
    signal_map = {day: hold for day, hold in signals if day in set(days)}
    book = run_faber(days, signal_map, spy["adj"])
    after, tax, years = after_tax_terminal(book["terminal"], book["realizations"])
    net = sum(pnl for _day, pnl in book["realizations"])
    if abs(net - (book["terminal"] - ACCOUNT)) > 0.05:
        raise SystemExit(f"T10 realizations ${net:,.2f} do not meet the terminal ${book['terminal']:,.2f}.")
    spy_curve, spy_returns = _spy_curve(days, spy["adj"])
    spy_book = buy_hold(spy["adj"][days[0]], spy["adj"][days[-1]])
    post = cagr(ACCOUNT, after, days[0], days[-1])
    pre = cagr(ACCOUNT, book["terminal"], days[0], days[-1])
    spy_post = cagr(ACCOUNT, spy_book["after"], days[0], days[-1])
    spy_pre = cagr(ACCOUNT, spy_book["terminal"], days[0], days[-1])
    dd = max_drawdown(book["curve"])
    spy_dd = max_drawdown(spy_curve)
    years_span = (dt.date.fromisoformat(days[-1]) - dt.date.fromisoformat(days[0])).days / 365.25
    switches_yr = book["switches"] / years_span if years_span else None
    spy_values = [ret for _day, ret in spy_returns]
    spy_sharpe = None
    if len(spy_values) >= 2 and statistics.stdev(spy_values):
        spy_sharpe = (sum(spy_values) / len(spy_values) / statistics.stdev(spy_values)) * (252 ** 0.5)
    verdict = "GO" if drawdown_reduced(dd, spy_dd) else "NO-GO"
    held_months = sum(1 for _day, hold in signals if start <= _day <= last and hold)
    lines = [
        "# Faber 10-month filter on SPY — T10",
        "",
        f"Run date {today}. The last session is {last}. This is a paper test. It places no order.",
        "",
        "At the close of each month's last trading day, the book holds SPY when that close is strictly "
        "above the average of the last ten month-end adjusted closes, including that month. Otherwise it "
        "holds cash at 0%. A close equal to the average is cash. The parameter was published in 2007 and "
        "is not fitted. The whole window from the December 2006 signal through the last session is one "
        "out-of-sample period. The December 2006 close is the first order. It needs the ten month-ends "
        "ending that month. This is not the earlier SPYM whole-share Faber report.",
        "",
        "The account starts at $460 and may hold fractional shares. Commission is IBKR Pro Fixed, "
        "$0.005 a share, minimum $1, maximum 1% of trade value, with no extra spread. A switch trades "
        "at the month-end close, and the next month's moves belong to the new holding. The last session "
        "liquidates so the exit cost is in the result. That sale is not counted as a switch. Sharpe is "
        "daily, the risk-free rate is zero, and the scale is the square root of 252. Maximum drawdown "
        "and Sharpe use the pre-tax path. The 25% tax nets each calendar year's realized gains and is "
        "subtracted once from terminal equity. Buy-and-hold SPY pays one entry, one exit, and one tax "
        "at the end.",
        "",
        "## Required sample",
        "",
        "T10 has no in-sample period. The 10-month average was published in 2007 and is not fitted here. "
        "Required n is not computed. The sample-size gate does not apply. The verdict is the drawdown "
        "comparison: GO only when the pre-tax maximum drawdown is strictly smaller than SPY's. Return "
        "is reported and is not the gate.",
        "",
        "## Results",
        "",
        f"Window {days[0]} to {days[-1]}. Month-end signals in the window: {len(signal_map)}. "
        f"Signals that held SPY: {held_months}.",
        "",
        "| | CAGR pre-tax | CAGR after tax | Max drawdown | Sharpe | Switches | Switches/yr | Fees | Tax |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| Faber | {_pct(pre)} | {_pct(post)} | {_dd(dd)} | {_num(book['sharpe'])} | {book['switches']} | "
        f"{_num(switches_yr)} | ${book['fees']:,.2f} | ${tax:,.2f} |",
        f"| SPY | {_pct(spy_pre)} | {_pct(spy_post)} | {_dd(spy_dd)} | {_num(spy_sharpe)} | 0 | 0.00 | "
        f"${spy_book['fees']:,.2f} | ${spy_book['tax']:,.2f} |",
        "",
        f"After-tax terminal ${after:,.2f} against SPY ${spy_book['after']:,.2f}. "
        f"Pre-tax terminal ${book['terminal']:,.2f} against SPY ${spy_book['terminal']:,.2f}."
        + (
            f" All {sum(1 for day, _ret in book['returns'] if day.startswith('2008'))} sessions in 2008 "
            "have a strategy return of zero, so that year was cash."
            if book["returns"] and all(
                abs(ret) < 1e-12 for day, ret in book["returns"] if day.startswith("2008")
            ) and any(day.startswith("2008") for day, _ret in book["returns"])
            else ""
        ),
        "",
        "## After tax",
        "",
        "Israeli capital-gains tax at 25% of each calendar year's net realized gain. A loss offsets a "
        "gain in that year. A losing year pays nothing and does not carry forward. Unrealized gains are "
        "not taxed until the sale. The tax is subtracted once from terminal equity. Sharpe and maximum "
        "drawdown stay pre-tax. Buy-and-hold SPY is taxed once, at the end.",
        "",
        "| Year | Realized net | Tax |",
        "|---|---:|---:|",
    ]
    if years:
        for row in years:
            lines.append(f"| {row['year']} | ${row['net']:,.2f} | ${row['tax']:,.2f} |")
    else:
        lines.append("| — | $0.00 | $0.00 |")
    lines.extend([
        "",
        f"The goal is drawdown reduction. Strategy maximum drawdown {_dd(dd)}. SPY maximum drawdown {_dd(spy_dd)}.",
        "",
        "## Verdict",
        "",
        f"- Faber 10-month filter on SPY: {verdict}.",
        "",
    ])
    out = DOCS / "faber-spy-2026-10-15.md"
    out.write_text("\n".join(lines))
    path = SERIES / "faber-spy.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    spy_map = dict(spy_returns)
    with path.open("w", newline="") as handle:
        handle.write("date,strategy,spy\n")
        for day, ret in book["returns"]:
            if day in spy_map:
                handle.write(f"{day},{ret:.8f},{spy_map[day]:.8f}\n")
    print(out, verdict, _pct(post), _pct(spy_post), _dd(dd), _dd(spy_dd))
    return verdict


def run():
    spy, sessions, last, today = _load()
    _t9(spy, sessions, last, today)
    _t10(spy, sessions, last, today)


if __name__ == "__main__":
    run()
