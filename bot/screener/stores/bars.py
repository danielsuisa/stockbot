"""Price and volume stores, all Alpaca SIP (free plan: queries end >= 15 minutes ago), split-adjusted (D-030).

  bars     per symbol, the last KEEP completed sessions: date ordinals + open/high/low/close/volume/trades. Daily bars
           carry regular-session OHLC and whole-day (04:00-20:00) volume (OBSERVED, D-006). Updated incrementally;
           an overlap bar that moved > 0.5% (a split re-adjusted history) triggers a full refetch of that symbol.
  long     per symbol, monthly bars since 2016-01 (beta, all-time high/low) and the closes on the 3Y/5Y/10Y anchor
           sessions (performance windows longer than the daily store).
  profile  per symbol, the mean cumulative volume at each hour mark 04:00-20:00 over PROFILE_DAYS sessions (the
           relative volume's time-of-day adjustment).
  today()  live 5-minute bars of the trading day for stage-2 symbols (never cached).

Series are compact typed arrays (base64 in the gz-JSON store; array('d') / array('i') in memory)."""
import array
import base64
import datetime as dt

from bot import alpaca, common
from bot.screener import stores

KEEP = 300  # sessions per symbol: SMA200, the 252-session high/low and RSI warm-up
CHUNK = 800  # symbols per request (URL ~6 KB, like bot/alpaca.OPEN_CHUNK)
SPLIT_TOL = 0.005
HISTORY_START = "2016-01-01"  # Alpaca's SIP history (OBSERVED: monthly bars from 2016-01)
ANCHORS = {"perf_3y": 756, "perf_5y": 1260, "perf_10y": 2520}
ANCHOR_SPAN = 25  # sessions after each anchor fetched at once (reused while the anchor moves)
PROFILE_DAYS = 63
MARKS = [f"{h:02}:00" for h in range(4, 21)]  # hour marks 04:00 .. 20:00 (cumulative volume at each)
FIELDS = ("o", "h", "l", "c", "v", "n")


class Series:
    """One symbol's daily bars, ascending (d = date ordinals)."""
    __slots__ = ("d", "o", "h", "l", "c", "v", "n")

    def __init__(self, d=None, **cols):
        self.d = d if d is not None else array.array("i")
        for k in FIELDS:
            setattr(self, k, cols.get(k) if cols.get(k) is not None else array.array("d"))

    def __len__(self):
        return len(self.d)

    def dates(self):
        return [dt.date.fromordinal(x).isoformat() for x in self.d]

    def last_date(self):
        return dt.date.fromordinal(self.d[-1]).isoformat() if len(self.d) else None

    def tail(self, n):
        return Series(self.d[-n:], **{k: getattr(self, k)[-n:] for k in FIELDS})

    def extend(self, rows):
        """rows: [(date ISO, o, h, l, c, v, n)] ascending, all after the last bar."""
        for r in rows:
            self.d.append(dt.date.fromisoformat(r[0]).toordinal())
            for k, x in zip(FIELDS, r[1:]):
                getattr(self, k).append(float(x or 0))

    def pack(self):
        return {"d": _b64(self.d), **{k: _b64(getattr(self, k)) for k in FIELDS}}

    @classmethod
    def unpack(cls, p):
        return cls(_arr("i", p["d"]), **{k: _arr("d", p[k]) for k in FIELDS})


def _b64(a):
    return base64.b64encode(a.tobytes()).decode()


def _arr(code, s):
    a = array.array(code)
    a.frombytes(base64.b64decode(s))
    return a


def _get_bars(symbols, start, end, timeframe):
    """Every page of multi-symbol SIP bars -> {symbol: [raw bar dicts]} (split-adjusted)."""
    out, token = {}, None
    while True:
        q = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "end": end, "feed": "sip",
             "adjustment": "split", "limit": alpaca.LIMIT, "sort": "asc", **({"page_token": token} if token else {})}
        js = alpaca._get(f"{alpaca.DATA}/bars", q)
        stores.count("alpaca")
        for s, items in (js.get("bars") or {}).items():
            out.setdefault(s, []).extend(items or [])
        token = js.get("next_page_token")
        if not token:
            return out


def end_param(day, now_ny=None):
    """Query end for daily bars through New York `day`: the date itself for a past day; for today a time at least 16
    minutes old (a plain date means the end of the day, which the free plan refuses as recent SIP data: HTTP 403,
    OBSERVED 2026-10-21)."""
    now_ny = now_ny or dt.datetime.now(alpaca.NY)
    if day < now_ny.date().isoformat():
        return day
    return alpaca.utc(day, min("20:00", end_time(now_ny)))


def _day_rows(items):
    return [(alpaca._ny(b["t"]).date().isoformat(), b["o"], b["h"], b["l"], b["c"], b["v"], b.get("n") or 0)
            for b in items]


# ---------- daily store ----------
def load():
    """The bars store -> ({symbol: Series}, meta) ({} and None when it was never built)."""
    st = stores.load("bars")
    if not st:
        return {}, None
    return {s: Series.unpack(p) for s, p in st["data"].items()}, st["meta"]


