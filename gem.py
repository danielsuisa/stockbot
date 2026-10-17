"""Independent GEM check. Not part of bot/.

Absolute and relative momentum. The twelve-month return ends at the month-end
adjusted close; the last month is included. The decision at that close is the
holding for the next calendar month. No costs and fractional shares.

SPY must beat BIL or the book holds the defensive fund. A tie is not a win.
If SPY wins that test, the book holds VEU when VEU beat SPY, otherwise SPY.
"""
import datetime as dt
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
CACHE = Path(__file__).resolve().parent / ".cache" / "yahoo" / "trend"
HOLD_START = "2014-01"
HOLD_END = "2025-12"


def month_ends(adjusted):
    """Last adjusted close of each calendar month, keyed YYYY-MM."""
    last = {}
    for day in sorted(adjusted):
        last[day[:7]] = float(adjusted[day])
    return last


def shift(month, count):
    year, mon = (int(part) for part in month.split("-"))
    mon += count
    while mon < 1:
        mon += 12
        year -= 1
    while mon > 12:
        mon -= 12
        year += 1
    return f"{year:04d}-{mon:02d}"


def months_between(start, end):
    out = []
    month = start
    while month <= end:
        out.append(month)
        month = shift(month, 1)
    return out


def load_adjusted(symbol):
    payload = json.loads(gzip.open(CACHE / f"{symbol}.json.gz", "rb").read().decode())
    block = payload["chart"]["result"][0]
    stamps = block["timestamp"]
    adj = block["indicators"]["adjclose"][0]["adjclose"]
    prices = {}
    for stamp, price in zip(stamps, adj):
        if stamp is None or not price or price <= 0:
            continue
        day = dt.datetime.fromtimestamp(stamp, dt.timezone.utc).astimezone(NY).date().isoformat()
        prices[day] = float(price)
    return month_ends(prices)


def twelve_month(closes, month):
    earlier = shift(month, -12)
    if month not in closes or earlier not in closes or closes[earlier] <= 0:
        return None
    return closes[month] / closes[earlier] - 1.0


def signals(spy, bil, veu, defensive_name, defensive_px):
    """Signal month -> asset. The asset is held the following calendar month."""
    chosen = {}
    for month in sorted(set(spy) & set(bil) & set(veu)):
        spy_r = twelve_month(spy, month)
        bil_r = twelve_month(bil, month)
        veu_r = twelve_month(veu, month)
        if spy_r is None or bil_r is None or veu_r is None:
            continue
        if defensive_name != "BIL" and month not in defensive_px:
            continue
        if spy_r > bil_r:
            chosen[month] = "VEU" if veu_r > spy_r else "SPY"
        else:
            chosen[month] = defensive_name
    return chosen


def holdings_for(signal, start, end):
    """Holding month -> asset. The signal is the previous month-end."""
    held = {}
    for month in months_between(start, end):
        decision = shift(month, -1)
        if decision in signal:
            held[month] = signal[decision]
    return held


def no_cost_cagr(held, prices):
    growth = 1.0
    count = 0
    for month in sorted(held):
        series = prices[held[month]]
        previous = shift(month, -1)
        if month not in series or previous not in series or series[previous] <= 0:
            continue
        growth *= series[month] / series[previous]
        count += 1
    if count == 0:
        return None, 0
    return growth ** (12.0 / count) - 1.0, count


def book(defensive="AGG"):
    prices = {
        "SPY": load_adjusted("SPY"),
        "VEU": load_adjusted("VEU"),
        "BIL": load_adjusted("BIL"),
        "AGG": load_adjusted("AGG"),
    }
    signal = signals(
        prices["SPY"], prices["BIL"], prices["VEU"], defensive, prices[defensive],
    )
    held = holdings_for(signal, HOLD_START, HOLD_END)
    cagr, count = no_cost_cagr(held, prices)
    return {
        "defensive": defensive,
        "signal": signal,
        "held": held,
        "cagr": cagr,
        "months": count,
        "sep_2026": signal.get("2026-09"),
        "prices": prices,
    }


if __name__ == "__main__":
    for name in ("AGG", "BIL"):
        result = book(name)
        print(
            f"defensive={name} months={result['months']} "
            f"cagr={result['cagr']:.4%} sep_2026={result['sep_2026']}"
        )
