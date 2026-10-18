"""P2 proxy ingestion (ADR 0003 section 0): Cboe VIX, macro calendar (FOMC, CPI, NFP), SEC EDGAR filings and the
ticker -> CIK map. Same mechanics as P1: append-only Parquet, manifest per request, holdout check before the network.

Whole-document sources (ADR 0003 section 0.2, Proposed): the declared ingest range passes check_range before the
request; rows dated outside it are dropped at parse time and never stored or inspected.

Identity: SEC_USER_AGENT (name and email) from the environment is sent only to sec.gov hosts. bls.gov gets
BLS_USER_AGENT; when that is unset the BLS step is skipped (ADR 0004 section 4). Neither is written to disk or logs.

    python -m scalp_engine.proxies STEP --start 2023-06-01 --end 2026-10-16
    STEP: vix | macro | cikmap | edgar | all
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

import polars as pl

from scalp_engine import ingest, storage, universe
from scalp_engine.alpaca_client import RateLimiter
from scalp_engine.data_access import ET, check_range, day_bounds, read_frame

UTC = dt.timezone.utc
CBOE_VIX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
FOMC = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BLS_CPI = "https://www.bls.gov/bls/news-release/cpi.htm"
BLS_NFP = "https://www.bls.gov/bls/news-release/empsit.htm"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/"
SEC_FTS = "https://efts.sec.gov/LATEST/search-index"
DILUTION_FORMS = re.compile(r"^(S-1|S-3)(/A|ASR|MEF)?$|^424B\d$")
_HOSTS = ("cdn.cboe.com", "www.federalreserve.gov", "www.bls.gov", "www.sec.gov", "data.sec.gov", "efts.sec.gov")


class IdentityMissing(RuntimeError):
    pass


def user_agent():
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua or "@" not in ua:
        raise IdentityMissing("SEC_USER_AGENT (name and email) is not set")
    return ua


class Http:
    """GET with a per-host rate limit (SEC fair access: <= 10/s; we use 5/s), retries, and a manifest entry per request."""

    def __init__(self):
        self.ua = user_agent()
        self.limits = {h: RateLimiter(300 if "sec.gov" in h else 60) for h in _HOSTS}
        self.stats = {"requests": 0, "bytes": 0}
        self.lock = threading.Lock()

    def agent(self, host):
        """SEC_USER_AGENT for SEC; BLS_USER_AGENT for bls.gov (ADR 0004 section 4); the SEC identity elsewhere is
        only sent to hosts that need no contact (Cboe, the Fed): they get a generic project agent."""
        if host == "www.bls.gov":
            ua = os.environ.get("BLS_USER_AGENT", "").strip()
            if not ua:
                raise IdentityMissing("BLS_USER_AGENT is not set")
            return ua
        if host.endswith("sec.gov"):
            return self.ua
        return "scalp-engine research (+https://github.com/danielsuisa/stockbot)"

    def get(self, url, params=None, *, table, declared_range, tries=6):
        host = urllib.parse.urlparse(url).hostname
        if host not in _HOSTS:
            raise ValueError(f"host not allowed: {host}")
        check_range(*declared_range)
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        rid = hashlib.sha256(full.encode()).hexdigest()
        for attempt in range(tries):
            self.limits[host].acquire()
            try:
                req = urllib.request.Request(full, headers={"User-Agent": self.agent(host), "Accept-Encoding": "gzip"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    body = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    status = r.status
                break
            except urllib.error.HTTPError as e:
                status = e.code
                if status == 404:
                    body = None
                    break
                if status in (403, 429, 500, 502, 503, 504) and attempt < tries - 1:
                    time.sleep(min(60, 2 ** (attempt + 1)))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt < tries - 1:
                    time.sleep(min(60, 2 ** (attempt + 1)))
                    continue
                raise
        with self.lock:
            self.stats["requests"] += 1
            self.stats["bytes"] += len(body or b"")
        storage.append_manifest(table, {
            "kind": "request", "table": table, "url": url, "params": params or {}, "request_id": rid, "status": status,
            "bytes": len(body or b""), "body_sha256": hashlib.sha256(body or b"").hexdigest(),
            "declared_range": [declared_range[0].isoformat(), declared_range[1].isoformat()],
            "received_at": dt.datetime.now(UTC).isoformat()})
        return body, rid


def in_range(day, start, end):
    """Parse-time filter for whole-document sources: only dates inside [start, end] are kept."""
    return start <= day <= end


def _write_by_date(table, df, part, source):
    done = storage.completed(table, part=part)
    df = df.with_columns(_d=pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date())
    n = 0
    for (day,), g in df.partition_by("_d", as_dict=True, maintain_order=True).items():
        if storage.partition_key(table, source=source, date=day) in done:
            continue
        storage.write_partition(table, g.drop("_d"), part=part, request_ids=g["request_id"].to_list(),
                                source=source, date=day)
        n += 1
    return n


# ---------------------------------------------------------------- VIX

def ingest_vix(http, start, end):
    table = "vix_daily"
    body, rid = http.get(CBOE_VIX, table=table, declared_range=ingest.range_bounds(start, end))
    got = dt.datetime.now(UTC)
    rows = []
    lines = body.decode("utf-8").splitlines()
    if lines[0].strip().upper() != "DATE,OPEN,HIGH,LOW,CLOSE":
        raise ValueError("unexpected Cboe VIX header")
    for line in lines[1:]:
        f = line.strip().split(",")
        if len(f) != 5:
            continue
        day = dt.datetime.strptime(f[0], "%m/%d/%Y").date()
        if not in_range(day, start, end):
            continue  # dropped at parse time (ADR 0003 section 0.2)
        rows.append({"event_at": ingest.et(day, dt.time(16, 15)), "trading_date": day, "open": float(f[1]),
                     "high": float(f[2]), "low": float(f[3]), "close": float(f[4]), "feed": "none", "request_id": rid,
                     "ingested_at": got, "source": "cboe", "schema_version": 1})
    n = _write_by_date(table, storage.frame(table, rows), "vix-v1", "cboe")
    ingest.log(f"vix: {len(rows)} sessions kept in range, {n} partitions written")


# ---------------------------------------------------------------- macro calendar

_MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                       "September", "October", "November", "December"], 1)}
_MON_ABBR = {m[:3]: i for m, i in _MONTHS.items()}


def parse_fomc(html, start, end):
    """Decision days (last day of each meeting) for years in range from the Fed calendar page."""
    out = []
    years = [int(y) for y in re.findall(r"(20\d\d) FOMC Meetings", html)]
    for y in sorted(set(years)):
        if not (start.year <= y <= end.year):
            continue  # whole years outside the range are not parsed
        i = html.index(f"{y} FOMC Meetings")
        nxt = [html.index(f"{z} FOMC Meetings") for z in set(years) if html.index(f"{z} FOMC Meetings") > i]
        blk = html[i:min(nxt) if nxt else len(html)]
        months = re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>', blk)
        dates = re.findall(r'fomc-meeting__date[^>]*>([^<]+)<', blk)
        for mon, days in zip(months, dates):
            mon_last = mon.split("/")[-1].strip()
            m = _MONTHS.get(mon_last) or _MON_ABBR.get(mon_last[:3])
            d_last = re.findall(r"\d+", days)
            if not m or not d_last:
                continue
            day = dt.date(y, m, int(d_last[-1]))
            if in_range(day, start, end):
                out.append((day, "fomc", "(unscheduled)" in days.lower() or "unscheduled" in days.lower()))
    return out


def parse_bls_archive(html, prefix, start, end):
    """Release dates from archive file names such as /news.release/archives/cpi_01152025.htm."""
    out = set()
    for mmddyyyy in re.findall(rf"/news\.release/archives/{prefix}_(\d{{8}})\.htm", html):
        day = dt.datetime.strptime(mmddyyyy, "%m%d%Y").date()
        if in_range(day, start, end):
            out.add(day)
    return sorted(out)


def ingest_macro(http, start, end):
    table = "macro_events"
    rng = ingest.range_bounds(start, end)
    rows = []
    got = dt.datetime.now(UTC)
    body, rid = http.get(FOMC, table=table, declared_range=rng)
    for day, kind, unscheduled in parse_fomc(body.decode("utf-8", "replace"), start, end):
        rows.append(_macro_row(day, "fomc", dt.time(14, 0), FOMC, unscheduled, rid, got))
    for url, prefix, kind in ((BLS_CPI, "cpi", "cpi"), (BLS_NFP, "empsit", "nfp")):
        if not os.environ.get("BLS_USER_AGENT", "").strip():
            ingest.log(f"macro: {kind} skipped: BLS_USER_AGENT is not set (ADR 0004 section 4)")
            continue
        body, rid = http.get(url, table=table, declared_range=rng)
        for day in parse_bls_archive(body.decode("utf-8", "replace"), prefix, start, end):
            rows.append(_macro_row(day, kind, dt.time(8, 30), url, False, rid, got))
    df = storage.frame(table, rows)
    n = _write_by_date(table, df, "macro-v1", "official")
    ingest.log(f"macro: {dict(Counter(r['event_type'] for r in rows))}, {n} partitions written")


def _macro_row(day, kind, at, url, unscheduled, rid, got):
    return {"event_at": ingest.et(day, at), "event_date": day, "event_type": kind, "unscheduled": unscheduled,
            "source_url": url, "feed": "none", "request_id": rid, "ingested_at": got, "source": "official",
            "schema_version": 1}


# ---------------------------------------------------------------- CIK map

def members_info(start, end):
    """{symbol: status} of every top500 member in range (current method)."""
    uni = read_frame(storage.data_root() / "universe_snapshots", *ingest.range_bounds(start, end))
    uni = uni.filter((pl.col("method_version") == universe.METHOD) & (pl.col("universe") == "top500"))
    return dict(uni.select("symbol", "status").unique("symbol").sort("symbol").iter_rows())


def fts_pick(ticker, hits):
    """CIK for a delisted ticker from full-text-search hits (ADR 0003 section 6, Proposed), or None.

    1. exactly one entity whose display name carries (TICKER) -> its CIK;
    2. else among entities whose display name carries no ticker (delisted filers), the one with the most hits,
       if it has >= 2 and strictly more than the runner-up."""
    exact, tickerless = set(), Counter()
    for h in hits:
        for name, cik in zip(h.get("display_names", []), h.get("ciks", [])):
            m = re.search(r"\(([A-Z0-9.,\- ]+)\)\s+\(CIK \d{10}\)", name)
            if m and ticker in [x.strip() for x in m.group(1).split(",")]:
                exact.add(cik)
            elif not m:
                tickerless[cik] += 1
    if len(exact) == 1:
        return exact.pop(), "fts_exact"
    if not exact and tickerless:
        (top, n), *rest = tickerless.most_common(2) + [(None, 0)]
        if n >= 2 and n > rest[0][1]:
            return top, "fts_frequency"
    return None, None


CIKMAP_PART = "cikmap-v3"  # superseded, kept: v1 (no check on fts_frequency), v2 (filing-stop check admitted 2 errors)
_DEREG = re.compile(r"^(25|25-NSE|15-12B|15-12G|15-15D|15F-12B|15F-12G)(/A)?$")


def delisting_filed(http, cik, delisted_on, rng, table):
    """fts_frequency check (ADR 0003 section 6, Proposed): accept the CIK only if it has a Form 25 / 25-NSE (delisting) or
    Form 15 (deregistration) filed within 45 days of the ticker's delisting date (from corporate actions)."""
    body, _ = http.get(f"{SEC_SUBMISSIONS}CIK{cik}.json", table=table, declared_range=rng)
    if not body:
        return False
    r = json.loads(body)["filings"]["recent"]
    return any(_DEREG.match(f) and abs((dt.date.fromisoformat(d) - delisted_on).days) <= 45
               for f, d in zip(r["form"], r["filingDate"]))


