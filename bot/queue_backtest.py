"""T9 turn-of-month quick screen and T10 Faber on SPY. Research only.

Nothing here places or transmits an order. T9 is closed on cost and does not
run the compounded path. T10's verdict is trend_verdict. No tax is computed.
"""
import datetime as dt
import statistics
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.faber_spy import LOOKBACK, faber_signals, month_ends, run_faber
from bot.gates import (
    bootstrap_rate, compound_months, leg_flags, trend_verdict, verdict as gate_verdict,
)
from bot.macro_backtest import _pct, _yahoo
from bot.queue_rules import sample_lines
from bot.tom_exec import ACCOUNT, buy_hold, quick_screen, tom_windows, window_fee
from bot.trend_exec import cagr, max_drawdown

NY = ZoneInfo("America/New_York")
DOCS = Path("docs/backtest")
SERIES = DOCS / "series"


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
        spy_cagr = cagr(ACCOUNT, spy_book["terminal"], start, last)
    block, _n = sample_lines("T9 turn of the month", is_nets, len(oos_windows), state_verdict=False)
    verdict = gate_verdict("t9")
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
        "This is the quick screen. Each window is a fresh $460, not a compounded path. The annual "
        "rate is the mean out-of-sample window return times 12, minus the mean Fixed commission on two "
        "orders times 12. No tax is computed. The compounded path is not run. "
        f"The verdict is {verdict}, from the gate file.",
        "",
        "## Required sample",
        "",
        *block,
        "",
        "## Quick screen",
        "",
    ]
    if screen is None or spy_book is None:
        lines.append(f"The out-of-sample window list is empty. The verdict is **{verdict}**.")
    else:
        lines.append(
            f"Out-of-sample windows: {screen['n']}, from {oos_windows[0]['entry']} to {oos_windows[-1]['exit']}. "
            f"Mean window return {_pct(screen['mean_gross'])}. Mean window return times 12: "
            f"{_pct(screen['gross_annual'])}. Annual commission {_pct(screen['commission_annual'])} "
            f"(mean fee ${screen['mean_fee']:.2f} on ${ACCOUNT:.0f}). Annual rate {_pct(screen['pretax'])}. "
            f"SPY CAGR {_pct(spy_cagr)}. Terminal ${spy_book['terminal']:,.2f}."
        )
        lines.append("")
        lines.append(f"The verdict is **{verdict}**. The full path is not run.")
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
    net = sum(pnl for _day, pnl in book["realizations"])
    if abs(net - (book["terminal"] - ACCOUNT)) > 0.05:
        raise SystemExit(f"T10 realizations ${net:,.2f} do not meet the terminal ${book['terminal']:,.2f}.")
    spy_curve, spy_returns = _spy_curve(days, spy["adj"])
    spy_book = buy_hold(spy["adj"][days[0]], spy["adj"][days[-1]])
    pre = cagr(ACCOUNT, book["terminal"], days[0], days[-1])
    spy_pre = cagr(ACCOUNT, spy_book["terminal"], days[0], days[-1])
    dd = max_drawdown(book["curve"])
    spy_dd = max_drawdown(spy_curve)
    years_span = (dt.date.fromisoformat(days[-1]) - dt.date.fromisoformat(days[0])).days / 365.25
    switches_yr = book["switches"] / years_span if years_span else None
    spy_values = [ret for _day, ret in spy_returns]
    spy_sharpe = None
    if len(spy_values) >= 2 and statistics.stdev(spy_values):
        spy_sharpe = (sum(spy_values) / len(spy_values) / statistics.stdev(spy_values)) * (252 ** 0.5)
    spy_map = dict(spy_returns)
    paired = [(day, ret, spy_map[day]) for day, ret in book["returns"] if day in spy_map]
    rate = bootstrap_rate(
        compound_months((day, ret) for day, ret, _spy in paired),
        compound_months((day, spy_ret) for day, _ret, spy_ret in paired),
        "faber-spy",
    )
    flags = leg_flags("faber-spy", pre, spy_pre, rate)
    verdict = trend_verdict("faber-spy", pre, spy_pre, rate)
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
        "daily, the risk-free rate is zero, and the scale is the square root of 252. "
        "No tax is computed. Buy-and-hold SPY pays one entry and one exit.",
        "",
        "## Required sample",
        "",
        "T10 has no in-sample period. The 10-month average was published in 2007 and is not fitted here. "
        "Required n is not computed. The sample-size gate does not apply. The verdict is "
        "`trend_verdict` for `faber-spy` in `docs/backtest/gates.json`: CAGR within the gate's shortfall "
        "of SPY, and the paired block bootstrap at or above the gate's floor. Both are required.",
        "",
        "## Results",
        "",
        f"Window {days[0]} to {days[-1]}. Month-end signals in the window: {len(signal_map)}. "
        f"Signals that held SPY: {held_months}.",
        "",
        "| | CAGR | Max drawdown | Sharpe | Switches | Switches/yr | Fees |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| Faber | {_pct(pre)} | {_dd(dd)} | {_num(book['sharpe'])} | {book['switches']} | "
        f"{_num(switches_yr)} | ${book['fees']:,.2f} |",
        f"| SPY | {_pct(spy_pre)} | {_dd(spy_dd)} | {_num(spy_sharpe)} | 0 | 0.00 | "
        f"${spy_book['fees']:,.2f} |",
        "",
        f"Terminal ${book['terminal']:,.2f} against SPY ${spy_book['terminal']:,.2f}."
        + (
            f" All {sum(1 for day, _ret in book['returns'] if day.startswith('2008'))} sessions in 2008 "
            "have a strategy return of zero, so that year was cash."
            if book["returns"] and all(
                abs(ret) < 1e-12 for day, ret in book["returns"] if day.startswith("2008")
            ) and any(day.startswith("2008") for day, _ret in book["returns"])
            else ""
        ),
        "",
        f"Path maximum drawdown {_dd(dd)} against SPY {_dd(spy_dd)}. "
        "That path comparison is a description. It is not the verdict.",
        "",
        f"- {'pass' if flags['cagr'] else 'fail'}: CAGR {_pct(pre)} against SPY {_pct(spy_pre)}.",
        f"- {'pass' if flags['drawdown'] else 'fail'}: bootstrap "
        f"{'n/a' if rate is None else f'{rate:.1%}'} of the gate's draws.",
        "",
        "## Verdict",
        "",
        f"- Faber 10-month filter on SPY: {verdict}.",
        "",
    ]
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
    print(out, verdict, _pct(pre), _pct(spy_pre), _dd(dd), _dd(spy_dd))
    return verdict


def run():
    spy, sessions, last, today = _load()
    _t9(spy, sessions, last, today)
    _t10(spy, sessions, last, today)


if __name__ == "__main__":
    run()
