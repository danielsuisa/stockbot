"""5%+ holders from Schedules 13D and 13G (the float's other subtraction, besides Forms 3/4/5).

Since 2024-12 both schedules are filed as structured XML (OBSERVED 2026-10-22 on 0000080255-26-000600, a SCHEDULE 13G:
issuerInfo/issuerCik, eventDateRequiresFilingThisStatement, per reporting person
reportingPersonBeneficiallyOwnedAggregateNumberOfShares and classPercent; and on 0001193125-26-384376, a SCHEDULE
13D/A: issuerInfo/issuerCIK, dateOfEvent, per reporting person reportingPersonCIK, aggregateAmountOwned and
percentOfClass). Older filings (form types SC 13D / SC 13G) are text or HTML and are not read.

Discovery: EDGAR's quarterly full index (edgar/full-index/YYYY/QTRn/form.idx, each closed quarter read once) and the
daily form index for the current quarter. A filing is listed once per party (the subject company and each filer, each
under its own edgar/data/<cik>/ path - OBSERVED), so the lines are grouped by accession; the pair is (issuer in
the universe, the other CIKs). Only the latest filing per (issuer, filer) is fetched (one request each), priority
issuers first, then newest first, at most PER_RUN a run - the backlog continues on the next nights and coverage is
recorded per issuer. A filer seen PASSIVE_AFTER times as a qualified institution only (Rule 13d-1(b): funds,
advisers, banks - kept in the float) is not fetched again.

Store: {"v": 1, "quarters": [...], "days": [...], "pending": {"issuer|filer": [path, filed, form]},
"pending_issuers": [...], "filers": {filer CIK: [passive filings, other filings]}, "issuers": {issuer CIK: {filer
key: {"ciks": [CIKs of the filer and reporting persons], "shares": the group's aggregate amount (the largest reporting person's: members of a group report the same shares, or parts of the
largest), "pct": class percent, "event", "filed", "acc", "form", "passive": a 13G under Rule 13d-1(b) only,
"types": reporting person types (IA, BD, IN, CO...)}}}}."""
import datetime as dt
import re
import xml.etree.ElementTree as ET

from bot import common
from bot.screener import stores

PER_RUN = 400  # filings fetched a run (the run's SEC budget is 2000 in all)
QUARTERS = 8  # closed quarters discovered (2024-12 on: the XML schedules)
QUARTERS_PER_RUN = 2  # full-index files read a run (~25-50 MB each)
BACK_DAYS = 70  # daily indexes of the current quarter
FORMS = ("SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A")
MIN_PCT = 5.0
PASSIVE_AFTER = 3  # a filer seen this many times as a Rule 13d-1(b) institution only is not fetched again (≈)
_XML = re.compile(r"<edgarSubmission[\s>].*?</edgarSubmission>", re.S)
_NS = re.compile(r"\{[^}]*\}")


def _local(tag):
    return _NS.sub("", tag)


def _num(s):
    try:
        return float((s or "").replace(",", "").strip())
    except ValueError:
        return None


def _date(s):
    """'09/03/2026' or '2026-09-03' -> ISO ('' otherwise)."""
    s = (s or "").strip()
    for f in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    return ""


def parse(text):
    """A Schedule 13D/13G submission (.txt) -> {"issuer", "event", "shares", "pct", "ciks", "form", "title"} or
    None when it has no XML cover page (a pre-2024-12 text filing) or no amounts."""
    m = _XML.search(text or "")
    if not m:
        return None
    try:
        root = ET.fromstring(m.group())
    except ET.ParseError:
        return None
    vals = {}
    persons = []
    for el in root.iter():
        tag = _local(el.tag)
        txt = (el.text or "").strip()
        if tag in ("coverPageHeaderReportingPersonDetails", "reportingPersonInfo"):
            p = {}
            for c in el.iter():
                t = _local(c.tag)
                if t in ("reportingPersonBeneficiallyOwnedAggregateNumberOfShares", "aggregateAmountOwned"):
                    p["shares"] = _num(c.text)
                elif t in ("classPercent", "percentOfClass"):
                    p["pct"] = _num(c.text)
                elif t in ("reportingPersonCIK", "reportingPersonCik"):
                    p["cik"] = (c.text or "").strip().lstrip("0")
            persons.append(p)
        elif tag in ("issuerCik", "issuerCIK") and "issuer" not in vals:
            vals["issuer"] = txt.lstrip("0")
        elif tag in ("eventDateRequiresFilingThisStatement", "dateOfEvent") and "event" not in vals:
            vals["event"] = _date(txt)
        elif tag == "submissionType" and "form" not in vals:
            vals["form"] = txt
        elif tag == "securitiesClassTitle" and "title" not in vals:
            vals["title"] = txt
        elif tag == "designateRulePursuantThisScheduleFiled":
            vals.setdefault("rules", []).append(txt)
        elif tag in ("typeOfReportingPerson", "typeOfPersonFiling") and txt:
            vals.setdefault("types", set()).add(txt.upper())
        elif tag == "cik":  # headerData/filerInfo/filer/filerCredentials/cik
            vals.setdefault("filers", []).append(txt.lstrip("0"))
    persons = [p for p in persons if p.get("shares") is not None]
    if not vals.get("issuer") or not persons:
        return None
    top = max(persons, key=lambda p: p["shares"])
    ciks = sorted({c for c in vals.get("filers", []) + [p.get("cik") for p in persons] if c})
    form = vals.get("form", "")
    rules = sorted({r.replace("Rule ", "").strip() for r in vals.get("rules", [])})
    return {"issuer": vals["issuer"], "event": vals.get("event", ""), "shares": top["shares"],
            "pct": top.get("pct"), "ciks": ciks, "form": form, "title": vals.get("title", ""),
            "passive": "13G" in form and rules == ["13d-1(b)"], "rules": rules, "types": sorted(vals.get("types", []))}


