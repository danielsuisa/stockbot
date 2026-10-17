"""The listener's breakout loop. Once a minute from 04:15 to 15:55 New York: build the context from yesterday's
SIP daily bars, quote the pre-market once the context is ready, send one watchlist at 08:45 (15:45 Israel, at most
8 names), and ping when a tracked name (at most 30) trades through its trigger.

Live prices are the free Alpaca plan's IEX feed, one snapshot call, never a SIP quote newer than 15 minutes.
The pre-market grade uses the same Nasdaq quote the movers path already calls. Neither is the consolidated tape,
and the ping says so. This module never places or transmits an order.
"""
import base64
import datetime as dt
import json
from pathlib import Path

from bot import alpaca, breakout, common, equity, movers, sessions, ticket_backtest

CHUNK = 100  # one Alpaca daily request per quiet minute; ten of them when 08:30 has arrived and the scan is behind
PATH = common.DATA / "breakout_state.json"


def _ny(now):
    now = now or dt.datetime.now(breakout.NY)
    return now.astimezone(breakout.NY) if now.tzinfo else now.replace(tzinfo=breakout.NY)


def _in_window(now):
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dt.time(4, 15) <= t <= dt.time(15, 55)


def _keys():
    return bool(common.env("ALPACA_KEY_ID") and common.env("ALPACA_SECRET_KEY"))


def fresh(day):
    return {"v": 1, "day": day, "cursor": 0, "n": None, "ready": False, "found": [], "names": [],
            "watch_sent": False, "allocated": 0.0, "open_risk": 0.0}


def _path():
    return Path(common.env("BREAKOUT_PATH")) if common.env("BREAKOUT_PATH") else PATH


def encode(state):
    """State for the next listener (one line, no equity and no share count). '' when there is nothing to hand on."""
    if not state:
        return ""
    return base64.b64encode(json.dumps(state, separators=(",", ":")).encode()).decode()


def decode(blob):
    if not blob:
        return None
    try:
        return json.loads(base64.b64decode(blob))
    except (ValueError, json.JSONDecodeError):
        return None


def load():
    """The chain's blob wins; otherwise the file committed at the last handoff."""
    state = decode(common.env("BREAKOUT_STATE"))
    if state:
        return state
    path = _path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def dump(state):
    """Write the file on Actions (the handoff commits it). Tests that set BREAKOUT_PATH get a file too.
    Equity and share counts are not in `state`."""
    if not state:
        return
    explicit = common.env("BREAKOUT_PATH")
    if not explicit and common.env("GITHUB_ACTIONS") != "true":
        return
    path = Path(explicit) if explicit else PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
    tmp.replace(path)


def _send(text):
    common.send(text, footer=False)


class LiveFeed:
    """SIP dailies through yesterday, Nasdaq pre-market quotes, IEX snapshots. Cached for this process only."""

    def __init__(self):
        self._syms, self._equity = None, None

    def symbols(self):
        if self._syms is None:
            self._syms = ticket_backtest.asset_universe(alpaca.assets())
        return self._syms

    def business(self, now):
        st = sessions.status(now)
        if not st:
            return now.weekday() < 5
        return bool(st.get("business"))

    def daily(self, syms, start, end):
        """`end` is yesterday: Alpaca SIP, which the free plan allows once the window is 15 minutes old."""
        return alpaca.daily(list(syms), start, end) if syms else {}

    def premarket(self, syms, today):
        out = {}
        for t, q in movers.quotes(list(syms), "pre").items():
            if q and q.get("date") == today:
                out[t] = q
        return out

    def iex(self, syms):
        return alpaca.iex_snapshots(list(syms))

    def equity(self):
        """The pinned equity, remembered after the first success. Never written to the state or the log."""
        if self._equity is not None:
            return self._equity
        try:
            got = equity.load()
        except Exception:
            common.trace()
            return None
        if got:
            self._equity = got[0]
        return self._equity


def _keep(found, row):
    found.append(row)
    found.sort(key=lambda r: (r["dist"], r["t"]))
    del found[breakout.FOUND_MAX:]


def _finish(state):
    best = sorted(state["found"], key=lambda r: (r["dist"], r["t"]))[:breakout.TRACK_MAX]
    state["names"] = [breakout.track(r, r["t"]) for r in best]
    state["found"] = []
    state["ready"] = True


