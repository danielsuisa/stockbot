"""Index program, phase 1: the data probe (docs/superpowers/specs/2026-10-12-index-program-design.md sections 3 and
8.1 with Amendments 1 and 2). Sources, integrity, calendars and price agreement between sources only: nothing here
computes a return, profit, equity curve or drawdown of any rule or policy (standing rule 2).

- Tiingo (SPY, BIL): downloaded into memory with the key in the `Authorization: Token` header; never written
  anywhere (B1). Only a per-year sha256 fingerprint of its raw rows leaves the run.
- Stooq SPY: the owner's browser download, read from the private repository's checkout (`;` or `,` separated).
- H.15 (`RIFSGFSM03_N.B`, the 3-month Treasury bill, secondary market, discount basis, business days): the Federal
  Reserve Board's Data Download Program; public domain, written with the Board named as its source.
- Alpaca SPY daily bars, adjustment "all": information only (A3.5).

    python -m bot.index_probe --stooq PATH --out DIR [--fingerprint data/index/tiingo-fingerprint.json]

writes DIR/probe.json (counts, dates, shares of days, relative differences; no price), DIR/tiingo-fingerprint.json,
DIR/h15-3m-tbill.csv and DIR/manifest.json; prints counts and pass or fail only. Exit 0 when every acceptance check
passes; 3 a source failed; 4 an acceptance check failed; 5 a calendar difference no documented closure explains;
6 a split factor other than 1; 7 the Tiingo fingerprint differs from the committed one."""
import argparse
import bisect
import collections
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
import io

from bot import alpaca

UA = "stockbot index-program data probe"
TIINGO = "https://api.tiingo.com/tiingo/daily/{sym}/prices?startDate={start}&endDate={end}"
H15 = "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H15&filetype=zip"
H15_SERIES = "RIFSGFSM03_N.B"
H15_SOURCE = ("Board of Governors of the Federal Reserve System, H.15 Selected Interest Rates, series RIFSGFSM03_N.B "
              "(3-month Treasury bill, secondary market, discount basis, business days), Data Download Program")
FIRST, LAST = "1993-01-29", "2026-09-30"
TWO_FIRST = "2005-02-25"  # B3: the two-source period starts with the Stooq file
SINGLE_LAST = "2005-02-24"
P_FIRST = "2009-01-02"  # window P (section 3)
ALPACA_FIRST = "2016-01-04"
H15_FIRST = "1993-12-01"
PERIODS = (("2005-2008", "2005-02-25", "2008-12-31"), ("2009-2015", "2009-01-01", "2015-12-31"),
           ("2016-2026", "2016-01-01", LAST))
TOL_CHANGE, SHARE_CHANGE = 0.0005, 0.995  # A3.3: close-to-close change within 0.0005 on >= 99.5% of days
TOL_OC, SHARE_OC = 0.0015, 0.99  # open-to-close ratio within 0.0015 on >= 99% of days
REL_TOL = 1e-9  # A3.1 on adjusted prices
BIG_MOVE = 0.05  # B3: close-to-close changes above 5% in size, for information


class SplitFound(Exception):
    """B2: a split factor other than 1 stops the run."""


# ---------- downloads ----------
def fetch(url, headers=None, timeout=120):
    """-> (HTTP status or an error type name, body bytes). Never raises, never prints the URL or a header."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # noqa: BLE001 - the type only
        return type(e).__name__, b""


def tiingo(symbol, token, start=FIRST, end=LAST):
    """Tiingo end-of-day rows in memory -> (status, [(date, open, high, low, close, div, split, adj_open, adj_high,
    adj_low, adj_close)] ascending or None). The key travels only in the Authorization header."""
    status, body = fetch(TIINGO.format(sym=symbol, start=start, end=end),
                         headers={"Authorization": f"Token {token}", "Content-Type": "application/json"})
    if status != 200:
        return status, None
    try:
        js = json.loads(body)
        rows = [(x["date"][:10], float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]),
                 float(x["divCash"]), float(x["splitFactor"]), float(x["adjOpen"]), float(x["adjHigh"]),
                 float(x["adjLow"]), float(x["adjClose"])) for x in js]
    except (ValueError, KeyError, TypeError):
        return "unparsable", None
    return status, sorted(rows)


def alpaca_adjusted(symbol, start, end):
    """Alpaca SIP daily bars with adjustment "all" -> [(date, o, h, l, c)] ascending (information only)."""
    rows, token = [], None
    while True:
        q = {"symbols": symbol, "timeframe": "1Day", "start": start, "end": end, "feed": "sip", "adjustment": "all",
             "limit": alpaca.LIMIT, "sort": "asc", **({"page_token": token} if token else {})}
        js = alpaca._get(f"{alpaca.DATA}/bars", q)
        for b in (js.get("bars") or {}).get(symbol) or []:
            rows.append((alpaca._ny(b["t"]).date().isoformat(), b["o"], b["h"], b["l"], b["c"]))
        token = js.get("next_page_token")
        if not token:
            return rows


# ---------- parsers ----------
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


# ---------- B1 fingerprint, B2 adjustment ----------
def fingerprint(rows, start=FIRST, end=LAST):
    """B1: one sha256 per calendar year over the raw rows (date, open, high, low, close, cash dividend, split
    factor), each as "date,open,high,low,close,div,split\\n" with Python's repr of each float."""
    by = collections.defaultdict(hashlib.sha256)
    for r in rows:
        if start <= r[0] <= end:
            by[r[0][:4]].update((",".join([r[0], *(repr(float(v)) for v in r[1:7])]) + "\n").encode())
    return {y: h.hexdigest() for y, h in sorted(by.items())}


