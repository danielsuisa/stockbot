"""STEP 0 probe for a swing ticket on insider-purchase clusters (spec section 15, docs/backtest/insider-step0-*.md).

    python -m bot.probe_insider        # prints a JSON report (and appends it to the Actions job summary)

BLIND RULE: no mean, median, win rate or any signed statistic of returns is computed, printed, logged or saved, for
any subset. Only counts, dispersion (SD, clustered SD, design effect, the 99th percentile of |r|) and costs.

a) SEC's Insider Transactions Data Sets (quarterly Form 3/4/5 tables): coverage and the fields bot/form4.py needs.
b) Cluster events 2016-01-04 -> latest, as close to the live rule as the data allows.
c) Alpaca daily-bar coverage of the events' entry sessions (symbol as filed).
d) Dispersion of 21- and 63-session returns net of SPY, clustered by entry month.
e) Costs on a fixed-seed sample of 200 covered events: quoted spread at 09:35, 20-day average dollar volume.
f) Request counts and runtime."""
import collections
import csv
import datetime as dt
import io
import json
import math
import random
import re
import time
import zipfile

from bot import alpaca, common, form4

DATASET = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{y}q{q}_form345.zip"
START = "2016-01-04"
WINDOW, MIN_PEOPLE, MIN_USD, GAP_SESSIONS = 30, 3, 100_000.0, 21  # the live cluster rule (bot/scan.py)
HORIZONS = (21, 63)
SAMPLE, SEED = 200, 7
CACHE = common.ROOT / ".cache" / "sec345"
SEC_REQUESTS = [0]


# ---------- a) the data set ----------
def quarter_zip(y, q):
    """One quarter's zip (cached on disk) -> ZipFile, or None when SEC has not published it."""
    path = CACHE / f"{y}q{q}_form345.zip"
    if not path.exists():
        SEC_REQUESTS[0] += 1
        body = common.fetch(DATASET.format(y=y, q=q), tries=3)
        if not body or not body.startswith(b"PK"):
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return zipfile.ZipFile(path)


def table(z, name):
    """Rows of one TSV table in the zip (dicts), whatever the member's case or folder."""
    member = next((n for n in z.namelist() if n.upper().endswith(name + ".TSV")), None)
    if member is None:
        return None, []
    f = io.TextIOWrapper(z.open(member), "utf-8", errors="replace", newline="")
    r = csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
    return r.fieldnames, r


