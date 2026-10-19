"""The stockbot insider rule at commit 2ffda0a, reimplemented on stored data-set rows (ADR 0009 section 3).

No `bot` import (R4). Each function mirrors its stockbot counterpart, named in its docstring, and stockbot's test
vectors are copied as fixtures in tests/test_insider_rule.py. Thresholds are the values in force (ADR 0008 section
7, E2): the GitHub repository variables are unset, so the defaults apply.

Clock (ADR 0009 section 2): filings are applied in acceptance-time order. A filing's own purchase lines are those
dated in [today - 30, today], with today = the ET date of its acceptance; a row stays alive while its last trade
date is >= today - 30 (bot/scan.py prune).
"""
import datetime as dt
import re

MIN_INSIDERS, MIN_CLUSTER_USD, MIN_SINGLE_USD, WINDOW_DAYS = 3, 100000.0, 500000.0, 30
RULE_VERSION = f"stockbot-2ffda0a/{MIN_INSIDERS}-{int(MIN_CLUSTER_USD)}-{WINDOW_DAYS}-{int(MIN_SINGLE_USD)}"
TOP = re.compile(r"(?i)\b(?:ceo|cfo|chief\s+(?:executive|financial)|principal\s+(?:executive|financial)|chair(?:man|woman|person)?)\b")
PLAN = re.compile(r"(?i)10b5-?1")
NOT_PLAN = re.compile(r"(?i)\bnot\b[^.;]{0,80}10b5-?1")
_US = "\x1f"


# ---------------------------------------------------------------- one filing (bot/form4.py parse)

def make_doc(form, issuer_cik, owners, lines, *, symbol=None, original=None, box=None, notes=()):
    """Stored rows -> the dict bot/form4.parse returns.
    owners: [(cik, name, relationship, title)] in filing order (the data sets carry the officer title, not the
    'other' text, so a director whose seniority is only in the other text gets 0.7, not 1.5); lines: dicts with trans_date, trans_code, acquired_disposed,
    shares, price, shares_after, direct_indirect, nature, fn_ids; notes: 'id<US>text' footnotes matching 10b5."""
    own = []
    for cik, name, rel, title in owners:
        flags = {x.strip() for x in (rel or "").split(",")}
        d, o = "Director" in flags, "Officer" in flags
        weight = 1.5 if (d or o) and TOP.search(title or "") else 1.0 if o else 0.7 if d else 0.0
        name = name or ""
        own.append({"cik": (cik or "").lstrip("0"), "name": name.title() if name.isupper() else name,
                     "od": d or o, "weight": weight})
    texts = dict(n.split(_US, 1) for n in notes or ())
    plan_notes = {k for k, v in texts.items() if PLAN.search(v) and not NOT_PLAN.search(v)}
    is_box = (box or "").strip().lower() in ("1", "true")
    buys = []
    for x in lines:
        if x.get("acquired_disposed") != "A" or x.get("trans_code") != "P":
            continue
        sh, px = x.get("shares"), x.get("price")
        refs = set(x.get("fn_ids") or ())
        buys.append({"date": _iso(x.get("trans_date")), "shares": sh, "price": px,
                     "value": sh * px if sh and px else None, "indirect": x.get("direct_indirect") == "I",
                     "nature": x.get("nature") or "", "post": x.get("shares_after"),
                     "plan": bool(refs & plan_notes) or (is_box and not plan_notes)})
    return {"form": form, "issuer_cik": int(issuer_cik) if str(issuer_cik).isdigit() else 0, "symbol": symbol,
            "owners": own, "buys": buys, "plan_box": is_box,
            "nd_dates": sorted({_iso(x.get("trans_date")) for x in lines if x.get("trans_date")}),
            "original": _iso(original)}


def _iso(d):
    if d is None:
        return ""
    return d.isoformat()[:10] if hasattr(d, "isoformat") else str(d)[:10]


