"""Index program: the inputs (docs/superpowers/specs/2026-10-12-index-program-design.md section 3, A2, C1, D1).
The Stooq file (private repository) and the H.15 file (data/index/) are read only after their sha256 equals the
committed manifest; the cash leg's daily return comes from section 3's formula. Pure functions; nothing printed."""
import bisect
import datetime as dt
import hashlib
import json

from bot import index_probe

FIRST, LAST = "2005-02-25", "2026-09-30"  # C1: the file's rows used
ACCOUNT_START = "2006-01-03"  # C2: no account starts before this day
FEE = 0.0014  # section 3: the fund fee, a year


class DataMismatch(Exception):
    """A file's sha256 differs from the committed manifest (A2): the run stops."""


def _checked(path, name, manifest_path):
    with open(path, "rb") as fh:
        body = fh.read()
    with open(manifest_path, encoding="utf-8") as fh:
        want = json.load(fh)[name]["sha256"]
    if hashlib.sha256(body).hexdigest() != want:
        raise DataMismatch(name)
    return body


def load_prices(path, manifest_path, name="spy_us_d.csv"):
    """The Stooq file's rows 2005-02-25 to 2026-09-30 -> [(date, o, h, l, c)] ascending."""
    rows = index_probe.parse_stooq(_checked(path, name, manifest_path))
    return [r for r in rows if FIRST <= r[0] <= LAST]


def load_h15(path, manifest_path, name="h15-3m-tbill.csv"):
    """The H.15 file -> {date: value in percent, or None for no data}."""
    out = {}
    for line in _checked(path, name, manifest_path).decode("utf-8").splitlines():
        if not line or line.startswith("#") or line.startswith("date,"):
            continue
        d, _, v = line.partition(",")
        out[d] = float(v) if v else None
    return out


def cash_returns(days, h15, fee=FEE):
    """Section 3: the cash leg's return on trading day t = the bill rate of the previous trading day / 100 x d / 360
    - fee x d / 365, d the calendar days since the previous trading day. A missing rate takes the previous value
    (8.1). The file's first day has no previous trading day: 0."""
    known = sorted((d, v) for d, v in h15.items() if v is not None)
    kd = [d for d, _ in known]

    def rate(day):
        k = bisect.bisect_right(kd, day) - 1
        if k < 0:
            raise ValueError(f"no bill rate on or before {day}")
        return known[k][1]

    out = {days[0]: 0.0}
    for prev, day in zip(days, days[1:]):
        d = (dt.date.fromisoformat(day) - dt.date.fromisoformat(prev)).days
        out[day] = rate(prev) / 100 * d / 360 - fee * d / 365
    return out


def fingerprint(*paths):
    """The data fingerprint of 8.2: sha256 over the files' own sha256, in order."""
    h = hashlib.sha256()
    for p in paths:
        with open(p, "rb") as fh:
            h.update(hashlib.sha256(fh.read()).hexdigest().encode())
    return h.hexdigest()[:16]
