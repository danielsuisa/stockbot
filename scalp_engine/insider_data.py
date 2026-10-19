"""I1 SEC ingestion (ADR 0009): Insider Transactions Data Sets 2022 Q1 .. 2026 Q3, SEC header acceptance times,
EDGAR submissions (current SIC, annual-report form, acceptance cross-check sample) and, for the journal parity only,
EDGAR ownership XML of 2026-10-01 .. 2026-10-16.

Same mechanics as P1/P2: append-only Parquet, a manifest record per request, check_range before every request,
resumable from the manifest. SEC fair access: 8 requests/s with SEC_USER_AGENT (proxies.Http). No outcome data.

    python -m scalp_engine.insider_data STEP
    STEP: datasets | headers | submissions | october | budget
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import io
import json
import random
import re
import sys
import xml.etree.ElementTree as ET_XML
import zipfile
from pathlib import Path

import polars as pl

from scalp_engine import envload, storage
from scalp_engine.alpaca_client import RateLimiter
from scalp_engine.data_access import ET, check_range, day_bounds, read_frame
from scalp_engine.proxies import Http, json_acceptance_utc

UTC = dt.timezone.utc
SOURCE = "sec"
FIRST_DAY = dt.date(2022, 1, 3)      # I1 requests nothing before this (ADR 0009 section 1)
SEALED_END = dt.datetime(2022, 1, 1, 5, tzinfo=UTC)  # end of legacy_2016_2021 (2021-12-31 ET inclusive)
LAST_QUARTER = (2026, 3)
PARITY_DAYS = (dt.date(2026, 10, 1), dt.date(2026, 10, 16))
INDEX_PAGE = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
HEADER_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/{acc}-index-headers.html"
DAILY_INDEX = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{d:%Y%m%d}.idx"
ARCHIVE = "https://www.sec.gov/Archives/"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_PER_MINUTE = 480                 # 8/s (fair access limit 10/s)
CHECK_SAMPLE, CHECK_SEED = 500, "i1-accept-check-v1"
_ACCEPT = re.compile(r"ACCEPTANCE-DATETIME(?:>|&gt;)\s*(\d{14})")
_TENB5 = re.compile(r"(?i)10b5")
_US = "\x1f"


# ---------------------------------------------------------------- small helpers

def log(msg):
    print(f"{dt.datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ} {msg}", flush=True)


def quarters(first=(2022, 1), last=LAST_QUARTER):
    y, q = first
    while (y, q) <= last:
        yield y, q
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)


def quarter_range(y, q):
    """Declared request range of a quarter's data set: [max(first day of quarter, 2022-01-03), next quarter)."""
    lo = max(dt.date(y, 3 * q - 2, 1), FIRST_DAY)
    hi = dt.date(y + 1, 1, 1) if q == 4 else dt.date(y, 3 * q + 1, 1)
    return day_bounds(lo)[0], day_bounds(hi)[0]


