"""Insider holdings from SEC Forms 3/4/5: a bulk backfill from SEC's quarterly Insider Transactions Data Sets (flattened
Forms 3/4/5, one ZIP a quarter, the last BULK_QUARTERS quarters) and the EDGAR daily index for the days since (each
filing's ownershipDocument XML; a small reader of the holdings after each transaction - D-012, bot/form4.py stays
unchanged). A filing read both ways counts once (accession dedupe; applying one is idempotent anyway).

Store (v2): {"v": 2, "issuers": {issuer CIK: {owner key: {"role": "d"|"o"|"t" (director / officer / 10% owner,
combined), "l": {line key: [[period date, shares, filed date], ...]}, "h": [[period date, shares], ...]}}},
"quarters": [bulk quarters read], "days": [index days read], "acc": {accession: filed}, "buys": [...],
"sales": [...]}. A line is one non-derivative holding (share class x direct/indirect x nature of ownership): a Form 4
that reports only some lines leaves the others as last reported. "h" = the owner's total at each period (the sum of
every line's latest value). For the same line and period the later filing wins (an amendment supersedes the original,
whatever order the filings are read in). Joint filings report one holding for the group: one owner key (owner_key)."""
import csv
import datetime as dt
import io
import re
import xml.etree.ElementTree as ET
import zipfile

from bot import common, form4
from bot.screener import stores

PER_RUN = 1500  # filings read per run (SEC budget 2000)
BACK_DAYS = 5  # index days caught up per run
KEEP_DAYS = 730
TRADES_KEEP_DAYS = 10
FORMS = ("3", "4", "5", "3/A", "4/A", "5/A")
BULK_LIST = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
BULK_QUARTERS = 8
ACC_KEEP_DAYS = 120
# non-derivative lines that are not the common equity the float is counted in
NOT_COMMON = re.compile(r"prefer|warrant|right|option|note|debenture|\bunits?\b|depositary|bond", re.I)
_CLASS = re.compile(r"\bclass\s+([a-z])\b", re.I)
_NATURE_DROP = {"by", "the", "held", "through", "via", "owned", "as", "of", "for", "in", "and", "a", "an", "is"}


def _t(el, path):
    x = el.find(path)
    return (x.text or "").strip() if x is not None and x.text else ""


def _num(s):
    try:
        return float(s.replace(",", ""))
    except (AttributeError, ValueError):
        return None


