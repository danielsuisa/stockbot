"""Two-stage evaluation (docs/screener/architecture.md, E3) of a parsed Query over the stores.

Stage 1: the predicates computable from the stores alone (descriptive, fundamentals without price, short interest,
and - outside a trading session, or in PRE - the technicals) for the whole universe; no network.
Stage 2 (PRE / RTH / POST only): today's 30-minute SIP bars for the survivors, at most STAGE2_MAX symbols (when more
survive, the ones that pass the intraday predicates on last-close data come first, each group ranked by the sort key
on daily data, and the cut is reported as truncated), then every predicate on today's numbers."""
import dataclasses
import datetime as dt

from bot import common
from bot.screener import catalog, signals
from bot.screener.metrics import registry
from bot.screener.stores import bars as BARS

STAGE2_MAX = common.env("SCREENER_STAGE2_MAX", 1500)
MAX_ROWS = 15
TODAY_METRICS = {"price", "change", "change_open", "gap", "volume", "trades", "rel_volume", "volume_pct_float",
                 "ah_close", "ah_change", "market_cap", "pe", "ps", "pb", "pc", "pfcf", "ev_ebitda", "ev_sales",
                 "dividend_yield", "news_count", "day_range"}


@dataclasses.dataclass
class Env:
    """Everything one evaluation reads. today: {symbol: bars.today() row} (filled by stage 2)."""
    now: dt.datetime
    session: str
    day: str
    sessions_done: list
    universe: dict
    bars: dict
    long: dict = dataclasses.field(default_factory=dict)
    profile: dict = dataclasses.field(default_factory=dict)
    fund: dict = dataclasses.field(default_factory=dict)
    reference: dict = dataclasses.field(default_factory=dict)
    short: dict = dataclasses.field(default_factory=dict)
    own: dict = dataclasses.field(default_factory=dict)
    today: dict = dataclasses.field(default_factory=dict)
    news: dict = dataclasses.field(default_factory=dict)  # {symbol: [ISO times]}
    news_count: dict = dataclasses.field(default_factory=dict)
    as_of: dict = dataclasses.field(default_factory=dict)  # store -> as_of text
    stale: set = dataclasses.field(default_factory=set)
    stage2: set = dataclasses.field(default_factory=set)
    calendar: list = dataclasses.field(default_factory=list)  # every session (past and future), ISO
    fetch_today: object = None  # callable(symbols) -> {symbol: today row}; None = no network (tests, CLOSED)
    _generic: list | None = None

    def generic_profile(self, ctx):
        """A market-wide cumulative volume curve (median shape over the stored profiles, scaled to the symbol's
        average volume) for symbols without their own profile."""
        if self._generic is None:
            shapes = []
            for s in list((self.profile.get("days") or {}))[:500]:
                m = BARS.profile_mean(self.profile, s)
                if m and m[-1]:
                    shapes.append([x / m[-1] for x in m])
            self._generic = [sorted(col)[len(col) // 2] for col in zip(*shapes)] if shapes else []
        avg = ctx.get("avg_volume")
        return [x * avg for x in self._generic] if self._generic and avg else None


@dataclasses.dataclass
class Result:
    rows: list  # [{"t": symbol, "ctx": Ctx}]
    total: int  # matches before the display cut
    ignored: list
    stage1: int
    stage2: int
    truncated: int
    missing: dict  # metric -> symbols where it was None
    session: str
    as_of: str
    notes: list


# ---------- window predicates ----------
def _sessions_around(env):
    cal = env.calendar or env.sessions_done
    past = [d for d in cal if d < env.day]
    future = [d for d in cal if d > env.day]
    return past, future


def earnings_window(env, code, events):
    """events: past 8-K Item 2.02 times (naive New York). Future dates are projected (+364 days, est.)."""
    if not events:
        return False
    proj = [e + dt.timedelta(days=364) for e in events]
    allev = events + [p for p in proj if p.date().isoformat() > max(e.date().isoformat() for e in events)]
    past, future = _sessions_around(env)
    day = env.day
    tom = future[0] if future else None
    yest = past[-1] if past else None
    d0 = dt.date.fromisoformat(day)

    def on(d, when=None):
        for e in allev:
            if e.date().isoformat() == d:
                t = e.strftime("%H:%M")
                if when is None or (when == "before" and t < "09:30") or (when == "after" and t >= "16:00"):
                    return True
        return False

    ds = sorted({e.date().isoformat() for e in allev})
    if code in ("today", "todaybefore", "todayafter"):
        return on(day, {"today": None, "todaybefore": "before", "todayafter": "after"}[code])
    if code in ("tomorrow", "tomorrowbefore", "tomorrowafter"):
        return bool(tom) and on(tom, {"tomorrow": None, "tomorrowbefore": "before", "tomorrowafter": "after"}[code])
    if code in ("yesterday", "yesterdaybefore", "yesterdayafter"):
        return bool(yest) and on(yest, {"yesterday": None, "yesterdaybefore": "before", "yesterdayafter": "after"}[code])
    if code == "nextdays5":
        win = future[:5]
        return any(d in win for d in ds)
    if code == "prevdays5":
        win = past[-5:]
        return any(d in win for d in ds)
    iso = d0.isocalendar()
    week = lambda d: dt.date.fromisoformat(d).isocalendar()[:2]  # noqa: E731
    if code == "thisweek":
        return any(week(d) == iso[:2] for d in ds)
    if code == "nextweek":
        nxt = (d0 + dt.timedelta(7)).isocalendar()[:2]
        return any(week(d) == nxt for d in ds)
    if code == "prevweek":
        prv = (d0 - dt.timedelta(7)).isocalendar()[:2]
        return any(week(d) == prv for d in ds)
    if code == "thismonth":
        return any(d[:7] == day[:7] for d in ds)
    return False


IPO_DAYS = {"prevweek": 7, "prevmonth": 30, "prevquarter": 91, "prevyear": 365, "prev2yrs": 730, "prev3yrs": 1095,
            "prev5yrs": 1826}
IPO_MORE = {"more1": 1, "more5": 5, "more10": 10, "more15": 15, "more20": 20, "more25": 25}


def ipo_window(env, code, ipo):
    """ipo: first-bar date ISO, or None = on or before the history start (2016-01)."""
    day = dt.date.fromisoformat(env.day)
    start = dt.date.fromisoformat(BARS.HISTORY_START)
    if ipo is None:  # listed since at least the history start: older than (today - 2016-01-01)
        years = (day - start).days / 365.25
        return code in IPO_MORE and IPO_MORE[code] <= years
    d = dt.date.fromisoformat(ipo[:10])
    age = (day - d).days
    past, _ = _sessions_around(env)
    if code == "today":
        return d == day
    if code == "yesterday":
        return bool(past) and d.isoformat() == past[-1]
    if code in IPO_DAYS:
        return 0 <= age <= IPO_DAYS[code]
    if code in IPO_MORE:
        return age > IPO_MORE[code] * 365.25
    return False


def news_window(env, code, times):
    if not times:
        return False
    now = env.now
    day = env.day
    past, _ = _sessions_around(env)
    yest = past[-1] if past else None
    ts = [dt.datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(BARS.alpaca.NY) for t in times]
    if code == "today":
        return any(t.date().isoformat() == day for t in ts)
    if code == "todayafter":
        return any(t.date().isoformat() == day and t.strftime("%H:%M") >= "16:00" for t in ts)
    if code == "yesterday":
        return any(t.date().isoformat() == yest for t in ts)
    if code == "yesterdayafter":
        return any(t.date().isoformat() == yest and t.strftime("%H:%M") >= "16:00" for t in ts)
    if code == "sinceyesterday":
        return any(t.date().isoformat() >= (yest or day) for t in ts)
    if code == "sinceyesterdayafter":
        return any((t.date().isoformat() == yest and t.strftime("%H:%M") >= "16:00") or t.date().isoformat() == day
                   for t in ts)
    mins = {"prevminutes5": 5, "prevminutes30": 30, "prevhours1": 60, "prevhours24": 1440, "prevdays7": 10080,
            "prevmonth": 43200}.get(code)
    return bool(mins) and any(0 <= (now - t).total_seconds() / 60 <= mins for t in ts)


def fn_eval(env, ctx):
    def fn(name, arg, value):
        if name == "not_in":
            return value is not None and value not in arg
        if name == "earnings_window":
            return earnings_window(env, arg, ctx.get("earnings_date"))
        if name == "ipo_window":
            return ipo_window(env, arg, ctx.get("ipo_date"))
        if name == "news_window":
            return news_window(env, arg, env.news.get(ctx.sym))
        return False
    return fn


# ---------- predicates ----------
def passes(p, ctx, env):
    """Does symbol ctx satisfy Predicate p (any alternative of a '|' OR)?"""
    fn = fn_eval(env, ctx)
    for alt in (p, *p.alts):
        if alt.pred and alt.pred[0] == "all":
            ok = catalog.check(alt.pred, None, 1.0, fn, values=ctx.get)
        else:
            v = ctx.get(alt.metric) if alt.pred and alt.pred[0] != "fn" else None
            ok = catalog.check(alt.pred, v, catalog.factor(alt.metric, alt.prefix), fn)
        if ok:
            return True
    return False


def metric_intraday(m, session):
    """Does metric m change with today's bars in this session?"""
    if session not in ("PRE", "RTH", "POST") or m in ("ticker", "avg_volume"):
        return False
    if m in TODAY_METRICS:
        return True
    return session != "PRE" and registry.METRICS.get(m, (None, ""))[1] in ("bars", "long", "profile")


def intraday(p, session):
    """Does predicate p need today's bars in this session?"""
    return any(metric_intraday(m, session) for m in p.metrics())


def _sort_value(ctx, metric):
    if metric == "ticker":
        return ctx.sym
    return ctx.get(metric)


def order(rows, metric, desc):
    """Sort Ctx rows by metric (None last; ties by ticker)."""
    have = [r for r in rows if _sort_value(r, metric) is not None]
    none = [r for r in rows if _sort_value(r, metric) is None]
    have.sort(key=lambda r: r.sym)
    have.sort(key=lambda r: _sort_value(r, metric), reverse=desc)
    return have + sorted(none, key=lambda r: r.sym)


def base_universe(query, env):
    """The symbols a screen starts from: the query's tickers, else the universe without ETFs unless the query asks
    for ETFs (D-036), only symbols that have daily bars."""
    if query.tickers:
        return [t for t in query.tickers if t in env.universe or t in env.bars]
    wants_etf = any(p.metric == "is_etf" for p in query.filters for p in (p, *p.alts))
    return sorted(s for s, u in env.universe.items()
                  if s in env.bars and (wants_etf or not u.get("etf")))


def run(query, env, max_rows=MAX_ROWS):
    """Evaluate `query` over `env` -> Result."""
    from bot.screener.metrics.registry import Ctx
    notes = []
    syms = base_universe(query, env)
    ctxs = {s: Ctx(s, env) for s in syms}
    static = [p for p in query.filters if not intraday(p, env.session)]
    live = [p for p in query.filters if intraday(p, env.session)]
    sig = query.signal
    rule = signals.rule(sig["code"], env) if sig else None
    if sig and rule is None:
        notes.append(f"signal {sig['code']}: no rule")
    survivors = [s for s in syms if all(passes(p, ctxs[s], env) for p in static)]
    stage1 = len(survivors)
    missing = {}
    for p in static:
        for m in p.metrics():
            n = sum(1 for s in syms if ctxs[s].get(m) is None)
            if n:
                missing[m] = n
    need_today = env.session in ("PRE", "RTH", "POST") and bool(
        live or (sig and (signals.needs_today(sig["code"]) or (rule and metric_intraday(rule[0], env.session))))
        or (query.sort and metric_intraday(query.sort[0], env.session)))
    truncated = 0
    if need_today and env.fetch_today:
        if len(survivors) > STAGE2_MAX:
            closed = dataclasses.replace(env, session="CLOSED", today={}, stage2=set())
            pre = {s: Ctx(s, closed) for s in survivors}
            key = query.sort[0] if query.sort else (rule[0] if rule else "avg_volume")
            desc = query.sort[1] if query.sort else (rule[1] if rule else True)
            ok = [pre[s] for s in survivors if all(passes(p, pre[s], closed) for p in live)]
            rest = [pre[s] for s in survivors if s not in {c.sym for c in ok}]
            ranked = order(ok, key, desc) + order(rest, key, desc)
            truncated = len(survivors) - STAGE2_MAX
            survivors = [c.sym for c in ranked[:STAGE2_MAX]]
            notes.append(f"truncated: {truncated} symbols beyond the stage-2 cap of {STAGE2_MAX}")
        env.today.update(env.fetch_today(survivors))
        env.stage2 |= set(survivors)
        ctxs = {s: Ctx(s, env) for s in survivors}
    elif need_today:
        notes.append("no live data: last close used")
    rows = [ctxs[s] for s in survivors if all(passes(p, ctxs[s], env) for p in live)]
    if rule:  # the signal admits, ranks and caps (Top 200 ...); o= then reorders the signal's list
        rows = order([c for c in rows if rule[2](c)], rule[0], rule[1])
        if sig.get("cap"):
            rows = rows[:sig["cap"]]
    if query.sort:
        rows = order(rows, query.sort[0], query.sort[1])
    elif not rule:
        rows = order(rows, "ticker", False)
    return Result(rows=[{"t": c.sym, "ctx": c} for c in rows[:max_rows]], total=len(rows), ignored=list(query.ignored),
                  stage1=stage1, stage2=len(env.stage2), truncated=truncated, missing=missing, session=env.session,
                  as_of=env.now.strftime("%H:%M"), notes=notes)
