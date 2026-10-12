"""Index program, phase 1: the data probe (docs/superpowers/specs/2026-10-12-index-program-design.md sections 3 and
8.1). Sources, integrity and price agreement between sources only: nothing here computes a return, profit, equity
curve or drawdown of any rule or policy (standing rule 2).

Sources, in the brief's order: Stooq daily CSV (SPY, BIL), FRED DTB3, Alpaca adjusted daily bars (the repository's
client and secrets, adjustment "all"), Yahoo's unofficial chart endpoint (compared in memory, never written).

    python -m bot.index_probe OUT_DIR

writes OUT_DIR/probe.json (statuses, rows, first and last day, agreement numbers) and the files of the sources whose
data may be kept (Stooq, FRED); prints counts only."""
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

from bot import alpaca

UA = "Mozilla/5.0 (stockbot index-program data probe)"
STOOQ = "https://stooq.com/q/d/l/?s={sym}&i=d"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"
YAHOO = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&events=div%2Csplit"
         "&period1={p1}&period2={p2}")
FIRST, LAST = "1993-01-29", "2026-09-30"
ALPACA_FIRST = "2016-01-04"
SECOND_LAST = "2015-12-31"
DTB3_FIRST = "1993-12-01"
TOL_CHANGE, SHARE_CHANGE = 0.0005, 0.995  # spec 8.1: close-to-close change within 0.0005 on >= 99.5% of days
TOL_OC, SHARE_OC = 0.0015, 0.99  # open-to-close ratio within 0.0015 on >= 99% of days


