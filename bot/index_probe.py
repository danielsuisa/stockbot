"""Index program, phase 1: the data probe (docs/superpowers/specs/2026-10-12-index-program-design.md sections 3 and
8.1 with Amendments 1 to 3; C1 sources, C3 acceptance). Integrity, the exchange calendar and price agreement with
Alpaca only: nothing here computes a return, profit, equity curve or drawdown of any rule or policy (standing rule
2).

- SPY: the owner's Stooq file, read from the private repository's checkout (`;` or `,` separated); its sha256
  must equal C1's. Rows from 2005-02-25 to 2026-09-30 are used.
- Alpaca SPY daily bars, raw and adjusted (adjustment "all"), held in memory only (no disk cache): checks C3.3 and
  C3.4.
- H.15 (`RIFSGFSM03_N.B`, 3-month Treasury bill, secondary market, discount basis, business days) from the Federal
  Reserve Board's Data Download Program: public domain, written with the Board named as its source.

    python -m bot.index_probe --stooq PATH --out DIR

writes DIR/probe.json (counts, dates, shares of days and differences; no price), DIR/h15-3m-tbill.csv and
DIR/manifest.json; prints counts and pass or fail only. Exit 0 when checks 1 to 4 of C3 pass; 2 the Stooq file's
sha256 differs from C1's; 3 a source failed; 4 check 1, 3 or 4 failed; 5 a calendar difference no documented
closure explains."""
import argparse
import collections
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile

from bot import alpaca

UA = "stockbot index-program data probe"
STOOQ_SHA256 = "3493247f082c56292780c23c9f9272f39da5459b0eb10525e66b7b3ab6baca6f"  # C1
H15 = "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H15&filetype=zip"
H15_SERIES = "RIFSGFSM03_N.B"
H15_SOURCE = ("Board of Governors of the Federal Reserve System, H.15 Selected Interest Rates, series RIFSGFSM03_N.B "
              "(3-month Treasury bill, secondary market, discount basis, business days), Data Download Program")
FIRST, LAST = "2005-02-25", "2026-09-30"  # C1: the file's rows used
ALPACA_FIRST = "2016-01-04"  # C3.3, C3.4
H15_FIRST = "1993-12-01"  # A3.4 (C3.5)
REL_TOL = 1e-9  # C3.1
EX_DIV_GAP = 0.001  # C3.3: raw and adjusted changes differ by more than this on an ex-dividend day
SPAN_TOL, NEARER_SHARE = 0.01, 0.90  # C3.3
GROSS_TOL, GROSS_SHARE = 0.005, 0.99  # C3.4, both measures
BIG_MOVE = 0.05  # C3.6


# ---------- sources ----------
def fetch(url, timeout=120):
    """-> (HTTP status or an error type name, body bytes). Never raises; prints nothing."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # noqa: BLE001 - the type only
        return type(e).__name__, b""


def alpaca_bars(symbol, start, end, adjustment):
    """Alpaca SIP daily bars -> [(date, o, h, l, c)] ascending, in memory only (alpaca._get, never the client's disk
    cache)."""
    rows, token = [], None
    while True:
        q = {"symbols": symbol, "timeframe": "1Day", "start": start, "end": end, "feed": "sip",
             "adjustment": adjustment, "limit": alpaca.LIMIT, "sort": "asc", **({"page_token": token} if token else {})}
        js = alpaca._get(f"{alpaca.DATA}/bars", q)
        for b in (js.get("bars") or {}).get(symbol) or []:
            rows.append((alpaca._ny(b["t"]).date().isoformat(), b["o"], b["h"], b["l"], b["c"]))
        token = js.get("next_page_token")
        if not token:
            return rows


def parse_stooq(body):
    """Stooq's daily file (`;` or `,` separated; Date, Open, High, Low, Close[, Volume]) -> [(date, o, h, l, c)]
    ascending, or None when it is not such a file."""
    lines = body.decode("utf-8", "replace").strip().splitlines()
    if not lines:
        return None
    sep = ";" if lines[0].count(";") > lines[0].count(",") else ","
    head = [h.strip().lower() for h in lines[0].split(sep)]
    try:
        idx = [head.index(k) for k in ("date", "open", "high", "low", "close")]
    except ValueError:
        return None
    out = []
    for line in lines[1:]:
        f = line.split(sep)
        out.append((f[idx[0]].strip(), *(float(f[k]) for k in idx[1:])))
    return sorted(out)


def parse_h15(zip_body, series=H15_SERIES):
    """The Board's H.15 zip -> [(date, value or None)] ascending for `series` (None for "ND", no data)."""
    xml = zipfile.ZipFile(io.BytesIO(zip_body)).read("H15_data.xml").decode("utf-8")
    i = xml.index(f'SERIES_NAME="{series}"')
    seg = xml[i:xml.index("</kf:Series>", i)]
    out = []
    for status, value, day in re.findall(r'<frb:Obs OBS_STATUS="([^"]*)" OBS_VALUE="([^"]*)" TIME_PERIOD="([^"]*)"',
                                         seg):
        out.append((day, None if status == "ND" else float(value)))
    return sorted(out)


