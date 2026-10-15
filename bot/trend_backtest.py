"""GEM and GEM-BIL on a $460 whole-share account. Research only.

Signals use SPY (and BIL, VEU). The executable book buys SPYM when the signal says SPY.
The SPY reference buys SPY instead. Nothing here places or transmits an order.
The verdict string is bot.gates.trend_verdict. This module does not re-run Faber.
"""
import csv
import datetime as dt
import gzip
import json
import sys
from zoneinfo import ZoneInfo

from bot import common
from bot.gates import (
    drawdown_fraction, dsr_trials_next, leg_flags, replication_note, trial, trend_verdict,
    write_trial_count,
)
from bot.trend_exec import (
    ACCOUNT, actual_dividends, actual_prints, block_bootstrap,
    cagr, deflated_sharpe, gem_signals, max_drawdown,
    monthly_returns, simulate, year_drawdown,
)

NY = ZoneInfo("America/New_York")
CACHE = common.ROOT / ".cache" / "yahoo" / "trend"
DECISION_START = "2014-01"
DECISION_END = "2026-09"
EARLY_END = "2021-12"
SUPPLEMENT_END = "2013-12"


def parse_payload(payload):
    block = payload["chart"]["result"][0]

    def as_day(stamp):
        return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).astimezone(NY).date().isoformat()

    events = block.get("events") or {}
    splits = {}
    for item in (events.get("splits") or {}).values():
        splits[as_day(item["date"])] = float(item["numerator"]) / float(item["denominator"])
    raw_div = {}
    for item in (events.get("dividends") or {}).values():
        raw_div[as_day(item["date"])] = float(item["amount"])
    quote = block["indicators"]["quote"][0]
    adj = block["indicators"]["adjclose"][0]["adjclose"]
    bars, adjusted = {}, {}
    for stamp, opx, cpx, apx in zip(block["timestamp"], quote["open"], quote["close"], adj):
        if stamp is None:
            continue
        day = as_day(stamp)
        if opx and cpx and opx > 0 and cpx > 0:
            bars[day] = (float(opx), float(cpx))
        if apx and apx > 0:
            adjusted[day] = float(apx)
    return {
        "actual": actual_prints(bars, splits),
        "adj": adjusted,
        "split": splits,
        "div": actual_dividends(raw_div, splits),
    }


def load_symbol(symbol):
    path = CACHE / f"{symbol}.json.gz"
    payload = json.loads(gzip.open(path, "rb").read().decode())
    return parse_payload(payload)


def _month_end(dates, yyyy_mm):
    days = [day for day in dates if day.startswith(yyyy_mm)]
    return days[-1] if days else None


def _paired(strategy_returns, spy_returns):
    spy = dict(spy_returns)
    left, right = [], []
    for day, ret in strategy_returns:
        if day in spy:
            left.append(ret)
            right.append(spy[day])
    return left, right


def _book(name, prices, splits, dividends, sessions, signals, start, end):
    result = simulate(prices, splits, dividends, sessions, signals, start, end, ACCOUNT)
    curve = result["curve"]
    growth = cagr(ACCOUNT, result["final"], start, end)
    returns = monthly_returns(curve, result["final"], end)
    buys = [trade for trade in result["trades"] if trade["side"] == "buy"]
    return {
        "name": name,
        "start": start,
        "end": end,
        "final": result["final"],
        "cagr": growth,
        "maxdd": max_drawdown(curve),
        "dd_2020": year_drawdown(curve, "2020"),
        "dd_2022": year_drawdown(curve, "2022"),
        "returns": returns,
        "curve": curve,
        "fees": result["fees"],
        "buys": len(buys),
        "first_buy": buys[0] if buys else None,
        "trades": result["trades"],
    }


