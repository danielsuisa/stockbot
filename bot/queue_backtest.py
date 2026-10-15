"""T9 turn-of-month quick screen and T10 Faber on SPY. Research only.

Nothing here places or transmits an order. T9 is closed on cost and does not
run the compounded path. T10's verdict is trend_verdict. No tax is computed.
"""
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.faber_spy import LOOKBACK, faber_signals, month_ends, run_faber
from bot.gates import (
    NOT_RECORDED, bootstrap_rate, compound_months, dsr_trials_next, info_only,
    leg_flags, symmetry, trend_verdict, verdict as gate_verdict,
)
from bot.macro_backtest import _pct, _yahoo
from bot.queue_rules import sample_lines
from bot.tom_exec import ACCOUNT, buy_hold, quick_screen, tom_windows, window_fee
from bot.trend_backtest import load_symbol
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


def _boot(rate):
    if rate is None:
        return "n/a"
    return f"{rate:.1%}"


def _balanced(book):
    net = sum(pnl for _day, pnl in book["realizations"])
    extra = book.get("cash_extras") or 0.0
    return abs(net + extra - (book["terminal"] - ACCOUNT)) <= 0.05


def _hold_signal(days):
    return {days[0]: True}


def _score(book, bench):
    start, end = book["curve"][0][0], book["curve"][-1][0]
    growth = cagr(ACCOUNT, book["terminal"], start, end)
    bench_growth = cagr(ACCOUNT, bench["terminal"], bench["curve"][0][0], bench["curve"][-1][0])
    bench_map = dict(bench["returns"])
    paired = [(day, ret, bench_map[day]) for day, ret in book["returns"] if day in bench_map]
    rate = bootstrap_rate(
        compound_months((day, ret) for day, ret, _bench in paired),
        compound_months((day, bench_ret) for day, _ret, bench_ret in paired),
        "faber-spy",
    )
    years = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days / 365.25
    per_year = book["switches"] / years if years else None
    return {
        "cagr": growth,
        "bench_cagr": bench_growth,
        "boot": rate,
        "dd": max_drawdown(book["curve"]),
        "bench_dd": max_drawdown(bench["curve"]),
        "sharpe": book["sharpe"],
        "bench_sharpe": bench["sharpe"],
        "switches": book["switches"],
        "per_year": per_year,
        "fees": book["fees"],
        "bench_fees": bench["fees"],
        "terminal": book["terminal"],
        "bench_terminal": bench["terminal"],
        "flags": leg_flags("faber-spy", growth, bench_growth, rate),
    }


def _closes(book):
    return {day: pair[1] for day, pair in book["actual"].items()}


def _opens(book):
    return {day: pair[0] for day, pair in book["actual"].items()}


