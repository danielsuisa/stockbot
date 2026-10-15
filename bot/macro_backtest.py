"""T7 pre-FOMC drift and T8 CPI/PPI gap fade. Research only.

The rules are the T7/T8 section of docs/backtest/TRIALS.md. Nothing here places or
transmits an order. A GO would be a paper result.
"""
import csv
import datetime as dt
import gzip
import json
import math
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

from bot import common
from bot.macro_exec import (
    ACCOUNT, N_EFF, OOS_MIN, beats, bls_releases, fomc_events, gap_signals,
    meeting_dates, release_days, run_close_book, run_open_fade,
)
from bot.tax import after_tax_terminal
from bot.trend_exec import cagr, deflated_sharpe, max_drawdown, monthly_returns

NY = ZoneInfo("America/New_York")
CACHE = common.ROOT / ".cache" / "macro"
SERIES = common.ROOT / "docs" / "backtest" / "series"
DOCS = common.ROOT / "docs" / "backtest"
UA = "Mozilla/5.0 stockbot-research (research; danielsuisa/stockbot)"
FED = "https://www.federalreserve.gov/monetarypolicy/"
VIX1D_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX1D_History.csv"


def _get(url):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def _yahoo(symbol):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol.replace('^', '')}.json.gz"
    if path.exists():
        payload = json.loads(gzip.open(path, "rb").read().decode())
    else:
        period1 = int(dt.datetime(1999, 12, 1, tzinfo=dt.timezone.utc).timestamp())
        period2 = int(dt.datetime.now(dt.timezone.utc).timestamp())
        quoted = urllib.request.quote(symbol)
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quoted}"
            f"?period1={period1}&period2={period2}&interval=1d&events=div%7Csplit"
        )
        try:
            raw = _get(url)
        except urllib.error.HTTPError:
            url = url.replace("query1", "query2")
            raw = _get(url)
        payload = json.loads(raw.decode())
        with gzip.open(path, "wb") as handle:
            handle.write(json.dumps(payload).encode())
    block = payload["chart"]["result"][0]
    quote = block["indicators"]["quote"][0]
    adjusted = block["indicators"]["adjclose"][0]["adjclose"]
    closes, opens, adj = {}, {}, {}
    for stamp, opx, cpx, apx in zip(block["timestamp"], quote["open"], quote["close"], adjusted):
        if stamp is None:
            continue
        day = dt.datetime.fromtimestamp(stamp, dt.timezone.utc).astimezone(NY).date().isoformat()
        if cpx and cpx > 0:
            closes[day] = float(cpx)
        if opx and cpx and opx > 0 and cpx > 0:
            opens[day] = float(opx)
        if apx and apx > 0:
            adj[day] = float(apx)
    splits = block.get("events", {}).get("splits") or {}
    return {"open": opens, "close": closes, "adj": adj, "splits": len(splits)}


def _forward(days, series):
    """Last available print carried onto a missing session. Returns the filled map and the gap count."""
    filled, last, gaps = {}, None, 0
    for day in days:
        if day in series:
            last = series[day]
            filled[day] = last
        elif last is not None:
            filled[day] = last
            gaps += 1
    return filled, gaps


def _fomc_html():
    CACHE.mkdir(parents=True, exist_ok=True)
    pages = []
    for year in range(2000, 2021):
        path = CACHE / f"fomc-{year}.html"
        if not path.exists():
            path.write_bytes(_get(f"{FED}fomchistorical{year}.htm"))
        pages.append(path.read_text(errors="replace"))
    path = CACHE / "fomc-calendars.html"
    if not path.exists():
        path.write_bytes(_get(f"{FED}fomccalendars.htm"))
    pages.append(path.read_text(errors="replace"))
    dates = []
    for page in pages:
        dates.extend(meeting_dates(page))
    return sorted(set(dates))


