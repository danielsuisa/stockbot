"""Live short-squeeze list - phases 2-3 of docs/superpowers/specs/2026-09-27-squeeze-screener-design.md.

Once a trading day before the US open (.github/workflows/squeeze.yml): rank the market with the variant the
backtest froze, send the top 10 to Telegram with the backtest's spike AND crash rates, journal the names, and fill
in the 10-session outcome of earlier lists. Borrow (IBKR) and options (CBOE) numbers are shown as unvalidated.

    python -m bot.squeeze_live          # run, send, write data/squeeze_last.json and data/squeeze_journal.json
    python -m bot.squeeze_live --dry    # print the message only, write nothing"""
import argparse
import bisect
import concurrent.futures as cf
import datetime as dt
import json
import os
import statistics
import traceback
from pathlib import Path

from bot import borrow, common, market, options, shorts, squeeze
from bot.common import code

VARIANT = {"w": 0.7, "g_si": 0.20, "g_rv": 2.0}  # frozen by the backtest: docs/backtest/squeeze-2026-09-27.md
NAME = "w0.7/si0.2/rv2"
BACKTEST = {"hit": 0.068, "crash": 0.071, "base": 0.010, "years": "2024–2026"}  # out-of-sample, run 3
CRASH = 1 / squeeze.HIT - 1  # -33%: the fall that mirrors the +50% spike
LAST, JOURNAL = "squeeze_last.json", "squeeze_journal.json"
FIELDS = ("score", "si_pct", "dtc", "chg", "rvol", "ret5", "brk", "price", "fuel", "trigger", "shares")


# ---------- files ----------
def path(name):
    return Path(common.env("SQUEEZE_DIR", str(common.DATA))) / name


def load(name, default):
    p = path(name)
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def save(name, obj):
    p = path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)


def _round(x):
    return round(x, 4) if isinstance(x, float) else x


# ---------- data ----------
def calendar(today):
    """Completed US sessions before `today` (ISO, ascending), from SPY's daily bars ([] when Yahoo fails)."""
    b = squeeze.to_bars(market.chart("SPY", range="6mo", interval="1d"))
    return [d for d in (b or {}).get("d", []) if d < today]


def latest_report(cal, today):
    """(settlement date, {FINRA symbol: row}) of the newest FINRA report usable on `today` (its 8th session after
    settlement, `today` counted as a session), or (None, {})."""
    for settle in reversed(shorts.settlement_dates()[-3:]):
        use = shorts.usable_from(settle, cal + [today])
        if use and use <= today:
            return settle, shorts.report(settle)
    return None, {}


def frames_now(today):
    """SEC shares frames of the current calendar quarter and the three before it (today: a date)."""
    y, q, out = today.year, (today.month - 1) // 3 + 1, []
    for _ in range(4):
        out.append(shorts.frame(y, q))
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    return out


def bars_for(tickers, threads=8):
    """{ticker: bars} with 3 months of Yahoo daily bars, for each ticker Yahoo answers for."""
    def one(t):
        return squeeze.to_bars(market.chart(t, range="3mo", interval="1d"))
    with cf.ThreadPoolExecutor(threads) as ex:
        got = dict(zip(tickers, ex.map(one, tickers)))
    return {t: b for t, b in got.items() if b}


def features_at(b, today, prev):
    """Features of a ticker for session `today` from its bars through `prev` (= D-1); None without a bar on D-1."""
    if not b:
        return None
    i = bisect.bisect_left(b["d"], today) - 1
    return squeeze.features(b, i) if i >= 0 and b["d"][i] == prev else None


def build(today):
    """Today's list (today: ISO) -> {"date", "prev", "si_date", "rows" (the top list), "gated" (short interest past
    the gate), "candidates" (also past price / liquidity), "missing" (no Yahoo bars), "recent" (the last WINDOW
    sessions incl. today)}. RuntimeError when Yahoo's calendar or a usable FINRA report is missing."""
    cal = calendar(today)
    if not cal:
        raise RuntimeError("Yahoo: no SPY sessions")
    settle, raw = latest_report(cal, today)
    if not raw:
        raise RuntimeError("FINRA: no usable short-interest report")
    primary = {t: cik for cik, t in common.cik_tickers().items()}
    shares = shorts.shares_index(frames_now(dt.date.fromisoformat(today)))
    gated = []
    for t, si in shorts.remap(raw, primary).items():
        n = shorts.shares_at(shares, primary[t], today) if t in primary else None
        if n and VARIANT["g_si"] <= si["si"] / n <= squeeze.MAX_SI_PCT:
            gated.append((t, si, n))
    bars = bars_for([t for t, _, _ in gated])
    rows = [r for t, si, n in gated if (r := squeeze.make_row(t, si, n, features_at(bars.get(t), today, cal[-1])))]
    return {"date": today, "prev": cal[-1], "si_date": settle, "rows": squeeze.rank(rows, VARIANT),
            "gated": len(gated), "candidates": len(rows), "missing": len(gated) - len(bars),
            "recent": cal[-(squeeze.WINDOW - 1):] + [today]}


def enrich(rows, today):
    """Unvalidated extras for the listed names: IBKR borrow fee / availability and CBOE gamma fuel (None = missing).
    today: a date."""
    fees = borrow.fetch()
    for r in rows:
        r["borrow"] = fees.get(r["t"])
        chain = options.chain(r["t"])
        r["gamma"] = options.gamma(chain, r["shares"], today) if chain else None
    return rows