def fetch(url, timeout=90):
    """-> (HTTP status or an error name, content type, body bytes)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", b""
    except Exception as e:  # noqa: BLE001 - the probe reports the failure's type, never a value
        return type(e).__name__, "", b""


# ---------- parsers ----------
def parse_ohlc_csv(body):
    """A daily CSV with Date, Open, High, Low, Close columns (Stooq) -> [(date, o, h, l, c)] ascending, or None
    when the body is not such a CSV (an HTML page, a key request)."""
    text = body.decode("utf-8", "replace").strip()
    lines = text.splitlines()
    if not lines or not lines[0].lower().startswith("date,"):
        return None
    head = [h.strip().lower() for h in lines[0].split(",")]
    try:
        i = [head.index(k) for k in ("date", "open", "high", "low", "close")]
    except ValueError:
        return None
    out = []
    for line in lines[1:]:
        f = line.split(",")
        if len(f) < len(head):
            continue
        out.append((f[i[0]], *(float(f[k]) for k in i[1:])))
    return sorted(out)


def parse_fred(body):
    """FRED's graph CSV -> [(date, value or None)] ascending (None for FRED's "." or an empty field), or None."""
    lines = body.decode("utf-8", "replace").strip().splitlines()
    if not lines or "DTB3" not in lines[0]:
        return None
    out = []
    for line in lines[1:]:
        d, _, v = line.partition(",")
        v = v.strip()
        out.append((d.strip(), float(v) if v not in ("", ".") else None))
    return sorted(out)


def parse_yahoo(body):
    """Yahoo's chart JSON -> ([(date, o, h, l, c)] dividend-adjusted by adjclose / close, {date: dividend}), or None.
    Held in memory only."""
    try:
        res = json.loads(body)["chart"]["result"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    q = res["indicators"]["quote"][0]
    adj = res["indicators"]["adjclose"][0]["adjclose"]
    out = []
    for k, ts in enumerate(res["timestamp"]):
        o, h, lo, c, a = q["open"][k], q["high"][k], q["low"][k], q["close"][k], adj[k]
        if None in (o, h, lo, c, a) or c == 0:
            continue
        day = (dt.datetime.fromtimestamp(ts, dt.timezone.utc) - dt.timedelta(hours=5)).date().isoformat()
        f = a / c
        out.append((day, o * f, h * f, lo * f, a))
    divs = {(dt.datetime.fromtimestamp(int(t), dt.timezone.utc) - dt.timedelta(hours=5)).date().isoformat():
            v["amount"] for t, v in (res.get("events", {}).get("dividends") or {}).items()}
    return sorted(out), divs


def alpaca_adjusted(symbol, start, end):
    """Alpaca SIP daily bars with adjustment "all" (splits and dividends) -> [(date, o, h, l, c)] ascending."""
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


# ---------- checks ----------
def integrity(rows):
    """Spec 8.1 check 1 (one file): non-positive prices and days where open or close is outside [low, high]."""
    bad_price = sum(1 for _, o, h, lo, c in rows if min(o, h, lo, c) <= 0)
    bad_range = sum(1 for _, o, h, lo, c in rows if not (lo <= o <= h and lo <= c <= h))
    dates = [r[0] for r in rows]
    return {"rows": len(rows), "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
            "non_positive": bad_price, "outside_range": bad_range,
            "duplicate_or_unsorted": sum(1 for a, b in zip(dates, dates[1:]) if a >= b)}


def _window(rows, start, end):
    return [r for r in rows if start <= r[0] <= end]


def agreement(a, b, start, end, top=5):
    """Spec 8.1 checks 1-3 between two sources over [start, end]: the same trading days; the close-to-close change
    within 0.0005 and the same day's open-to-close ratio within 0.0015, as shares of the compared days, with the
    largest differences and their dates. A price-agreement check: no return of any rule."""
    A, B = {r[0]: r for r in _window(a, start, end)}, {r[0]: r for r in _window(b, start, end)}
    days = sorted(set(A) & set(B))
    d_change, d_oc = [], []
    for prev, day in zip(days, days[1:]):
        ca = A[day][4] / A[prev][4] - 1
        cb = B[day][4] / B[prev][4] - 1
        d_change.append((abs(ca - cb), day))
    for day in days:
        d_oc.append((abs(A[day][1] / A[day][4] - B[day][1] / B[day][4]), day))
    within = lambda ds, tol: sum(1 for d, _ in ds if d <= tol) / len(ds) if ds else 0.0  # noqa: E731
    out = {"days_a": len(A), "days_b": len(B), "common": len(days),
           "only_a": sorted(set(A) - set(B))[:20], "only_b": sorted(set(B) - set(A))[:20],
           "n_only_a": len(set(A) - set(B)), "n_only_b": len(set(B) - set(A)),
           "change_within": within(d_change, TOL_CHANGE), "oc_within": within(d_oc, TOL_OC),
           "largest_change_diff": [(round(d, 6), day) for d, day in sorted(d_change, reverse=True)[:top]],
           "largest_oc_diff": [(round(d, 6), day) for d, day in sorted(d_oc, reverse=True)[:top]]}
    out["pass"] = (not out["n_only_a"] and not out["n_only_b"] and out["change_within"] >= SHARE_CHANGE
                   and out["oc_within"] >= SHARE_OC)
    return out


def dtb3_missing(rows, start=DTB3_FIRST, end=LAST):
    """DTB3's missing values (FRED's ".") over [start, end]: count and the first 20 dates."""
    miss = [d for d, v in rows if start <= d <= end and v is None]
    have = [d for d, v in rows if start <= d <= end]
    return {"rows": len(have), "first": have[0] if have else None, "last": have[-1] if have else None,
            "missing": len(miss), "missing_first": miss[:20]}


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def _epoch(day):
    return int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp())


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    out_dir = argv[0] if argv else "index-probe-out"
    os.makedirs(out_dir, exist_ok=True)
    rep, series = {"sources": {}}, {}
    for sym in ("spy", "bil"):
        status, ctype, body = fetch(STOOQ.format(sym=f"{sym}.us"))
        rows = parse_ohlc_csv(body) if body else None
        rep["sources"][f"stooq_{sym}"] = {"status": status, "content_type": ctype, "bytes": len(body),
                                           "csv": rows is not None}
        if rows:
            name = f"stooq_{sym}.csv"
            with open(os.path.join(out_dir, name), "wb") as fh:
                fh.write(body)
            rep["sources"][f"stooq_{sym}"].update(integrity(rows), file=name, sha256=sha256(body))
            series[f"stooq_{sym}"] = rows
    status, ctype, body = fetch(FRED)
    rows = parse_fred(body) if body else None
    rep["sources"]["fred_dtb3"] = {"status": status, "content_type": ctype, "bytes": len(body), "csv": rows is not None}
    if rows:
        with open(os.path.join(out_dir, "fred_dtb3.csv"), "wb") as fh:
            fh.write(body)
        rep["sources"]["fred_dtb3"].update(dtb3_missing(rows), file="fred_dtb3.csv", sha256=sha256(body))
    for sym in ("SPY", "BIL"):
        status, ctype, body = fetch(YAHOO.format(sym=sym, p1=_epoch("1993-01-01"), p2=_epoch("2026-10-01")))
        got = parse_yahoo(body) if body else None
        rep["sources"][f"yahoo_{sym.lower()}"] = {"status": status, "content_type": ctype, "bytes": len(body),
                                                  "parsed": got is not None, "stored": False}
        if got:
            rep["sources"][f"yahoo_{sym.lower()}"].update(integrity(got[0]), dividends=len(got[1]))
            series[f"yahoo_{sym.lower()}"] = got[0]
    try:
        rows = alpaca_adjusted("SPY", ALPACA_FIRST, LAST)
        rep["sources"]["alpaca_spy"] = {"status": 200, **integrity(rows), "stored": False}
        series["alpaca_spy"] = rows
    except Exception as e:  # noqa: BLE001
        rep["sources"]["alpaca_spy"] = {"status": type(e).__name__}
    cmp = rep["agreement"] = {}
    for full in ("stooq_spy", "yahoo_spy"):
        if full in series and "alpaca_spy" in series:
            cmp[f"{full}_vs_alpaca_2016_2026"] = agreement(series[full], series["alpaca_spy"], ALPACA_FIRST, LAST)
    if "stooq_spy" in series and "yahoo_spy" in series:
        cmp["stooq_spy_vs_yahoo_spy_1993_2015"] = agreement(series["stooq_spy"], series["yahoo_spy"], FIRST,
                                                             SECOND_LAST)
    with open(os.path.join(out_dir, "probe.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    for k, v in rep["sources"].items():
        print(f"{k}: status {v.get('status')}, rows {v.get('rows', '-')}, {v.get('first', '-')} to {v.get('last', '-')}")
    for k, v in cmp.items():
        print(f"{k}: common {v['common']}, change within {v['change_within']:.4%}, oc within {v['oc_within']:.4%}, "
              f"pass {v['pass']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
