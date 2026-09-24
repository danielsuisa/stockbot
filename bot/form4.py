"""Form 4 ownershipDocument parsing -> open-market purchases (transactionCode P, acquired A)."""
import re
import xml.etree.ElementTree as ET

from bot import common

_DOC = re.compile(r"<ownershipDocument[\s>].*?</ownershipDocument>", re.S)


def _txt(el, path):
    x = el.find(path) if el is not None else None
    return (x.text or "").strip() if x is not None else ""


def _num(s):
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _role(rel):
    """reportingOwnerRelationship -> (Hebrew role label, is officer/director)."""
    d, o, t = (_txt(rel, k).lower() in ("1", "true") for k in ("isDirector", "isOfficer", "isTenPercentOwner"))
    title = _txt(rel, "officerTitle")  # as filed ("EVP & CFO" would not survive .title())
    label = ", ".join((["דירקטור"] if d else []) + ([title or "נושא משרה"] if o else []))
    return label or ("בעל 10%" if t else _txt(rel, "otherText") or "אחר"), d or o


def parse(text):
    """Raw Form 4 XML or full .txt submission -> dict, or None if there is no parsable ownershipDocument."""
    m = _DOC.search(text)
    if not m:
        return None
    try:
        root = ET.fromstring(m.group())
    except ET.ParseError:
        return None
    owners = []
    for ro in root.findall("reportingOwner"):
        role, od = _role(ro.find("reportingOwnerRelationship"))
        name = _txt(ro, "reportingOwnerId/rptOwnerName")
        owners.append({"cik": _txt(ro, "reportingOwnerId/rptOwnerCik").lstrip("0"),
                       "name": name.title() if name.isupper() else name, "role": role, "od": od})
    buys = []
    for tx in root.iter("nonDerivativeTransaction"):
        if (_txt(tx, "transactionCoding/transactionCode") != "P"
                or _txt(tx, "transactionAmounts/transactionAcquiredDisposedCode/value") != "A"):
            continue
        sh = _num(_txt(tx, "transactionAmounts/transactionShares/value"))
        px = _num(_txt(tx, "transactionAmounts/transactionPricePerShare/value"))
        buys.append({"date": _txt(tx, "transactionDate/value")[:10], "shares": sh, "price": px,
                     "value": sh * px if sh and px else None,
                     "indirect": _txt(tx, "ownershipNature/directOrIndirectOwnership/value") == "I"})
    cik = _txt(root, "issuer/issuerCik")
    return {"form": _txt(root, "documentType"), "issuer_cik": int(cik) if cik.isdigit() else 0,
            "issuer": _txt(root, "issuer/issuerName"), "symbol": _txt(root, "issuer/issuerTradingSymbol"),
            "owners": owners, "buys": buys}


def fetch(url):
    """Download and parse a Form 4 (raw .xml or full .txt submission); None if missing/unparsable."""
    body = common.fetch(url)
    return parse(body.decode("utf-8", "replace")) if body else None


def summarize(doc):
    """Aggregate a filing's purchase lines -> {shares, value, price (weighted), first, last, sig, indirect}."""
    b = doc["buys"]
    priced = [x for x in b if x["value"]]
    value = sum(x["value"] for x in priced)
    psh = sum(x["shares"] for x in priced)
    dates = sorted(x["date"] for x in b if x["date"])
    return {"shares": sum(x["shares"] or 0 for x in b), "value": value, "price": value / psh if psh else None,
            "first": dates[0] if dates else "", "last": dates[-1] if dates else "",
            "sig": sorted(([x["date"], x["shares"], x["price"]] for x in b), key=repr),  # exact trades, for joint()
            "indirect": any(x.get("indirect") for x in b)}


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
            continue
        out.append(dict(e))
        if e.get("sig"):
            by[k] = out[-1]
    return out


def insider(doc):
    """Headline insider of a filing: first officer/director, else first reporting owner."""
    o = doc["owners"]
    return next((x for x in o if x["od"]), o[0] if o else {"cik": "", "name": "?", "role": "אחר", "od": False})