def index_lines(body):
    """A form index (full-index form.idx or daily form.YYYYMMDD.idx) -> [(form, cik, filed ISO, path)] for FORMS."""
    out = []
    for line in body.decode("latin-1").splitlines():
        if not line.startswith("SCHEDULE 13"):
            continue
        r = re.split(r"\s{2,}", line.strip())
        if len(r) >= 5 and r[0] in FORMS and r[-3].isdigit():
            d = r[-2] if "-" in r[-2] else f"{r[-2][:4]}-{r[-2][4:6]}-{r[-2][6:8]}"
            out.append((r[0], r[-3], d, r[-1]))
    return out


def discover(store, lines, issuers):
    """Index lines -> store["pending"] (mutated): the latest filing per (issuer, filer) not read yet."""
    by_acc = {}
    for form, cik, filed, path in lines:  # one line per party, each under its own edgar/data/<cik>/ (OBSERVED)
        e = by_acc.setdefault(acc_of(path), {"form": form, "filed": filed, "ciks": set(), "paths": {}})
        e["ciks"].add(cik)
        e["paths"][cik] = path
    pend = store.setdefault("pending", {})
    have = store.setdefault("issuers", {})
    n = 0
    for acc, e in by_acc.items():
        subj = sorted(e["ciks"] & issuers)
        path = e["paths"][subj[0]] if subj else None
        others = sorted(e["ciks"] - set(subj)) or subj[1:]
        if not subj:
            continue
        for iss in subj:
            filer = min([c for c in others if c != iss] or [iss])
            key = f"{iss}|{filer}"
            done = (have.get(iss) or {}).get(filer)
            if done and (done["filed"], done["acc"]) >= (e["filed"], acc_of(path)):
                continue
            cur = pend.get(key)
            if cur is None or (cur[1], acc_of(cur[0])) < (e["filed"], acc_of(path)):
                pend[key] = [path, e["filed"], e["form"]]
                n += 1
    return n


def passive_filer(store, filer):
    """A filer seen PASSIVE_AFTER times, always as a qualified institution (Rule 13d-1(b)): its other filings are
    not fetched (the float keeps such holders anyway)."""
    p, a = (store.get("filers") or {}).get(filer, (0, 0))
    return p >= PASSIVE_AFTER and a == 0


def acc_of(path):
    return path.rsplit("/", 1)[-1].removesuffix(".txt")


def apply(store, key, doc, path, filed):
    """One parsed filing -> store["issuers"] (mutated)."""
    iss, filer = key.split("|")
    if doc["issuer"] and doc["issuer"] != iss:  # listed under a filer that is itself in the universe
        iss = doc["issuer"]
    rec = store.setdefault("issuers", {}).setdefault(iss, {})
    cur = rec.get(filer)
    if cur and (cur["filed"], cur["acc"]) > (filed, acc_of(path)):
        return
    seen = store.setdefault("filers", {}).setdefault(filer, [0, 0])  # [passive filings, other filings]
    seen[0 if doc.get("passive") else 1] += 1
    rec[filer] = {"ciks": sorted(set(doc["ciks"]) | {filer}), "shares": doc["shares"], "pct": doc["pct"],
                  "event": doc["event"], "filed": filed, "acc": acc_of(path), "form": doc["form"],
                  "passive": doc.get("passive", False), "types": doc.get("types", [])}