def ingest_cikmap(http, start, end):
    table = "cik_map"
    if storage.completed(table, part=CIKMAP_PART):
        ingest.log("cikmap: exists, skipped")
        return
    members = members_info(start, end)
    delisted_on = {}
    for sym, day, _ in ingest.delistings(start, end):
        delisted_on[sym] = max(delisted_on.get(sym, day), day)
    rng = ingest.range_bounds(start, end)
    body, rid = http.get(SEC_TICKERS, table=table, declared_range=rng)
    current = {}
    for v in json.loads(body).values():
        current.setdefault(v["ticker"].upper().replace("-", "."), f"{int(v['cik_str']):010d}")
    renames = {old: new for old, new, _ in ingest.renames(start, end)}
    got = dt.datetime.now(UTC)
    rows = []
    for sym, status in members.items():
        cik, method, frid = current.get(sym), "company_tickers", rid
        if cik is None and sym in renames and renames[sym] in current:
            cik, method = current[renames[sym]], "rename"
        if cik is None and status in ("delisted", "inactive"):
            body, frid = http.get(SEC_FTS, {"q": f'"{sym}"', "dateRange": "custom", "startdt": "2023-01-01",
                                            "enddt": end.isoformat(), "forms": "10-K,10-Q"},
                                  table=table, declared_range=rng)
            hits = [h["_source"] for h in json.loads(body)["hits"]["hits"]] if body else []
            cik, method = fts_pick(sym, hits)
            cik = f"{int(cik):010d}" if cik else None
            if method == "fts_frequency" and not (sym in delisted_on and
                                                  delisting_filed(http, cik, delisted_on[sym], rng, table)):
                cik, method = None, "fts_rejected"
        rows.append({"event_at": got, "symbol": sym, "member_status": status, "cik": cik, "method": method or "none",
                     "feed": "none", "request_id": frid if method and method.startswith("fts") else rid,
                     "ingested_at": got, "source": "sec", "schema_version": 1})
    storage.write_partition(table, storage.frame(table, rows), part=CIKMAP_PART, request_ids=[rid], source="sec",
                            date=got.astimezone(ET).date())
    df = pl.DataFrame(rows)
    ingest.log(f"cikmap: {df.height} members; mapped by method {dict(Counter(df['method'].to_list()))}")