def total_return_buy_hold(adj, actual, sessions, start, end, name="SPY"):
    """Fractional SPY total return on the adjusted close.

    The account is cash at `start`. It buys at the next session's adjusted open
    and holds to the adjusted close of `end`. The adjusted open is that session's
    raw open scaled by adjclose/close. Dividends sit inside the adjusted close.
    There is no whole-share leftover and no commission.
    """
    days = [day for day in sessions if start <= day <= end and day in adj and day in actual]
    if not days:
        raise ValueError(f"no {name} sessions from {start} to {end}")
    buy = next((day for day in days if day > start), None)
    if buy is None:
        curve = [(day, float(ACCOUNT)) for day in days]
    else:
        raw_open, raw_close = actual[buy]
        if raw_open <= 0 or raw_close <= 0 or adj[buy] <= 0:
            raise ValueError(f"bad {name} print on {buy}")
        entry = raw_open * adj[buy] / raw_close
        shares = ACCOUNT / entry
        curve = []
        for day in days:
            if day < buy:
                curve.append((day, float(ACCOUNT)))
            else:
                curve.append((day, shares * adj[day]))
    final = curve[-1][1]
    return {
        "name": name,
        "start": start,
        "end": end,
        "final": final,
        "cagr": cagr(ACCOUNT, final, start, end),
        "maxdd": max_drawdown(curve),
        "dd_2020": year_drawdown(curve, "2020"),
        "dd_2022": year_drawdown(curve, "2022"),
        "returns": monthly_returns(curve, final, end),
        "curve": curve,
        "fees": 0.0,
        "buys": 1 if buy else 0,
        "first_buy": {"date": buy} if buy else None,
        "trades": [],
        "field": "adjclose",
        "entry": buy,
    }


def _judge(book, spy_book, trial_id):
    left, right = _paired(book["returns"], spy_book["returns"])
    gate = trial(trial_id)
    rate = block_bootstrap(
        left, right,
        draws=gate["bootstrap_draws"],
        block=gate["bootstrap_block"],
        seed=gate["bootstrap_seed"],
        fraction=drawdown_fraction(gate),
    )
    return {
        "boot": rate,
        "gap": None if book["cagr"] is None or spy_book["cagr"] is None else book["cagr"] - spy_book["cagr"],
        "left": left,
        "months": len(left),
        "gate": gate,
    }


def _pct(value):
    return "—" if value is None else f"{value:+.2%}"


def _dd(value):
    return "—" if value is None else f"{-value:.2%}"


def _num(value, spec="{:.2f}"):
    return "—" if value is None else spec.format(value)


def render(title, decision, reference, spy, vt, early_note, supplement, signal_note):
    judge = decision["judge"]
    gate = judge["gate"]
    flags = decision["flags"]
    lines = [
        f"# {title}",
        "",
        "Account $460. Whole shares of the fund the signal holds, bought at that fund's actual open. "
        "Market-on-open at the next session. A switch from one fund to another sells at the next open "
        "and buys at the open after that. Leftover cash earns nothing and is set again at each switch "
        "from the open of the fund being bought. Signals are month-end adjusted closes of SPY "
        "(GEM also uses BIL and VEU). The executable equity sleeve is SPYM. "
        "The SPY reference is the same signals filled in SPY, and it is not the verdict. "
        "The SPY buy-and-hold column is the fractional adjusted-close total return: "
        "it buys at the next session's adjusted open and holds to the window's adjusted close. "
        "Dividends are inside that adjusted close. It is not the raw close and it is not a whole-share account. "
        "No tax is computed. This replay does not place or transmit an order.",
        "",
        f"Verdict: **{decision['verdict_text']}**.",
        "",
        "The verdict is the return value of `trend_verdict` in `bot/gates.py`, from the gate in "
        "`docs/backtest/gates.json`.",
        "",
    ]
    shortfall = gate["cagr_max_shortfall"]
    lines.append(
        f"- {'pass' if flags['cagr'] else 'fail'}: CAGR {_pct(decision['cagr'])} against SPY "
        f"{_pct(spy['cagr'])} (gap {_pct(judge['gap'])}; allowed shortfall {shortfall:.0%} from the gate)."
    )
    lines.append(
        f"- {'pass' if flags['drawdown'] else 'fail'}: bootstrap pass rate "
        f"{_num(None if judge['boot'] is None else judge['boot'], '{:.1%}')} "
        f"of {gate['bootstrap_draws']} paired {gate['bootstrap_block']}-month blocks "
        f"(floor {gate['bootstrap_floor']:.0%})."
    )
    if early_note:
        lines.append(early_note)
    lines += [
        "",
        f"Decision window {decision['start']} through {decision['end']}. "
        f"Ending equity ${decision['final']:.2f}. Commission ${decision['fees']:.2f}. "
        f"Buys {decision['buys']}.",
        "",
    ]
    first = decision["first_buy"]
    if first:
        lines.append(
            f"First fill: {first['shares']} shares of {first['symbol']} at {first['open']:.4f} "
            f"on {first['date']}. Cash left after that buy: ${first['cash']:.2f}."
        )
        lines.append("")
    if signal_note:
        lines.append(signal_note)
        lines.append("")
    lines += [
        "| | Executable | SPY reference | SPY buy-and-hold | VT buy-and-hold |",
        "|---|---:|---:|---:|---:|",
        f"| CAGR | {_pct(decision['cagr'])} | {_pct(reference['cagr'])} | {_pct(spy['cagr'])} | {_pct(vt['cagr'])} |",
        f"| Max drawdown | {_dd(decision['maxdd'])} | {_dd(reference['maxdd'])} | {_dd(spy['maxdd'])} | {_dd(vt['maxdd'])} |",
        f"| 2020 drawdown | {_dd(decision['dd_2020'])} | {_dd(reference['dd_2020'])} | {_dd(spy['dd_2020'])} | {_dd(vt['dd_2020'])} |",
        f"| 2022 drawdown | {_dd(decision['dd_2022'])} | {_dd(reference['dd_2022'])} | {_dd(spy['dd_2022'])} | {_dd(vt['dd_2022'])} |",
        f"| Ending equity | ${decision['final']:.2f} | ${reference['final']:.2f} | ${spy['final']:.2f} | ${vt['final']:.2f} |",
        "",
    ]
    stats = judge["stats"] or {}
    lines += [
        f"Monthly observations {judge['months']}. "
        f"Probabilistic Sharpe {_num(stats.get('psr'), '{:.3f}')}. "
        f"Deflated Sharpe {_num(stats.get('dsr'), '{:.3f}')} at {decision['n_eff']} variants, "
        "the count the next test uses. "
        "Those two ratios are reported. They do not change the verdict.",
        "",
    ]
    if supplement:
        lines += [
            "## Supplementary, through 2013",
            "",
            "A separate $460 account. It does not change the verdict.",
            "",
            f"Window {supplement['start']} through {supplement['end']}. "
            f"CAGR {_pct(supplement['cagr'])} against SPY {_pct(supplement['spy_cagr'])}. "
            f"Max drawdown {_dd(supplement['maxdd'])} against SPY {_dd(supplement['spy_maxdd'])}. "
            f"Ending equity ${supplement['final']:.2f}.",
            "",
        ]
    return "\n".join(lines)