def day(s):
    s = (s or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s[:11] if fmt == "%d-%b-%Y" else s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def num(s):
    try:
        return float((s or "").replace(",", ""))
    except ValueError:
        return None


def read_quarter(z, schema):
    """One quarter -> per-accession purchase filings: {acc: {"filed", "form", "issuer", "symbol", "owners", "lines",
    "box", "notes"}} for filings with at least one code-P acquisition of non-derivative stock."""
    cols, rows = table(z, "NONDERIV_TRANS")
    schema.setdefault("NONDERIV_TRANS", cols)
    lines = collections.defaultdict(list)
    for r in rows:
        if r.get("TRANS_CODE") != "P" or r.get("TRANS_ACQUIRED_DISP_CD") != "A":
            continue
        refs = set()
        for k, v in r.items():
            if k and k.endswith("_FN") and v:
                refs |= {x.strip() for x in re.split(r"[,; ]+", v) if x.strip()}
        sh, px = num(r.get("TRANS_SHARES")), num(r.get("TRANS_PRICEPERSHARE"))
        lines[r["ACCESSION_NUMBER"]].append({"date": day(r.get("TRANS_DATE")), "shares": sh, "price": px,
                                             "value": sh * px if sh and px else None, "refs": refs})
    out = {}
    cols, rows = table(z, "SUBMISSION")
    schema.setdefault("SUBMISSION", cols)
    for r in rows:
        acc = r["ACCESSION_NUMBER"]
        if acc in lines:
            out[acc] = {"filed": day(r.get("FILING_DATE")), "form": r.get("DOCUMENT_TYPE"), "issuer": r.get("ISSUERCIK"),
                        "symbol": (r.get("ISSUERTRADINGSYMBOL") or "").strip().upper(), "owners": [],
                        "lines": lines[acc], "box": (r.get("AFF10B5ONE") or "").strip().lower() in ("1", "true"),
                        "notes": {}}
    cols, rows = table(z, "REPORTINGOWNER")
    schema.setdefault("REPORTINGOWNER", cols)
    for r in rows:
        f = out.get(r["ACCESSION_NUMBER"])
        if f is not None:
            rel = (r.get("RPTOWNER_RELATIONSHIP") or "").lower()
            f["owners"].append({"cik": r.get("RPTOWNERCIK"), "od": "director" in rel or "officer" in rel})
    cols, rows = table(z, "FOOTNOTES")
    schema.setdefault("FOOTNOTES", cols)
    for r in rows:
        f = out.get(r["ACCESSION_NUMBER"])
        if f is not None:
            f["notes"][r.get("FOOTNOTE_ID")] = r.get("FOOTNOTE_TXT") or ""
    return out


def classify(f):
    """bot/form4.py's rules on a data-set filing: plan lines (a referenced footnote mentions Rule 10b5-1 and does not
    deny it, or the 10b5-1 box with no plan footnote) -> (open-market value, last trade date, first od owner CIK)."""
    plan_notes = {k for k, v in f["notes"].items() if form4.PLAN.search(v) and not form4.NOT_PLAN.search(v)}
    op = [x for x in f["lines"] if not (bool(x["refs"] & plan_notes) or (f["box"] and not plan_notes))]
    who = next((o["cik"] for o in f["owners"] if o["od"]), None)
    value = sum(x["value"] or 0 for x in op)
    dates = [x["date"] for x in op if x["date"]]
    return value, (max(dates) if dates else None), who, len(f["lines"]) - len(op)


# ---------- b) events ----------
def events(filings, sessions):
    """The live cluster rule on filing dates: at filing date F, the issuer's open-market purchase filings filed on or
    before F with a last trade date within WINDOW days before F, by officers/directors: >= MIN_PEOPLE distinct
    people and >= MIN_USD -> an event at the first such F; none again for the issuer within GAP_SESSIONS sessions."""
    by = collections.defaultdict(list)
    for acc, f in filings.items():
        value, last, who, _ = classify(f)
        if f["filed"] and last and who and value > 0 and f["form"] == "4":  # 4/A amendments are not merged here
            by[f["issuer"]].append((f["filed"], last, who, value, f["symbol"]))
    out = []
    for issuer, rows in by.items():
        rows.sort()
        blocked_until = ""
        for F in sorted({r[0] for r in rows}):
            if F < START or F <= blocked_until:
                continue
            since = (dt.date.fromisoformat(F) - dt.timedelta(WINDOW)).isoformat()
            live = [r for r in rows if r[0] <= F and r[1] >= since]
            if len({r[2] for r in live}) >= MIN_PEOPLE and sum(r[3] for r in live) >= MIN_USD:
                sym = max(live)[4]  # the symbol on the latest qualifying filing
                k = next((i for i, s in enumerate(sessions) if s > F), None)
                out.append({"issuer": issuer, "filed": F, "symbol": sym, "entry": sessions[k] if k is not None else None,
                            "k": k})
                if k is not None:
                    blocked_until = sessions[min(k - 1 + GAP_SESSIONS, len(sessions) - 1)]
    return sorted(out, key=lambda e: e["filed"])


# ---------- c-e) prices ----------
def split_daily(symbols, start, end):
    """Split-adjusted daily bars (returns only) -> {symbol: {date: (open, close, volume)}}, cached per chunk."""
    out = {}
    for i in range(0, len(symbols), alpaca.CHUNK):
        part = symbols[i:i + alpaca.CHUNK]
        path = alpaca.CACHE / "1d_split" / f"{start}_{end}_{i}.json.gz"
        got = alpaca._read(path)
        if got is None:
            got, token = {}, None
            while True:
                q = {"symbols": ",".join(part), "timeframe": "1Day", "start": start, "end": end, "feed": "sip",
                     "adjustment": "split", "limit": alpaca.LIMIT, **({"page_token": token} if token else {})}
                js = alpaca._get(f"{alpaca.DATA}/bars", q)
                for s, items in (js.get("bars") or {}).items():
                    got.setdefault(s, []).extend([b["t"][:10], b["o"], b["c"], b["v"]] for b in items or [])
                token = js.get("next_page_token")
                if not token:
                    break
            alpaca._write(path, got)
        for s, rows in got.items():
            out[s] = {d: (o, c, v) for d, o, c, v in rows}
    return out


def dispersion(rows):
    """[(entry month, r)] -> count, SD, SD clustered by month (SE x sqrt(n)), design effect, 99th pct of |r|. The
    mean is used inside the formulas only and never returned."""
    n = len(rows)
    if n < 3:
        return {"n": n}
    xs = [r for _, r in rows]
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    g = collections.defaultdict(float)
    for month, r in rows:
        g[month] += r - m
    G = len(g)
    se = math.sqrt(G / (G - 1) * sum(v * v for v in g.values())) / n if G > 1 else float("nan")
    sd_c = se * math.sqrt(n)
    a = sorted(abs(x) for x in xs)
    return {"n": n, "months": G, "sd": round(sd, 4), "sd_clustered_by_month": round(sd_c, 4),
            "design_effect": round((sd_c / sd) ** 2, 2) if sd else None, "p99_abs": round(a[int(0.99 * (n - 1))], 4)}


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1)))] if xs else None


