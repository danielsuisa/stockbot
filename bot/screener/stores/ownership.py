"""Insider holdings from SEC Forms 3/4/5 (EDGAR daily index -> each filing's ownershipDocument XML; a small reader of
the holdings after each transaction - D-012, bot/form4.py stays unchanged).

Store: {"issuers": {issuer CIK: {owner key: {"role": "d"|"o"|"t" (director / officer / 10% owner, combined),
"h": [[period date, shares], ...]}}}, "days": [index days read], "buys": [...], "sales": [...]}. Shares = the sum over
non-derivative lines of the latest "shares owned following transaction" per (security, direct/indirect). Coverage
grows from the first day read (no free bulk history), so the float falls back to shares outstanding where no filing
has been read yet (float_method = outstanding_proxy)."""
import datetime as dt
import re
import xml.etree.ElementTree as ET

from bot import common, form4
from bot.screener import stores

PER_RUN = 1500  # filings read per run (SEC budget 2000)
BACK_DAYS = 5  # index days caught up per run
KEEP_DAYS = 730
TRADES_KEEP_DAYS = 10
FORMS = ("3", "4", "5", "3/A", "4/A", "5/A")


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
            key = (_t(row, "securityTitle/value").lower(), _t(row, "ownershipNature/directOrIndirectOwnership/value"))
            v = _num(_t(row, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"))
            if v is not None:
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
            "buys": buys, "sales": sales, "form": _t(root, "documentType")}


def owner_key(owners):
    """Joint filings report one holding for the group: one key for all of them."""
    return "+".join(sorted(c for c, _ in owners if c)) or "?"


def apply(store, doc, filed):
    """Merge one parsed filing into the store (mutated)."""
    if not doc or not doc["issuer"] or not doc["period"]:
        return
    iss = store.setdefault("issuers", {}).setdefault(doc["issuer"], {})
    key = owner_key(doc["owners"])
    rec = iss.setdefault(key, {"role": "", "h": []})
    rec["role"] = "".join(sorted(set(rec["role"] + "".join(r for _, r in doc["owners"])) - {"x"}))
    rec["h"] = sorted([h for h in rec["h"] if h[0] != doc["period"]] + [[doc["period"], doc["shares"]]])
    for kind in ("buys", "sales"):
        if doc[kind] > 0:
            store.setdefault(kind, []).append({"issuer": doc["issuer"], "symbol": doc["symbol"], "filed": filed,
                                               "shares": doc[kind], "role": rec["role"]})


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
            out.append((r[0], r[-3], r[-1]))
    return out


def prune(store, today):
    cut = (dt.date.fromisoformat(today) - dt.timedelta(KEEP_DAYS)).isoformat()
    for iss in store.get("issuers", {}).values():
        for rec in iss.values():
            keep = [h for h in rec["h"] if h[0] >= cut]
            rec["h"] = keep or rec["h"][-1:]
    tcut = (dt.date.fromisoformat(today) - dt.timedelta(TRADES_KEEP_DAYS)).isoformat()
    for kind in ("buys", "sales"):
        store[kind] = [x for x in store.get(kind, []) if x["filed"] >= tcut]
    store["days"] = sorted(store.get("days", []))[-60:]


def update(issuers, sessions_done, today, per_run=PER_RUN):
    """Read the daily indexes of the last BACK_DAYS completed sessions not read yet (newest first) -> (store,
    meta, stats). issuers: set of issuer CIK strings (the universe)."""
    st = stores.load("ownership")
    store = st["data"] if st else {"issuers": {}, "days": []}
    stats = {"days": 0, "filings": 0, "parsed": 0}
    todo = [d for d in sessions_done[-BACK_DAYS:] if d not in store.get("days", [])]
    budget = per_run
    for day in sorted(todo, reverse=True):
        rows = index_rows(day, issuers)
        if rows is None:
            continue
        if len(rows) > budget:  # not enough budget left for the whole day: read it next run
            break
        for form, cik, path in rows:
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
                apply(store, doc, day)
        store.setdefault("days", []).append(day)
        stats["days"] += 1
    prune(store, today)
    meta = stores.save("ownership", store, "sec-form345", rows=len(store.get("issuers", {})),
                       days=len(store.get("days", []))) if (stats["days"] or st is None) else st["meta"]
    return store, meta, stats


def holdings(store, cik, day=None, roles="do"):
    """Σ latest holdings (as of `day`, ISO; None = latest) of the issuer's owners whose role includes one of
    `roles` -> (shares, owners counted) or (None, 0) without data."""
    iss = (store or {}).get("issuers", {}).get(str(cik))
    if not iss:
        return None, 0
    total, n = 0.0, 0
    for rec in iss.values():
        if not set(rec["role"]) & set(roles):
            continue
        h = [x for x in rec["h"] if day is None or x[0] <= day]
        if h:
            total += h[-1][1]
            n += 1
    return (total, n) if n else (None, 0)


def trades(store, kind, since):
    """Recent code-P buys or code-S sales filed on/after `since` (ISO) -> [{"issuer", "symbol", "filed", "shares"}]."""
    return [x for x in (store or {}).get(kind, []) if x["filed"] >= since]


SYMBOL = re.compile(r"^[A-Z.\-]{1,6}$")