def _t10(spy, sessions, last, today):
    rule = symmetry()
    ends = month_ends(sessions)
    signals = faber_signals(ends, spy["adj"], LOOKBACK)
    opening = [row for row in signals if row[0][:7] == "2006-12"]
    if not opening:
        raise SystemExit("December 2006 does not have ten month-end closes.")
    start = opening[0][0]
    days = [day for day in sessions if start <= day <= last]
    signal_map = {day: hold for day, hold in signals if day in set(days)}
    spym = load_symbol("SPYM")
    bil = load_symbol("BIL")
    spym_px = _closes(spym)
    bil_px = _closes(bil)
    days_b = [day for day in sorted(spym_px) if start <= day <= last]
    if not days_b or days_b[0] != start:
        raise SystemExit("SPYM does not have the December 2006 Faber close.")
    signal_b = {day: hold for day, hold in signals if day in set(days_b)}
    missed = [day for day, _hold in signals if start <= day <= last and day not in signal_b]
    if missed:
        raise SystemExit(f"SPYM is missing Faber signal dates: {missed[:5]}")

    logic_book = run_faber(days, signal_map, spy["adj"])
    logic_bench = run_faber(days, _hold_signal(days), spy["adj"])
    if not _balanced(logic_book) or not _balanced(logic_bench):
        raise SystemExit("T10 fractional cash book does not meet its terminal.")
    logic = _score(logic_book, logic_bench)

    corporate = {
        "dividends": spym["div"],
        "splits": spym["split"],
        "opens": _opens(spym),
    }
    decision_book = run_faber(days_b, signal_b, spym_px, whole=True, **corporate)
    decision_bench = run_faber(days_b, _hold_signal(days_b), spym_px, whole=True, **corporate)
    if not _balanced(decision_book) or not _balanced(decision_bench):
        raise SystemExit("T10 whole-share cash book does not meet its terminal.")
    decision = _score(decision_book, decision_bench)
    verdict = trend_verdict("faber-spy", decision["cagr"], decision["bench_cagr"], decision["boot"])

    bil_logic_book = run_faber(days, signal_map, spy["adj"], sleeve=bil["adj"])
    bil_whole_book = run_faber(
        days_b, signal_b, spym_px, whole=True, sleeve=bil_px,
        sleeve_dividends=bil["div"], sleeve_splits=bil["split"], sleeve_opens=_opens(bil),
        **corporate,
    )
    if not _balanced(bil_logic_book) or not _balanced(bil_whole_book):
        raise SystemExit("T10 BIL sleeve book does not meet its terminal.")
    bil_logic = _score(bil_logic_book, logic_bench)
    bil_whole = _score(bil_whole_book, decision_bench)
    bil_gate = info_only(trend_verdict(
        "faber-spy", bil_whole["cagr"], bil_whole["bench_cagr"], bil_whole["boot"], replicated=True,
    ))
    bil_first = min(bil_px)
    trials = dsr_trials_next()
    if bil_gate == NOT_RECORDED:
        bil_line = (
            f"{NOT_RECORDED}. On the whole-share row the BIL sleeve would be a GO. "
            "It is not recorded. It stays pending a pre-2014 test or a paper test."
        )
    else:
        bil_line = (
            f"{bil_gate}. Information only. The sleeve is not a completed test and does not change N. "
            f"N stays {trials}."
        )
    held_months = sum(1 for _day, hold in signals if start <= _day <= last and hold)
    cash_2008 = (
        decision_book["returns"]
        and any(day.startswith("2008") for day, _ret in decision_book["returns"])
        and all(abs(ret) < 1e-12 for day, ret in decision_book["returns"] if day.startswith("2008"))
    )
    lines = [
        "# Faber 10-month filter on SPY — T10",
        "",
        f"Run date {today}. The last session is {last}. This is a paper test. It places no order.",
        "",
        "At the close of each month's last trading day, the book holds the equity sleeve when SPY's "
        "adjusted close is strictly above the average of the last ten month-end adjusted closes, "
        "including that month. Otherwise the recorded book holds cash at 0%. A close equal to the "
        "average is cash. The parameter was published in 2007 and is not fitted. The whole window from "
        "the December 2006 signal through the last session is one out-of-sample period. The December "
        "2006 close is the first order. It needs the ten month-ends ending that month. This is not the "
        "earlier SPYM whole-share Faber report on the GEM window.",
        "",
        "The gate has two rows, and both sides of a row use the same share rule. "
        f"Row (a) is fractional units. Row (b) is whole shares of a ${rule['account']:.0f} account, "
        "and SPY exposure is SPYM because one SPY share costs more than $460. The verdict is row (b), "
        "and that row holds cash at 0% when it is out of the market. "
        "A BIL sleeve is reported on the same signals as information only. It pays a commission on "
        f"every BIL entry and every BIL exit. Before BIL's first print ({bil_first}) the sleeve is cash. "
        "The sleeve does not add a trial. This re-run is a bug fix, so N stays "
        f"{trials}.",
        "",
        "Commission is IBKR Pro Fixed, $0.005 a share, minimum $1, maximum 1% of trade value, with no "
        "extra spread. A switch trades at the month-end close, and the next month's moves belong to the "
        "new holding. The last session liquidates so the exit cost is in the result. That sale is not "
        "counted as a switch. Row (a) marks the adjusted close. Row (b) marks the actual close, pays "
        "dividends as cash, and applies splits to shares already held. Sharpe is daily, the risk-free "
        "rate is zero, and the scale is the square root of 252. No tax is computed. "
        "Buy-and-hold pays one entry and one exit.",
        "",
        "## Required sample",
        "",
        "T10 has no in-sample period. The 10-month average was published in 2007 and is not fitted here. "
        "Required n is not computed. The sample-size gate does not apply. The verdict is "
        "`trend_verdict` for `faber-spy` in `docs/backtest/gates.json`, on row (b), cash at 0%. "
        "Both of these are required: CAGR no further below the benchmark than the gate's shortfall, "
        "and the paired block bootstrap at or above the gate's floor. "
        f"`dsr_n_next` stays {trials}. This run does not call the trial counter.",
        "",
        "## Results",
        "",
        f"Window {days[0]} to {days[-1]}. Month-end signals in the window: {len(signal_map)}. "
        f"Signals that held the equity sleeve: {held_months}.",
        "",
        "| Row | Book | CAGR | Benchmark CAGR | Bootstrap | Max drawdown | Fees | Switches | Role |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
        f"| (a) fractional | cash at 0% | {_pct(logic['cagr'])} | {_pct(logic['bench_cagr'])} | "
        f"{_boot(logic['boot'])} | {_dd(logic['dd'])} | ${logic['fees']:,.2f} | {logic['switches']} | "
        "logic, not the verdict |",
        f"| (b) whole shares | cash at 0%, SPYM | {_pct(decision['cagr'])} | {_pct(decision['bench_cagr'])} | "
        f"{_boot(decision['boot'])} | {_dd(decision['dd'])} | ${decision['fees']:,.2f} | "
        f"{decision['switches']} | verdict |",
        f"| (a) fractional | BIL when out | {_pct(bil_logic['cagr'])} | {_pct(bil_logic['bench_cagr'])} | "
        f"{_boot(bil_logic['boot'])} | {_dd(bil_logic['dd'])} | ${bil_logic['fees']:,.2f} | "
        f"{bil_logic['switches']} | information only |",
        f"| (b) whole shares | BIL when out, SPYM | {_pct(bil_whole['cagr'])} | {_pct(bil_whole['bench_cagr'])} | "
        f"{_boot(bil_whole['boot'])} | {_dd(bil_whole['dd'])} | ${bil_whole['fees']:,.2f} | "
        f"{bil_whole['switches']} | information only |",
        "",
        f"Row (b) terminal ${decision['terminal']:,.2f} against SPYM ${decision['bench_terminal']:,.2f}. "
        f"BIL sleeve terminal ${bil_whole['terminal']:,.2f}. "
        f"BIL sleeve commission ${bil_whole['fees']:,.2f}, against ${decision['fees']:,.2f} on the cash book."
        + (
            f" All {sum(1 for day, _ret in decision_book['returns'] if day.startswith('2008'))} sessions "
            "in 2008 have a strategy return of zero on the cash book, so that year was cash."
            if cash_2008 else ""
        ),
        "",
        f"- {'pass' if decision['flags']['cagr'] else 'fail'}: whole-share CAGR {_pct(decision['cagr'])} "
        f"against SPYM {_pct(decision['bench_cagr'])}.",
        f"- {'pass' if decision['flags']['drawdown'] else 'fail'}: whole-share bootstrap "
        f"{_boot(decision['boot'])} of the gate's draws.",
        "",
        "## Verdict",
        "",
        f"- Faber, cash at 0% when out of the market, whole shares of SPYM: {verdict}.",
        f"- BIL out-of-market sleeve: {bil_line}",
        "",
    ]
    out = DOCS / "faber-spy-2026-10-15.md"
    out.write_text("\n".join(lines))
    path = SERIES / "faber-spy.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    bench_map = dict(decision_bench["returns"])
    with path.open("w", newline="") as handle:
        handle.write("date,strategy,spy\n")
        for day, ret in decision_book["returns"]:
            if day in bench_map:
                handle.write(f"{day},{ret:.8f},{bench_map[day]:.8f}\n")
    print(
        out, verdict, "cash", _pct(decision["cagr"]), _pct(decision["bench_cagr"]),
        "bil", bil_gate, _pct(bil_whole["cagr"]), f"trials={trials}",
    )
    return verdict


def run():
    spy, sessions, last, today = _load()
    _t9(spy, sessions, last, today)
    _t10(spy, sessions, last, today)


if __name__ == "__main__":
    run()