def h15_csv(rows, start=H15_FIRST, end=LAST):
    """The committed H.15 file: a source line, a header, then date,value (empty for no data)."""
    lines = [f"# source: {H15_SOURCE}", "date,value"]
    lines += [f"{d},{'' if v is None else repr(v)}" for d, v in rows if start <= d <= end]
    return ("\n".join(lines) + "\n").encode("utf-8")


# ---------- C3.1, C3.2 ----------
def integrity(rows, rel=REL_TOL):
    """C3.1: non-positive prices and days with open or close outside [low, high] (relative tolerance `rel`), and
    the order of the dates."""
    out_r = lambda x, lo, h: not (lo * (1 - rel) <= x <= h * (1 + rel))  # noqa: E731
    bad = [r[0] for r in rows if out_r(r[1], r[3], r[2]) or out_r(r[4], r[3], r[2])]
    dates = [r[0] for r in rows]
    out = {"rows": len(rows), "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
           "non_positive": sum(1 for r in rows if min(r[1:5]) <= 0), "outside_range": len(bad),
           "outside_range_days": bad[:20],
           "unsorted_or_duplicate": sum(1 for a, b in zip(dates, dates[1:]) if a >= b)}
    out["pass"] = not (out["non_positive"] or out["outside_range"] or out["unsorted_or_duplicate"])
    return out


SPECIAL_CLOSURES = {  # documented full-day closures of the New York Stock Exchange
    "1994-04-27": "national day of mourning, President Nixon",
    "2001-09-11": "September 11 attacks", "2001-09-12": "September 11 attacks",
    "2001-09-13": "September 11 attacks", "2001-09-14": "September 11 attacks",
    "2004-06-11": "national day of mourning, President Reagan",
    "2007-01-02": "national day of mourning, President Ford",
    "2012-10-29": "Hurricane Sandy", "2012-10-30": "Hurricane Sandy",
    "2018-12-05": "national day of mourning, President George H. W. Bush",
    "2025-01-09": "national day of mourning, President Carter",
}