def _hold(symbol, start):
    return {start: symbol}


def _slice_signals(signals, start, end):
    return {day: target for day, target in signals.items() if start <= day < end}


def run_pair(label, trial_id, signals_exec, signals_ref, books, sessions, start, end):
    prices = {sym: books[sym]["actual"] for sym in books}
    splits = {sym: books[sym]["split"] for sym in books}
    dividends = {sym: books[sym]["div"] for sym in books}
    executable = _book(label, prices, splits, dividends, sessions,
                       _slice_signals(signals_exec, start, end), start, end)
    reference = _book(label + " SPY", prices, splits, dividends, sessions,
                      _slice_signals(signals_ref, start, end), start, end)
    spy = total_return_buy_hold(books["SPY"]["adj"], books["SPY"]["actual"], sessions, start, end)
    vt = _book("VT", prices, splits, dividends, sessions, _hold("VT", start), start, end)
    executable["judge"] = _judge(executable, spy, trial_id)
    return executable, reference, spy, vt


def _write_series(stem, strategy, spy):
    path = common.ROOT / "docs" / "backtest" / "series" / f"{stem}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    s_curve = dict(strategy["curve"])
    b_curve = dict(spy["curve"])
    dates = [day for day, _eq in strategy["curve"] if day in b_curve]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "strategy", "spy"])
        for prev, day in zip(dates, dates[1:]):
            writer.writerow([
                day,
                f"{s_curve[day] / s_curve[prev] - 1:.8f}",
                f"{b_curve[day] / b_curve[prev] - 1:.8f}",
            ])
    return path