def _bls_rows():
    CACHE.mkdir(parents=True, exist_ok=True)
    rows = []
    for year in range(2010, dt.datetime.now(NY).year + 1):
        path = CACHE / f"bls-{year}.html"
        if not path.exists():
            try:
                path.write_bytes(_get(f"https://www.bls.gov/schedule/{year}/home.htm"))
            except urllib.error.HTTPError:
                continue
        rows.extend(bls_releases(path.read_text(errors="replace")))
    return rows


def _vix1d():
    path = CACHE / "VIX1D.csv"
    if not path.exists():
        path.write_bytes(_get(VIX1D_URL))
    out = {}
    for row in csv.DictReader(path.read_text().splitlines()):
        raw = row.get("DATE") or row.get("Date")
        close = row.get("CLOSE") or row.get("Close")
        if not raw or not close:
            continue
        month, day, year = raw.split("/")
        out[f"{year}-{int(month):02d}-{int(day):02d}"] = float(close)
    return out


def _slice(days, start, end):
    return [day for day in days if start <= day <= end]


def _book(days, spy_days, prices, irx, bil_first, sgov_first):
    return run_close_book(days, spy_days, prices, irx, bil_first, sgov_first)


def _summarize(book, days):
    after, tax, years = after_tax_terminal(book["terminal"], book["realizations"])
    net = sum(pnl for _day, pnl in book["realizations"])
    if abs(net - (book["terminal"] - ACCOUNT)) > 0.05:
        raise SystemExit(
            f"Realized pnl ${net:,.2f} does not meet terminal ${book['terminal']:,.2f} from ${ACCOUNT:.0f}."
        )
    pre = cagr(ACCOUNT, book["terminal"], days[0], days[-1])
    post = cagr(ACCOUNT, after, days[0], days[-1])
    monthly = [ret for _day, ret in monthly_returns(book["curve"])]
    stats = deflated_sharpe(monthly, N_EFF)
    gross = []
    for trade in book["trades"]:
        if trade.get("entry_price") and trade.get("exit_price"):
            gross.append(trade["exit_price"] / trade["entry_price"] - 1)
        elif trade.get("gross") is not None:
            gross.append(trade["gross"])
    return {
        "after": after,
        "tax": tax,
        "tax_years": years,
        "pre": pre,
        "post": post,
        "dd": max_drawdown(book["curve"]),
        "stats": stats,
        "fees": book["fees"],
        "n": len(book["trades"]),
        "gross": gross,
        "terminal": book["terminal"],
    }


def _pct(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value * 100:+.2f}%"


def _num(value, digits=2):
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _series(path, strategy, spy):
    path.parent.mkdir(parents=True, exist_ok=True)
    spy_map = dict(spy)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("date", "strategy", "spy"))
        for day, ret in strategy:
            if day in spy_map:
                writer.writerow((day, f"{ret:.8f}", f"{spy_map[day]:.8f}"))


def _prices_for(days, spy, bil, sgov, irx):
    spy_adj, spy_gaps = _forward(days, spy["adj"])
    bil_adj, bil_gaps = _forward(days, bil["adj"])
    sgov_adj, sgov_gaps = _forward(days, sgov["adj"])
    irx_fill, irx_gaps = _forward(days, irx)
    bil_first = min(bil["adj"]) if bil["adj"] else None
    sgov_first = min(sgov["adj"]) if sgov["adj"] else None
    prices = {"SPY": spy_adj, "BIL": bil_adj, "SGOV": sgov_adj}
    return prices, irx_fill, bil_first, sgov_first, {
        "spy": spy_gaps, "bil": bil_gaps, "sgov": sgov_gaps, "irx": irx_gaps,
    }


def _line(label, summary, spy_summary):
    verdict = "GO" if beats(summary["post"], spy_summary["post"]) else "NO-GO"
    return verdict, (
        f"| {label} | {summary['n']} | {_pct(summary['post'])} | {_pct(spy_summary['post'])} | "
        f"{_pct(summary['pre'])} | {_pct(spy_summary['pre'])} | ${summary['fees']:,.0f} | "
        f"${summary['tax']:,.0f} | {_pct(summary['dd'])} | {verdict} |"
    )