def fingerprint_diff(new, old):
    """The years whose hash differs, or that are in only one of the two."""
    return sorted(y for y in set(new) | set(old) if new.get(y) != old.get(y))


def adjust(rows, end=LAST, symbol=""):
    """B2: from raw prices and cash dividends up to `end`, every price before an ex-dividend day d with dividend D
    is multiplied by 1 - D / (raw close of the trading day before d) -> [(date, o, h, l, c)]. A split factor other
    than 1 stops the run."""
    rows = [r for r in rows if r[0] <= end]
    for r in rows:
        if r[6] != 1.0:
            raise SplitFound(f"{symbol} {r[0]}".strip())
    out, f = [], 1.0
    for k in range(len(rows) - 1, -1, -1):
        d, o, h, lo, c = rows[k][:5]
        out.append((d, o * f, h * f, lo * f, c * f))
        if rows[k][5] > 0 and k > 0:
            f *= 1 - rows[k][5] / rows[k - 1][4]
    return out[::-1]


# ---------- checks ----------
def integrity(rows, rel=0.0):
    """A3.1 on one series [(date, o, h, l, c)]: non-positive prices and days with open or close outside [low, high]
    (with a relative tolerance `rel`), and the order of the dates."""
    out_r = lambda x, lo, h: not (lo * (1 - rel) <= x <= h * (1 + rel))  # noqa: E731
    bad_range = [r[0] for r in rows if out_r(r[1], r[3], r[2]) or out_r(r[4], r[3], r[2])]
    dates = [r[0] for r in rows]
    return {"rows": len(rows), "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
            "non_positive": sum(1 for r in rows if min(r[1:5]) <= 0), "outside_range": len(bad_range),
            "outside_range_days": bad_range[:20], "unsorted_or_duplicate": sum(1 for a, b in zip(dates, dates[1:]) if a >= b)}


def dividends_per_year(rows, start=FIRST, end=LAST):
    """A3.1: cash dividends per calendar year, and the years whose count is not 4."""
    per = collections.Counter(r[0][:4] for r in rows if start <= r[0] <= end and r[5] > 0)
    years = sorted({r[0][:4] for r in rows if start <= r[0] <= end})
    counts = {y: per.get(y, 0) for y in years}
    return {"per_year": counts, "not_4": {y: n for y, n in counts.items() if n != 4}}


def day_diff(a_days, b_days, start, end):
    a = {d for d in a_days if start <= d <= end}
    b = {d for d in b_days if start <= d <= end}
    return {"a": len(a), "b": len(b), "only_a": sorted(a - b), "only_b": sorted(b - a)}


def _measures(a, b, start, end):
    """On the days both series have in [start, end]: (|change difference|, day) and (|open-to-close difference|,
    day). A price-agreement check: no return of any rule."""
    A = {r[0]: r for r in a if start <= r[0] <= end}
    B = {r[0]: r for r in b if start <= r[0] <= end}
    days = sorted(set(A) & set(B))
    ch = [(abs((A[d][4] / A[p][4]) - (B[d][4] / B[p][4])), d) for p, d in zip(days, days[1:])]
    oc = [(abs(A[d][1] / A[d][4] - B[d][1] / B[d][4]), d) for d in days]
    return ch, oc