def main(argv=None):
    chosen = [arg for arg in (sys.argv[1:] if argv is None else argv) if not arg.startswith("-")]
    if not chosen:
        chosen = ["gem", "gembil"]
    allowed = {"gem", "gembil"}
    refused = [key for key in chosen if key not in allowed]
    if refused:
        raise SystemExit(f"This entry point runs only gem and gembil. Refusing {refused}.")
    symbols = ["SPY", "SPYM", "VEU", "BIL", "AGG", "VT"]
    books = {sym: load_symbol(sym) for sym in symbols}
    sessions = sorted(books["SPY"]["actual"])
    start = _month_end(sessions, DECISION_START)
    end = _month_end(sessions, DECISION_END)
    early_end = _month_end(sessions, EARLY_END)
    supplement_end = _month_end(sessions, SUPPLEMENT_END)
    spy_adj = sorted(books["SPY"]["adj"].items())
    bil_adj = sorted(books["BIL"]["adj"].items())
    veu_adj = sorted(books["VEU"]["adj"].items())

    jobs = [
        ("gem", "gem-agg", "GEM (AGG) — 2026-10-15",
         gem_signals(spy_adj, bil_adj, veu_adj, "AGG", "SPYM"),
         gem_signals(spy_adj, bil_adj, veu_adj, "AGG", "SPY"),
         "gem-agg", False, "The September 2026 signal is VEU. See docs/backtest/GEM_SIGNAL_2026-09.md. "
         "That month-end is the last signal inside this window, so its fill is the next open, outside the window. "
         "The desk's independent cost path is 5.25% a year with 1.75 switches a year. That path closes this book NO-GO."),
        ("gembil", "gem-bil", "GEM-BIL — 2026-10-15",
         gem_signals(spy_adj, bil_adj, veu_adj, "BIL", "SPYM"),
         gem_signals(spy_adj, bil_adj, veu_adj, "BIL", "SPY"),
         "gem-bil", True, "Reported beside GEM. It is not selected when it is ahead of the AGG book. "
         "The decision for the rule is the AGG book. "
         "The desk's independent cost path is 5.42% a year. That path closes this book NO-GO."),
    ]
    written = []
    for key, trial_id, title, sig_x, sig_r, stem, is_bil, note in jobs:
        if key not in chosen:
            continue
        decision, reference, spy, vt = run_pair(key, trial_id, sig_x, sig_r, books, sessions, start, end)
        early = None
        early_note = None
        if is_bil:
            early_book, _ref, early_spy, _vt = run_pair(
                key, trial_id, sig_x, sig_r, books, sessions, start, early_end,
            )
            early = {
                "cagr": early_book["cagr"],
                "spy_cagr": early_spy["cagr"],
                "boot_rate": early_book["judge"]["boot"],
            }
            early_flags = leg_flags(trial_id, early["cagr"], early["spy_cagr"], early["boot_rate"])
            early_note = (
                f"- {'pass' if early_flags['cagr'] else 'fail'}: 2014-2021 CAGR "
                f"{_pct(early['cagr'])} against SPY {_pct(early['spy_cagr'])} "
                f"(gap {_pct(early_book['judge']['gap'])}).\n"
                f"- {'pass' if early_flags['drawdown'] else 'fail'}: 2014-2021 bootstrap "
                f"{_num(None if early['boot_rate'] is None else early['boot_rate'], '{:.1%}')}. "
                "GEM-BIL needs this window as well as 2014-today. 2022 is excluded from it."
            )
        verdict_text = trend_verdict(
            trial_id, decision["cagr"], spy["cagr"], decision["judge"]["boot"], early=early,
        )
        if trial_id == "gem-agg":
            write_trial_count(verdict_text)
        note = f"{note} {replication_note(trial_id)}"
        n_eff = dsr_trials_next()
        decision["verdict_text"] = verdict_text
        decision["flags"] = leg_flags(trial_id, decision["cagr"], spy["cagr"], decision["judge"]["boot"])
        decision["n_eff"] = n_eff
        decision["judge"]["stats"] = deflated_sharpe(decision["judge"]["left"], n_eff)
        listed = min(books["SPYM"]["actual"])
        live_signals = [day for day in sig_x if day >= listed]
        first_signal = min(live_signals) if live_signals else None
        supplement = None
        if first_signal is not None and first_signal <= supplement_end:
            sup, _r, sup_spy, _v = run_pair(
                key, trial_id, sig_x, sig_r, books, sessions, first_signal, supplement_end,
            )
            supplement = {
                "start": sup["start"], "end": sup["end"], "cagr": sup["cagr"], "final": sup["final"],
                "maxdd": sup["maxdd"], "spy_cagr": sup_spy["cagr"], "spy_maxdd": sup_spy["maxdd"],
            }
        text = render(title, decision, reference, spy, vt, early_note, supplement, note)
        path = common.ROOT / "docs" / "backtest" / f"{stem}-2026-10-15.md"
        path.write_text(text + "\n", encoding="utf-8")
        _write_series(stem, decision, spy)
        written.append((stem, verdict_text, decision["cagr"], spy["cagr"], decision["judge"]["boot"]))
        print(stem, verdict_text,
              f"cagr={decision['cagr']}", f"spy={spy['cagr']}", f"boot={decision['judge']['boot']}",
              f"trials={n_eff}",
              flush=True)
    return written


if __name__ == "__main__":
    main()