def sec_date(s):
    """'31-JAN-2024' or '2024-01-31' -> date; '' -> None."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s[:11] if fmt == "%d-%b-%Y" else s[:10], fmt).date()
        except ValueError:
            pass
    return None


def num(s):
    try:
        return float((s or "").replace(",", ""))
    except ValueError:
        return None


def cik_str(s):
    s = (s or "").strip().lstrip("0")
    return s or None


def fn_ids(values):
    out = set()
    for v in values:
        out |= {x.strip() for x in (v or "").split(",") if x.strip()}
    return sorted(out)


def parse_acceptance(text):
    """SEC header text -> (raw 'YYYYMMDDHHMMSS', aware UTC datetime) or (None, None). The value is Eastern wall time,
    converted with the tz database (ADR 0009 section 1)."""
    m = _ACCEPT.search(text or "")
    if not m:
        return None, None
    raw = m.group(1)
    wall = dt.datetime.strptime(raw, "%Y%m%d%H%M%S")
    return raw, dt.datetime.combine(wall.date(), wall.time(), ET).astimezone(UTC)


def midnight_et(day):
    return day_bounds(day)[0]


def sec_http():
    envload.require("SEC_USER_AGENT")
    http = Http()
    for host in ("www.sec.gov", "data.sec.gov"):
        http.limits[host] = RateLimiter(SEC_PER_MINUTE)
    return http


# ---------------------------------------------------------------- data sets

def dataset_urls(http):
    body, _ = http.get(INDEX_PAGE, table="insider_filings", declared_range=(midnight_et(FIRST_DAY), midnight_et(PARITY_DAYS[1])))
    html = (body or b"").decode("utf-8", "replace")
    out = {}
    for href in re.findall(r'href="([^"]+?(\d{4})q(\d)_form345\.zip)"', html):
        url, y, q = href[0], int(href[1]), int(href[2])
        out[(y, q)] = url if url.startswith("http") else "https://www.sec.gov" + url
    return out


class _Tsv:
    """Rows of a data-set member as dicts. The SEC files are tab-delimited UTF-8 with no quoting (readme section 4),
    so a plain split is exact; this parses a downloaded vendor payload, it does not read the store."""

    def __init__(self, z, name):
        self.f = io.TextIOWrapper(z.open(name), encoding="utf-8", errors="replace", newline="")
        self.fieldnames = self.f.readline().rstrip("\r\n").split("\t")

    def __iter__(self):
        n = len(self.fieldnames)
        for line in self.f:
            line = line.rstrip("\r\n")
            if not line:
                continue
            vals = line.split("\t")
            yield dict(zip(self.fieldnames, vals[:n] + [""] * (n - len(vals))))


def _tsv(z, name):
    return _Tsv(z, name)


def parse_dataset(zbytes, quarter, got, rid):
    """Quarter zip -> (filings rows, lines rows, stats). Filings: every Form 3/4/5 submission x reporting owner.
    Lines: Table I lines of Forms 4 and 4/A with transaction date >= 2022-01-03 (earlier ones dropped, counted)."""
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    subs = {}
    for x in _tsv(z, "SUBMISSION.tsv"):
        subs[x["ACCESSION_NUMBER"]] = x
    notes = {}
    for x in _tsv(z, "FOOTNOTES.tsv"):
        if _TENB5.search(x.get("FOOTNOTE_TXT") or ""):
            notes.setdefault(x["ACCESSION_NUMBER"], []).append(f"{x['FOOTNOTE_ID']}{_US}{x['FOOTNOTE_TXT']}")
    stats = {"submissions": len(subs), "lines_dropped_pre_2022": 0, "lines": 0, "filings_rows": 0}
    filings, seq = [], {}
    for x in _tsv(z, "REPORTINGOWNER.tsv"):
        acc = x["ACCESSION_NUMBER"]
        s = subs.get(acc)
        if s is None:
            continue
        fd = sec_date(s["FILING_DATE"])
        n = seq.get(acc, 0)
        seq[acc] = n + 1
        filings.append({
            "event_at": midnight_et(fd), "accession": acc, "filing_date": fd, "document_type": s["DOCUMENT_TYPE"].strip(),
            "period_of_report": sec_date(s.get("PERIOD_OF_REPORT")), "date_of_orig_sub": sec_date(s.get("DATE_OF_ORIG_SUB")),
            "issuer_cik": cik_str(s["ISSUERCIK"]) or "0", "issuer_name": s.get("ISSUERNAME") or None,
            "issuer_symbol": (s.get("ISSUERTRADINGSYMBOL") or "").strip() or None,
            "aff10b5one": (s.get("AFF10B5ONE") or "").strip() or None, "notes_10b5": sorted(notes.get(acc, [])) or None,
            "owner_seq": n, "owner_cik": cik_str(x["RPTOWNERCIK"]), "owner_name": x.get("RPTOWNERNAME") or None,
            "relationship": (x.get("RPTOWNER_RELATIONSHIP") or "").strip() or None,
            "owner_title": (x.get("RPTOWNER_TITLE") or "").strip() or None, "dataset_quarter": quarter,
            "feed": "none", "request_id": rid, "ingested_at": got, "source": SOURCE, "schema_version": 1})
    lines = []
    rdr = _tsv(z, "NONDERIV_TRANS.tsv")
    fcols = [c for c in rdr.fieldnames if c.endswith("_FN")]
    for x in rdr:
        acc = x["ACCESSION_NUMBER"]
        s = subs.get(acc)
        if s is None or s["DOCUMENT_TYPE"].strip() not in ("4", "4/A"):
            continue
        td = sec_date(x.get("TRANS_DATE"))
        if td is None or td < FIRST_DAY:
            stats["lines_dropped_pre_2022"] += 1
            continue
        fd = sec_date(s["FILING_DATE"])
        lines.append({
            "event_at": midnight_et(fd), "accession": acc, "line_seq": int(x["NONDERIV_TRANS_SK"]), "trans_date": td,
            "trans_code": (x.get("TRANS_CODE") or "").strip() or None,
            "acquired_disposed": (x.get("TRANS_ACQUIRED_DISP_CD") or "").strip() or None,
            "shares": num(x.get("TRANS_SHARES")), "price": num(x.get("TRANS_PRICEPERSHARE")),
            "shares_after": num(x.get("SHRS_OWND_FOLWNG_TRANS")),
            "direct_indirect": (x.get("DIRECT_INDIRECT_OWNERSHIP") or "").strip() or None,
            "nature": (x.get("NATURE_OF_OWNERSHIP") or "").strip() or None,
            "fn_ids": fn_ids(x.get(c) for c in fcols) or None,
            "feed": "none", "request_id": rid, "ingested_at": got, "source": SOURCE, "schema_version": 1})
    stats["lines"], stats["filings_rows"] = len(lines), len(filings)
    return filings, lines, stats


def _write_by_day(table, rows, part, day_col="filing_date"):
    if not rows:
        return 0
    df = storage.frame(table, rows) if isinstance(rows, list) else rows
    done = storage.completed(table, part=part)
    d = pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date()
    n = 0
    for (day,), g in df.with_columns(_d=d).partition_by("_d", as_dict=True, maintain_order=True).items():
        if storage.partition_key(table, source=SOURCE, date=day) in done:
            continue
        storage.write_partition(table, g.drop("_d"), part=part, request_ids=g["request_id"].unique().to_list(),
                                source=SOURCE, date=day)
        n += g.height
    return n


def ingest_datasets(http):
    cache = storage.data_root() / "raw_sec" / "insider_datasets"
    cache.mkdir(parents=True, exist_ok=True)
    done = {r["quarter"] for r in storage.manifest("insider_filings") if r.get("kind") == "quarter_done"}
    todo = [(y, q) for y, q in quarters() if f"{y}q{q}" not in done]
    if not todo:
        log("datasets: every quarter done")
        return
    urls = dataset_urls(http)
    for y, q in todo:
        name = f"{y}q{q}"
        if (y, q) not in urls:
            raise LookupError(f"data set {name} not listed on the SEC page")
        lo, hi = quarter_range(y, q)
        path = cache / f"{name}_form345.zip"
        if path.exists():
            body, rid = path.read_bytes(), "cache:" + hashlib.sha256(path.read_bytes()).hexdigest()
            check_range(lo, hi)
        else:
            body, rid = http.get(urls[(y, q)], table="insider_filings", declared_range=(lo, hi))
            if body is None:
                raise LookupError(f"{name}: 404")
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
        got = dt.datetime.now(UTC)
        filings, lines, st = parse_dataset(body, name, got, rid)
        fds = [r["filing_date"] for r in filings]
        if fds and (min(fds) < lo.astimezone(ET).date() or max(fds) >= hi.astimezone(ET).date()):
            raise ValueError(f"{name}: filing dates {min(fds)}..{max(fds)} outside the declared range")
        nf = _write_by_day("insider_filings", filings, f"ds-{name}")
        nl = _write_by_day("insider_lines", lines, f"ds-{name}")
        storage.append_manifest("insider_filings", {"kind": "quarter_done", "quarter": name, "stats": st,
                                                    "zip_sha256": hashlib.sha256(body).hexdigest(),
                                                    "written_filings": nf, "written_lines": nl})
        log(f"datasets {name}: {st}")


# ---------------------------------------------------------------- frames read back

ALL = (midnight_et(FIRST_DAY), midnight_et(PARITY_DAYS[1] + dt.timedelta(days=1)))


def _dedup(df, key):
    return df.sort("ingested_at").unique(subset=key, keep="last", maintain_order=True) if df.height else df


def filings_frame():
    return _dedup(read_frame(storage.data_root() / "insider_filings", *ALL), ["accession", "owner_seq"])


def lines_frame():
    return _dedup(read_frame(storage.data_root() / "insider_lines", *ALL), ["accession", "line_seq"])


def required_filings(filings=None, lines=None):
    """ADR 0009 section 1: Forms 4 and 4/A with a retained code-P line, plus every 4/A of an issuer that has one (an
    amendment acts from its own acceptance time). A 4/A filed on 2022-01-03 without a retained P line may have been
    accepted on 2021-12-31 (sealed): excluded and counted. -> (DataFrame accession, issuer_cik, filing_date, form), stats."""
    filings = filings_frame() if filings is None else filings
    lines = lines_frame() if lines is None else lines
    sub = filings.filter(pl.col("owner_seq") == 0).select("accession", "issuer_cik", "filing_date", "document_type")
    p_accs = set(lines.filter(pl.col("trans_code") == "P")["accession"].to_list())
    f4 = sub.filter(pl.col("document_type").is_in(["4", "4/A"]))
    with_p = f4.filter(pl.col("accession").is_in(p_accs))
    p_issuers = set(with_p["issuer_cik"].to_list())
    amend = f4.filter((pl.col("document_type") == "4/A") & pl.col("issuer_cik").is_in(p_issuers)
                      & ~pl.col("accession").is_in(p_accs))
    edge = amend.filter(pl.col("filing_date") <= FIRST_DAY)
    req = pl.concat([with_p, amend.filter(pl.col("filing_date") > FIRST_DAY)]).unique("accession").sort("accession")
    return req, {"with_p": with_p.height, "amendments_without_p": amend.height, "edge_excluded": edge.height,
                 "required": req.height}


# ---------------------------------------------------------------- acceptance headers

def header_range(fd):
    lo = max(fd - dt.timedelta(days=5), FIRST_DAY)
    return midnight_et(lo), midnight_et(fd + dt.timedelta(days=1))


def ingest_headers(http, workers=8):
    table = "filing_acceptance"
    req, st = required_filings()
    done_days = {r["filing_date"] for r in storage.manifest(table) if r.get("kind") == "header_day_done"}
    todo = req.filter(~pl.col("filing_date").cast(pl.Utf8).is_in(sorted(done_days)))
    log(f"headers: {st}; {todo.height} filings in {todo['filing_date'].n_unique()} filing days to fetch")
    first_fail = {"n": 0, "ok": 0}

    def one(r):
        acc, cik, fd = r["accession"], r["issuer_cik"], r["filing_date"]
        url = HEADER_URL.format(cik=cik, nodash=acc.replace("-", ""), acc=acc)
        body, rid = http.get(url, table=table, declared_range=header_range(fd))
        raw, at = parse_acceptance((body or b"").decode("utf-8", "replace"))
        return acc, cik, raw, at, rid

    for (fd,), g in todo.sort("filing_date").partition_by("filing_date", as_dict=True, maintain_order=True).items():
        rows, sealed, missing = [], 0, 0
        with cf.ThreadPoolExecutor(workers) as ex:
            res = list(ex.map(one, g.iter_rows(named=True)))
        got = dt.datetime.now(UTC)
        for acc, cik, raw, at, rid in res:
            if at is None:
                missing += 1
                first_fail["n"] += 1
                rows.append({"event_at": midnight_et(fd), "accession": acc, "cik": cik, "raw": raw, "status": "missing",
                             "method": "header", "feed": "none", "request_id": rid, "ingested_at": got,
                             "source": SOURCE, "schema_version": 1})
                continue
            first_fail["ok"] += 1
            if at < SEALED_END:
                sealed += 1  # never stored: its event time is in legacy_2016_2021
                continue
            rows.append({"event_at": at, "accession": acc, "cik": cik, "raw": raw, "status": "ok", "method": "header",
                         "feed": "none", "request_id": rid, "ingested_at": got, "source": SOURCE, "schema_version": 1})
        if first_fail["ok"] == 0 and first_fail["n"] >= 20:
            raise RuntimeError("headers: the first 20 headers had no parsable ACCEPTANCE-DATETIME; stopping")
        _write_by_day(table, rows, f"hdr-{fd.isoformat()}")
        storage.append_manifest(table, {"kind": "header_day_done", "filing_date": fd.isoformat(), "filings": g.height,
                                        "missing": missing, "sealed_dropped": sealed})
        log(f"headers {fd}: {g.height} filings, {missing} missing, {sealed} sealed-dropped; http {http.stats}")


def acceptance_frame():
    df = read_frame(storage.data_root() / "filing_acceptance", *ALL)
    return _dedup(df, ["accession", "method"])


# ---------------------------------------------------------------- submissions (SIC, annual form, cross-check)

def check_sample(acc_df):
    """Fixed-seed sample of header-ok filings for the JSON cross-check (ADR 0009 section 1)."""
    ok = sorted(acc_df.filter((pl.col("method") == "header") & (pl.col("status") == "ok"))["accession"].to_list())
    rng = random.Random(int.from_bytes(hashlib.sha256(CHECK_SEED.encode()).digest()[:8], "big"))
    return set(rng.sample(ok, min(CHECK_SAMPLE, len(ok))))


def parse_submissions(doc, cik, want, lo_day):
    """-> (sic row fields, {accession: raw acceptanceDateTime} for wanted accessions). Filing rows dated before lo_day
    are dropped at parse time and never inspected (whole-document source, ADR 0003 section 0.2)."""
    rec = (doc.get("filings") or {}).get("recent") or {}
    forms, dates = rec.get("form") or [], rec.get("filingDate") or []
    accs, acc_t = rec.get("accessionNumber") or [], rec.get("acceptanceDateTime") or []
    annual, found = None, {}
    for i in range(len(forms)):
        d = dt.date.fromisoformat(dates[i]) if i < len(dates) and dates[i] else None
        if d is None or d < lo_day:
            continue
        if annual is None and forms[i] in ("10-K", "20-F", "40-F"):
            annual = forms[i]
        if i < len(accs) and accs[i] in want and i < len(acc_t):
            found[accs[i]] = acc_t[i]
    return {"sic": str(doc.get("sic") or "") or None, "sic_description": doc.get("sicDescription") or None,
            "name": doc.get("name") or None, "tickers": doc.get("tickers") or None, "annual_form": annual}, found


def ingest_submissions(http, workers=8):
    table = "issuer_sic"
    fil = filings_frame()
    ciks = sorted({int(c) for c in fil.filter(pl.col("document_type").is_in(["4", "4/A"]))["issuer_cik"].to_list()
                   if c and c.isdigit() and int(c) > 0})
    done = {r["cik"] for r in storage.manifest(table) if r.get("kind") == "cik_done"}
    todo = [c for c in ciks if str(c) not in done]
    acc = acceptance_frame()
    sample = check_sample(acc)
    first = fil.filter(pl.col("owner_seq") == 0)
    acc_cik = dict(zip(first["accession"].to_list(), first["issuer_cik"].to_list()))
    want_by_cik = {}
    for a in sample:
        want_by_cik.setdefault(int(acc_cik[a]), set()).add(a)
    log(f"submissions: {len(ciks)} issuer CIKs, {len(todo)} to fetch; cross-check sample {len(sample)}")
    rng = (midnight_et(FIRST_DAY), midnight_et(PARITY_DAYS[1] + dt.timedelta(days=1)))
    batch, n = [], 0

    def one(c):
        body, rid = http.get(SUBMISSIONS.format(cik=c), table=table, declared_range=rng)
        doc = json.loads(body) if body else None
        return c, doc, rid

    def flush():
        nonlocal batch
        if not batch:
            return
        got = dt.datetime.now(UTC)
        sic_rows, chk_rows, cks = [], [], []
        for c, doc, rid in batch:
            cks.append(str(c))
            if doc is None:
                sic_rows.append({"event_at": got, "cik": str(c), "status": "missing", "sic": None, "sic_description": None,
                                 "name": None, "tickers": None, "annual_form": None, "feed": "none", "request_id": rid,
                                 "ingested_at": got, "source": SOURCE, "schema_version": 1})
                continue
            fields, found = parse_submissions(doc, c, want_by_cik.get(c, set()), FIRST_DAY)
            sic_rows.append({"event_at": got, "cik": str(c), "status": "ok", **fields, "feed": "none", "request_id": rid,
                             "ingested_at": got, "source": SOURCE, "schema_version": 1})
            for a, raw in found.items():
                as_utc = dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC).replace(microsecond=0)
                for method, t in (("json_as_utc", as_utc), ("json_et_rule", json_acceptance_utc(raw))):
                    if t < SEALED_END:
                        continue
                    chk_rows.append({"event_at": t, "accession": a, "cik": str(c), "raw": raw, "status": "ok",
                                     "method": method, "feed": "none", "request_id": rid, "ingested_at": got,
                                     "source": SOURCE, "schema_version": 1})
        part = f"sub-{hashlib.sha256(','.join(cks).encode()).hexdigest()[:12]}"
        _write_by_day(table, sic_rows, part)
        _write_by_day("filing_acceptance", chk_rows, part)
        for c in cks:
            storage.append_manifest(table, {"kind": "cik_done", "cik": c})
        batch = []

    with cf.ThreadPoolExecutor(workers) as ex:
        for res in ex.map(one, todo):
            batch.append(res)
            n += 1
            if len(batch) >= 500:
                flush()
                log(f"submissions: {n}/{len(todo)}; http {http.stats}")
    flush()
    log(f"submissions: done {n}; http {http.stats}")


ALL_FETCH = (midnight_et(FIRST_DAY), dt.datetime(2026, 11, 2, 4, tzinfo=UTC))


def sic_frame():
    return _dedup(read_frame(storage.data_root() / "issuer_sic", *ALL_FETCH), ["cik"])


# ---------------------------------------------------------------- 2026-10 EDGAR ownership XML (parity only)

_DOC = re.compile(r"<ownershipDocument[\s>].*?</ownershipDocument>", re.S)


def _t(el, path):
    x = el.find(path) if el is not None else None
    return (x.text or "").strip() if x is not None else ""


def parse_ownership(text, acc, filing_date, got, rid, quarter="edgar-2026-10"):
    """Full .txt submission -> (filings rows, lines rows, acceptance (raw, utc)) in the data-set schema, or None."""
    raw, at = parse_acceptance(text)
    m = _DOC.search(text)
    if not m:
        return None
    try:
        root = ET_XML.fromstring(m.group())
    except ET_XML.ParseError:
        return None
    form = _t(root, "documentType")
    notes = sorted(f"{f.get('id')}{_US}{''.join(f.itertext())}" for f in root.iter("footnote")
                   if _TENB5.search("".join(f.itertext())))
    base = {"event_at": midnight_et(filing_date), "accession": acc, "filing_date": filing_date, "document_type": form,
            "period_of_report": sec_date(_t(root, "periodOfReport")), "date_of_orig_sub": sec_date(_t(root, "dateOfOriginalSubmission")),
            "issuer_cik": cik_str(_t(root, "issuer/issuerCik")) or "0", "issuer_name": _t(root, "issuer/issuerName") or None,
            "issuer_symbol": _t(root, "issuer/issuerTradingSymbol") or None, "aff10b5one": _t(root, "aff10b5One") or None,
            "notes_10b5": notes or None, "dataset_quarter": quarter, "feed": "none", "request_id": rid,
            "ingested_at": got, "source": SOURCE, "schema_version": 1}
    filings = []
    for i, ro in enumerate(root.findall("reportingOwner")):
        rel = ro.find("reportingOwnerRelationship")
        flags = [k for k, tag in (("Director", "isDirector"), ("Officer", "isOfficer"), ("TenPercentOwner", "isTenPercentOwner"),
                                  ("Other", "isOther")) if _t(rel, tag).lower() in ("1", "true")]
        filings.append({**base, "owner_seq": i, "owner_cik": cik_str(_t(ro, "reportingOwnerId/rptOwnerCik")),
                        "owner_name": _t(ro, "reportingOwnerId/rptOwnerName") or None,
                        "relationship": ",".join(flags) or None, "owner_title": _t(rel, "officerTitle") or None})
    lines = []
    if form in ("4", "4/A"):
        for j, tx in enumerate(root.iter("nonDerivativeTransaction")):
            td = sec_date(_t(tx, "transactionDate/value"))
            if td is None or td < FIRST_DAY:
                continue
            lines.append({"event_at": midnight_et(filing_date), "accession": acc, "line_seq": j, "trans_date": td,
                          "trans_code": _t(tx, "transactionCoding/transactionCode") or None,
                          "acquired_disposed": _t(tx, "transactionAmounts/transactionAcquiredDisposedCode/value") or None,
                          "shares": num(_t(tx, "transactionAmounts/transactionShares/value")),
                          "price": num(_t(tx, "transactionAmounts/transactionPricePerShare/value")),
                          "shares_after": num(_t(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")),
                          "direct_indirect": _t(tx, "ownershipNature/directOrIndirectOwnership/value") or None,
                          "nature": _t(tx, "ownershipNature/natureOfOwnership/value") or None,
                          "fn_ids": sorted({f.get("id") for f in tx.iter("footnoteId")}) or None,
                          "feed": "none", "request_id": rid, "ingested_at": got, "source": SOURCE, "schema_version": 1})
    return filings, lines, (raw, at)


def parse_daily_index(text):
    """form.YYYYMMDD.idx -> {accession: (form, cik, path)} for Forms 4 and 4/A (one entry per accession)."""
    out = {}
    for line in text.splitlines():
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 5 or parts[0] not in ("4", "4/A"):
            continue
        path = parts[-1]
        acc = Path(path).stem
        if re.fullmatch(r"\d{10}-\d{2}-\d{6}", acc):
            out.setdefault(acc, (parts[0], parts[2], path))
    return out


def ingest_october(http, workers=8):
    days = [PARITY_DAYS[0] + dt.timedelta(days=i) for i in range((PARITY_DAYS[1] - PARITY_DAYS[0]).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    done = {r["day"] for r in storage.manifest("insider_filings") if r.get("kind") == "october_day_done"}
    for d in days:
        if d.isoformat() in done:
            continue
        rng = (midnight_et(d - dt.timedelta(days=4)), midnight_et(d + dt.timedelta(days=1)))
        body, rid = http.get(DAILY_INDEX.format(y=d.year, q=(d.month - 1) // 3 + 1, d=d), table="insider_filings",
                             declared_range=(midnight_et(d), midnight_et(d + dt.timedelta(days=1))))
        if body is None:
            storage.append_manifest("insider_filings", {"kind": "october_day_done", "day": d.isoformat(), "filings": 0,
                                                        "note": "no index (holiday)"})
            continue
        idx = parse_daily_index(body.decode("latin-1"))

        def one(item):
            acc, (form, cik, path) = item
            b, r = http.get(ARCHIVE + path, table="insider_filings", declared_range=rng)
            return acc, b, r

        with cf.ThreadPoolExecutor(workers) as ex:
            res = list(ex.map(one, sorted(idx.items())))
        got = dt.datetime.now(UTC)
        fil, lin, acc_rows, bad = [], [], [], 0
        for acc, b, r in res:
            parsed = parse_ownership((b or b"").decode("utf-8", "replace"), acc, d, got, r) if b else None
            if parsed is None:
                bad += 1
                continue
            f, l, (raw, at) = parsed
            fil += f
            lin += l
            if f and f[0]["document_type"] in ("4", "4/A"):
                acc_rows.append({"event_at": at if at else midnight_et(d), "accession": acc, "cik": f[0]["issuer_cik"] if f else "0",
                                 "raw": raw, "status": "ok" if at else "missing", "method": "header", "feed": "none",
                                 "request_id": r, "ingested_at": got, "source": SOURCE, "schema_version": 1})
        part = f"edgar-{d.isoformat()}"
        _write_by_day("insider_filings", fil, part)
        _write_by_day("insider_lines", lin, part)
        _write_by_day("filing_acceptance", acc_rows, part)
        storage.append_manifest("insider_filings", {"kind": "october_day_done", "day": d.isoformat(), "filings": len(idx),
                                                    "unparsed": bad, "form4": len(acc_rows)})
        log(f"october {d}: {len(idx)} filings, {bad} unparsed, {len(acc_rows)} Forms 4 and 4/A; http {http.stats}")


# ---------------------------------------------------------------- budget

def budget(sample_quarter_filings=3071, sample_quarter_bytes=13_874_620, amend_share=0.05, ciks=10_000,
           october_per_day=1_000, per_second=SEC_PER_MINUTE / 60):
    """SEC request/time/disk estimate from the I0 sample quarter (2024 Q1)."""
    nq = len(list(quarters()))
    req = {"datasets": nq + 1, "headers": int(nq * sample_quarter_filings * (1 + amend_share)), "submissions": ciks,
           "october": 12 * (october_per_day + 1)}
    total = sum(req.values())
    return {"quarters": nq, "requests": req, "total_requests": total, "hours": round(total / per_second / 3600, 2),
            "download_gb": round(nq * sample_quarter_bytes / 1e9 + 12 * october_per_day * 30e3 / 1e9, 2),
            "disk_gb_est": round(nq * 0.08 + 0.2, 2)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.insider_data")
    ap.add_argument("step", choices=["datasets", "headers", "submissions", "october", "budget"])
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    if a.step == "budget":
        print(json.dumps(budget(), indent=2))
        return 0
    http = sec_http()
    log(f"data root {storage.data_root()}; step {a.step}")
    {"datasets": lambda: ingest_datasets(http), "headers": lambda: ingest_headers(http, a.workers),
     "submissions": lambda: ingest_submissions(http, a.workers), "october": lambda: ingest_october(http, a.workers)}[a.step]()
    log(f"http stats {http.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
