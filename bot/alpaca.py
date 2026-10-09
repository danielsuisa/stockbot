"""Alpaca market data on the free Basic plan: historical SIP bars and quotes (older than 15 minutes) and the trading
calendar. Keys come from env ALPACA_KEY_ID / ALPACA_SECRET_KEY and travel only in headers. Everything is kept in a
gz-JSON disk cache under .cache/alpaca/ (1-minute bars: one file per symbol and day); an empty answer is cached as
[] ("no trades" is an answer), a failed download caches nothing, so an interrupted run resumes where it stopped.
Prices are raw (not split-adjusted); minutes are New York time."""
import datetime as dt
import gzip
import json
import threading
import time
import urllib.parse
from zoneinfo import ZoneInfo

from bot import common

DATA = "https://data.alpaca.markets/v2/stocks"
CALENDAR = "https://paper-api.alpaca.markets/v2/calendar"
ASSETS = "https://paper-api.alpaca.markets/v2/assets"
OPEN_CHUNK = 800  # symbols per opening-bar request (keeps the URL near 6 KB)
CACHE = common.ROOT / ".cache" / "alpaca"
NY = ZoneInfo("America/New_York")
LIMIT, CHUNK = 10_000, 100  # bars per page (Alpaca's maximum); symbols per multi-symbol request
QUOTE_WINDOW = dt.timedelta(seconds=60)
GAP = 60 / 190  # seconds between requests: under Alpaca's 200 a minute
REQUESTS = [0]  # requests sent this run (the report's metadata)
CUTOFF = {}  # {New York day: "HH:MM"} for a live look at a session still trading: its data ends there (the free plan
# refuses SIP data newer than 15 minutes) and is never cached (a later call must see the whole day)
_lock, _next = threading.Lock(), [0.0]


def _headers():
    kid, secret = common.env("ALPACA_KEY_ID"), common.env("ALPACA_SECRET_KEY")
    if not kid or not secret:
        raise RuntimeError("ALPACA_KEY_ID / ALPACA_SECRET_KEY are not set")
    return {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": secret}


def _get(url, query):
    with _lock:
        time.sleep(max(0.0, _next[0] - time.monotonic()))
        _next[0] = time.monotonic() + GAP
        REQUESTS[0] += 1
    body = common.fetch(f"{url}?{urllib.parse.urlencode(query)}", headers=_headers(), tries=5, timeout=60)
    if body is None:
        raise RuntimeError(f"alpaca: 404 for {url}")
    return json.loads(body)


def _read(path):
    return json.loads(gzip.decompress(path.read_bytes())) if path.exists() else None


def _write(path, val):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(val, separators=(",", ":")).encode()))
    tmp.replace(path)  # never a half-written cache file


def _ny(t):
    return dt.datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(NY)


def utc(day, hhmm):
    """New York `day` ("YYYY-MM-DD") at `hhmm` ("HH:MM" or "HH:MM:SS") -> UTC ISO "YYYY-MM-DDTHH:MM:SSZ"."""
    t = dt.datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=NY)
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bars(symbols, start, end, timeframe):
    """Every page of Alpaca's multi-symbol bars -> {symbol: [raw bar dicts]} (every requested symbol present)."""
    out, token = {s: [] for s in symbols}, None
    while True:
        q = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "end": end, "feed": "sip",
             "adjustment": "raw", "limit": LIMIT, "sort": "asc", **({"page_token": token} if token else {})}
        js = _get(f"{DATA}/bars", q)
        for s, items in (js.get("bars") or {}).items():
            out.setdefault(s, []).extend(items or [])
        token = js.get("next_page_token")
        if not token:
            return out


def _row(b, fmt):
    return [_ny(b["t"]).strftime(fmt), b["o"], b["h"], b["l"], b["c"], b["v"], b.get("vw")]


def minute_bars(symbols, day):
    """1-minute SIP bars of New York `day`, 04:00-20:00 -> {symbol: [(minute "HH:MM", o, h, l, c, v, vw)]}
    ascending; [] for a symbol without trades."""
    path = lambda s: CACHE / "1m" / day / f"{s}.json.gz"
    live = CUTOFF.get(day)
    out = {s: None if live else _read(path(s)) for s in dict.fromkeys(symbols)}
    todo = [s for s, v in out.items() if v is None]
    for i in range(0, len(todo), CHUNK):
        part = todo[i:i + CHUNK]
        got = _bars(part, utc(day, "04:00"), utc(day, live or "20:00"), "1Min")
        for s in part:
            out[s] = [_row(b, "%H:%M") for b in got.get(s, [])]
            if not live:
                _write(path(s), out[s])
    return {s: [tuple(b) for b in v] for s, v in out.items()}


def bars(symbol, start, end, timeframe):
    """Bars of any timeframe ("5Min", "1Day", ...) over [start, end] (ISO dates or UTC times) ->
    [("YYYY-MM-DD HH:MM" New York, o, h, l, c, v, vw)] ascending."""
    path = CACHE / "bars" / timeframe / f"{symbol}_{start}_{end}.json.gz".replace(":", "")
    rows = _read(path)
    if rows is None:
        rows = [_row(b, "%Y-%m-%d %H:%M") for b in _bars([symbol], start, end, timeframe).get(symbol, [])]
        _write(path, rows)
    return [tuple(b) for b in rows]


