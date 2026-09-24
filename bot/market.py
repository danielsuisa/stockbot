"""Yahoo Finance chart data (free, unofficial): last price, splits, daily history, liquidity, market regime.
Every value says where it came from - live Yahoo, or the last cached price with its date - never a default."""
import datetime as dt
import json
import math
import statistics
import time
from pathlib import Path

from bot import common

CACHE = {}  # ticker -> {"price", "asof"}: last known prices; the scan persists it in data/state.json["prices"]
_loaded = [False]
LOW_LIQUIDITY = 2_000_000  # $/day
TAGS = {"panic": "פאניקה", "normal": "רגיל", "euphoria": "אופוריה", "unknown": "לא ידוע"}


def _load_cache():
    """Fill CACHE once from the committed state file (read-only here; only the scan writes it)."""
    if not _loaded[0]:
        _loaded[0] = True
        try:
            path = Path(common.env("STATE_FILE", str(common.DATA / "state.json")))
            for t, v in json.loads(path.read_text("utf-8")).get("prices", {}).items():
                CACHE.setdefault(t, v)
        except (OSError, ValueError, AttributeError):
            pass


def chart(ticker, **params):
    """Yahoo v8 chart result for `ticker` (query1, then query2), or None."""
    q = "&".join(f"{k}={v}" for k, v in params.items())
    for host in ("query1", "query2"):
        try:
            body = common.fetch(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}?{q}", tries=2, timeout=20)
            r = json.loads(body)["chart"]["result"][0]
            common.stamp("price")
            return r
        except Exception as e:  # unofficial endpoint: any failure just means "no live data"
            print(f"yahoo {host} {ticker}: {type(e).__name__} {e}")
    return None


def remember(ticker, price):
    CACHE[ticker] = {"price": price, "asof": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")}


def quote(ticker, since=None):
    """{price, split, asof, source}: live price and the split factor after date `since` (shares on `since` x split =
    shares today); if Yahoo fails, the last cached price (split unknown -> 1, source "cache"); else price None."""
    now = int(time.time())
    p1 = int(dt.datetime.combine(dt.date.fromisoformat(since) + dt.timedelta(1), dt.time(), dt.timezone.utc).timestamp()) \
        if since else now - 5 * 86400
    r = chart(ticker, period1=min(p1, now - 5 * 86400), period2=now, interval="1d", events="split")
    m = (r or {}).get("meta", {})
    if m.get("currency") in (None, "USD") and (m.get("regularMarketPrice") or 0) > 0:
        f = 1.0
        for s in (r.get("events") or {}).get("splits", {}).values():
            f *= s["numerator"] / s["denominator"] if s["date"] >= p1 else 1
        remember(ticker, float(m["regularMarketPrice"]))
        return {"price": float(m["regularMarketPrice"]), "split": f, "asof": CACHE[ticker]["asof"], "source": "live"}
    _load_cache()
    c = CACHE.get(ticker)
    if c:
        common.stamp("price", f"{c['asof']} (⚠ מטמון)")
        return {"price": c["price"], "split": 1.0, "asof": c["asof"], "source": "cache"}
    return {"price": None, "split": 1.0, "asof": None, "source": None}


def history(ticker, rng="6mo"):
    """[(date, close, volume)] daily bars, oldest first ([] if Yahoo fails)."""
    r = chart(ticker, range=rng, interval="1d")
    if not r:
        return []
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    bars = zip(r.get("timestamp") or [], q.get("close") or [], q.get("volume") or [])
    return [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date(), c, v or 0) for t, c, v in bars if c]


def _ret(bars, n):
    return bars[-1][1] / bars[-1 - n][1] - 1 if len(bars) > n else None


def regime():
    """Broad-market tag from SPY / IWM 20- and 60-day returns and VIX (SPY 20-day realized vol if VIX is missing):
    panic = VIX >= 28 or SPY 20d <= -7% or IWM 20d <= -10%; euphoria = VIX <= 15 and (SPY 60d >= +8% or IWM 60d >= +12%)."""
    spy, iwm, vix = history("SPY"), history("IWM"), history("%5EVIX", "1mo")
    out = {"spy20": _ret(spy, 20), "spy60": _ret(spy, 60), "iwm20": _ret(iwm, 20), "iwm60": _ret(iwm, 60),
           "vix": vix[-1][1] if vix else None, "vol_source": "VIX" if vix else None,
           "asof": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")}
    if out["vix"] is None and len(spy) > 21:
        rets = [math.log(b[1] / a[1]) for a, b in zip(spy[-21:], spy[-20:])]
        out["vix"], out["vol_source"] = statistics.stdev(rets) * math.sqrt(252) * 100, "SPY realized"
    if out["spy20"] is None or out["vix"] is None:
        return {**out, "tag": "unknown"}
    panic = out["vix"] >= 28 or out["spy20"] <= -0.07 or (out["iwm20"] is not None and out["iwm20"] <= -0.10)
    euphoria = out["vix"] <= 15 and ((out["spy60"] or 0) >= 0.08 or (out["iwm60"] or 0) >= 0.12)
    return {**out, "tag": "panic" if panic else "euphoria" if euphoria else "normal"}


def regime_line(r):
    """One Hebrew line for an alert: the tag, its inputs, and what it means for cluster quality."""
    from bot.common import code
    pct = lambda x: code(f"{x * 100:+.1f}%") if x is not None else "חסר"
    tag = r.get("tag") if r.get("tag") in TAGS else "unknown"
    head = f"📈 מצב שוק: <b>{TAGS[tag]}</b>"
    if tag == "unknown" or r.get("vix") is None or r.get("spy20") is None:
        return head + (" (נתוני Yahoo לא זמינים)" if tag == "unknown" else "")
    level = f"{r['vix']:.1f}"
    vol = f"{'VIX' if r.get('vol_source') == 'VIX' else 'תנודתיות SPY'} {code(level)}"
    why = {"panic": " — אשכול בזמן ירידות רוחב איכותי יותר", "euphoria": " — בזמן אופוריה האיכות נמוכה יותר",
           "normal": ""}[tag]
    return f"{head} (SPY 20 יום {pct(r['spy20'])} · IWM 20 יום {pct(r.get('iwm20'))} · {vol}){why}"


def liquidity(ticker):
    """Average daily dollar volume over the last 30 calendar days, or None."""
    bars = history(ticker, "3mo")
    if not bars:
        return None
    since = bars[-1][0] - dt.timedelta(30)
    dv = [c * v for d, c, v in bars if d > since and v]
    return sum(dv) / len(dv) if dv else None


def close_on(ticker, day):
    """(date, close) of the first session on/after `day` (a date), or None - for journal follow-ups."""
    p1 = int(dt.datetime.combine(day, dt.time(), dt.timezone.utc).timestamp())
    r = chart(ticker, period1=p1, period2=p1 + 10 * 86400, interval="1d")
    for t, c in zip((r or {}).get("timestamp") or [], ((r or {}).get("indicators", {}).get("quote") or [{}])[0].get("close") or []):
        if c:
            return dt.datetime.fromtimestamp(t, dt.timezone.utc).date(), c
    return None