def _easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    return dt.date(y, (h + l_ - 7 * m + 114) // 31, (h + l_ - 7 * m + 114) % 31 + 1)


def _nth(y, month, weekday, n):
    d = dt.date(y, month, 1)
    return d + dt.timedelta((weekday - d.weekday()) % 7 + 7 * (n - 1))


def _last(y, month, weekday):
    d = dt.date(y, month + 1, 1) - dt.timedelta(1)
    return d - dt.timedelta((d.weekday() - weekday) % 7)


def _observed(d, saturday_back=True):
    if d.weekday() == 5:
        return d - dt.timedelta(1) if saturday_back else None
    return d + dt.timedelta(1) if d.weekday() == 6 else d


def nyse_holidays(y):
    """The exchange's regular holidays of year `y` -> {date ISO: name}. New Year's Day on a Saturday is not moved
    (the exchange's rule); Martin Luther King Jr. Day from 1998; Juneteenth from 2022."""
    pairs = [(_observed(dt.date(y, 1, 1), saturday_back=False), "New Year's Day"),
             (_nth(y, 2, 0, 3), "Washington's Birthday"), (_easter(y) - dt.timedelta(2), "Good Friday"),
             (_last(y, 5, 0), "Memorial Day"), (_observed(dt.date(y, 7, 4)), "Independence Day"),
             (_nth(y, 9, 0, 1), "Labor Day"), (_nth(y, 11, 3, 4), "Thanksgiving Day"),
             (_observed(dt.date(y, 12, 25)), "Christmas Day")]
    if y >= 1998:
        pairs.append((_nth(y, 1, 0, 3), "Martin Luther King Jr. Day"))
    if y >= 2022:
        pairs.append((_observed(dt.date(y, 6, 19)), "Juneteenth"))
    return {d.isoformat(): n for d, n in pairs if d is not None}


def nyse_days(start, end):
    """Weekdays in [start, end] less the regular holidays and the documented special closures."""
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    closed = {**{d: n for y in range(s.year, e.year + 1) for d, n in nyse_holidays(y).items()}, **SPECIAL_CLOSURES}
    out, d = [], s
    while d <= e:
        if d.weekday() < 5 and d.isoformat() not in closed:
            out.append(d.isoformat())
        d += dt.timedelta(1)
    return out, closed


def calendar_check(days, start, end):
    """C3.2: the file's trading days against the exchange calendar. Every difference is listed with its
    explanation (None when no documented closure explains it)."""
    want, closed = nyse_days(start, end)
    have = {d for d in days if start <= d <= end}
    extra, missing = sorted(have - set(want)), sorted(set(want) - have)
    out = {"days": len(have), "calendar_days": len(want),
           "in_file_not_calendar": [[d, closed.get(d)] for d in extra],
           "in_calendar_not_file": [[d, None] for d in missing],
           "unexplained": len(missing) + sum(1 for d in extra if d not in closed)}
    out["pass"] = out["unexplained"] == 0
    return out


# ---------- C3.3, C3.4 ----------
def _changes(rows):
    return {b[0]: b[4] / a[4] - 1 for a, b in zip(rows, rows[1:])}


def dividend_check(file_rows, raw, adj, start, end):
    """C3.3. Ex-dividend days: Alpaca's raw and adjusted close-to-close changes differ by more than 0.001. Passes if
    the file's change over the span is within 1% (relative) of Alpaca's adjusted change over the span, and on at
    least 90% of the ex-dividend days the file's change is nearer to Alpaca's adjusted change than to its raw
    change. Only the relative difference of the two span changes is reported, not the changes."""
    f = [r for r in file_rows if start <= r[0] <= end]
    a = [r for r in adj if start <= r[0] <= end]
    fc, rc, ac = _changes(f), _changes([r for r in raw if start <= r[0] <= end]), _changes(a)
    ex = sorted(d for d in set(rc) & set(ac) if abs(rc[d] - ac[d]) > EX_DIV_GAP)
    usable = [d for d in ex if d in fc]
    nearer = sum(1 for d in usable if abs(fc[d] - ac[d]) < abs(fc[d] - rc[d]))
    span_f = f[-1][4] / f[0][4] - 1
    span_a = a[-1][4] / a[0][4] - 1
    rel = abs(span_f - span_a) / abs(span_a) if span_a else float("inf")
    out = {"span": [f[0][0], f[-1][0]], "span_relative_difference": round(rel, 6), "ex_dividend_days": len(ex),
           "ex_days_in_file": len(usable), "nearer_adjusted": nearer,
           "nearer_share": round(nearer / len(usable), 4) if usable else 0.0, "ex_days": ex}
    out["pass"] = rel <= SPAN_TOL and bool(usable) and nearer / len(usable) >= NEARER_SHARE
    return out


def gross_errors(file_rows, adj, start, end, top=10):
    """C3.4 against Alpaca's adjusted bars: the same trading days; the close-to-close change within 0.005 and the
    same day's open-to-close ratio within 0.005, each on at least 99% of days; the 10 largest differences of each
    measure (date and difference)."""
    A = {r[0]: r for r in file_rows if start <= r[0] <= end}
    B = {r[0]: r for r in adj if start <= r[0] <= end}
    days = sorted(set(A) & set(B))
    ch = [(abs(A[d][4] / A[p][4] - B[d][4] / B[p][4]), d) for p, d in zip(days, days[1:])]
    oc = [(abs(A[d][1] / A[d][4] - B[d][1] / B[d][4]), d) for d in days]
    share = lambda ds: sum(1 for x, _ in ds if x <= GROSS_TOL) / len(ds) if ds else 0.0  # noqa: E731
    out = {"only_file": sorted(set(A) - set(B)), "only_alpaca": sorted(set(B) - set(A)), "common": len(days),
           "change_within": round(share(ch), 6), "oc_within": round(share(oc), 6),
           "largest_change": [[d, round(x, 6)] for x, d in sorted(ch, reverse=True)[:top]],
           "largest_oc": [[d, round(x, 6)] for x, d in sorted(oc, reverse=True)[:top]]}
    out["pass"] = (not out["only_file"] and not out["only_alpaca"] and share(ch) >= GROSS_SHARE
                   and share(oc) >= GROSS_SHARE)
    return out


# ---------- C3.5, C3.6 ----------
def h15_check(rows, start=H15_FIRST, end=LAST):
    """C3.5 (A3.4): rows and missing days over [start, end]; a missing day takes the previous value later."""
    r = [(d, v) for d, v in rows if start <= d <= end]
    return {"rows": len(r), "first": r[0][0] if r else None, "last": r[-1][0] if r else None,
            "missing": sum(1 for _, v in r if v is None), "missing_days": [d for d, v in r if v is None]}


def big_moves(rows, size=BIG_MOVE):
    """C3.6, information: the days whose close-to-close change is above 5% in size (date and change)."""
    return [[b[0], round(b[4] / a[4] - 1, 4)] for a, b in zip(rows, rows[1:]) if abs(b[4] / a[4] - 1) > size]


def open_equals_prev_close(rows):
    """C3.6, information: per year, the share of days whose open equals the previous close."""
    per = collections.defaultdict(lambda: [0, 0])
    for a, b in zip(rows, rows[1:]):
        per[b[0][:4]][0] += b[1] == a[4]
        per[b[0][:4]][1] += 1
    return {y: round(n / t, 4) for y, (n, t) in sorted(per.items())}


def sha256(body):
    return hashlib.sha256(body).hexdigest()


# ---------- the run ----------
def run(stooq_path, out_dir):
    """The probe -> (exit code, report). Writes only the H.15 file and the manifest into `out_dir` (the caller
    writes probe.json); Alpaca bars and the Stooq rows stay in memory."""
    os.makedirs(out_dir, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rep = {"sources": {}, "checks": {}}
    with open(stooq_path, "rb") as fh:
        body = fh.read()
    rep["sources"]["stooq_spy"] = {"file": os.path.basename(stooq_path), "bytes": len(body), "sha256": sha256(body),
                                   "sha256_matches_c1": sha256(body) == STOOQ_SHA256}
    if sha256(body) != STOOQ_SHA256:
        return 2, rep
    parsed = parse_stooq(body)
    if not parsed:
        return 3, rep
    spy = [r for r in parsed if FIRST <= r[0] <= LAST]
    rep["sources"]["stooq_spy"].update(rows_in_file=len(parsed), rows_used=len(spy), first=spy[0][0], last=spy[-1][0])
    h15_s, h15_body = fetch(H15)
    rep["sources"]["h15"] = {"status": h15_s, "zip_sha256": sha256(h15_body) if h15_body else None}
    try:
        raw = alpaca_bars("SPY", ALPACA_FIRST, LAST, "raw")
        adj = alpaca_bars("SPY", ALPACA_FIRST, LAST, "all")
        rep["sources"]["alpaca_spy"] = {"status": 200, "rows_raw": len(raw), "rows_adjusted": len(adj)}
    except Exception as e:  # noqa: BLE001 - the type only
        rep["sources"]["alpaca_spy"] = {"status": type(e).__name__}
        return 3, rep
    if h15_s != 200:
        return 3, rep
    h15 = parse_h15(h15_body)
    c = rep["checks"]
    c["1_integrity"] = integrity(spy)
    c["2_calendar"] = calendar_check([r[0] for r in spy], FIRST, LAST)
    c["3_dividends"] = dividend_check(spy, raw, adj, ALPACA_FIRST, LAST)
    c["4_gross_errors"] = gross_errors(spy, adj, ALPACA_FIRST, LAST)
    c["5_h15"] = h15_check(h15)
    c["6_information"] = {"big_moves": big_moves(spy), "open_equals_prev_close": open_equals_prev_close(spy)}
    h15_file = h15_csv(h15)
    manifest = {
        "spy_us_d.csv": {"source": "Stooq daily CSV (https://stooq.com/q/d/l/?s=spy.us&i=d), downloaded by the owner "
                                   "in a browser", "rows": len(parsed), "first": parsed[0][0], "last": parsed[-1][0],
                         "rows_used": len(spy), "used": [FIRST, LAST], "sha256": sha256(body),
                         "where": "private repository danielsuisa/stockbot-data"},
        "h15-3m-tbill.csv": {"source": H15_SOURCE, "url": H15, "downloaded": now, "rows": c["5_h15"]["rows"],
                             "first": c["5_h15"]["first"], "last": c["5_h15"]["last"], "sha256": sha256(h15_file),
                             "where": "public repository, data/index/"}}
    with open(os.path.join(out_dir, "h15-3m-tbill.csv"), "wb") as fh:
        fh.write(h15_file)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    if not c["2_calendar"]["pass"]:
        return 5, rep
    if not (c["1_integrity"]["pass"] and c["3_dividends"]["pass"] and c["4_gross_errors"]["pass"]):
        return 4, rep
    return 0, rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="Index program data probe (no returns)")
    ap.add_argument("--stooq", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    code, rep = run(a.stooq, a.out)
    with open(os.path.join(a.out, "probe.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    for k, v in rep["checks"].items():
        if isinstance(v, dict) and "pass" in v:
            print(f"check {k}: pass {v['pass']}")
    print(f"exit {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