def latest_cik_map():
    root = storage.data_root() / "cik_map"
    from scalp_engine.data_access import partition_dates
    parts = partition_dates(root)
    if not parts:
        raise LookupError("cik_map not ingested")
    df = read_frame(root, *day_bounds(max(parts)))
    return df.filter(pl.col("ingested_at") == df["ingested_at"].max())  # the latest map version only


# ---------------------------------------------------------------- EDGAR filings

def json_acceptance_utc(value):
    """True UTC acceptance time from the submissions JSON acceptanceDateTime (ADR 0003 section 0.3): the JSON wall
    time is ET + 8 h under EDT and ET + 10 h under EST."""
    wall = dt.datetime.fromisoformat(value.replace("Z", "").split(".")[0])
    et_wall = wall - dt.timedelta(hours=8)
    if dt.datetime.combine(et_wall.date(), et_wall.time(), ET).utcoffset() == dt.timedelta(hours=-5):
        et_wall -= dt.timedelta(hours=2)
    return dt.datetime.combine(et_wall.date(), et_wall.time(), ET).astimezone(UTC)


def _filings(block, cik, start, end, rid, got):
    out = []
    n = len(block.get("form", []))
    for i in range(n):
        acc = block["acceptanceDateTime"][i]
        if not acc:
            continue
        at = json_acceptance_utc(acc)
        if not in_range(at.astimezone(ET).date(), start, end):
            continue  # dropped at parse time
        out.append({"event_at": at, "cik": cik, "accession": block["accessionNumber"][i], "form": block["form"][i],
                    "items": block.get("items", [""] * n)[i] or None, "filing_date": dt.date.fromisoformat(block["filingDate"][i]),
                    "feed": "none", "request_id": rid, "ingested_at": got, "source": "sec", "schema_version": 1})
    return out


