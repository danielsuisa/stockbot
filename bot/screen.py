"""The owner's short-squeeze screen (2026-10-08, Finviz-style, NOT backtested): float under 50M shares, short float
over 20%, short ratio over 5, 3-month average volume over 500K, price over $2, relative volume over 2, listed
options; sorted by short float, highest first. Shortable is not checked on its own: a stock with 20%+ of its float
sold short is being shorted (and IBKR's borrow file never arrives on GitHub's runners).

Data, all free: short shares from FINRA (twice a month); price, change, today's volume and the 3-month average volume
from Yahoo's batch quote (crumb-protected, BATCH stocks a call); float from Yahoo's key statistics (one call a stock,
only for stocks that pass every other number); listed options from CBOE. Like Finviz: relative volume = today's
volume so far / the 3-month average, short ratio = short shares / the 3-month average."""
import concurrent.futures as cf
import datetime as dt
import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from bot import common, options

FLOAT_MAX, SHORT_FLOAT_MIN, SHORT_RATIO_MIN = 50_000_000, 0.20, 5.0
AVG_VOL_MIN, PRICE_MIN, REL_VOL_MIN = 500_000, 2.0, 2.0
MIN_SHORT = SHORT_RATIO_MIN * AVG_VOL_MIN  # 2.5M shares: a short ratio over 5 on over 500K a day needs more than this
# (metric, test, bound) in the owner's order; every bound is strict ("under", "over")
CRITERIA = (("float", "<", FLOAT_MAX), ("short_float", ">", SHORT_FLOAT_MIN), ("short_ratio", ">", SHORT_RATIO_MIN),
            ("avg_vol", ">", AVG_VOL_MIN), ("price", ">", PRICE_MIN), ("rel_vol", ">", REL_VOL_MIN),
            ("options", "is", True))
QUOTE_KEYS = ("short_ratio", "avg_vol", "price", "rel_vol")  # decided by FINRA + the batch quote alone
FLOAT_KEYS = ("float", "short_float")
BATCH, THREADS = 50, 4
NY = ZoneInfo("America/New_York")
HEADERS = {"User-Agent": "Mozilla/5.0"}
COOKIE, CRUMB = "https://fc.yahoo.com/", "https://query1.finance.yahoo.com/v1/test/getcrumb"
QUOTE = "https://query1.finance.yahoo.com/v7/finance/quote?symbols={s}"
STATS = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{t}?modules=defaultKeyStatistics"


def metrics(short, q, flt=None, opt=None):
    """FINRA short shares, a quote (quotes()), float shares and "has options" -> the screen's numbers; a number that
    cannot be computed is None."""
    q = q or {}
    avg, vol = q.get("avg"), q.get("vol")
    return {"price": q.get("price"), "chg": q.get("chg"), "avg_vol": avg,
            "rel_vol": vol / avg if vol is not None and avg else None,
            "short_ratio": short / avg if short and avg else None,
            "float": flt, "short_float": short / flt if short and flt else None, "options": opt}


def fails(m, keys=None):
    """The criteria `m` does not meet, in CRITERIA order (a missing number does not meet one); keys: check only these."""
    out = []
    for key, test, bound in CRITERIA:
        if keys and key not in keys:
            continue
        v = m.get(key)
        ok = v is True if test == "is" else v is not None and (v < bound if test == "<" else v > bound)
        if not ok:
            out.append(key)
    return out


# ---------- Yahoo ----------
def connect():
    """A getter for Yahoo's crumb-protected JSON: url -> parsed JSON. Yahoo hands out the crumb only with the cookie
    fc.yahoo.com sets (on a 404). RuntimeError when no crumb comes back."""
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def raw(url):
        with op.open(urllib.request.Request(url, headers=HEADERS), timeout=20) as r:
            return r.read()
    try:
        raw(COOKIE)
    except urllib.error.HTTPError:
        pass  # the 404 still sets the cookie
    crumb = raw(CRUMB).decode().strip()
    if not crumb or "<" in crumb or " " in crumb:
        raise RuntimeError("Yahoo: no crumb")
    return lambda url: json.loads(raw(f"{url}&crumb={urllib.parse.quote(crumb)}"))