def _ingest(state, day, feed):
    """One chunk of the universe (the caller may ask for several when 08:30 has passed and the scan is behind).
    False: the daily request failed and the cursor stays put."""
    syms = feed.symbols()
    n = len(syms)
    if state.get("n") not in (None, n):
        state["cursor"], state["found"] = 0, []
    state["n"] = n
    if state["cursor"] >= n:
        _finish(state)
        return True
    part = syms[state["cursor"]:state["cursor"] + CHUNK]
    start = (dt.date.fromisoformat(day) - dt.timedelta(days=60)).isoformat()
    end = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()  # not today: no recent SIP
    try:
        got = feed.daily(part, start, end)
    except Exception:
        common.trace()
        return False
    for t in part:
        row = breakout.context_row(got.get(t) or [], day)
        if breakout.passes(row):
            _keep(state["found"], {**row, "t": t})
    state["cursor"] += len(part)
    if state["cursor"] >= n:
        _finish(state)
    return True


def _quote(state, day, feed, fire, send):
    """Grade the tracked names from the pre-market quote. `fire` lets a name that was already under the level ping
    if this quote is the print that went through it."""
    names = state["names"]
    if not names:
        return False
    try:
        quotes = feed.premarket([n["t"] for n in names], day)
    except Exception:
        common.trace()
        return False
    E, sent = _equity(feed), False
    kept = []
    for name in names:
        mark = breakout.apply_quote(name, quotes.get(name["t"]), day)
        if mark == "drop":
            continue
        kept.append(name)
        quote = quotes.get(name["t"]) or {}
        if mark != "keep" or not quote.get("price"):
            continue  # a pinged name, or a skipped quote, stays without a new print
        level = breakout.observe(name, quote["price"], fire=fire)
        if level is not None:
            sent = _ping(state, name, level, _spread(None), E, day, "Nasdaq", send) or sent
    state["names"] = kept
    return sent


def _equity(feed):
    try:
        return feed.equity()
    except Exception:
        common.trace()
        return None


def _spread(snap):
    if not snap:
        return None
    bid, ask = snap.get("bid"), snap.get("ask")
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return ask - bid


def _ping(state, name, level, spread, E, day, tape, send):
    order = breakout.bracket(name, level)
    grade = breakout.grade_for_cross(name, spread, order["R"])
    sizing = breakout.allocation(grade, E, order["R"], state["allocated"], state["open_risk"])
    try:
        send(breakout.ping_text(name, order, sizing, day, tape))
    except Exception:
        common.trace()
    if sizing["shares"]:
        state["allocated"] = round(state["allocated"] + sizing["pct"], 6)
        state["open_risk"] = round(state["open_risk"] + sizing["pct"], 6)
    common.log("breakout_ping", symbol=name["t"], grade=grade, sized=bool(sizing["shares"]))
    return True


def _cross(state, day, feed, send):
    names = [n for n in state["names"] if not n.get("pinged")]
    if not names:
        return False
    try:
        snaps = feed.iex([n["t"] for n in names])
    except Exception:
        common.trace()
        return False
    E, sent = _equity(feed), False
    for name in names:
        snap = snaps.get(name["t"]) or {}
        level = breakout.observe(name, snap.get("price"), fire=True)
        if level is None:
            continue
        sent = _ping(state, name, level, _spread(snap), E, day, "IEX", send) or sent
    return sent


def _watch_due(now, state):
    if state.get("watch_sent") or not state.get("ready"):
        return False
    t = now.time()
    return dt.time(8, 45) <= t < dt.time(9, 30)


def tick(state, now=None, feed=None, send=None):
    """One pass. Returns (state, changed). Outside 04:15-15:55 New York, or without Alpaca keys when using the live
    feed, this is a no-op so the listener's tests and its off-hours polls stay quiet."""
    now = _ny(now)
    if not _in_window(now):
        return state, False
    if feed is None and not _keys():
        return state, False
    feed = feed or LiveFeed()
    send = send or _send
    try:
        business = feed.business(now) if getattr(feed, "business", None) else True
    except Exception:
        common.trace()
        business = True
    if not business:
        return state, False
    day = now.date().isoformat()
    changed = False
    if state is None or state.get("day") != day:
        state = fresh(day)
        changed = True
    if not state["ready"]:
        bursts = 10 if now.time() >= dt.time(8, 30) else 1
        for _ in range(bursts):
            if state["ready"]:
                break
            if not _ingest(state, day, feed):
                break
            changed = True
        if state["ready"]:
            _quote(state, day, feed, fire=False, send=send)
            changed = True
    if _watch_due(now, state):
        _quote(state, day, feed, fire=True, send=send)
        try:
            send(breakout.watch_text(state["names"], now))
        except Exception:
            common.trace()
        state["watch_sent"] = True
        changed = True
        common.log("breakout_watch", names=len(state["names"]))
    if state["ready"] and _cross(state, day, feed, send):
        changed = True
    return state, changed
