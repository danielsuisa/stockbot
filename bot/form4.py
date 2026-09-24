"""Form 4 ownershipDocument parsing. Every acquisition is classified - open-market purchase (code P), 10b5-1 plan
purchase, option exercise, grant, other - and only open-market purchases outside a 10b5-1 plan are signals."""
import re
import xml.etree.ElementTree as ET

from bot import common

_DOC = re.compile(r"<ownershipDocument[\s>].*?</ownershipDocument>", re.S)
KINDS = {"M": "exercise", "X": "exercise", "C": "exercise", "A": "grant"}  # other acquisition codes -> "other"
TOP = re.compile(r"(?i)\b(?:ceo|cfo|chief\s+(?:executive|financial)|principal\s+(?:executive|financial)|chair(?:man|woman|person)?)\b")
PLAN = re.compile(r"(?i)10b5-?1")
SYMBOLIC_PCT, SYMBOLIC_USD = 0.05, 100_000


def _txt(el, path):
    x = el.find(path) if el is not None else None
    return (x.text or "").strip() if x is not None else ""


def _num(s):
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _role(rel):
    """reportingOwnerRelationship -> (Hebrew role label, is officer/director, cluster weight:
    CEO/CFO/Chair 1.5, other officer 1.0, director 0.7, others 0)."""
    d, o, t = (_txt(rel, k).lower() in ("1", "true") for k in ("isDirector", "isOfficer", "isTenPercentOwner"))
    title, other = _txt(rel, "officerTitle"), _txt(rel, "otherText")  # as filed ("EVP & CFO" would not survive .title())
    label = ", ".join((["דירקטור"] if d else []) + ([title or "נושא משרה"] if o else []))
    weight = 1.5 if (d or o) and TOP.search(f"{title} {other}") else 1.0 if o else 0.7 if d else 0.0
    return label or ("בעל 10%" if t else other or "אחר"), d or o, weight


def _line(tx):
    sh = _num(_txt(tx, "transactionAmounts/transactionShares/value"))
    px = _num(_txt(tx, "transactionAmounts/transactionPricePerShare/value"))
    return {"date": _txt(tx, "transactionDate/value")[:10], "shares": sh, "price": px,
            "value": sh * px if sh and px else None,
            "indirect": _txt(tx, "ownershipNature/directOrIndirectOwnership/value") == "I",
            "post": _num(_txt(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"))}


def parse(text):
    """Raw Form 4 XML or full .txt submission -> dict, or None if there is no parsable ownershipDocument.
    buys: code-P acquisitions of the stock (plan=True when a footnote it references mentions Rule 10b5-1, or the
    filing's 10b5-1 checkbox is ticked and no footnote says which line it covers); acq: every other acquisition."""
    m = _DOC.search(text)
    if not m:
        return None
    try:
        root = ET.fromstring(m.group())
    except ET.ParseError:
        return None
    owners = []
    for ro in root.findall("reportingOwner"):
        role, od, weight = _role(ro.find("reportingOwnerRelationship"))
        name = _txt(ro, "reportingOwnerId/rptOwnerName")
        owners.append({"cik": _txt(ro, "reportingOwnerId/rptOwnerCik").lstrip("0"),
                       "name": name.title() if name.isupper() else name, "role": role, "od": od, "weight": weight})
    plan_notes = {f.get("id") for f in root.iter("footnote") if PLAN.search("".join(f.itertext()))}
    box = _txt(root, "aff10b5One").lower() in ("1", "true")
    buys, acq = [], []
    for table, derivative in (("nonDerivativeTransaction", False), ("derivativeTransaction", True)):
        for tx in root.iter(table):
            if _txt(tx, "transactionAmounts/transactionAcquiredDisposedCode/value") != "A":
                continue
            code, line = _txt(tx, "transactionCoding/transactionCode"), _line(tx)
            if code == "P" and not derivative:
                refs = {f.get("id") for f in tx.iter("footnoteId")}
                buys.append({**line, "plan": bool(refs & plan_notes) or (box and not plan_notes)})
            else:
                acq.append({**line, "code": code, "kind": KINDS.get(code, "other"), "derivative": derivative})
    cik = _txt(root, "issuer/issuerCik")
    return {"form": _txt(root, "documentType"), "issuer_cik": int(cik) if cik.isdigit() else 0,
            "issuer": _txt(root, "issuer/issuerName"), "symbol": _txt(root, "issuer/issuerTradingSymbol"),
            "owners": owners, "buys": buys, "acq": acq, "plan_box": box,
            "original": _txt(root, "dateOfOriginalSubmission")[:10]}


def fetch(url):
    """Download and parse a Form 4 (raw .xml or full .txt submission); None if missing/unparsable."""
    body = common.fetch(url)
    return parse(body.decode("utf-8", "replace")) if body else None


def summarize(doc):
    """A filing's purchase lines -> the open-market part {shares, value, price (weighted), first, last, pct = shares
    bought / shares held after} with kind "open"; a filing with only 10b5-1 plan purchases gets kind "plan" and
    its plan totals instead (informational, never a signal). sig = the exact trades, for joint()."""
    b = doc["buys"]
    op = [x for x in b if not x.get("plan")]
    use = op or b
    priced = [x for x in use if x["value"]]
    value = sum(x["value"] for x in priced)
    psh = sum(x["shares"] for x in priced)
    shares = sum(x["shares"] or 0 for x in use)
    dates = sorted(x["date"] for x in use if x["date"])
    post = next((x["post"] for x in reversed(use) if x.get("post")), None)
    return {"shares": shares, "value": value, "price": value / psh if psh else None,
            "first": dates[0] if dates else "", "last": dates[-1] if dates else "",
            "sig": sorted(([x["date"], x["shares"], x["price"]] for x in b), key=repr),
            "indirect": any(x.get("indirect") for x in b), "kind": "open" if op else "plan",
            "plan_value": sum(x["value"] or 0 for x in b if x.get("plan")),
            "post": post, "pct": min(1.0, shares / post) if post and shares else None}


def symbolic(r):
    """A token buy: under 5% of what the insider holds and under $100K (down-weighted in cluster scoring)."""
    return r.get("pct") is not None and r["pct"] < SYMBOLIC_PCT and (r.get("value") or 0) < SYMBOLIC_USD


def joint(entries, name="name"):
    """One issuer's purchase filings with joint reports collapsed: a fund and the director who sits on the board
    for it each file a Form 4 with the identical trades (held indirectly), which is one purchase, not two buyers
    and double the dollars. Merged entries join the reporters' names; direct twins stay separate."""
    out, by = [], {}
    for e in entries:
        k = repr(e.get("sig") or "")
        m = by.get(k)
        if m is not None and (m.get("indirect") or e.get("indirect")):
            m[name] = f"{m[name]} / {e[name]}"
            m.setdefault("joint_accs", []).append(e.get("acc"))
            continue
        out.append(dict(e))
        if e.get("sig"):
            by[k] = out[-1]
    return out


def insider(doc):
    """Headline insider of a filing: first officer/director, else first reporting owner."""
    o = doc["owners"]
    return next((x for x in o if x["od"]), o[0] if o else {"cik": "", "name": "?", "role": "אחר", "od": False,
                                                           "weight": 0.0})