def agreement(a, b, start, end, top=10):
    """A3.3's two measures over [start, end]: the share of days within each tolerance, whether both shares hold,
    and the `top` largest differences of each measure (date and relative difference)."""
    ch, oc = _measures(a, b, start, end)
    share = lambda ds, tol: sum(1 for d, _ in ds if d <= tol) / len(ds) if ds else 0.0  # noqa: E731
    out = {"days": len(oc), "change_within": share(ch, TOL_CHANGE), "oc_within": share(oc, TOL_OC),
           "largest_change": [[day, round(d, 6)] for d, day in sorted(ch, reverse=True)[:top]],
           "largest_oc": [[day, round(d, 6)] for d, day in sorted(oc, reverse=True)[:top]]}
    out["pass"] = out["change_within"] >= SHARE_CHANGE and out["oc_within"] >= SHARE_OC
    return out


def stooq_adjusted(stooq, raw, adj, ex_days):
    """Is the Stooq file dividend-adjusted? On Tiingo's ex-dividend days in the overlap: how many of Stooq's
    close-to-close changes agree (within 0.0005) with the raw series' change and how many with the B2-adjusted
    series' change."""
    S, R, A = ({r[0]: r[4] for r in x} for x in (stooq, raw, adj))
    days = sorted(S)
    hits = {"days": 0, "match_raw": 0, "match_adjusted": 0}
    for d in ex_days:
        k = bisect.bisect_left(days, d)
        if k == 0 or k >= len(days) or days[k] != d or days[k - 1] not in R:
            continue
        p = days[k - 1]
        s = S[d] / S[p]
        hits["days"] += 1
        hits["match_raw"] += abs(s - R[d] / R[p]) <= TOL_CHANGE
        hits["match_adjusted"] += abs(s - A[d] / A[p]) <= TOL_CHANGE
    hits["adjusted"] = hits["days"] > 0 and hits["match_adjusted"] > hits["days"] / 2 >= hits["match_raw"]
    return hits