def summarize(doc):
    """bot/form4.summarize (the fields the cluster and big tests use)."""
    b = doc["buys"]
    op = [x for x in b if not x.get("plan")]
    use = op or b
    priced = [x for x in use if x["value"]]
    value = sum(x["value"] for x in priced)
    psh = sum(x["shares"] for x in priced)
    shares = sum(x["shares"] or 0 for x in use)
    dates = sorted(x["date"] for x in use if x["date"])
    return {"shares": shares, "value": value, "price": value / psh if psh else None,
            "first": dates[0] if dates else "", "last": dates[-1] if dates else "",
            "sig": sorted(([x["date"], x["shares"], x["price"]] for x in b), key=repr),
            "indirect": any(x.get("indirect") for x in b), "kind": "open" if op else "plan"}


def insider(doc):
    """bot/form4.insider: first officer/director, else first reporting owner."""
    o = doc["owners"]
    return next((x for x in o if x["od"]), o[0] if o else {"cik": "", "name": "?", "od": False, "weight": 0.0})


def joint(entries, name="insider"):
    """bot/form4.joint: identical trades reported by two filers, either side indirect, are one purchase. The merged
    row takes the more senior identity (od, then weight), order-independent."""
    out, by = [], {}
    ident = ("od", "weight", "insider_cik")
    for e in entries:
        k = repr(e.get("sig") or "")
        m = by.get(k)
        if m is not None and (m.get("indirect") or e.get("indirect")):
            m[name] = f"{m[name]} / {e[name]}"
            m.setdefault("joint_accs", []).append(e.get("acc"))
            if (bool(e.get("od")), e.get("weight") or 0) > (bool(m.get("od")), m.get("weight") or 0):
                m.update({x: e[x] for x in ident if x in e})
            if "plan" in (m.get("kind"), e.get("kind")):
                m["kind"] = "plan"
            continue
        out.append(dict(e))
        if e.get("sig"):
            by[k] = out[-1]
    return out


# ---------------------------------------------------------------- state rows (bot/scan.py)

def make_row(acc, doc, own, filed):
    """bot/scan.make_row (rule fields only)."""
    s, who = summarize({"buys": own}), insider(doc)
    return {"acc": acc, "cik": doc["issuer_cik"], "insider": who["name"], "insider_cik": who["cik"], "od": who["od"],
            "weight": who.get("weight", 0.0),
            "first": s["first"], "last": s["last"], "shares": s["shares"],
            "value": round(s["value"], 2) if s["price"] else None, "price": round(s["price"], 4) if s["price"] else None,
            "filed": filed, "form": doc["form"] or "4", "sig": s["sig"], "indirect": s["indirect"], "kind": s["kind"],
            "original": doc.get("original") or None}


def _trade_dates(b):
    return {t[0] for t in b["sig"]} if b.get("sig") else {b["first"], b["last"]}


def amend(buys, acc, doc, row, removed=None, today=None):
    """bot/scan.amend: a 4/A replaces its original (same issuer and insider, filed on the amendment's date of original
    submission; same trades or overlapping dates), or withdraws it if it restates non-derivative lines on its dates."""
    who = insider(doc)
    same = [b for b in buys.values() if b["cik"] == doc["issuer_cik"] and b["insider_cik"] == who["cik"]
            and b["acc"] != acc]
    dated = [b for b in same if doc.get("original") and b["filed"] == doc["original"]]
    if row:
        orig = next((b for b in dated if b.get("sig") == row["sig"]), None) \
            or next((b for b in dated if _trade_dates(b) & _trade_dates(row)), None) \
            or (dated[0] if len(dated) == 1 else None) \
            or next((b for b in same if b.get("sig") == row["sig"]), None)
    else:
        hit = [b for b in dated if _trade_dates(b) & set(doc.get("nd_dates") or ())]
        orig = hit[0] if len(hit) == 1 else None
    if not orig:
        return None
    if row:
        buys[orig["acc"]] = {**row, "acc": orig["acc"], "filed": orig["filed"], "amended_by": acc}
    else:
        del buys[orig["acc"]]
        if removed is not None:
            removed[orig["acc"]] = {"by": acc, "cik": orig["cik"], "date": orig["last"]}
    return orig["acc"]


