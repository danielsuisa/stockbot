"""US market sessions for the extended-hours runs - docs/superpowers/specs/2026-09-28-extended-hours-scans-design.md,
section 3: which run slot a scheduled start serves, which session is trading now, and which trading day a squeeze
list is for. All times are New York times; Nasdaq's market-info decides holidays and early closes."""
import datetime as dt
import json
from zoneinfo import ZoneInfo

from bot import common

NY = ZoneInfo("America/New_York")
MARKET_INFO = "https://api.nasdaq.com/api/market-info"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
SLOTS = {"pre0": (dt.time(4, 15), "pre"), "pre1": (dt.time(7, 30), "pre"), "pre2": (dt.time(9, 15), "pre"),
         **{f"reg{h - 9}": (dt.time(h, 30), "regular") for h in range(10, 16)},  # the screen, hourly 10:30-15:30
         "post1": (dt.time(16, 30), "post"), "post2": (dt.time(19, 30), "post")}
INSIDER = [n for n, (_, session) in SLOTS.items()  # the same-day insider scan: SEC takes filings from 06:00 New York
           if n != "pre0" and session != "regular"]   # only, and the hourly slots are the screen's
LATE = dt.timedelta(minutes=90)  # GitHub starts schedules late; a run later than this is stale
WINDOWS = {"pre": (dt.time(4), dt.time(9, 30)), "regular": (dt.time(9, 30), dt.time(16)),
           "post": (dt.time(16), dt.time(20))}  # only when Nasdaq's market-info is unavailable
KEEP = 7  # days of slot and warning records


def now_ny():
    return dt.datetime.now(NY)


def _naive(now):
    return now.astimezone(NY).replace(tzinfo=None) if now.tzinfo else now


def _next_weekday(day):
    day += dt.timedelta(1)
    while day.weekday() >= 5:
        day += dt.timedelta(1)
    return day


def status(now=None):
    """Today's sessions from Nasdaq's market-info -> {"business", "pre", "regular", "post" ((start, end), naive New
    York datetimes), "next" (ISO)}, or None when it cannot be read. When Nasdaq shows another day than today's New
    York date (it may roll over before the winter 19:30 run - not measured), today gets the weekday windows and trades
    iff it is Nasdaq's previous or next trading day."""
    try:
        d = json.loads(common.fetch(MARKET_INFO, headers=HEADERS, tries=2, timeout=20))["data"]
        at = dt.datetime.fromisoformat
        on = lambda k: dt.datetime.strptime(d[k], "%b %d, %Y").date()  # noqa: E731
        st = {"business": bool(d["isBusinessDay"]), "pre": (at(d["pmOpenRaw"]), at(d["openRaw"])),
              "regular": (at(d["openRaw"]), at(d["closeRaw"])), "post": (at(d["closeRaw"]), at(d["ahCloseRaw"])),
              "next": on("nextTradeDate").isoformat()}
        known = {on("previousTradeDate"), on("nextTradeDate")}
    except Exception as e:  # any failure -> the caller falls back to the weekday rule
        print(f"nasdaq market-info: {type(e).__name__} {e}")
        return None
    now, shown = _naive(now or now_ny()), st["regular"][0].date()
    if shown == now.date():
        return st
    later = sorted(x for x in known | ({shown} if st["business"] else set()) if x > now.date())
    return {**fallback(now), "business": now.date() in known, **({"next": later[0].isoformat()} if later else {})}


def fallback(now):
    """The weekday rule with fixed session windows, for when Nasdaq's market-info is unavailable."""
    day = _naive(now).date()
    return {"business": day.weekday() < 5, "next": _next_weekday(day).isoformat(),
            **{s: (dt.datetime.combine(day, a), dt.datetime.combine(day, b)) for s, (a, b) in WINDOWS.items()}}


def slot(now, st, names=None):
    """The slot a scheduled run started at `now` serves, or None: 0-90 minutes after the slot's time, on a business
    day whose slot's session is trading at the slot's time - so holidays and sessions that do not exist that day
    give None, and a late start still serves its slot. Windows overlap (hourly slots), so the latest slot started
    wins. names: the slots this run may serve (e.g. INSIDER). A daylight-saving twin that lands inside the 90 minutes
    is a retry: claim() stops it when the slot already ran."""
    now = _naive(now)
    if not st or not st["business"]:
        return None
    best = None  # windows overlap (hourly slots, 90 minutes each): the latest slot started is the one this run serves
    for name, (t, session) in SLOTS.items():
        if names is not None and name not in names:
            continue
        at = dt.datetime.combine(now.date(), t)
        start, end = st[session]
        if start <= at < end and at <= now <= at + LATE and (best is None or at > best[0]):
            best = (at, name)
    return best and best[1]


def session_now(now, st):
    """"pre" | "regular" | "post" while that session trades at `now`, else None."""
    now = _naive(now)
    if not st or not st["business"]:
        return None
    return next((s for s in ("pre", "regular", "post") if st[s][0] <= now < st[s][1]), None)


def next_open(now, st):
    """The trading day of the next regular open at or after `now` (ISO): today before today's open, else the next
    trading day - where a stock seen now can first be bought (the screen's journal entry)."""
    now, st = _naive(now), st or fallback(now)
    if st["business"] and now < st["regular"][0]:
        return now.date().isoformat()
    if st.get("next", "") > now.date().isoformat():
        return st["next"]
    return _next_weekday(now.date()).isoformat()


def _mark(state, key, name, today):
    """Record `name` under state[key][today] -> False if it was there already; records older than KEEP days go."""
    rec = state.setdefault(key, {})
    if name in rec.get(today, []):
        return False
    rec[today] = rec.get(today, []) + [name]
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(KEEP)).isoformat()
    for d in [d for d in rec if d < cutoff]:
        del rec[d]
    return True


def claim(state, name, today):
    """Record slot `name` as run on New York date `today` -> False if it already ran (e.g. the other
    daylight-saving twin of the same schedule, or a retry)."""
    return _mark(state, "slots", name, today)


def first(state, key, today):
    """True the first time `key` (e.g. "efts:post") is warned on New York date `today`: one warning per source per
    session, not per run."""
    return _mark(state, "warned", key, today)
