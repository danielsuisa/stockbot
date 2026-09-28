"""The bot's clock. GitHub starts this repo's scheduled runs 3.5-5.5 hours late (measured 2026-09-25..28: 5 of 5)
but a dispatched run within seconds, so the always-on Telegram listener starts the timed runs itself, from its poll
loop (at least once every listen.POLL seconds). Each run still decides for itself - sessions.slot/claim for the
session slots, scan.morning_done for the morning scan - so a second start (e.g. around a listener hand-over) only
costs a run that exits quietly. The watchdog keeps its own GitHub schedule, so a stopped listener is still noticed."""
import datetime as dt

from bot import common, sessions
from bot.common import code

UTC = dt.timezone.utc
WINDOW = dt.timedelta(minutes=60)  # a job is started up to this long after its time (a listener hand-over or restart)
WEEKDAYS, TUE_SAT = range(0, 5), range(1, 6)
# (name, time zone, time, weekdays in that zone, workflow, dispatch inputs)
JOBS = ([(f"squeeze:{s}", sessions.NY, t, WEEKDAYS, "squeeze.yml", {"mode": "slot"})
         for s, (t, _) in sessions.SLOTS.items()]
        + [(f"insider:{s}", sessions.NY, t, WEEKDAYS, "daily-scan.yml", {"mode": "intraday"})
           for s, (t, _) in sessions.SLOTS.items() if s != "pre0"]  # SEC takes filings from 06:00 New York only
        + [("scan:morning", UTC, dt.time(5, 30), TUE_SAT, "daily-scan.yml", {"mode": "morning"})])


def due(now, done):
    """Jobs to start at `now` (aware) -> [(key, workflow, inputs)]: 0-60 minutes after their time on one of their
    weekdays, and not in `done`. key = name:date, the date in the job's own time zone."""
    out = []
    for name, tz, t, days, wf, inputs in JOBS:
        local = now.astimezone(tz)
        at = dt.datetime.combine(local.date(), t, tzinfo=tz)
        key = f"{name}:{local.date()}"
        if local.weekday() in days and at <= local <= at + WINDOW and key not in done:
            out.append((key, wf, inputs))
    return out


def tick(done, now=None):
    """Start every due job (only on Actions, where the run has a token) -> the keys started. A failed start is tried
    again on every tick until its hour is over, and reported once."""
    if not (common.env("GITHUB_REPOSITORY") and common.env("GITHUB_TOKEN")):
        return []
    started, failed = [], {}
    for key, wf, inputs in due(now or dt.datetime.now(UTC), done):
        err = common.dispatch(wf, inputs)
        if err:
            failed[key] = err
            continue
        done.add(key)
        started.append(key)
        common.log("clock", job=key, workflow=wf, **inputs)
    new = [k for k in failed if f"warned:{k}" not in done]
    if new:
        done.update(f"warned:{k}" for k in new)
        common.send(f"⚠️ לא הצלחתי להפעיל ב־GitHub את הריצות {', '.join(code(k) for k in new)}"
                    f" ({code(failed[new[0]])}). אנסה שוב בכל דקה, עד שעה אחרי המועד.")
    return started