def parse(text):
    """Form 3/4/5 text -> {"issuer": cik, "symbol", "period", "owners": [(cik, role)], "shares": total non-derivative
    shares held after the filing, "buys": shares bought (code P), "sales": shares sold (code S)} or None."""
    m = form4._DOC.search(text)
    if not m:
        return None
    try:
        root = ET.fromstring(m.group())
    except ET.ParseError:
        return None
    owners = []
    for ro in root.findall("reportingOwner"):
        rel = ro.find("reportingOwnerRelationship")
        flags = {k: _t(rel, k).lower() in ("1", "true") for k in ("isDirector", "isOfficer", "isTenPercentOwner")} \
            if rel is not None else {}
        role = ("d" if flags.get("isDirector") else "") + ("o" if flags.get("isOfficer") else "") + \
               ("t" if flags.get("isTenPercentOwner") else "")
        owners.append((_t(ro, "reportingOwnerId/rptOwnerCik").lstrip("0"), role or "x"))
    held, buys, sales = {}, 0.0, 0.0
    for tag in ("nonDerivativeTransaction", "nonDerivativeHolding"):
        for row in root.iter(tag):
            key = line_key(_t(row, "securityTitle/value"), _t(row, "ownershipNature/directOrIndirectOwnership/value"),
                           _t(row, "ownershipNature/natureOfOwnership/value"))
            v = _num(_t(row, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"))
            if v is not None and key:
                held[key] = v  # document order: the last line per security and nature is the latest
            if tag == "nonDerivativeTransaction":
                code = _t(row, "transactionCoding/transactionCode")
                n = _num(_t(row, "transactionAmounts/transactionShares/value")) or 0.0
                if code == "P":
                    buys += n
                elif code == "S":
                    sales += n
    cik = _t(root, "issuer/issuerCik").lstrip("0")
    return {"issuer": cik, "symbol": _t(root, "issuer/issuerTradingSymbol").upper(),
            "period": _t(root, "periodOfReport")[:10], "owners": owners, "shares": sum(held.values()),
            "lines": held, "buys": buys, "sales": sales, "form": _t(root, "documentType")}


def line_key(title, direct, nature=""):
    """One holding line: share class | D/I | nature of an indirect holding (normalized) -> str, or None for a
    non-common security (preferred, warrants, units, notes...)."""
    if NOT_COMMON.search(title or ""):
        return None
    m = _CLASS.search(title or "")
    cls = "class " + m.group(1).lower() if m else "common"
    di = (direct or "D").strip().upper()[:1] or "D"
    nat = ""
    if di == "I":
        words = re.findall(r"[a-z0-9]+", (nature or "").lower())
        nat = " ".join(w for w in words if w not in _NATURE_DROP)[:40]
    return f"{cls}|{di}|{nat}"


def owner_key(owners):
    """Joint filings report one holding for the group: one key for all of them."""
    return "+".join(sorted(c for c, _ in owners if c)) or "?"


def _totals(lines):
    """Line histories -> the owner's total at each period [[period, shares], ...] (each line's latest value)."""
    periods = sorted({e[0] for hist in lines.values() for e in hist})
    out = []
    for p in periods:
        tot = 0.0
        for hist in lines.values():
            v = None
            for e in hist:  # ascending by period
                if e[0] > p:
                    break
                v = e[1]
            tot += v or 0.0
        out.append([p, tot])
    return out


def apply(store, doc, filed):
    """Merge one parsed filing (doc["lines"]: {line key: shares}) into the store (mutated)."""
    if not doc or not doc["issuer"] or not doc["period"]:
        return
    iss = store.setdefault("issuers", {}).setdefault(doc["issuer"], {})
    key = owner_key(doc["owners"])
    rec = iss.setdefault(key, {"role": "", "l": {}, "h": []})
    rec["role"] = "".join(sorted(set(rec["role"] + "".join(r for _, r in doc["owners"])) - {"x"}))
    lines = rec.setdefault("l", {})
    changed = False
    for lk, v in (doc.get("lines") or {}).items():
        hist = lines.setdefault(lk, [])
        old = next((e for e in hist if e[0] == doc["period"]), None)
        if old is not None and old[2] > filed:
            continue  # a later filing (an amendment) already set this line for this period
        if old is not None:
            hist.remove(old)
        hist.append([doc["period"], v, filed])
        hist.sort()
        changed = True
    if changed:
        rec["h"] = _totals(lines)
    if doc.get("acc"):
        store.setdefault("acc", {})[doc["acc"]] = filed
    for kind in ("buys", "sales"):
        if doc.get(kind, 0) > 0:
            store.setdefault(kind, []).append({"issuer": doc["issuer"], "symbol": doc["symbol"], "filed": filed,
                                               "shares": doc[kind], "role": rec["role"]})


# ---------- bulk: SEC Insider Transactions Data Sets (OBSERVED 2026-10-22, FORM_345_readme.htm) ----------
def bulk_links():
    """The data sets listing page -> {"2026q3": absolute ZIP URL, ...} (hrefs as listed, never guessed)."""
    body = common.fetch(BULK_LIST)
    stores.count("sec")
    out = {}
    for href, q in re.findall(r'href="([^"]+?(\d{4}q[1-4])_form345\.zip)"', (body or b"").decode("utf-8", "replace")):
        out[q] = href if href.startswith("http") else "https://www.sec.gov" + href
    return out


def _tsv(z, name):
    """Stream one tab-separated table of the ZIP as dict rows (never loaded whole)."""
    with z.open(name) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, "utf-8", errors="replace", newline=""), delimiter="\t",
                                  quoting=csv.QUOTE_NONE)


