"""Sector-momentum replay. Locked on 2026-10-15, before this run.

Nothing in this module places or transmits an order. A GO is a paper result, not a merge and not
permission to trade. The listener is unchanged. Prices are Yahoo Finance adjusted closes. Alpaca's
daily history starts near 2016, so it is not the source.
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
from bot.sector_mom import (
    ACCOUNT, EXCLUDED, SMA_DAYS, SPREAD, UNIVERSE, goes, simulate, slice_stats,
)

NY = ZoneInfo("America/New_York")
CACHE = common.ROOT / ".cache" / "yahoo"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
WARM = "2008-01-01"
SOURCE = "Yahoo Finance chart v8 adjclose"


def _get(symbol, start, end):
    path = CACHE / f"{symbol}.json.gz"
    got = None
    if path.exists():
        got = json.loads(gzip.GzipFile(path, "rb").read().decode("utf-8"))
    if got is None:
        q = urllib.parse.urlencode({
            "period1": str(int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp())),
            "period2": str(int(dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone.utc).timestamp()) + 86400),
            "interval": "1d", "events": "div|split", "includeAdjustedClose": "true",
        })
        req = urllib.request.Request(URL.format(symbol=symbol) + "?" + q, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            got = json.loads(resp.read().decode("utf-8"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.GzipFile(path, "wb") as fh:
            fh.write(json.dumps(got).encode("utf-8"))
    result = (got.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"no chart for {symbol}")
    block = result[0]
    stamps = block.get("timestamp") or []
    adj = ((block.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    rows = {}
    for ts, px in zip(stamps, adj):
        if px is None:
            continue
        day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).astimezone(NY).date().isoformat()
        rows[day] = float(px)
    return sorted(rows.items())


def _fmt(x, spec="{:+.3f}"):
    return "—" if x is None else spec.format(x)


def _window(result, sample):
    months = [m for m in result["months"] if m["sample"] == sample]
    daily = [row for row in result["daily"] if row[2] == sample]
    if not months:
        return slice_stats([], [], None, None, None, None, None, None)
    opened = (result.get("opens") or {}).get(sample)
    start = opened[0] if opened else months[0]["end"]
    start_eq = opened[1] if opened else None
    end = result["end"] if sample == "oos" else months[-1]["end"]
    end_eq = result["final_equity"] if sample == "oos" else months[-1]["equity"]
    spy = {day: px for day, px in result["spy"]}
    return slice_stats(months, daily, start, end, start_eq, end_eq, spy.get(start), spy.get(end))


def render(result):
    oos = _window(result, "oos")
    ins = _window(result, "is")
    verdict = goes(oos, result.get("complete", False))
    lines = [
        f"# Sector momentum — {result.get('run')}",
        "",
        "Nine original SPDR sector ETFs, equal weight in the top two, held for one month. XLRE and XLC are "
        "excluded. The formation return at month-end t is the adjusted close at t-1 divided by the adjusted "
        f"close at t-6, minus one. If SPY's month-end adjusted close is below its {SMA_DAYS}-day average, "
        "the account holds cash. Cash earns nothing. Prices are Yahoo Finance adjusted closes "
        f"(`{SOURCE}`). This replay does not place or transmit an order.",
        "",
        f"Account ${ACCOUNT:.0f}, compounding, fractional shares. IBKR fixed commission is $0.0035 a share "
        f"with a $0.35 minimum on each order. The spread is {SPREAD:.2%} round trip, half on the buy and half "
        "on the sell. In-sample months end in 2010–2019. Out-of-sample months end in 2020 through the last "
        "completed month. The rule is the same on both sides.",
        "",
        f"Verdict: **{'GO' if verdict['go'] else 'NO-GO'}**.",
        "",
    ]
    for label, ok in verdict["checks"]:
        lines.append(f"- {'pass' if ok else 'fail'}: {label}")
    if not result.get("complete", False):
        lines.append(f"- fail: the download finished ({result.get('reason')})")
    lines += ["",
              f"Window: {result.get('start')} through {result.get('end')}. First SPY bar: {result.get('first_bar')}.",
              f"Universe: {', '.join(UNIVERSE)}. Excluded: {', '.join(EXCLUDED)}.",
              f"Commission paid ${result.get('fee', 0):.2f}. Spread paid ${result.get('spread', 0):.2f}. "
              f"Ending equity ${result.get('final_equity', 0):.2f}.",
              "",
              "| Sample | Months | Mean monthly | Sharpe | SPY Sharpe | CAGR | SPY CAGR | Max drawdown | Cash months |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, stats in (("2020–now", oos), ("2010–2019", ins)):
        lines.append(f"| {name} | {stats['n']} | {_fmt(stats['mean'], '{:+.2%}')} | {_fmt(stats['sharpe'], '{:.2f}')} | "
                     f"{_fmt(stats['spy_sharpe'], '{:.2f}')} | {_fmt(stats['cagr'], '{:+.2%}')} | "
                     f"{_fmt(stats['spy_cagr'], '{:+.2%}')} | {_fmt(stats['maxdd'], '{:+.2%}')} | {stats['cash_months']} |")
    lines += ["", "Out-of-sample years. The return is the compounded monthly result. SPY is buy-and-hold "
              "over the same months, with no commission and no spread.",
              "",
              "| Year | Months | Strategy | SPY | Months in cash |",
              "|---|---:|---:|---:|---:|"]
    for year, row in oos["years"].items():
        lines.append(f"| {year} | {row['n']} | {_fmt(row['ret'], '{:+.2%}')} | {_fmt(row['spy'], '{:+.2%}')} | {row['cash']} |")
    lines += ["", "## Verdict", "",
              f"- Sector momentum: {'GO' if verdict['go'] else 'NO-GO'}.",
              ""]
    return "\n".join(lines)


def _write(result):
    stem = Path(f"docs/backtest/sector-mom-{result['run']}")
    stem.parent.mkdir(parents=True, exist_ok=True)
    text = render(result)
    if not result.get("complete", True):
        text = text.replace("**GO**", "**NO-GO**").replace(": GO.", ": NO-GO.")
    stem.with_suffix(".md").write_text(text, encoding="utf-8")
    fields = ["end", "sample", "ret", "spy", "regime", "names", "equity", "fee", "spread"]
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fields, extrasaction="ignore")
        writer.writeheader()
        for row in result.get("months") or []:
            writer.writerow(row)
    slim = {k: v for k, v in result.items() if k not in ("months", "daily", "spy", "prices")}
    slim["oos"] = _window(result, "oos")
    slim["is"] = _window(result, "is")
    slim["verdict"] = goes(slim["oos"], result.get("complete", False))
    stem.with_suffix(".json").write_text(json.dumps(slim, indent=1, default=str), encoding="utf-8")
    print(f"wrote {stem}.md", flush=True)


def run():
    today = dt.datetime.now(NY).date().isoformat()
    end = today
    symbols = list(UNIVERSE) + ["SPY"]
    series = {sym: _get(sym, WARM, end) for sym in symbols}
    first = series["SPY"][0][0] if series["SPY"] else None
    result = simulate({sym: series[sym] for sym in UNIVERSE}, series["SPY"], today)
    result.update(run=today, first_bar=first, spy=series["SPY"], source=SOURCE)
    return result


def main():
    try:
        result = run()
    except Exception:
        traceback.print_exc()
        today = dt.datetime.now(NY).date().isoformat()
        stub = {"run": today, "complete": False, "reason": "download failed", "months": [], "daily": [],
                "opens": {}, "fee": 0, "spread": 0, "final_equity": ACCOUNT, "start": None, "end": None,
                "first_bar": None, "spy": [], "source": SOURCE}
        _write(stub)
        return 1
    _write(result)
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