# ---------- B3: the New York Stock Exchange calendar, 1993 to 2005 ----------
SPECIAL_CLOSURES = {
    "1994-04-27": "national day of mourning, President Nixon",
    "2001-09-11": "September 11 attacks", "2001-09-12": "September 11 attacks",
    "2001-09-13": "September 11 attacks", "2001-09-14": "September 11 attacks",
    "2004-06-11": "national day of mourning, President Reagan",
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
    month = (h + l_ - 7 * m + 114) // 31
    return dt.date(y, month, (h + l_ - 7 * m + 114) % 31 + 1)


def _nth(y, month, weekday, n):
    d = dt.date(y, month, 1)
    d += dt.timedelta((weekday - d.weekday()) % 7)
    return d + dt.timedelta(7 * (n - 1))


def _last(y, month, weekday):
    d = dt.date(y, month + 1, 1) - dt.timedelta(1)
    return d - dt.timedelta((d.weekday() - weekday) % 7)


def _observed(d, saturday_back=True):
    if d.weekday() == 5:
        return d - dt.timedelta(1) if saturday_back else None
    if d.weekday() == 6:
        return d + dt.timedelta(1)
    return d


def nyse_holidays(y):
    """The exchange's regular holidays of year `y` (1993-2005 rules) -> {date ISO: name}. New Year's Day on a
    Saturday is not moved (the exchange's rule); Martin Luther King Jr. Day from 1998."""
    out = {}
    for d, name in ((_observed(dt.date(y, 1, 1), saturday_back=False), "New Year's Day"),
                    (_nth(y, 2, 0, 3), "Washington's Birthday"), (_easter(y) - dt.timedelta(2), "Good Friday"),
                    (_last(y, 5, 0), "Memorial Day"), (_observed(dt.date(y, 7, 4)), "Independence Day"),
                    (_nth(y, 9, 0, 1), "Labor Day"), (_nth(y, 11, 3, 4), "Thanksgiving Day"),
                    (_observed(dt.date(y, 12, 25)), "Christmas Day")):
        if d is not None:
            out[d.isoformat()] = name
    if y >= 1998:
        out[_nth(y, 1, 0, 3).isoformat()] = "Martin Luther King Jr. Day"
    return out


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


def calendar_check(days, start=FIRST, end=SINGLE_LAST):
    """B3: the file's trading days against the exchange calendar -> the differences, each with its explanation
    (None when no documented closure explains it)."""
    want, closed = nyse_days(start, end)
    have = {d for d in days if start <= d <= end}
    extra = sorted(have - set(want))
    missing = sorted(set(want) - have)
    return {"days": len(have), "calendar_days": len(want),
            "in_file_not_calendar": [[d, closed.get(d)] for d in extra],
            "in_calendar_not_file": [[d, None] for d in missing],
            "unexplained": len(missing) + sum(1 for d in extra if d not in closed)}


def big_moves(adj, start=FIRST, end=SINGLE_LAST, size=BIG_MOVE):
    """B3, information: the days whose close-to-close change is above 5% in size (date and change)."""
    r = [x for x in adj if start <= x[0] <= end]
    return [[b[0], round(b[4] / a[4] - 1, 4)] for a, b in zip(r, r[1:]) if abs(b[4] / a[4] - 1) > size]


def open_equals_prev_close(raw, start=FIRST, end=SINGLE_LAST):
    """B3, information: per year, the share of days whose raw open equals the previous raw close."""
    r = [x for x in raw if start <= x[0] <= end]
    per = collections.defaultdict(lambda: [0, 0])
    for a, b in zip(r, r[1:]):
        per[b[0][:4]][0] += b[1] == a[4]
        per[b[0][:4]][1] += 1
    return {y: round(n / t, 4) for y, (n, t) in sorted(per.items())}


def sha256(body):
    return hashlib.sha256(body).hexdigest()


# ---------- the run ----------
def run(stooq_path, out_dir, committed_fp=None):
    """The probe -> (exit code, report). Writes only probe.json, tiingo-fingerprint.json, the H.15 file and the
    manifest into `out_dir`; Tiingo rows stay in memory."""
    os.makedirs(out_dir, exist_ok=True)
    rep, code = {"sources": {}, "checks": {}}, 0
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    token = os.environ.get("TIINGO_TOKEN", "")
    spy_s, spy = tiingo("SPY", token)
    bil_s, bil = tiingo("BIL", token, start="2007-05-30")
    rep["sources"]["tiingo_spy"] = {"status": spy_s, **(integrity([r[:5] for r in spy]) if spy else {})}
    rep["sources"]["tiingo_bil"] = {"status": bil_s, **(integrity([r[:5] for r in bil]) if bil else {})}
    with open(stooq_path, "rb") as fh:
        stooq_body = fh.read()
    stooq = parse_stooq(stooq_body)
    h15_s, h15_body = fetch(H15)
    h15 = parse_h15(h15_body) if h15_s == 200 else None
    rep["sources"]["stooq_spy"] = {"file": os.path.basename(stooq_path), "parsed": stooq is not None,
                                   **(integrity(stooq) if stooq else {})}
    rep["sources"]["h15"] = {"status": h15_s, "zip_sha256": sha256(h15_body) if h15_body else None}
    if not (spy and stooq and h15):
        return 3, rep
    fp = {"SPY": fingerprint(spy, FIRST, LAST), "BIL": fingerprint(bil, FIRST, LAST) if bil else {}}
    if committed_fp:
        diff = {s: fingerprint_diff(fp[s], committed_fp.get(s, {})) for s in fp}
        rep["fingerprint_diff"] = diff
        if any(diff.values()):
            code = 7
    try:
        adj = adjust(spy, LAST, "SPY")
        adj_bil = adjust(bil, LAST, "BIL") if bil else []
    except SplitFound as e:
        rep["split_found"] = str(e)
        return 6, rep
    raw = [r[:5] for r in spy]
    tiingo_adj = [(r[0], *r[7:11]) for r in spy]
    c = rep["checks"]
    c["a3_1"] = {"raw": integrity(raw), "adjusted_b2": integrity(adj, rel=REL_TOL),
                 "dividends": dividends_per_year(spy, FIRST, LAST)}
    c["a3_1"]["pass"] = (c["a3_1"]["raw"]["non_positive"] == 0 and c["a3_1"]["raw"]["outside_range"] == 0
                         and c["a3_1"]["adjusted_b2"]["non_positive"] == 0
                         and c["a3_1"]["adjusted_b2"]["outside_range"] == 0
                         and c["a3_1"]["raw"]["unsorted_or_duplicate"] == 0
                         and c["a3_1"]["raw"]["first"] == FIRST and c["a3_1"]["raw"]["last"] == LAST)
    ex_days = [r[0] for r in spy if r[5] > 0 and r[0] >= TWO_FIRST]
    det = stooq_adjusted(stooq, raw, adj, ex_days)
    like = adj if det["adjusted"] else raw
    two = {"stooq_dividend_adjusted": det,
           "days": day_diff([r[0] for r in spy], [r[0] for r in stooq], TWO_FIRST, LAST),
           "whole": agreement(like, stooq, TWO_FIRST, LAST), "window_p": agreement(like, stooq, P_FIRST, LAST),
           "periods": {n: agreement(like, stooq, a, b) for n, a, b in PERIODS}}
    two["pass"] = (not two["days"]["only_a"] and not two["days"]["only_b"] and two["whole"]["pass"]
                   and two["window_p"]["pass"])
    cal = calendar_check([r[0] for r in spy], FIRST, SINGLE_LAST)
    single = {"calendar": cal, "big_moves": big_moves(adj, FIRST, SINGLE_LAST),
              "open_equals_prev_close": open_equals_prev_close(raw, FIRST, SINGLE_LAST),
              "pass": cal["unexplained"] == 0}
    c["b3"] = {"two_source": two, "single_source": single, "pass": two["pass"] and single["pass"]}
    hs = [(d, v) for d, v in h15 if H15_FIRST <= d <= LAST]
    c["a3_4"] = {"rows": len(hs), "first": hs[0][0], "last": hs[-1][0], "missing": sum(1 for _, v in hs if v is None)}
    try:
        al = alpaca_adjusted("SPY", ALPACA_FIRST, LAST)
        c["a3_5"] = {"rows": len(al), "days": day_diff([r[0] for r in adj], [r[0] for r in al], ALPACA_FIRST, LAST),
                     "agreement": agreement(adj, al, ALPACA_FIRST, LAST)}
    except Exception as e:  # noqa: BLE001 - information only; the type only
        c["a3_5"] = {"status": type(e).__name__}
    c["b2_vs_tiingo_adjusted"] = agreement(adj, tiingo_adj, FIRST, LAST)
    c["bil"] = {"integrity_raw": integrity([r[:5] for r in bil]) if bil else None,
                "integrity_adjusted_b2": integrity(adj_bil, rel=REL_TOL) if bil else None}
    if code == 0 and not (c["a3_1"]["pass"] and c["b3"]["pass"]):
        code = 5 if not single["pass"] else 4
    h15_file = h15_csv(h15)
    files = {"tiingo-fingerprint.json": (json.dumps(fp, indent=1) + "\n").encode(), "h15-3m-tbill.csv": h15_file}
    manifest = {
        "h15-3m-tbill.csv": {"source": H15_SOURCE, "url": H15, "downloaded": now, "rows": len(hs),
                             "first": hs[0][0], "last": hs[-1][0], "sha256": sha256(h15_file),
                             "where": "public repository, data/index/"},
        "spy_us_d.csv": {"source": "Stooq daily CSV, downloaded by the owner in a browser", "rows": len(stooq),
                         "first": stooq[0][0], "last": stooq[-1][0], "sha256": sha256(stooq_body),
                         "where": "private repository danielsuisa/stockbot-data"},
        "tiingo SPY, BIL": {"source": "Tiingo end-of-day API (free plan, owner's key), held in memory only",
                            "downloaded": now, "rows": {"SPY": len(spy), "BIL": len(bil or [])},
                            "first": {"SPY": spy[0][0], "BIL": bil[0][0] if bil else None},
                            "last": {"SPY": spy[-1][0], "BIL": bil[-1][0] if bil else None},
                            "fingerprint": "tiingo-fingerprint.json", "where": "not stored (Amendment 2, B1)"}}
    files["manifest.json"] = (json.dumps(manifest, indent=1) + "\n").encode()
    for name, body in files.items():
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(body)
    return code, rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="Index program data probe (no returns)")
    ap.add_argument("--stooq", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fingerprint", default="", help="the committed Tiingo fingerprint (later runs)")
    a = ap.parse_args(argv)
    committed = None
    if a.fingerprint and os.path.exists(a.fingerprint):
        with open(a.fingerprint, encoding="utf-8") as fh:
            committed = json.load(fh)
    code, rep = run(a.stooq, a.out, committed)
    with open(os.path.join(a.out, "probe.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    for k, v in rep["sources"].items():
        print(f"{k}: status {v.get('status', '-')}, rows {v.get('rows', '-')}, {v.get('first', '-')} to {v.get('last', '-')}")
    for k, v in rep.get("checks", {}).items():
        if isinstance(v, dict) and "pass" in v:
            print(f"{k}: pass {v['pass']}")
    print(f"exit {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