def _dsr(summary):
    stats = summary["stats"]
    if not stats or stats.get("dsr") is None:
        return "The deflated Sharpe ratio is not available for this window."
    return (
        f"Monthly Sharpe {stats['sr']:.2f}. Probabilistic Sharpe {stats['psr']:.3f}. "
        f"Deflated Sharpe {stats['dsr']:.3f} at {N_EFF} effective trials."
    )


def run():
    print("loading prices")
    spy = _yahoo("SPY")
    bil = _yahoo("BIL")
    sgov = _yahoo("SGOV")
    vt = _yahoo("VT")
    irx_raw = _yahoo("^IRX")
    irx = irx_raw["close"]
    print("loading calendars")
    announcements = _fomc_html()
    releases = _bls_rows()
    vix = _vix1d()
    today = dt.datetime.now(NY).date().isoformat()
    sessions = sorted(day for day in spy["adj"] if day < today)
    last = sessions[-1]
    by_year = Counter(day[:4] for day in announcements if "2000" <= day[:4] <= "2025")
    thin = [year for year in range(2000, 2026) if by_year[f"{year}"] < 6]
    if thin:
        raise SystemExit(f"FOMC calendar is short in {thin}: {dict(sorted(by_year.items()))}")
    cpi_years = Counter(day[:4] for day, kind in releases if kind == "CPI")
    ppi_years = Counter(day[:4] for day, kind in releases if kind == "PPI")
    thin_cpi = [year for year in range(2010, 2026) if cpi_years[f"{year}"] < 10 or ppi_years[f"{year}"] < 10]
    if thin_cpi:
        raise SystemExit(f"BLS calendar is short in {thin_cpi}: CPI {dict(cpi_years)} PPI {dict(ppi_years)}")

    SERIES.mkdir(parents=True, exist_ok=True)
    with (SERIES / "fomc-announcements.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("date",))
        for day in announcements:
            writer.writerow((day,))
    with (SERIES / "bls-cpi-ppi.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("date", "release"))
        for day, kind in sorted(releases):
            writer.writerow((day, kind))

    fomc_report(sessions, announcements, spy, bil, sgov, vt, irx, today, last, by_year)
    cpi_report(sessions, releases, spy, bil, sgov, vt, irx, vix, today, last, cpi_years, ppi_years)
    print("wrote reports")


def _sample_books(sessions, announcements, variant, start, end, spy, bil, sgov, vt, irx):
    days = _slice(sessions, start, end)
    prices, irx_fill, bil_first, sgov_first, gaps = _prices_for(days, spy, bil, sgov, irx)
    events = [
        event for event in fomc_events(sessions, announcements, variant)
        if start <= event["entry"] <= end and start <= event["exit"] <= end
    ]
    held = [day for event in events for day in event["spy_days"]]
    book = _book(days, held, prices, irx_fill, bil_first, sgov_first)
    spy_book = _book(days, days[1:], prices, irx_fill, bil_first, sgov_first)
    vt_prices = {"SPY": _forward(days, vt["adj"])[0], "BIL": {}, "SGOV": {}}
    vt_days = [day for day in days if day in vt_prices["SPY"]]
    vt_book = None
    if len(vt_days) > 10:
        vt_book = _book(vt_days, vt_days[1:], {"SPY": vt_prices["SPY"]}, {}, None, None)
    return days, events, book, spy_book, vt_book, gaps, bil_first, sgov_first


def fomc_report(sessions, announcements, spy, bil, sgov, vt, irx, today, last, by_year):
    samples = (("2000-01-01", "2014-12-31", "IS"), ("2015-01-01", last, "OOS"))
    lines = [
        "# Pre-FOMC drift — T7",
        "",
        f"Run date {today}. The last session is {last}. This is a paper test. It places no order.",
        "",
        "Variant A buys SPY at the close two trading sessions before the announcement session and "
        "sells at the close of the session before the announcement. Variant C sells at the close of "
        "the announcement session. Variant B, the 13:55 ET sale, has no print in this run. There is "
        "no Alpaca credential and no public minute archive, so B is skipped and has no verdict.",
        "",
        "Announcement dates are the Fed historical calendar: a Meeting heading on "
        "`fomchistorical2000.htm` through `fomchistorical2020.htm`, and `fomccalendars.htm` after that. "
        "A conference call, a notation vote, and a cancelled meeting are left out. A Fed date that is "
        "not a SPY session uses the next SPY session. Daily prices are Yahoo Finance chart v8, including "
        f"the years before 2016. SPY splits in the download: {spy['splits']}. "
        "The ETF marks are adjusted closes.",
        "",
        f"The calendar holds {len(announcements)} announcement dates. "
        f"Counts from 2000 through 2025 run from {min(by_year.values())} to {max(by_year.values())} a year.",
        "",
        "The account starts at $460 and may hold fractional shares. Out of SPY it holds BIL before "
        "2020-01-01, SGOV from its first bar in 2020, and before BIL a cash accrual at the prior "
        "session's Yahoo ^IRX close divided by 100 and by 252. Commission is IBKR Pro fixed, $0.005 a "
        "share, minimum $1, maximum 1% of trade value, plus 1 basis point a side. Selling the sleeve "
        "and buying SPY are both orders. The 25% tax nets each calendar year and is subtracted once "
        "from terminal equity. The daily path stays pre-tax.",
        "",
        "The bar is the out-of-sample after-tax CAGR, at least 1 percentage point above buy-and-hold "
        "SPY over the same dates. In-sample cannot flip the verdict. A and C are judged separately. "
        "The higher one is not selected. VT is shown pre-tax, beside SPY. SPY is the bar.",
        "",
        "| Book | Trades | After-tax CAGR | SPY after-tax | Pre-tax CAGR | SPY pre-tax | Fees | Tax | Max DD | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    verdicts = {}
    for start, end, label in samples:
        for variant in ("A", "C"):
            days, events, book, spy_book, vt_book, gaps, bil_first, sgov_first = _sample_books(
                sessions, announcements, variant, start, end, spy, bil, sgov, vt, irx,
            )
            summary = _summarize(book, days)
            spy_summary = _summarize(spy_book, days)
            verdict, row = _line(f"{variant} {label}", summary, spy_summary)
            lines.append(row)
            _series(SERIES / f"fomc-{variant.lower()}-{label.lower()}.csv", book["returns"], spy_book["returns"])
            verdicts[(variant, label)] = (verdict, summary, spy_summary, vt_book, days, events, gaps, bil_first, sgov_first)
    lines.extend(["", "## Out of sample", ""])
    for variant in ("A", "C"):
        verdict, summary, spy_summary, vt_book, days, events, gaps, bil_first, sgov_first = verdicts[(variant, "OOS")]
        vt_text = "VT has no book on this window."
        if vt_book is not None:
            vt_pre = cagr(ACCOUNT, vt_book["terminal"], vt_book["curve"][0][0], vt_book["curve"][-1][0])
            vt_text = f"VT buy-and-hold pre-tax CAGR is {_pct(vt_pre)} from {vt_book['curve'][0][0]}."
        gross = summary["gross"]
        mean = sum(gross) / len(gross) if gross else None
        hit = sum(1 for value in gross if value > 0) / len(gross) if gross else None
        lines.append(
            f"Variant {variant} is **{verdict}**. {summary['n']} round trips, from {days[0]} to {days[-1]}. "
            f"After-tax CAGR {_pct(summary['post'])} against SPY {_pct(spy_summary['post'])}. "
            f"Pre-tax CAGR {_pct(summary['pre'])} against SPY {_pct(spy_summary['pre'])}. "
            f"Mean held-window SPY move {_pct(mean)}. "
            f"Winning windows {(_num(hit * 100, 1) + '%') if hit is not None else 'n/a'}. "
            f"{vt_text} {_dsr(summary)} "
            f"Maximum drawdown {_pct(summary['dd'])}, pre-tax. "
            f"Fees ${summary['fees']:,.2f}. Tax ${summary['tax']:,.2f}. "
            f"BIL starts {bil_first}. SGOV starts {sgov_first}. "
            f"Forward-filled sessions: SPY {gaps['spy']}, BIL {gaps['bil']}, SGOV {gaps['sgov']}, ^IRX {gaps['irx']}."
        )
        lines.append("")
    lines.extend([
        "## In sample",
        "",
        "In-sample is 2000 through 2014. It is reported here and does not change the verdicts above.",
        "",
    ])
    for variant in ("A", "C"):
        verdict, summary, spy_summary, _vt, days, _events, _gaps, _b, _s = verdicts[(variant, "IS")]
        lines.append(
            f"Variant {variant} in sample would read {verdict} on the same bar: after-tax CAGR "
            f"{_pct(summary['post'])} against SPY {_pct(spy_summary['post'])}, {summary['n']} round trips, "
            f"{days[0]} to {days[-1]}. {_dsr(summary)}"
        )
        lines.append("")
    lines.extend([
        "## Variant B",
        "",
        "Variant B is skipped. The registered sale is 13:55 ET on the announcement session, and this "
        "run has no bar at that time. It is inside the pre-FOMC cluster. It is not a separate trial "
        "and it is not a verdict.",
        "",
        "Daily pre-tax returns, strategy and SPY, are in `docs/backtest/series/fomc-a-is.csv`, "
        "`fomc-a-oos.csv`, `fomc-c-is.csv`, and `fomc-c-oos.csv`. The announcement dates are in "
        "`fomc-announcements.csv`.",
        "",
    ])
    path = DOCS / "fomc-drift-2026-10-15.md"
    path.write_text("\n".join(lines))
    print(path)


def cpi_report(sessions, releases, spy, bil, sgov, vt, irx, vix, today, last, cpi_years, ppi_years):
    days_by = {
        "IS": _slice(sessions, "2010-01-01", "2018-12-31"),
        "OOS": _slice(sessions, "2019-01-01", last),
    }
    published = release_days(releases)
    lines = [
        "# CPI and PPI gap-down fade — T8",
        "",
        f"Run date {today}. The last session is {last}. This is a paper test. It places no order.",
        "",
        "The registered buy is 9:35 ET on a CPI or PPI release day, and the exit is the close, when "
        "SPY opens at least 0.5% below the prior close. This run has no 9:35 print. There is no Alpaca "
        "credential and no public minute archive. Registered out-of-sample trades at that fill: 0. "
        f"The pre-registered minimum is {OOS_MIN}. The verdict is **unknown**.",
        "",
        "The numbers below are a separate description. They buy the Yahoo official open and sell the "
        "official close on the same rule. They are not the 9:35 book and they are not the verdict. "
        "On a traded morning the sleeve does not accrue from the prior close to the open. Every other "
        "session accrues the sleeve from close to close.",
        "",
        "Release dates are the BLS year pages from 2010 through the run year. A row counts when its "
        "name is exactly Consumer Price Index or Producer Price Index. Two releases on one session are "
        "one day. The gap uses the official open and the prior official close, dividend included. "
        f"SPY splits in the download: {spy['splits']}. Daily prices are Yahoo Finance chart v8.",
        "",
        f"CPI rows by year run from {min(cpi_years.values())} to {max(cpi_years.values())}. "
        f"PPI rows run from {min(ppi_years.values())} to {max(ppi_years.values())}. "
        f"Distinct release sessions: {len(published)}.",
        "",
        "Costs, the sleeve, the $460 account, and the 25% tax match the pre-FOMC book. The description "
        "is scored on the same 1 percentage point after-tax line so the distance is visible. That score "
        "is not a verdict.",
        "",
        "| Book | Trades | After-tax CAGR | SPY after-tax | Pre-tax CAGR | SPY pre-tax | Fees | Tax | Max DD | On the line |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    stored = {}
    for label, days in days_by.items():
        prices, irx_fill, bil_first, sgov_first, gaps = _prices_for(days, spy, bil, sgov, irx)
        signals = gap_signals(days, spy["open"], spy["close"], published)
        book = run_open_fade(
            days, signals, prices, spy["open"], spy["close"], irx_fill, bil_first, sgov_first,
        )
        spy_book = _book(days, days[1:], prices, irx_fill, bil_first, sgov_first)
        summary = _summarize(book, days)
        spy_summary = _summarize(spy_book, days)
        mark = "above" if beats(summary["post"], spy_summary["post"]) else "below"
        lines.append(
            f"| Open {label} | {summary['n']} | {_pct(summary['post'])} | {_pct(spy_summary['post'])} | "
            f"{_pct(summary['pre'])} | {_pct(spy_summary['pre'])} | ${summary['fees']:,.0f} | "
            f"${summary['tax']:,.0f} | {_pct(summary['dd'])} | {mark} |"
        )
        _series(SERIES / f"cpi-ppi-open-{label.lower()}.csv", book["returns"], spy_book["returns"])
        vt_prices = _forward(days, vt["adj"])[0]
        vt_days = [day for day in days if day in vt_prices]
        vt_book = _book(vt_days, vt_days[1:], {"SPY": vt_prices}, {}, None, None) if len(vt_days) > 10 else None
        stored[label] = (summary, spy_summary, book, vt_book, gaps, bil_first, sgov_first, signals)
    oos_summary, oos_spy, oos_book, oos_vt, gaps, bil_first, sgov_first, _signals = stored["OOS"]
    lines.extend(["", "## The registered book", ""])
    lines.append(
        f"Out-of-sample trades at 9:35 ET: 0. That is below {OOS_MIN}. The verdict is **unknown**. "
        "In-sample 9:35 trades are 0 as well. No tail and no 2022 result exists for the registered fill, "
        "because that fill was not observed."
    )
    lines.extend(["", "## Open-fill description", ""])
    vt_text = "VT has no book on this window."
    if oos_vt is not None:
        vt_pre = cagr(ACCOUNT, oos_vt["terminal"], oos_vt["curve"][0][0], oos_vt["curve"][-1][0])
        vt_text = f"VT buy-and-hold pre-tax CAGR is {_pct(vt_pre)}."
    lines.append(
        f"Out of sample the open-fill description has {oos_summary['n']} trades. "
        f"After-tax CAGR {_pct(oos_summary['post'])} against SPY {_pct(oos_spy['post'])}. "
        f"Pre-tax CAGR {_pct(oos_summary['pre'])} against SPY {_pct(oos_spy['pre'])}. "
        f"{vt_text} {_dsr(oos_summary)} "
        f"Maximum drawdown {_pct(oos_summary['dd'])}, pre-tax. "
        f"Fees ${oos_summary['fees']:,.2f}. Tax ${oos_summary['tax']:,.2f}. "
        f"BIL starts {bil_first}. SGOV starts {sgov_first}. "
        f"Forward-filled sessions: SPY {gaps['spy']}, BIL {gaps['bil']}, SGOV {gaps['sgov']}, ^IRX {gaps['irx']}."
    )
    is_summary, is_spy, _is_book, _is_vt, _g, _b, _s, _sig = stored["IS"]
    lines.extend(["", "## In sample, open fill", ""])
    lines.append(
        f"In sample, 2010 through 2018, the open fill has {is_summary['n']} trades. After-tax CAGR "
        f"{_pct(is_summary['post'])} against SPY {_pct(is_spy['post'])}. It does not change the verdict."
    )
    tails = sorted(oos_book["trades"], key=lambda row: row["gross"])[:5]
    lines.extend(["", "## Tail losses, open fill, out of sample", ""])
    if not tails:
        lines.append("There are no open-fill trades in the out-of-sample window.")
    else:
        lines.append("| Date | Gap | Open-to-close | Dollar pnl |")
        lines.append("|---|---:|---:|---:|")
        for trade in tails:
            lines.append(
                f"| {trade['day']} | {_pct(trade['gap'])} | {_pct(trade['gross'])} | ${trade['pnl']:,.2f} |"
            )
    y2022 = [trade for trade in oos_book["trades"] if trade["day"].startswith("2022")]
    lines.extend(["", "## 2022, open fill", ""])
    if not y2022:
        lines.append("No open-fill trade fell in 2022.")
    else:
        lines.append("| Date | Gap | Open-to-close | Dollar pnl |")
        lines.append("|---|---:|---:|---:|")
        for trade in y2022:
            lines.append(
                f"| {trade['day']} | {_pct(trade['gap'])} | {_pct(trade['gross'])} | ${trade['pnl']:,.2f} |"
            )
        gross = [trade["gross"] for trade in y2022]
        lines.append("")
        lines.append(
            f"2022 open-fill trades: {len(y2022)}. Mean open-to-close {_pct(sum(gross) / len(gross))}."
        )
    # VIX1D description, from the first session that has a prior print.
    vix_days = [day for day in sessions if day >= "2019-01-01" and day <= last]
    first_vix = None
    prev = None
    for day in vix_days:
        if prev in vix:
            first_vix = day
            break
        prev = day
    lines.extend(["", "## VIX1D variant, description only", ""])
    if first_vix is None:
        lines.append("VIX1D has no prior close on this sample. The variant is empty.")
    else:
        window = [day for day in vix_days if day >= first_vix]
        # Include the session before first_vix so the first gap has a prior close inside `window`.
        prior = sessions[sessions.index(first_vix) - 1]
        window = [prior] + window
        prices, irx_fill, bil_first, sgov_first, _gaps = _prices_for(window, spy, bil, sgov, irx)
        signals = gap_signals(window, spy["open"], spy["close"], published, vix=vix, vix_filter=True)
        book = run_open_fade(window, signals, prices, spy["open"], spy["close"], irx_fill, bil_first, sgov_first)
        spy_book = _book(window, window[1:], prices, irx_fill, bil_first, sgov_first)
        summary = _summarize(book, window)
        spy_summary = _summarize(spy_book, window)
        _series(SERIES / "cpi-ppi-vix1d.csv", book["returns"], spy_book["returns"])
        lines.append(
            f"VIX1D is the CBOE daily file. The first session with a prior close is {first_vix}. "
            f"The variant keeps the 0.5% rule and also requires the gap to be strictly below that prior "
            f"close divided by 100 and by the square root of 252. It is an out-of-sample description. "
            f"Trades: {summary['n']}. After-tax CAGR {_pct(summary['post'])} against SPY "
            f"{_pct(spy_summary['post'])}. Pre-tax CAGR {_pct(summary['pre'])}. {_dsr(summary)} "
            "This description is not a verdict."
        )
    lines.extend([
        "",
        "Daily pre-tax returns are in `docs/backtest/series/cpi-ppi-open-is.csv`, "
        "`cpi-ppi-open-oos.csv`, and `cpi-ppi-vix1d.csv`. The release dates are in `bls-cpi-ppi.csv`.",
        "",
    ])
    path = DOCS / "cpi-ppi-fade-2026-10-15.md"
    path.write_text("\n".join(lines))
    print(path)


if __name__ == "__main__":
    run()