def superseded(buys, row):
    """bot/scan._superseded: the original's 4/A is already stored under the amendment's own accession."""
    return any(b.get("form") == "4/A" and not b.get("amended_by") and b["cik"] == row["cik"]
               and b["insider_cik"] == row["insider_cik"] and b.get("original") == row["filed"] for b in buys.values())


def evaluate(bs):
    """bot/scan.evaluate without the quality score: unique rows, open rows, cluster (n, $) or False, big $ or False."""
    uniq = joint(bs, "insider")
    op = [b for b in uniq if b.get("kind", "open") == "open"]
    od = [b for b in op if b["od"]]
    people, od_total = {b["insider_cik"] or b["insider"] for b in od}, sum(b["value"] or 0 for b in od)
    cluster = len(people) >= MIN_INSIDERS and od_total >= MIN_CLUSTER_USD and (len(people), od_total)
    big = max((b["value"] or 0 for b in op), default=0)
    return {"uniq": uniq, "open": op, "od": od, "cluster": cluster, "big": big >= MIN_SINGLE_USD and big}


def accs_of(b):
    return [b["acc"], *b.get("joint_accs", [])]


# ---------------------------------------------------------------- one issuer over time

class Issuer:
    """One issuer's state (bot/scan.py state['buys'], 'removed', 'alerted'), advanced filing by filing."""

    def __init__(self, cik):
        self.cik, self.buys, self.removed, self.alerted = cik, {}, {}, set()

    def prune(self, today):
        since = (today - dt.timedelta(WINDOW_DAYS)).isoformat()
        self.buys = {a: b for a, b in self.buys.items() if b["last"] >= since}
        self.removed = {a: r for a, r in self.removed.items() if r["date"] >= since}
        self.alerted &= set(self.buys)

    def apply(self, acc, doc, filed, today):
        """bot/scan.scan_day for one filing -> 'amended' | 'added' | 'skipped' | 'none'."""
        self.prune(today)
        since, until = (today - dt.timedelta(WINDOW_DAYS)).isoformat(), today.isoformat()
        own = [b for b in doc["buys"] if since <= b["date"] <= until]
        row = make_row(acc, doc, own, filed) if own else None
        if doc["form"] == "4/A" and amend(self.buys, acc, doc, row, self.removed):
            return "amended"
        if row and doc["form"] != "4/A" and (acc in self.removed or superseded(self.buys, row)):
            return "skipped"
        if row:
            if not self.buys.get(acc, {}).get("amended_by"):
                self.buys[acc] = row
                return "added"
            return "skipped"
        return "none"

    def evaluate(self):
        return evaluate(sorted(self.buys.values(), key=lambda b: (b["last"], b["acc"])))

    def events(self, acc, ev):
        """Event semantics (ADR 0009 section 2): the filing just applied must be a constituent.
        -> [(trigger, accessions, people, total)]."""
        out = []
        if ev["cluster"]:
            od_accs = sorted({a for b in ev["od"] for a in accs_of(b)})
            if acc in od_accs:
                out.append(("cluster", od_accs, ev["cluster"][0], float(ev["cluster"][1])))
        for b in ev["open"]:
            if (b["value"] or 0) >= MIN_SINGLE_USD and acc in accs_of(b):
                out.append(("big", sorted(accs_of(b)), None, float(b["value"])))
        return out

    def live_alert(self, ev):
        """bot/scan.alerts condition for this issuer: (cluster or big) and a counted row not alerted yet. Marks every
        stored row as alerted, as a delivered message does. -> journal-style snapshot dict or None."""
        if not (ev["cluster"] or ev["big"]):
            return None
        if not any(not (set(accs_of(b)) & self.alerted) for b in ev["open"]):
            return None
        self.alerted |= set(self.buys)
        kind = "+".join(k for k, v in (("cluster", ev["cluster"]), ("big", ev["big"])) if v)
        return {"kind": kind, "accs": sorted({a for b in ev["uniq"] for a in accs_of(b)}),
                "total": round(sum(b["value"] or 0 for b in ev["open"]), 2)}