def save(bars, meta_extra=None):
    return stores.save("bars", {s: b.pack() for s, b in bars.items()}, "alpaca-sip-1Day-split", **(meta_extra or {}))


def plan(bars, symbols, sessions_done):
    """Which fetches bring every symbol up to the last completed session -> {start date ISO: [symbols]}.
    New symbols (or < 20 bars) start KEEP sessions back; others 3 sessions before their last bar (the overlap
    that detects a split re-adjustment)."""
    if not sessions_done:
        return {}
    end, groups = sessions_done[-1], {}
    full = sessions_done[-KEEP] if len(sessions_done) >= KEEP else sessions_done[0]
    pos = {d: i for i, d in enumerate(sessions_done)}
    for s in symbols:
        b = bars.get(s)
        last = b.last_date() if b is not None else None
        if last is not None and last >= end:
            continue
        if b is None or len(b) < 20 or last not in pos:
            start = full
        else:
            start = sessions_done[max(0, pos[last] - 3)]
        groups.setdefault(start, []).append(s)
    return groups


def merge(old, rows, keep=KEEP):
    """Old Series + fetched rows (which may overlap its last bars) -> (new Series, split_detected)."""
    if old is None or not len(old):
        s = Series()
        s.extend(rows)
        return s.tail(keep), False
    have = {dt.date.fromordinal(d).isoformat(): c for d, c in zip(old.d[-10:], old.c[-10:])}
    last = old.last_date()
    for r in rows:
        if r[0] in have and have[r[0]] and abs(r[4] / have[r[0]] - 1) > SPLIT_TOL:
            return None, True
    new = old.tail(len(old))
    new.extend([r for r in rows if r[0] > last])
    return new.tail(keep), False


def update(bars, symbols, sessions_done, max_calls=None):
    """Bring `bars` (mutated) up to the last completed session for `symbols` -> stats dict. A split re-adjustment
    refetches that symbol's whole window in a second pass. Symbols without any bar are left out of the store."""
    stats = {"fetched": 0, "split_refetch": 0, "empty": 0, "calls": 0}
    if not sessions_done:
        return stats
    end, before = end_param(sessions_done[-1]), stores.COUNTS["alpaca"]
    for pass_ in range(2):
        groups = plan(bars, symbols, sessions_done)
        redo = []
        for start, syms in sorted(groups.items()):
            for i in range(0, len(syms), CHUNK):
                if max_calls is not None and stores.COUNTS["alpaca"] - before >= max_calls:
                    stats["calls"] = stores.COUNTS["alpaca"] - before
                    stats["budget_stop"] = True
                    return stats
                part = syms[i:i + CHUNK]
                got = _get_bars(part, start, end, "1Day")
                for s in part:
                    rows = _day_rows(got.get(s) or [])
                    if not rows:
                        stats["empty"] += 1
                        continue
                    new, split = merge(bars.get(s), rows)
                    if split:
                        bars.pop(s, None)
                        redo.append(s)
                        continue
                    bars[s] = new
                    stats["fetched"] += 1
        stats["split_refetch"] += len(redo)
        if not redo:
            break
        symbols = redo
    stats["calls"] = stores.COUNTS["alpaca"] - before
    return stats


# ---------- long store (monthly bars, anchors) ----------
def update_long(long, symbols, sessions_all, today):
    """Refresh the long store (mutated dict): monthly bars when the month changed (or for symbols missing them),
    the anchor-window closes when an anchor left its fetched window -> stats."""
    stats = {"monthly": 0, "anchors": 0}
    month = today[:7]
    need = [s for s in symbols if (long.get("m", {}).get(s) or {}).get("month") != month]
    end = (dt.date.fromisoformat(today).replace(day=1) - dt.timedelta(1)).isoformat()  # completed months only
    for i in range(0, len(need), CHUNK):
        part = need[i:i + CHUNK]
        got = _get_bars(part, HISTORY_START, end, "1Month")
        for s in part:
            items = got.get(s) or []
            mi = array.array("i", (int(b["t"][:4]) * 12 + int(b["t"][5:7]) - 1 for b in items))
            long.setdefault("m", {})[s] = {"month": month, "t": _b64(mi),
                                           **{k: _b64(array.array("d", (b[k] for b in items))) for k in ("h", "l", "c")}}
        stats["monthly"] += len(part)
    done = [d for d in sessions_all if d <= today]
    for key, n in ANCHORS.items():
        if len(done) <= n:
            continue
        anchor = done[-1 - n]
        win = long.setdefault("a", {}).get(key) or {}
        if win.get("from", "~") <= anchor <= win.get("to", ""):
            continue
        j = done.index(anchor)
        to = done[min(len(done) - 1, j + ANCHOR_SPAN)]
        closes = {}
        if anchor >= HISTORY_START:
            for i in range(0, len(symbols), CHUNK):
                got = _get_bars(symbols[i:i + CHUNK], anchor, end_param(to), "1Day")
                for s, items in got.items():
                    closes[s] = {b["t"][:10]: b["c"] for b in items}
        long.setdefault("a", {})[key] = {"from": anchor, "to": to, "closes": closes}
        stats["anchors"] += 1
    return stats