def quotes(get, symbols):
    """{symbol: {"price", "chg" (fraction), "vol" (today so far), "avg" (3 months), "date" (New York date of the last
    trade)}} from Yahoo's batch quote, BATCH a call; None = Yahoo does not know it, False = its batch failed."""
    out = {}
    for i in range(0, len(symbols), BATCH):
        part = symbols[i:i + BATCH]
        try:
            got = {x["symbol"]: x for x in get(QUOTE.format(s=",".join(part)))["quoteResponse"]["result"]}
        except Exception as e:  # an unofficial source: the batch counts as unread
            print(f"yahoo quote {part[0]}..{part[-1]}: {type(e).__name__} {e}")
            out.update(dict.fromkeys(part, False))
            continue
        for s in part:
            x = got.get(s)
            chg, at = (x or {}).get("regularMarketChangePercent"), (x or {}).get("regularMarketTime")
            out[s] = None if x is None else {
                "price": x.get("regularMarketPrice"), "chg": None if chg is None else chg / 100,
                "vol": x.get("regularMarketVolume"), "avg": x.get("averageDailyVolume3Month"),
                "date": dt.datetime.fromtimestamp(at, NY).date().isoformat() if at else None}
    return out


def float_shares(get, t):
    """Yahoo's float of ticker t -> shares, None when Yahoo has none, False when it could not be read."""
    try:
        stats = get(STATS.format(t=t))["quoteSummary"]["result"][0]["defaultKeyStatistics"]
    except Exception as e:
        print(f"yahoo float {t}: {type(e).__name__} {e}")
        return False
    return (stats.get("floatShares") or {}).get("raw") or None


def optionable(t):
    """Does ticker t have listed options (CBOE)? True / False (CBOE answers 403 for a stock without a chain) / None
    (CBOE could not be read)."""
    try:
        body = common.fetch(options.URL.format(t=t.replace("-", ".")), tries=2, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return False
        print(f"cboe {t}: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"cboe {t}: {type(e).__name__} {e}")
        return None
    if body is None:
        return False
    try:
        return bool(json.loads(body)["data"].get("options"))
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


# ---------- the screen ----------
def screen(short, get):
    """short: {ticker: FINRA short shares} -> {"rows" (every stock meeting all criteria, its metrics + "t", short
    float highest first), "screened" (stocks quoted), "failed" (quotes Yahoo could not read), "no_float" (Yahoo has
    no float), "no_options" (CBOE could not be read), "date" (the session of the quotes)}. RuntimeError when Yahoo
    quotes none of them."""
    cands = sorted(t for t, n in short.items() if n and n > MIN_SHORT)
    qs = quotes(get, cands)
    failed = sum(q is False for q in qs.values())
    if cands and failed == len(cands):
        raise RuntimeError("Yahoo: no quotes")
    stage = [t for t in cands if qs.get(t) and not fails(metrics(short[t], qs[t]), QUOTE_KEYS)]
    with cf.ThreadPoolExecutor(THREADS) as ex:
        floats = dict(zip(stage, ex.map(lambda t: float_shares(get, t), stage)))
    rows, no_float, no_options = [], 0, 0
    for t in stage:
        if not floats[t]:
            no_float += 1
            continue
        if fails(metrics(short[t], qs[t], floats[t]), FLOAT_KEYS):
            continue
        m = metrics(short[t], qs[t], floats[t], optionable(t))
        no_options += m["options"] is None
        if not fails(m):
            rows.append({"t": t, **m})
    rows.sort(key=lambda r: -r["short_float"])
    dates = [q["date"] for q in qs.values() if q and q.get("date")]
    return {"rows": rows, "screened": len(cands), "failed": failed, "no_float": no_float, "no_options": no_options,
            "date": max(dates) if dates else None}