def daily(symbols, start, end):
    """Raw daily bars over [start, end] -> {symbol: [(date ISO New York, o, h, l, c, v)]} ascending."""
    path = lambda s: CACHE / "1d" / f"{start}_{end}" / f"{s}.json.gz"
    live = end in CUTOFF  # a live day's own bar is still forming: end before it, never cache
    out = {s: None if live else _read(path(s)) for s in dict.fromkeys(symbols)}
    todo = [s for s, v in out.items() if v is None]
    for i in range(0, len(todo), CHUNK):
        part = todo[i:i + CHUNK]
        got = _bars(part, start, utc(end, "00:00") if live else end, "1Day")
        for s in part:
            out[s] = [_row(b, "%Y-%m-%d")[:6] for b in got.get(s, [])]
            if not live:
                _write(path(s), out[s])
    return {s: [tuple(b) for b in v] for s, v in out.items()}


def quote_at(symbol, at):
    """The last SIP quote in the 60 seconds up to UTC ISO `at` -> (bid, ask); None when there is none or it is
    unusable (a side at zero, or crossed)."""
    path = CACHE / "q" / at[:10] / f"{symbol}_{at[11:19].replace(':', '')}.json.gz"
    q = _read(path)
    if q is None:
        t = dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
        start = (t - QUOTE_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
        js = _get(f"{DATA}/{symbol}/quotes", {"start": start, "end": at, "feed": "sip", "sort": "desc", "limit": 1})
        got = js.get("quotes") or []
        q = [got[0].get("bp") or 0, got[0].get("ap") or 0] if got else []
        _write(path, q)
    if not q or q[0] <= 0 or q[1] <= 0 or q[1] < q[0]:
        return None
    return q[0], q[1]


def calendar(start, end):
    """Trading sessions over [start, end] -> {date: (open "HH:MM", close "HH:MM")} New York (early closes included)."""
    path = CACHE / "calendar" / f"{start}_{end}.json.gz"
    rows = _read(path)
    if rows is None:
        rows = _get(CALENDAR, {"start": start, "end": end})
        _write(path, rows)
    return {r["date"]: (r["open"], r["close"]) for r in rows}


def assets():
    """Every US-equity asset Alpaca knows, active and inactive (delisted names included): [{"symbol", "exchange",
    "name", "status"}], cached."""
    path = CACHE / "assets.json.gz"
    rows = _read(path)
    if rows is None:
        rows = []
        for status in ("active", "inactive"):
            rows += [{k: a.get(k) for k in ("symbol", "exchange", "name", "status")}
                     for a in _get(ASSETS, {"status": status, "asset_class": "us_equity"})]
        _write(path, rows)
    return rows


def volume_since(symbols, day, start, end):
    """Each symbol's volume and last price between New York `start` and `end` ("HH:MM") of `day`, from 15-minute
    bars -> {symbol: (volume, last close)}; symbols without trades are left out. Never cached (a live look)."""
    out = {}
    for i in range(0, len(symbols), CHUNK):
        for s, items in _bars(symbols[i:i + CHUNK], utc(day, start), utc(day, end), "15Min").items():
            if items:
                out[s] = (sum(b["v"] for b in items), items[-1]["c"])
    return out


def opening_bars(symbols, day):
    """The 09:30-09:35 five-minute SIP bar of New York `day` -> {symbol: (o, h, l, c, v) or None (no trades)}; one
    cache file per day, extended with the symbols a later call asks for."""
    path = CACHE / "open5" / f"{day}.json.gz"
    have = _read(path) or {}
    todo = [s for s in dict.fromkeys(symbols) if s not in have]
    if todo:
        at = utc(day, "09:30")
        for i in range(0, len(todo), OPEN_CHUNK):
            part = todo[i:i + OPEN_CHUNK]
            got = _bars(part, at, at, "5Min")
            for s in part:
                b = (got.get(s) or [None])[0]
                have[s] = None if b is None else [b["o"], b["h"], b["l"], b["c"], b["v"]]
        _write(path, have)
    return {s: None if have[s] is None else tuple(have[s]) for s in dict.fromkeys(symbols)}


NOT_LAST_SALE = set("I4BWZTUMQCGHNPRV79")  # odd lots and trade conditions that do not set the last sale


def trades(symbol, day, hhmm):
    """SIP trades of one New York minute that set the last sale (round lots, regular conditions) -> [(time UTC ISO,
    price)] in order; cached per symbol and minute."""
    path = CACHE / "trades" / day / f"{symbol}_{hhmm.replace(':', '')}.json.gz"
    rows = _read(path)
    if rows is None:
        start = utc(day, hhmm)
        end = (dt.datetime.fromisoformat(start.replace("Z", "+00:00")) + dt.timedelta(minutes=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        rows, token = [], None
        while True:
            js = _get(f"{DATA}/{symbol}/trades", {"start": start, "end": end, "feed": "sip", "limit": LIMIT,
                                                  **({"page_token": token} if token else {})})
            rows += [[x["t"], x["p"]] for x in js.get("trades") or [] if not set(x.get("c") or []) & NOT_LAST_SALE]
            token = js.get("next_page_token")
            if not token:
                break
        _write(path, rows)
    return [tuple(r) for r in rows]