def _date(s):
    """'31-JUL-2026' -> '2026-07-31' ('' when blank or malformed)."""
    try:
        return dt.datetime.strptime((s or "").strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return ""


ROLE = {"director": "d", "officer": "o", "tenpercentowner": "t"}


def read_quarter(zf, issuers):
    """One quarter's ZIP (a path or file object) -> [doc] for issuers in `issuers` (CIK strings without leading
    zeros), ordered by (filed, accession). Tables (OBSERVED in FORM_345_readme.htm): SUBMISSION (ACCESSION_NUMBER,
    FILING_DATE, PERIOD_OF_REPORT, DOCUMENT_TYPE, ISSUERCIK, ISSUERTRADINGSYMBOL, NO_SECURITIES_OWNED),
    REPORTINGOWNER (RPTOWNERCIK, RPTOWNER_RELATIONSHIP: comma-separated Director / Officer / TenPercentOwner / Other),
    NONDERIV_TRANS and NONDERIV_HOLDING (SECURITY_TITLE, SHRS_OWND_FOLWNG_TRANS, DIRECT_INDIRECT_OWNERSHIP,
    NATURE_OF_OWNERSHIP; within a filing the *_SK key follows the document order, OBSERVED on 0001829126-26-010633)."""
    z = zipfile.ZipFile(zf)
    docs = {}
    for r in _tsv(z, "SUBMISSION.tsv"):
        cik = (r.get("ISSUERCIK") or "").lstrip("0")
        if cik not in issuers or r.get("DOCUMENT_TYPE") not in FORMS:
            continue
        docs[r["ACCESSION_NUMBER"]] = {"acc": r["ACCESSION_NUMBER"], "issuer": cik,
                                       "symbol": (r.get("ISSUERTRADINGSYMBOL") or "").upper(),
                                       "period": _date(r.get("PERIOD_OF_REPORT")), "filed": _date(r.get("FILING_DATE")),
                                       "form": r["DOCUMENT_TYPE"], "owners": [], "lines": {}, "_sk": {},
                                       "none": (r.get("NO_SECURITIES_OWNED") or "") == "1"}
    for r in _tsv(z, "REPORTINGOWNER.tsv"):
        d = docs.get(r["ACCESSION_NUMBER"])
        if d is not None:
            rel = {x.strip().lower() for x in (r.get("RPTOWNER_RELATIONSHIP") or "").split(",")}
            role = "".join(v for k, v in ROLE.items() if k in rel)
            d["owners"].append(((r.get("RPTOWNERCIK") or "").lstrip("0"), role or "x"))
    for table, sk in (("NONDERIV_TRANS.tsv", "NONDERIV_TRANS_SK"), ("NONDERIV_HOLDING.tsv", "NONDERIV_HOLDING_SK")):
        for r in _tsv(z, table):
            d = docs.get(r["ACCESSION_NUMBER"])
            if d is None:
                continue
            v = _num(r.get("SHRS_OWND_FOLWNG_TRANS") or "")
            key = line_key(r.get("SECURITY_TITLE"), r.get("DIRECT_INDIRECT_OWNERSHIP"), r.get("NATURE_OF_OWNERSHIP"))
            if v is None or not key:
                continue
            order = (0 if table.startswith("NONDERIV_TRANS") else 1, int(r.get(sk) or 0))  # as ownership.parse
            if key not in d["_sk"] or order >= d["_sk"][key]:
                d["_sk"][key], d["lines"][key] = order, v
    out = []
    for d in docs.values():
        if d["none"] and not d["lines"]:
            d["lines"] = {"common|D|": 0.0}  # Form 3: no securities owned
        d.pop("_sk"), d.pop("none")
        d["shares"] = sum(d["lines"].values())
        if d["period"] and d["filed"]:
            out.append(d)
    return sorted(out, key=lambda d: (d["filed"], d["acc"]))


def bulk_update(store, issuers, today, links=None, max_quarters=BULK_QUARTERS, late=None):
    """Backfill the last BULK_QUARTERS published quarters not read yet, newest first, into `store` (mutated);
    idempotent (store["quarters"]) -> stats. One SEC request per quarter (+1 for the listing)."""
    stats = {"quarters": [], "filings": 0}
    links = links if links is not None else bulk_links()
    want = sorted(links, reverse=True)[:BULK_QUARTERS]
    done = set(store.setdefault("quarters", []))
    acc_cut = (dt.date.fromisoformat(today) - dt.timedelta(ACC_KEEP_DAYS)).isoformat()
    for q in [q for q in want if q not in done][:max_quarters]:
        if late and late():
            break
        body = common.fetch(links[q])
        stores.count("sec")
        if not body:
            continue
        for d in read_quarter(io.BytesIO(body), issuers):
            acc = d.pop("acc")
            apply(store, {**d, "buys": 0, "sales": 0}, d["filed"])
            if d["filed"] >= acc_cut:
                store.setdefault("acc", {})[acc] = d["filed"]
            stats["filings"] += 1
        store["quarters"].append(q)
        stats["quarters"].append(q)
    return stats


def index_rows(day, issuers):
    """EDGAR daily form index of `day` (ISO) -> [(form, cik, file path)] for Forms 3/4/5 listed under an issuer in
    `issuers` (each filing is listed once per party; the issuer's line is the one kept) - None if not published."""
    d = dt.date.fromisoformat(day)
    body = common.fetch(f"https://www.sec.gov/Archives/edgar/daily-index/{d.year}/QTR{(d.month + 2) // 3}/"
                        f"form.{d:%Y%m%d}.idx")
    stores.count("sec")
    if body is None:
        return None
    out = []
    for line in body.decode("latin-1").splitlines():
        r = line.split()
        if len(r) >= 5 and r[0] in FORMS and r[-3].isdigit() and r[-3] in issuers:
            out.append((r[0], r[-3], r[-1]))  # path edgar/data/<cik>/<accession>.txt
    return out


def prune(store, today):
    cut = (dt.date.fromisoformat(today) - dt.timedelta(KEEP_DAYS)).isoformat()
    for iss in store.get("issuers", {}).values():
        for rec in iss.values():
            for lk, hist in list(rec.get("l", {}).items()):
                rec["l"][lk] = [e for e in hist if e[0] >= cut] or hist[-1:]
            keep = [h for h in rec["h"] if h[0] >= cut]
            rec["h"] = keep or rec["h"][-1:]
    acut = (dt.date.fromisoformat(today) - dt.timedelta(ACC_KEEP_DAYS)).isoformat()
    store["acc"] = {a: f for a, f in store.get("acc", {}).items() if f >= acut}
    tcut = (dt.date.fromisoformat(today) - dt.timedelta(TRADES_KEEP_DAYS)).isoformat()
    for kind in ("buys", "sales"):
        store[kind] = [x for x in store.get(kind, []) if x["filed"] >= tcut]
    store["days"] = sorted(store.get("days", []))[-60:]


def acc_of(path):
    """edgar/data/<cik>/<accession>.txt -> accession."""
    return path.rsplit("/", 1)[-1].removesuffix(".txt")


def _quarter_end(q):
    """'2026q3' -> '2026-09-30' (the last day a bulk quarter covers); '' for none."""
    y, n = int(q[:4]), int(q[-1])
    if y == 0:
        return ""
    return (dt.date(y + (n == 4), 1 if n == 4 else 3 * n + 1, 1) - dt.timedelta(1)).isoformat()


def load_store():
    """The ownership store (v2) -> (store, stored record or None). A v1 store (whole-filing totals, at most a few
    days of the daily reader) is rebuilt: the bulk backfill and the daily reader refill it."""
    st = stores.load("ownership")
    store = st["data"] if st else None
    if not store or store.get("v") != 2:
        store = {"v": 2, "issuers": {}, "quarters": [], "days": [], "acc": {}}
    return store, st


def update(issuers, sessions_done, today, per_run=PER_RUN, bulk=True, late=None, links=None):
    """The bulk quarters not read yet, then the daily indexes of the last BACK_DAYS completed sessions after the
    newest bulk quarter and not read yet (newest first) -> (store, meta, stats). issuers: set of issuer CIK strings
    (the universe)."""
    store, st = load_store()
    stats = {"days": 0, "filings": 0, "parsed": 0}
    meta = None
    try:  # saved even when stopped (the deadline, or the refresh watchdog's exception)
        if bulk:
            try:
                stats["bulk"] = bulk_update(store, issuers, today, links=links, late=late)
            except Exception:
                common.trace()
        q_end = _quarter_end(max(store.get("quarters") or ["0000q1"]))
        todo = [d for d in sessions_done[-BACK_DAYS:] if d not in store.get("days", []) and d > q_end]
        budget = per_run
        acc = store.setdefault("acc", {})
        for day in sorted(todo, reverse=True):
            if late and late():
                break
            rows = index_rows(day, issuers)
            if rows is None:
                continue
            rows = [r for r in rows if acc_of(r[2]) not in acc]  # read already (bulk or an earlier index day)
            if len(rows) > budget:  # not enough budget left for the whole day: read it next run
                break
            stopped = False
            for form, cik, path in rows:
                if late and late():  # the day is not marked read: its filings come again (accessions
                    stopped = True  # already applied are skipped)
                    break
                budget -= 1
                try:
                    body = common.fetch("https://www.sec.gov/Archives/" + path)
                    stores.count("sec")
                except Exception:
                    common.trace()
                    continue
                stats["filings"] += 1
                doc = parse((body or b"").decode("utf-8", "replace"))
                if doc:
                    stats["parsed"] += 1
                    apply(store, {**doc, "acc": acc_of(path)}, day)
            if stopped:
                stats["stopped"] = True
                break
            store.setdefault("days", []).append(day)
            stats["days"] += 1
    finally:
        prune(store, today)
        changed = stats["days"] or stats["filings"] or (stats.get("bulk") or {}).get("quarters") or st is None \
            or st["data"].get("v") != 2
        meta = stores.save("ownership", store, "sec-form345", rows=len(store.get("issuers", {})),
                           days=len(store.get("days", [])), quarters=len(store.get("quarters", []))) \
            if changed else st["meta"]
    return store, meta, stats


def groups(store, cik, day=None, roles="do", adjust=None):
    """The issuer's owners as groups -> [(CIKs, role, latest [period, shares] as of `day`)]. Owner keys that share a
    CIK are one group: the same holder files jointly on some forms and alone on others (ZYME's EcoR1: two CIKs
    jointly and each alone, OBSERVED), and each filing reports the group's whole holding - so the group counts
    once, at its most recent filing (ties: the larger). adjust(period) -> the split factor from that period to
    `day` (shares filed before a split are pre-split shares)."""
    iss = (store or {}).get("issuers", {}).get(str(cik)) or {}
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for key in iss:
        ciks = key.split("+")
        for c in ciks[1:]:
            parent[find(c)] = find(ciks[0])
    comp = {}
    for key, rec in iss.items():
        comp.setdefault(find(key.split("+")[0]), []).append((key, rec))
    out = []
    for members in comp.values():
        role = "".join(sorted({r for _, rec in members for r in rec["role"]}))
        if not set(role) & set(roles):
            continue
        latest = None
        for _, rec in members:
            h = [x for x in rec["h"] if day is None or x[0] <= day]
            if h and (latest is None or (h[-1][0], h[-1][1]) > (latest[0], latest[1])):
                latest = h[-1]
        if latest:
            if adjust:
                latest = [latest[0], latest[1] * adjust(latest[0])]
            out.append(({c for key, _ in members for c in key.split("+")}, role, latest))
    return out


def holdings(store, cik, day=None, roles="do", adjust=None):
    """Σ latest holdings (as of `day`, ISO; None = latest) of the issuer's owner groups whose role includes one of
    `roles` -> (shares, groups counted) or (None, 0) without data."""
    g = groups(store, cik, day, roles, adjust)
    return (sum(x[2][1] for x in g), len(g)) if g else (None, 0)


def owner_holdings(store, cik, day=None, roles="dot", adjust=None):
    """{owner CIK: latest holding (as of `day`) of the group it belongs to} for the issuer's owner groups with one
    of `roles`."""
    return {c: latest[1] for ciks, _, latest in groups(store, cik, day, roles, adjust) for c in ciks}


def trades(store, kind, since):
    """Recent code-P buys or code-S sales filed on/after `since` (ISO) -> [{"issuer", "symbol", "filed", "shares"}]."""
    return [x for x in (store or {}).get(kind, []) if x["filed"] >= since]


SYMBOL = re.compile(r"^[A-Z.\-]{1,6}$")
