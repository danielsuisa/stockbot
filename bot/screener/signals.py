"""The s= signals (docs/screener/metric-spec.md, "Signals"): which stocks a signal admits, how it ranks them and its
cap. Applied after the filters (DECISION D-041: a signal works like a filter plus an ordering)."""
import datetime as dt

from bot.screener.stores import ownership as OWN

# code -> (metric to rank by, descending, admit(ctx) -> bool)
RULES = {
    "ta_topgainers": ("change", True, lambda c: (c.get("change") or 0) > 0),
    "ta_toplosers": ("change", False, lambda c: (c.get("change") or 0) < 0),
    "ta_newhigh": ("change", True, lambda c: c.get("new_high_52w") is True),
    "ta_newlow": ("change", False, lambda c: c.get("new_low_52w") is True),
    "ta_mostvolatile": ("day_range", True, lambda c: c.get("day_range") is not None and (c.get("volume") or 0) > 0),
    "ta_mostactive": ("volume", True, lambda c: (c.get("volume") or 0) > 0),
    "ta_unusualvolume": ("rel_volume", True, lambda c: (c.get("rel_volume") or 0) > 1),
    "ta_overbought": ("rsi14", True, lambda c: (c.get("rsi14") or 0) > 70),
    "ta_oversold": ("rsi14", False, lambda c: c.get("rsi14") is not None and c.get("rsi14") < 30),
    "f_ssr": ("change", False, lambda c: c.get("ssr") is True),
}
TODAY = {"ta_topgainers", "ta_toplosers", "ta_newhigh", "ta_newlow", "ta_mostvolatile", "ta_mostactive",
         "ta_unusualvolume", "f_ssr", "n_majornews"}  # need today's bars while a session trades


def _earnings_today(c, before):
    day = c.env.day
    for t in c.get("earnings_date") or []:
        if t.date().isoformat() == day and ((t.strftime("%H:%M") < "09:30") if before else (t.strftime("%H:%M") >= "16:00")):
            return True
    return False


def rule(code, env):
    """(rank metric, descending, admit) for a signal code, or None when the signal has no rule."""
    if code in RULES:
        return RULES[code]
    if code == "n_earningsbefore":
        return ("market_cap", True, lambda c: _earnings_today(c, True))
    if code == "n_earningsafter":
        return ("market_cap", True, lambda c: _earnings_today(c, False))
    if code in ("it_latestbuys", "it_latestsales"):
        since = env.sessions_done[-2] if len(env.sessions_done) >= 2 else env.day
        kind = "buys" if code == "it_latestbuys" else "sales"
        hit = {str(x["issuer"]) for x in OWN.trades(env.own, kind, since)}
        return ("market_cap", True, lambda c: str(c.cik) in hit)
    if code == "n_majornews":
        return ("news_count", True, lambda c: (env.news_count.get(c.sym) or 0) > 0)
    return None


def needs_today(code):
    return code in TODAY


def since_days(day, n):
    return (dt.date.fromisoformat(day) - dt.timedelta(n)).isoformat()