def ingest_edgar(http, start, end, workers=4):
    import concurrent.futures as cf
    table = "edgar_filings"
    if any(r.get("kind") == "step_done" for r in storage.manifest(table)):
        ingest.log("edgar: done, skipped")
        return
    ciks = sorted(set(latest_cik_map()["cik"].drop_nulls().to_list()))
    rng = ingest.range_bounds(start, end)

    def one(cik):
        got = dt.datetime.now(UTC)
        body, rid = http.get(f"{SEC_SUBMISSIONS}CIK{cik}.json", table=table, declared_range=rng)
        if body is None:
            return []
        d = json.loads(body)
        rows = _filings(d["filings"]["recent"], cik, start, end, rid, got)
        for f in d["filings"].get("files", []):
            if f.get("filingTo", "") >= start.isoformat():
                b2, rid2 = http.get(f"{SEC_SUBMISSIONS}{f['name']}", table=table, declared_range=rng)
                if b2:
                    rows += _filings(json.loads(b2), cik, start, end, rid2, got)
        return rows

    rows = []
    with cf.ThreadPoolExecutor(workers) as ex:
        for i, r in enumerate(ex.map(one, ciks), 1):
            rows += r
            if i % 100 == 0:
                ingest.log(f"edgar: {i}/{len(ciks)} CIKs, {len(rows)} filings")
    df = storage.frame(table, rows).unique(["cik", "accession"], keep="first", maintain_order=True)
    n = _write_by_date(table, df, "edgar-v1", "sec")
    storage.append_manifest(table, {"kind": "step_done", "table": table, "rows": df.height, "ciks": len(ciks)})
    ingest.log(f"edgar: {df.height} filings for {len(ciks)} CIKs, {n} partitions written; http {http.stats}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.proxies")
    ap.add_argument("step", choices=["vix", "macro", "cikmap", "edgar", "all"])
    ap.add_argument("--start", required=True, type=dt.date.fromisoformat)
    ap.add_argument("--end", required=True, type=dt.date.fromisoformat)
    a = ap.parse_args(argv)
    http = Http()
    for step in (["vix", "macro", "cikmap", "edgar"] if a.step == "all" else [a.step]):
        {"vix": ingest_vix, "macro": ingest_macro, "cikmap": ingest_cikmap, "edgar": ingest_edgar}[step](http, a.start, a.end)
    ingest.log(f"http stats {http.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