def _quarters(today, n):
    d = dt.date.fromisoformat(today)
    y, q = d.year, (d.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        out.append(f"{y}QTR{q}")
    return out  # closed quarters, newest first


def update(issuers, sessions_done, today, priority=(), per_run=PER_RUN, late=None):
    """Discover (closed quarters once each, the current quarter's daily indexes), then fetch the pending latest
    filings, priority issuers first, newest first -> (store, meta, stats)."""
    st = stores.load("holders")
    store = st["data"] if st else {"v": 1, "quarters": [], "days": [], "pending": {}, "issuers": {}}
    stats = {"quarters": 0, "days": 0, "discovered": 0, "fetched": 0, "parsed": 0, "unparsed": 0}
    for qtr in [q for q in _quarters(today, QUARTERS) if q not in store["quarters"]][:QUARTERS_PER_RUN]:
        if late and late():
            break
        body = common.fetch(f"https://www.sec.gov/Archives/edgar/full-index/{qtr[:4]}/{qtr[4:]}/form.idx")
        stores.count("sec")
        if body:
            stats["discovered"] += discover(store, index_lines(body), issuers)
            store["quarters"].append(qtr)
            stats["quarters"] += 1
    d0 = dt.date.fromisoformat(today)
    q_start = dt.date(d0.year, 3 * ((d0.month - 1) // 3) + 1, 1).isoformat()
    for day in [d for d in sessions_done[-BACK_DAYS:] if d >= q_start and d not in store["days"]]:
        d = dt.date.fromisoformat(day)
        body = common.fetch(f"https://www.sec.gov/Archives/edgar/daily-index/{d.year}/QTR{(d.month + 2) // 3}/"
                            f"form.{d:%Y%m%d}.idx")
        stores.count("sec")
        if body is None:
            continue
        stats["discovered"] += discover(store, index_lines(body), issuers)
        store["days"].append(day)
        stats["days"] += 1
    prio = set(priority)
    todo = sorted(store["pending"].items(), key=lambda kv: (kv[1][1], kv[0]), reverse=True)  # newest first
    todo.sort(key=lambda kv: kv[0].split("|")[0] not in prio)  # stable: priority issuers first
    keep = []
    for kv in todo:
        if passive_filer(store, kv[0].split("|")[1]):
            del store["pending"][kv[0]]
            stats["passive_skipped"] = stats.get("passive_skipped", 0) + 1
        else:
            keep.append(kv)
    for key, (path, filed, form) in keep[:per_run]:
        if late and late():
            break
        if passive_filer(store, key.split("|")[1]):  # learnt during this run
            stats["passive_skipped"] = stats.get("passive_skipped", 0) + 1
            del store["pending"][key]
            continue
        try:
            body = common.fetch("https://www.sec.gov/Archives/" + path)
            stores.count("sec")
        except Exception:
            common.trace()
            continue
        stats["fetched"] += 1
        del store["pending"][key]
        doc = parse((body or b"").decode("utf-8", "replace"))
        if doc:
            stats["parsed"] += 1
            apply(store, key, doc, path, filed)
        else:
            stats["unparsed"] += 1
    store["days"] = sorted(store["days"])[-BACK_DAYS:]
    store["pending_issuers"] = sorted({k.split("|")[0] for k in store["pending"]})
    stats["pending"] = len(store["pending"])
    meta = stores.save("holders", store, "sec-13d13g-xml", rows=len(store["issuers"]), pending=len(store["pending"]))
    return store, meta, stats


def covered(store, cik):
    """Is the issuer's 13D/13G picture complete: every quarter discovered and none of its latest filings pending?"""
    st = store or {}
    return len(st.get("quarters") or []) >= QUARTERS and str(cik) not in set(st.get("pending_issuers") or [])


def holders(store, cik, owners=None, day=None, min_pct=MIN_PCT, passive=False, adjust=None):
    """Σ shares of the issuer's 13D/13G holders whose latest filing (filed on/before `day`, ISO; None = latest)
    reports >= min_pct of the class -> (shares, holders counted); (0.0, 0) when none counts. Unless `passive`, the
    qualified institutions filing a 13G under Rule 13d-1(b) alone (funds, advisers, banks) are left out: finviz's and
    Yahoo's floats keep them (AAPL's float is within 1% of its shares outstanding). owners: {CIK: shares} of the
    issuer's Form 3/4/5 owners: a holder that is also one counts once, at the larger amount (only the excess over
    its Form 3/4/5 holding is added here - a 13D group often holds more than its Form 4 lines show). Point in time by
    filing date (≈: a holder whose latest filing came after `day` is left out; its earlier filing is not kept).
    adjust(date) -> the split factor from the filing's event date to `day`."""
    rec = ((store or {}).get("issuers") or {}).get(str(cik)) or {}
    owners = owners or {}
    tot, n = 0.0, 0
    for h in rec.values():
        if day is not None and h["filed"] > day:
            continue
        if h.get("passive") and not passive:  # a qualified institution (Rule 13d-1(b)): not closely held
            continue
        if h.get("pct") is not None and h["pct"] < min_pct:
            continue
        shares = h["shares"] * (adjust(h.get("event") or h["filed"]) if adjust else 1.0)
        both = [owners[c] for c in h["ciks"] if c in owners]
        tot += max(0.0, shares - max(both)) if both else shares
        n += 1
    return tot, n