def main():
    t0 = time.time()
    out, schema, filings, quarters = {}, {}, {}, []
    today = dt.date.today()
    for y in range(2015, today.year + 1):
        for q in range(1, 5):
            if (y, q) > (today.year, (today.month - 1) // 3 + 1):
                break
            z = quarter_zip(y, q)
            if z is None:
                continue
            quarters.append(f"{y}Q{q}")
            if y == 2015 and q < 4:
                continue  # only the 30-day window before 2016-01-04 is needed from 2015
            filings.update(read_quarter(z, schema))
            print(f"{y}Q{q}: {len(filings)} purchase filings so far ({time.time() - t0:.0f}s)", flush=True)
    out["a_quarters"] = {"first": quarters[0] if quarters else None, "last": quarters[-1] if quarters else None,
                         "count": len(quarters), "missing_since_2016": [
                             f"{y}Q{q}" for y in range(2016, today.year + 1) for q in range(1, 5)
                             if (y, q) <= (today.year, (today.month - 1) // 3 + 1) and f"{y}Q{q}" not in quarters]}
    out["a_schema"] = schema
    need = {"filing date": ("SUBMISSION", "FILING_DATE"), "document type": ("SUBMISSION", "DOCUMENT_TYPE"),
            "issuer symbol": ("SUBMISSION", "ISSUERTRADINGSYMBOL"), "10b5-1 box": ("SUBMISSION", "AFF10B5ONE"),
            "transaction code": ("NONDERIV_TRANS", "TRANS_CODE"),
            "acquired/disposed": ("NONDERIV_TRANS", "TRANS_ACQUIRED_DISP_CD"),
            "shares": ("NONDERIV_TRANS", "TRANS_SHARES"), "price": ("NONDERIV_TRANS", "TRANS_PRICEPERSHARE"),
            "transaction date": ("NONDERIV_TRANS", "TRANS_DATE"),
            "relationship": ("REPORTINGOWNER", "RPTOWNER_RELATIONSHIP"), "title": ("REPORTINGOWNER", "RPTOWNER_TITLE"),
            "footnote text": ("FOOTNOTES", "FOOTNOTE_TXT")}
    out["a_fields"] = {k: c in (schema.get(t) or []) for k, (t, c) in need.items()}
    out["a_purchase_filings"] = len(filings)
    out["a_plan_lines_excluded"] = sum(classify(f)[3] for f in filings.values())
    out["a_box_filings"] = sum(1 for f in filings.values() if f["box"])
    latest = max((f["filed"] for f in filings.values() if f["filed"]), default=START)
    cal = alpaca.calendar("2015-12-01", (dt.date.fromisoformat(latest) + dt.timedelta(120)).isoformat())
    sessions = sorted(d for d in cal if d <= today.isoformat())
    ev = events(filings, sessions)
    out["b_events_per_year"] = dict(sorted(collections.Counter(e["filed"][:4] for e in ev).items()))
    out["b_events"], out["b_latest_filing"] = len(ev), latest
    symbols = sorted({e["symbol"] for e in ev if re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", e["symbol"] or "")})
    out["c_symbols_queried"] = len(symbols)
    bars = split_daily(symbols + ["SPY"], "2015-12-01", today.isoformat())
    spy = bars.get("SPY", {})
    cov = collections.defaultdict(lambda: [0, 0, 0])  # year -> [events, covered, missing]
    covered = []
    for e in ev:
        y = e["filed"][:4]
        cov[y][0] += 1
        if e["entry"] and e["entry"] in bars.get(e["symbol"], {}):
            cov[y][1] += 1
            covered.append(e)
        else:
            cov[y][2] += 1
    out["c_coverage_per_year"] = {y: {"events": a, "covered": c, "share": round(c / a, 3) if a else None,
                                      "missing": m} for y, (a, c, m) in sorted(cov.items())}
    out["c_missing_no_entry_session_yet"] = sum(1 for e in ev if not e["entry"])
    for h in HORIZONS:
        rows, lost = [], 0
        for e in covered:
            k = e["k"]
            if k + h >= len(sessions):
                lost += 1
                continue
            d0, dh = sessions[k], sessions[k + h]
            b, s = bars[e["symbol"]], spy
            if dh not in b or d0 not in s or dh not in s or not b[d0][0] or not s[d0][0]:
                lost += 1
                continue
            rows.append((d0[:7], (b[dh][1] / b[d0][0]) - (s[dh][1] / s[d0][0])))
        out[f"d_{h}_sessions"] = {**dispersion(rows), "no_bar_at_horizon_or_not_reached": lost}
    rng = random.Random(SEED)
    sample = rng.sample(covered, min(SAMPLE, len(covered)))
    spreads, dvols, no_quote = [], [], 0
    for e in sample:
        q = alpaca.quote_at(e["symbol"], alpaca.utc(e["entry"], "09:35"))
        if q:
            mid = (q[0] + q[1]) / 2
            spreads.append((q[1] - q[0]) / mid * 100)
        else:
            no_quote += 1
        prior = [v for d, v in sorted(bars[e["symbol"]].items()) if d < e["entry"]][-20:]
        if len(prior) == 20:
            dvols.append(sum(c * v for _, c, v in prior) / 20)
    out["e_costs"] = {"sample": len(sample), "no_quote_at_0935": no_quote,
                      "spread_pct_of_mid": {"median": pct(spreads, 0.5), "p75": pct(spreads, 0.75),
                                            "p90": pct(spreads, 0.9)},
                      "avg_dollar_volume_20d": {"n": len(dvols), "median": pct(dvols, 0.5), "p10": pct(dvols, 0.1)}}
    out["f_requests"] = {"sec": SEC_REQUESTS[0], "alpaca": alpaca.REQUESTS[0], "runtime_s": round(time.time() - t0)}
    text = json.dumps(out, indent=1, default=str)
    print(text)
    common.summary("### Insider STEP 0 probe\n\n```json\n" + text + "\n```")


if __name__ == "__main__":
    main()
