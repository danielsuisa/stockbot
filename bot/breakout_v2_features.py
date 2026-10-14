"""Daily-bar features for the v2 A/B fills. No orders, no P&L, no minute bars.

The trade file stores the fill and the result. It does not store the gap, the distance to the 20-day high,
the prior path, or the index regime. This module builds those from SIP daily bars only. Intraday failed probes
are not here: the minute tape is not downloaded again.
"""
import csv
import sys
from pathlib import Path

from bot import alpaca, ticket

TRADES = Path("docs/backtest/breakout-rule-v2-2026-10-14.csv")
OUT = Path("docs/backtest/breakout-v2-feature-slice-2026-10-14.csv")
WARM, END = "2023-06-01", "2026-10-13"
FIELDS = ("day", "t", "gap", "dist_high", "high20", "prev_close", "atr", "atr_pct", "atr_rank",
          "ret1", "ret2", "ret3", "ret5", "tight", "nr7", "probes",
          "spy_up20", "spy_up50", "qqq_up20", "qqq_up50", "spy_ret20", "qqq_ret20")


def _sma(prior, n):
    if len(prior) < n:
        return None
    return sum(b[4] for b in prior[-n:]) / n


def _ret(prior, k):
    if len(prior) <= k or not prior[-1 - k][4]:
        return None
    return prior[-1][4] / prior[-1 - k][4] - 1


def failed_probes(prior, n=5):
    """How many of the last n sessions traded at or through the prior 20-day high and closed back under it."""
    if len(prior) < 20 + 1:
        return None
    count = 0
    for i in range(len(prior) - n, len(prior)):
        window = prior[i - 20:i]
        if len(window) < 20:
            continue
        level = max(b[2] for b in window)
        if prior[i][2] > level + 1e-9 and prior[i][4] < level - 1e-9:
            count += 1
    return count


def row_features(bars, day):
    """Features known from daily bars strictly before `day`, plus that day's open for the gap. None if the
    history is short or the session bar is missing."""
    prior = [b for b in bars if b[0] < day]
    today = next((b for b in bars if b[0] == day), None)
    if today is None or len(prior) < 20 or not prior[-1][4] or not today[1]:
        return None
    prev_close = prior[-1][4]
    high20 = max(b[2] for b in prior[-20:])
    atr = ticket.atr14(bars, day)
    if not atr or high20 <= 0:
        return None
    last10 = prior[-10:]
    ranges = [b[2] - b[3] for b in prior[-7:]]
    atr_pct = atr / prev_close
    hist = []
    for b in prior:
        a = ticket.atr14(bars, b[0])
        if a and b[4]:
            hist.append(a / b[4])
    rank = None if len(hist) < 20 else sum(x <= atr_pct for x in hist[-60:]) / min(len(hist), 60)
    return {
        "gap": today[1] / prev_close - 1,
        "dist_high": (high20 - prev_close) / high20,
        "high20": high20,
        "prev_close": prev_close,
        "atr": atr,
        "atr_pct": atr_pct,
        "atr_rank": rank,
        "ret1": _ret(prior, 1),
        "ret2": _ret(prior, 2),
        "ret3": _ret(prior, 3),
        "ret5": _ret(prior, 5),
        "tight": (max(b[2] for b in last10) - min(b[3] for b in last10)) / atr,
        "nr7": int(ranges[-1] <= min(ranges) + 1e-9),
        "probes": failed_probes(prior),
    }


def index_flags(bars, day):
    """1 when the prior close is above the 20- or 50-day average, and the 20-day return. All use bars before `day`."""
    prior = [b for b in bars if b[0] < day]
    close = prior[-1][4] if prior else None
    s20, s50 = _sma(prior, 20), _sma(prior, 50)
    return {
        "up20": int(close > s20) if close and s20 else None,
        "up50": int(close > s50) if close and s50 else None,
        "ret20": _ret(prior, 20),
    }


def build(trades, daily, spy, qqq):
    """trades: iterable of {day, t}. daily: {symbol: bars}. Rows with no history are still emitted, fields blank."""
    days = sorted({t["day"] for t in trades})
    spy_i = {d: index_flags(spy, d) for d in days}
    qqq_i = {d: index_flags(qqq, d) for d in days}
    out = []
    for t in trades:
        feat = row_features(daily.get(t["t"]) or [], t["day"]) or {}
        s, q = spy_i[t["day"]], qqq_i[t["day"]]
        out.append({"day": t["day"], "t": t["t"], **feat,
                    "spy_up20": s["up20"], "spy_up50": s["up50"], "spy_ret20": s["ret20"],
                    "qqq_up20": q["up20"], "qqq_up50": q["up50"], "qqq_ret20": q["ret20"]})
    return out


def _load_trades(path):
    with path.open(encoding="utf-8") as fh:
        return [{"day": r["day"], "t": r["t"]} for r in csv.DictReader(fh)]


def main():
    try:
        alpaca._headers()
    except RuntimeError as e:
        raise SystemExit(str(e))
    trades = _load_trades(TRADES)
    symbols = sorted({t["t"] for t in trades} | {"SPY", "QQQ"})
    print(f"daily bars for {len(symbols)} symbols, {WARM} → {END}", flush=True)
    daily = alpaca.daily(symbols, WARM, END)
    rows = build(trades, daily, daily.get("SPY") or [], daily.get("QQQ") or [])
    missing = sum(1 for r in rows if r.get("gap") is None)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"wrote {OUT} rows={len(rows)} missing={missing}", flush=True)
    return 0 if missing < len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
