"""The screener's timed runs (DECISION D-004): GitHub starts this repo's schedules hours late, so - like bot/clock.py -
the always-on listener dispatches them, from its poll loop. Its own job list, because tests/test_clock.py pins the
exact set of clock.JOBS. Each run decides for itself (the market calendar, presets.claim), so a second start costs only
a run that exits quietly."""
import datetime as dt
from zoneinfo import ZoneInfo

from bot import common
from bot.common import code

NY = ZoneInfo("America/New_York")
WINDOW = dt.timedelta(minutes=60)
WORKFLOW = "screener.yml"
SLOTS = {"pre1": dt.time(7, 30), "pre2": dt.time(9, 15),  # the extended-hours run times
         **{f"reg{h - 9}": dt.time(h, 30) for h in range(10, 16)},  # hourly in the regular session
         "post1": dt.time(16, 30), "post2": dt.time(19, 30)}
MAINT = dt.time(20, 30)  # the stores' maintenance, after the day's bars are final (20:16)
# (name, time, inputs)
JOBS = [(f"screener:{s}", t, {"mode": "slot", "slot": s}) for s, t in SLOTS.items()] + [
    ("screener:maint", MAINT, {"mode": "refresh"})]


def due(now, done):
    """Jobs to start at `now` (aware) -> [(key, inputs)]: 0-60 minutes after their New York time on a weekday, and
    not in `done`."""
    local = now.astimezone(NY)
    out = []
    if local.weekday() >= 5:
        return out
    for name, t, inputs in JOBS:
        at = dt.datetime.combine(local.date(), t, tzinfo=NY)
        key = f"{name}:{local.date()}"
        if at <= local <= at + WINDOW and key not in done:
            out.append((key, inputs))
    return out


def tick(done, now=None):
    """Dispatch every due job (only on Actions, where the run has a token) -> the keys started. A failed start is
    retried on every tick within its hour and reported once."""
    if not (common.env("GITHUB_REPOSITORY") and common.env("GITHUB_TOKEN")):
        return []
    started, failed = [], {}
    for key, inputs in due(now or dt.datetime.now(dt.timezone.utc), done):
        err = common.dispatch(WORKFLOW, inputs)
        if err:
            failed[key] = err
            continue
        done.add(key)
        started.append(key)
        common.log("screener_clock", job=key, **inputs)
    new = [k for k in failed if f"warned:{k}" not in done]
    if new:
        done.update(f"warned:{k}" for k in new)
        common.send(f"⚠️ לא הצלחתי להפעיל ב־GitHub את הסורק {', '.join(code(k) for k in new)}"
                    f" ({code(failed[new[0]])}). אנסה שוב בכל דקה, עד שעה אחרי המועד.")
    return started