def monthly(long, symbol):
    """One symbol's monthly bars from the long store -> {"t": ["YYYY-MM", ...], "h", "l", "c": arrays} or None."""
    m = ((long or {}).get("m") or {}).get(symbol)
    if not m or not m.get("t"):
        return None
    t = _arr("i", m["t"])
    return {"t": [f"{x // 12}-{x % 12 + 1:02}" for x in t], **{k: _arr("d", m[k]) for k in ("h", "l", "c")}}


# ---------- profile store (time-of-day volume) ----------
def cumulative(items):
    """1-hour bars of one day -> cumulative volume at each MARKS entry (volume traded before that time)."""
    vols = {}
    for b in items:
        vols[alpaca._ny(b["t"]).strftime("%H:00")] = vols.get(alpaca._ny(b["t"]).strftime("%H:00"), 0) + b["v"]
    out, run = [], 0.0
    for m in MARKS:
        out.append(run)
        run += vols.get(m, 0)
    out[-1] = run  # 20:00 = the whole day
    return out


def update_profile(prof, symbols, sessions_done):
    """Add the missing completed sessions (up to PROFILE_DAYS back) to the profile store (mutated:
    {"days": {symbol: {date: [cumulative at MARKS]}}}) -> number of symbol-days added."""
    days = sessions_done[-PROFILE_DAYS:]
    added = 0
    by = prof.setdefault("days", {})
    missing = {}
    for s in symbols:
        have = by.get(s, {})
        for d in days:
            if d not in have:
                missing.setdefault(d, []).append(s)
    for d, syms in sorted(missing.items()):
        for i in range(0, len(syms), CHUNK):
            part = syms[i:i + CHUNK]
            got = _get_bars(part, alpaca.utc(d, "04:00"), alpaca.utc(d, "20:00"), "1Hour")
            for s in part:
                by.setdefault(s, {})[d] = cumulative(got.get(s) or [])
                added += 1
    for s in by:  # keep the window
        for d in [d for d in by[s] if d not in days]:
            del by[s][d]
    return added


def profile_mean(prof, symbol):
    """Mean cumulative volume at each MARKS entry over the stored sessions -> list or None (< 20 sessions)."""
    days = (prof.get("days") or {}).get(symbol) or {}
    if len(days) < 20:
        return None
    rows = list(days.values())
    return [sum(r[k] for r in rows) / len(rows) for k in range(len(MARKS))]


def expected_volume(mean, hhmm):
    """The profile's mean cumulative volume at New York time hhmm (linear between hour marks)."""
    if not mean:
        return None
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    if h < 4:
        return 0.0
    if h >= 20:
        return mean[-1]
    k = h - 4
    a, b = mean[k], mean[k + 1]
    return a + (b - a) * m / 60


# ---------- today (stage 2) ----------
TODAY_TF = "30Min"  # stage 2 (D-040): open, high, low, last and volume so far at a 30-minute grain


def today(symbols, day, end_hhmm, open_hhmm="09:30", close_hhmm="16:00", timeframe=TODAY_TF):
    """Live SIP bars (30-minute) of New York `day` from 04:00 to end_hhmm (>= 16 minutes ago) for `symbols` ->
    {symbol: {"o", "h", "l", "c" (regular session; None before the open), "last" (last trade of any session),
    "v" (volume since 04:00), "n", "pre" (last pre-market price), "post" (last after-hours price), "at"}}."""
    out = {}
    if end_hhmm <= "04:00":
        return out
    for i in range(0, len(symbols), CHUNK):
        part = symbols[i:i + CHUNK]
        got = _get_bars(part, alpaca.utc(day, "04:00"), alpaca.utc(day, end_hhmm), timeframe)
        for s in part:
            items = got.get(s) or []
            if not items:
                continue
            rth, pre, post = [], [], []
            for b in items:
                t = alpaca._ny(b["t"]).strftime("%H:%M")
                (pre if t < open_hhmm else rth if t < close_hhmm else post).append((t, b))
            out[s] = {"o": rth[0][1]["o"] if rth else None, "h": max(b["h"] for _, b in rth) if rth else None,
                      "l": min(b["l"] for _, b in rth) if rth else None, "c": rth[-1][1]["c"] if rth else None,
                      "last": items[-1]["c"], "v": float(sum(b["v"] for b in items)),
                      "n": float(sum(b.get("n") or 0 for b in items)),
                      "pre": pre[-1][1]["c"] if pre else None, "post": post[-1][1]["c"] if post else None,
                      "at": alpaca._ny(items[-1]["t"]).strftime("%H:%M")}
    return out


def end_time(now_ny, delay_min=16):
    """The latest SIP query end the free plan allows at New York time now_ny -> "HH:MM" (today)."""
    return (now_ny - dt.timedelta(minutes=delay_min)).strftime("%H:%M")


def log_line():
    return {"alpaca_requests": alpaca.REQUESTS[0]}
