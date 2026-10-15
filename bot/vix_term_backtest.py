"""VIX term-structure replay. Locked on 2026-10-15, before this run.

Nothing in this module places or transmits an order. A GO is a paper result, not a merge and not
permission to trade. The listener is unchanged. VIX and VIX3M are the free CBOE history. SPY, BIL,
and SGOV are Yahoo Finance adjusted opens and closes. The rules are in bot/vix_term.py.
"""
import csv
import datetime as dt
import gzip
import json
import traceback
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from bot import common
from bot.tax import TAX, after_tax_terminal, spy_hold_tax
from bot.vix_term import ACCOUNT, IS_START, OOS_START, SPREAD, goes, parse_cboe, parse_yahoo, simulate

NY = ZoneInfo("America/New_York")
CACHE = common.ROOT / ".cache" / "yahoo"
CBOE_CACHE = common.ROOT / ".cache" / "cboe"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
CBOE = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"
SOURCE = "CBOE daily history; Yahoo Finance chart v8 adjusted open and close"
ETFS = ("SPY", "BIL", "SGOV")


def _needed(today):
    """The last weekday strictly before the run date. A holiday gap is still a download miss."""
    day = dt.date.fromisoformat(today) - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day.isoformat()


def _yahoo(symbol, today):
    path = CACHE / f"{symbol}.json.gz"
    got = None
    if path.exists():
        got = json.loads(gzip.GzipFile(path, "rb").read().decode("utf-8"))
        rows = parse_yahoo(got)
        if rows and max(rows) >= _needed(today):
            return rows
    q = urllib.parse.urlencode({
        "period1": str(int(dt.datetime(2007, 1, 1, tzinfo=dt.timezone.utc).timestamp())),
        "period2": str(int(dt.datetime.fromisoformat(today).replace(tzinfo=dt.timezone.utc).timestamp()) + 86400),
        "interval": "1d", "events": "div|split", "includeAdjustedClose": "true",
    })
    req = urllib.request.Request(YAHOO.format(symbol=symbol) + "?" + q, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        got = json.loads(resp.read().decode("utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(path, "wb") as fh:
        fh.write(json.dumps(got).encode("utf-8"))
    return parse_yahoo(got)


def _cboe(name, today):
    path = CBOE_CACHE / f"{name}_History.csv"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        rows = parse_cboe(text)
        if rows and max(rows) >= _needed(today):
            return rows
    req = urllib.request.Request(CBOE.format(name=name), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return parse_cboe(text)


def _fmt(x, spec="{:.2f}"):
    return "—" if x is None else spec.format(x)


def _tax_row(book):
    marks = list(book["realizations"])
    if book.get("final_mark"):
        marks.append(tuple(book["final_mark"]))
    after, tax, _years = after_tax_terminal(book["final_equity"], marks)
    spy_after, spy_tax = spy_hold_tax(ACCOUNT, book["spy_final"])
    return after, tax, spy_after, spy_tax


def render(result):
    verdict = result.get("verdict") or {"go": False, "checks": []}
    one = (result.get("books") or {}).get("1") or {}
    two = (result.get("books") or {}).get("2") or {}
    lines = [
        f"# VIX term structure — {result.get('run')}",
        "",
        "Hold SPY when the prior session's VIX close is below the prior session's VIX3M close. Otherwise "
        "hold SGOV. Before 2020-01-01, and until SGOV's first bar, the sleeve is BIL. The fill is the next "
        "session's open, so today's close does not trade today's open. A second book requires the same "
        "relationship on two consecutive prior closes. That book is not the verdict. VIX and VIX3M are the "
        f"free CBOE history. ETF opens and closes are Yahoo adjusted prices (`{SOURCE}`). This replay does "
        "not place or transmit an order.",
        "",
        f"Account ${ACCOUNT:.0f}, compounding, fractional shares. IBKR fixed commission is $0.0035 a share "
        f"with a $0.35 minimum. The spread is {SPREAD:.2%} round trip, half on the buy and half on the sell. "
        "Costs are paid on the first buy and on each switch. Buy-and-hold SPY pays the entry cost once, on "
        f"the same first open, and then holds. In-sample is {IS_START[:4]}–2019. Out-of-sample is "
        f"{OOS_START[:4]} through the last session before the run date. Sharpe is daily, the risk-free rate "
        "is zero, and the scale is the square root of 252. Maximum drawdown uses out-of-sample closes only. "
        "The first out-of-sample close starts the peak.",
        "",
        f"Verdict: **{'GO' if verdict.get('go') else 'NO-GO'}**.",
        "",
    ]
    for label, ok in verdict.get("checks") or []:
        lines.append(f"- {'pass' if ok else 'fail'}: {label}")
    if not result.get("complete", False) and result.get("reason"):
        lines.append(f"Download note: {result.get('reason')}.")
    rate = result.get("drop_rate")
    lines += ["",
              f"Window: overlap {result.get('window_start')} through {result.get('end')}. "
              f"One-day execution {((one.get('start'), one.get('end')))}. "
              f"Two-day execution {(two.get('start'), two.get('end'))}.",
              result.get("gap") or "VIX, VIX3M, SPY, and BIL all print on or before 2008-01-01.",
              f"SGOV first bar: {result.get('sgov_first')}. Sessions considered {result.get('candidates')}. "
              f"Dropped {result.get('dropped')} ({_fmt(rate, '{:.2%}')}).",
              "",
              "| Book | Sample | Days | Sharpe | SPY Sharpe | Max drawdown | SPY max drawdown | "
              "Switches | Ending equity |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for label, book in (("1-day", one), ("2-day (not the verdict)", two)):
        for sample, title in (("oos", "2020–now"), ("is", "2008–2019")):
            stats = book.get(sample) or {}
            lines.append(f"| {label} | {title} | {stats.get('n', 0)} | {_fmt(stats.get('sharpe'))} | "
                         f"{_fmt(stats.get('spy_sharpe'))} | {_fmt(stats.get('maxdd'), '{:+.2%}')} | "
                         f"{_fmt(stats.get('spy_maxdd'), '{:+.2%}')} | {stats.get('switches', 0)} | "
                         f"{_fmt(stats.get('final'), '${:,.2f}')} |")
    lines += ["",
              f"One-day commission ${one.get('fee', 0):.2f}, spread ${one.get('spread', 0):.2f}. "
              f"Two-day commission ${two.get('fee', 0):.2f}, spread ${two.get('spread', 0):.2f}.",
              "",
              "## Switches per year",
              "",
              "A switch is a change of symbol, including BIL to SGOV. The first buy is not a switch.",
              "",
              "| Year | 1-day | 2-day |",
              "|---|---:|---:|"]
    years = sorted(set(one.get("per_year") or {}) | set(two.get("per_year") or {}))
    for year in years:
        lines.append(f"| {year} | {(one.get('per_year') or {}).get(year, 0)} | "
                     f"{(two.get('per_year') or {}).get(year, 0)} |")
    lines += ["",
              f"Average switches per year: 1-day {_fmt(one.get('switches_per_year'), '{:.2f}')}, "
              f"2-day {_fmt(two.get('switches_per_year'), '{:.2f}')}.",
              "",
              "## After tax",
              "",
              f"Israeli capital-gains tax at {TAX:.0%}. Realized losses offset realized gains inside the "
              "same calendar year. A year that nets to a loss pays nothing and does not carry forward. "
              "There is no refund. The tax is subtracted from terminal equity and is not compounded inside "
              "the path. SGOV and BIL gains are taxed the same way. There is no exemption. Sharpe and "
              "maximum drawdown stay pre-tax.",
              "",
              "The strategy's tax line adds one final mark: shares times the last close, minus the all-in "
              "cost basis, with no extra spread. That mark is not a trade in the Sharpe path. Buy-and-hold "
              "SPY is taxed once, on the gain from the starting $460 to its terminal equity.",
              "",
              "| Book | Pre-tax terminal | Tax | After tax | SPY pre-tax | SPY tax | SPY after tax |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for label, book in (("1-day", one), ("2-day (not the verdict)", two)):
        if not book:
            lines.append(f"| {label} | — | — | — | — | — | — |")
            continue
        after, tax, spy_after, spy_tax = _tax_row(book)
        lines.append(f"| {label} | {_fmt(book['final_equity'], '${:,.2f}')} | {_fmt(tax, '${:,.2f}')} | "
                     f"{_fmt(after, '${:,.2f}')} | {_fmt(book['spy_final'], '${:,.2f}')} | "
                     f"{_fmt(spy_tax, '${:,.2f}')} | {_fmt(spy_after, '${:,.2f}')} |")
    two_line = "- Two consecutive closes: reported, not the verdict."
    if two:
        two_verdict = goes(two.get("oos") or {}, result.get("complete", False))
        failed = [label for label, ok in two_verdict["checks"] if not ok]
        if failed:
            two_line += " Its out-of-sample checks would fail: " + "; ".join(failed) + "."
        else:
            two_line += " Its out-of-sample checks would pass."
    lines += ["", "## Verdict", "",
              f"- VIX term structure, one-day book: {'GO' if verdict.get('go') else 'NO-GO'}.",
              two_line,
              ""]
    return "\n".join(lines)


def _write(result):
    stem = Path(f"docs/backtest/vix-term-{result['run']}")
    stem.parent.mkdir(parents=True, exist_ok=True)
    text = render(result)
    if not result.get("complete", True):
        text = text.replace("**GO**", "**NO-GO**").replace(": GO.", ": NO-GO.")
    stem.with_suffix(".md").write_text(text, encoding="utf-8")
    fields = ["day", "book", "symbol", "equity", "ret", "switch", "sample"]
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fields, extrasaction="ignore")
        writer.writeheader()
        for key, book in (result.get("books") or {}).items():
            for row in book.get("rows") or []:
                writer.writerow({"book": key, **row})
    slim = {k: v for k, v in result.items() if k != "books"}
    slim["books"] = {}
    for key, book in (result.get("books") or {}).items():
        slim["books"][key] = {k: v for k, v in book.items() if k != "rows"}
    stem.with_suffix(".json").write_text(json.dumps(slim, indent=1, default=str), encoding="utf-8")
    print(f"wrote {stem}.md", flush=True)


def run():
    today = dt.datetime.now(NY).date().isoformat()
    prices = {symbol: _yahoo(symbol, today) for symbol in ETFS}
    vix = _cboe("VIX", today)
    vix3m = _cboe("VIX3M", today)
    result = simulate(prices, vix, vix3m, today)
    result.update(run=today, source=SOURCE)
    return result


def main():
    try:
        result = run()
    except Exception:
        traceback.print_exc()
        today = dt.datetime.now(NY).date().isoformat()
        stub = {"run": today, "complete": False, "reason": "download failed", "books": {},
                "verdict": {"go": False, "checks": [("the download finished", False)]},
                "window_start": None, "end": None, "gap": None, "sgov_first": None,
                "candidates": 0, "dropped": 0, "drop_rate": None, "source": SOURCE}
        _write(stub)
        return 1
    _write(result)
    return 0 if result.get("complete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
