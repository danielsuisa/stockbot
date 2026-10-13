"""The squeeze list's forward test (docs/superpowers/specs/2026-10-13-squeeze-forward-test.md, registered 2026-10-13):
how the journal (data/squeeze_journal.json) is judged. Pure functions; nothing is fetched, written or printed.

The population is the journal's entries of the current screen and its pre-market list with a closed trade, in
journal order; x = r10 - 1% a trade; the verdict uses exactly the first 605 and is never computed earlier. GO only
if the mean of x is above 0, t (the entry weeks as blocks, bot.ensemble_stats.se) is at least 2.0, and the mean of x
is above 0 in the first 302 and in the last 303. N, the cost and the rule do not change."""
import datetime as dt

from bot import ensemble_stats as st
from bot.common import change, code

N, COST, T_MIN, FIRST_HALF = 605, 0.01, 2.0, 302


def population(entries):
    """The test's trades: entries of SCREEN or SCREEN_PRE whose outcome is a trade, in journal order."""
    from bot import squeeze_live  # the screen's names live there (a lazy import: squeeze_live imports this module)
    variants = (squeeze_live.SCREEN, squeeze_live.SCREEN_PRE)
    return [e for e in entries if e.get("variant") in variants and (e.get("outcome") or {}).get("trade")]


def progress(entries):
    """The count only: closed trades of the test (at most 605), and whether the verdict can be computed."""
    k = len(population(entries))
    return {"closed": min(k, N), "n": N, "done": k >= N}


def _week(e):
    y, w, _ = dt.date.fromisoformat(e["outcome"].get("start") or e["date"]).isocalendar()
    return (y, w)


def verdict(entries):
    """The verdict on exactly the first 605 trades, or None before there are 605."""
    pop = population(entries)
    if len(pop) < N:
        return None
    pop = pop[:N]
    x = [e["outcome"]["r10"] - COST for e in pop]
    index, blocks = {}, []
    for e in pop:
        blocks.append(index.setdefault(_week(e), len(index)))
    g = len(index)
    mean_x = st.mean(x)
    se = st.se(x, blocks, g) if g > 1 else 0.0
    t = mean_x / se if se else 0.0
    first, second = x[:FIRST_HALF], x[FIRST_HALF:]
    out = {"n": N, "mean_x": mean_x, "se": se, "t": t, "weeks": g, "first_half_n": len(first),
           "second_half_n": len(second), "first_half_mean": st.mean(first), "second_half_mean": st.mean(second)}
    out["mean_positive"] = mean_x > 0
    out["t_ok"] = t >= T_MIN
    out["halves_positive"] = out["first_half_mean"] > 0 and out["second_half_mean"] > 0
    out["verdict"] = "GO" if out["mean_positive"] and out["t_ok"] and out["halves_positive"] else "NO-GO"
    return out


def line(entries):
    """The one line of /squeeze stats: the count until 605, then the verdict and the mean of x."""
    v = verdict(entries)
    if v is None:
        return (f"מבחן קדימה: הושלמו {code(progress(entries)['closed'])} מתוך {code(N)} עסקאות. "
                "התוצאה תוצג כשיושלמו כולן.")
    return (f"מבחן קדימה: הושלמו {code(N)} מתוך {code(N)} עסקאות. התוצאה: {code(v['verdict'])}, "
            f"ממוצע לעסקה אחרי עלות של {code('1%')}: {change(v['mean_x'], 1)}.")
