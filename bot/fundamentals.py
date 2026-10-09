"""Forensic scores from SEC XBRL companyfacts: Piotroski F, Altman Z, Beneish M -> Hebrew lines.
Annual 10-K figures; Piotroski and Altman are refreshed to trailing-12-months (TTM) when a newer 10-Q exists."""
import datetime as dt

from bot import common, market
from bot.common import code

FORMS = {"10-K", "10-K/A", "10-KT", "10-KT/A"}
QFORMS = {"10-Q", "10-Q/A"}
Q_MAX_DAYS = 300  # a 10-Q ends at most ~9 months (Q3 of a 53-week year: ~280 days) after the FY end it follows
STALE_DAYS = 274  # ~9 months: older than this, the newest filing used is flagged
YOUNG_DAYS = 1826  # ~5 years: earliest XBRL filing newer than this -> Beneish false-positive caveat
SGI_HIGH = 1.30
FLOW = {  # duration items, fallback tags in priority order
    "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet",
            "SalesRevenueServicesNet"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices"],
    "gp": ["GrossProfit"],
    "ni": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "cont": ["IncomeLossFromContinuingOperations",
             "IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "ebit": ["OperatingIncomeLoss",
             "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
             "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "dep": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "Depreciation",
            "DepreciationAmortizationAndAccretionNet"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "sm": ["SellingAndMarketingExpense", "MarketingExpense", "SellingExpense"],
    "ga": ["GeneralAndAdministrativeExpense"],
    "sh": ["WeightedAverageNumberOfSharesOutstandingBasic", "WeightedAverageNumberOfDilutedSharesOutstanding"],
}
INST = {  # balance-sheet instants
    "ta": ["Assets"], "ca": ["AssetsCurrent"], "cl": ["LiabilitiesCurrent"], "tl": ["Liabilities"],
    "lse": ["LiabilitiesAndStockholdersEquity"],
    "eq": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "eqn": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"],
    "ltd": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt",
            "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", "ConvertibleLongTermNotesPayable",
            "LongTermNotesPayable"],
    "re": ["RetainedEarningsAccumulatedDeficit"],
    "ar": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent", "AccountsAndOtherReceivablesNetCurrent",
           "AccountsNotesAndLoansReceivableNetCurrent", "AccountsReceivableNet"],
    "ppe": ["PropertyPlantAndEquipmentNet",
            "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
}
TAGS = FLOW | INST
SHARES = "EntityCommonStockSharesOutstanding"
BENEISH = {"DSRI": .920, "GMI": .528, "AQI": .404, "SGI": .892, "DEPI": .115, "SGAI": -.172, "TATA": 4.679,
           "LVGI": -.327}
PIO_IN = {"roa": ("ni0", "ta1"), "cfo": ("cfo0",), "droa": ("ni0", "ta1", "ni1", "ta2"), "accr": ("cfo0", "ni0"),
          "lev": ("ltd0", "ta0", "ltd1", "ta1"), "liq": ("ca0", "cl0", "ca1", "cl1"), "sh": ("sh0", "sh1"),
          "gm": ("gp0", "rev0", "gp1", "rev1"), "at": ("rev0", "ta1", "rev1", "ta2")}
PIO_KEYS = sorted({k for ks in PIO_IN.values() for k in ks})
BEN_IN = tuple(f"{f}{i}" for f in ("rev", "gp", "ar", "ca", "ppe", "ta", "dep", "sga", "cl") for i in (0, 1)) \
    + ("cont0", "cfo0")
DERIVED = (("gp", "rev", "cogs", -1, "rev-cogs"), ("tl", "lse", "eqn", -1, "lse-eq"), ("sga", "sm", "ga", 1, "sm+ga"))
_d = dt.date.fromisoformat


def _today():
    return dt.date.today()


def _days(a, b):
    return (_d(a) - _d(b)).days


def _facts(g, tag, forms, flow):
    """{period: (val, form, filed, accn)} of the given forms; period = (start, end) for flows, end for instants;
    the latest filing wins per period."""
    best = {}
    for unit in ("USD", "shares"):
        for e in g.get(tag, {}).get("units", {}).get(unit, []):
            if e["form"] not in forms or ("start" in e) != flow:
                continue
            p = (e["start"], e["end"]) if flow else e["end"]
            if p not in best or (e["filed"], e["accn"]) > best[p][2:]:
                best[p] = (e["val"], e["form"], e["filed"], e["accn"])
    return best


def _series(g, tag, flow):
    """{end: (val, form, filed)} from 10-K facts; flows must span 350-380 days; the latest filing wins per period end."""
    best = {}
    for p, f in _facts(g, tag, FORMS, flow).items():
        if flow and not 350 <= _days(p[1], p[0]) <= 380:
            continue
        end = p[1] if flow else p
        if end not in best or f[2:] > best[end][2:]:
            best[end] = f
    return {end: f[:3] for end, f in best.items()}


def _chain(q, e, fy0):
    """YTD 10-Q periods [(start, end)]: the one ending at e that starts the day after the FY end fy0 (the YTD,
    not the 3-month fact), then the comparatives ending ~1y and ~2y earlier with the same duration (+-7 days)."""
    cur = [p for p in q if p[1] == e and abs(_days(p[0], fy0) - 1) <= 3]
    chain = [min(cur, key=lambda p: abs(_days(p[0], fy0) - 1))] if cur else []
    while chain and len(chain) < 3:
        s, end = chain[-1]
        dur = _days(end, s)
        prev = [p for p in q if abs(_days(end, p[1]) - 365) <= 7 and abs(_days(p[1], p[0]) - dur) <= 7]
        if not prev:
            break
        chain.append(min(prev, key=lambda p: (abs(_days(end, p[1]) - 365), abs(_days(p[1], p[0]) - dur))))
    return chain


def _ttm(g, tag, fy, e):
    """Per tag ({0: TTM at quarter end e, 1: TTM a year earlier}, {0: YTD at e, 1: YTD a year earlier}) as
    (val, form, filed). TTM = FY(last FY end before the quarter) + YTD - prior-year YTD, all from this tag;
    form/filed = the latest-filed of the three facts."""
    ann, q = _series(g, tag, True), _facts(g, tag, QFORMS, True)
    chain = _chain(q, e, fy[0])
    ttm, ytd = {}, {}
    for i, p in enumerate(chain[:2]):
        ytd[i] = q[p][:3]
        f = fy[i] if i < len(fy) else None
        if i + 1 < len(chain) and f in ann and 0 < _days(p[1], f) <= Q_MAX_DAYS and abs(_days(p[0], f) - 1) <= 7:
            y, prior = q[p][:3], q[chain[i + 1]][:3]
            last = max((y, prior, ann[f]), key=lambda c: c[2])
            ttm[i] = (ann[f][0] + y[0] - prior[0], last[1], last[2])
    return ttm, ytd


def _inst_q(g, tag, e):
    """{0: at quarter end e, 1: ~1y earlier, 2: ~2y earlier} balance-sheet instants from any 10-K/10-Q
    (+-7 days for the earlier ones; the latest filing wins per date)."""
    s, out, want = _facts(g, tag, FORMS | QFORMS, False), {}, e
    for i in range(3):
        near = [d for d in s if abs(_days(d, want)) <= (7 if i else 0)]
        if near:
            want = min(near, key=lambda d: abs(_days(d, want)))
            out[i] = s[want][:3]
        want = (_d(want) - dt.timedelta(365)).isoformat()
    return out


def _pick(ser, dates):
    """Per date (val, tag, form, filed): the tag covering most of the two compared years wins (ties -> list order),
    remaining gaps are filled per year from the other tags."""
    ser = sorted(ser, key=lambda ts: -sum(d in ts[1] for d in dates[:2]))
    return [next(((s[d][0], t, *s[d][1:]) for t, s in ser if d in s), (None,) * 4) for d in dates]


def _values(x):
    """Picks -> (values, source tag per field, (form, filed) per value), with the derived fallbacks flagged in src."""
    v = {f: [p[0] for p in ps] for f, ps in x.items()}
    meta = {f: [p[2:] if p[0] is not None else None for p in ps] for f, ps in x.items()}
    src = {f: next((p[1] for p in ps if p[1]), None) for f, ps in x.items()}
    for i in range(3):
        for f, a, b, sign, how in DERIVED:
            if v[f][i] is None and None not in (v[a][i], v[b][i]):
                v[f][i], src[f] = v[a][i] + sign * v[b][i], how
                meta[f][i] = max(meta[a][i], meta[b][i], key=lambda m: m[1])
        if v["cont"][i] is None and v["ni"][i] is not None:
            v["cont"][i], src["cont"], meta["cont"][i] = v["ni"][i], src["ni"], meta["ni"][i]
    return v, src, meta


def _basis(kind, end, keys, meta):
    """What a score stands on: FY/TTM, period end, and the latest-filed fact among its current-period inputs."""
    got = [m for k in keys if k[-1] == "0" and (m := meta[k[:-1]][0])]
    form, filed = max(got, key=lambda m: m[1], default=(None, None))
    return {"kind": kind, "end": end, "form": form, "filed": filed}


QUOTED = {}  # ticker -> market.quote() result of this process (source "cache" -> the report says so)


def quote(ticker, since):
    """(last price, split factor after date `since`) via market.quote: live Yahoo, else the last cached price (the
    report then shows its date), else (None, 1.0) and the caller degrades to Z''. shares_on_since x factor = shares
    on today's basis (1:30 reverse split -> 1/30)."""
    q = QUOTED[ticker] = market.quote(ticker, since)
    return q["price"], q["split"]


def _div(a, b):
    return None if a is None or not b else a / b


def _getter(v):
    """'ni0' -> v['ni'][0] (field + period index: 0 = current, 1 = a year earlier, 2 = two years earlier)."""
    return lambda k: v[k[:-1]][int(k[-1])]


def _piotroski(V, need):
    roa = [_div(V("ni0"), V("ta1")), _div(V("ni1"), V("ta2"))]
    lev = [_div(V("ltd0"), V("ta0")), _div(V("ltd1"), V("ta1"))]
    cr = [_div(V("ca0"), V("cl0")), _div(V("ca1"), V("cl1"))]
    gm = [_div(V("gp0"), V("rev0")), _div(V("gp1"), V("rev1"))]
    at = [_div(V("rev0"), V("ta1")), _div(V("rev1"), V("ta2"))]
    crit = [  # (id, pass?, shown values, format); inputs in PIO_IN
        ("roa", lambda: roa[0] > 0, roa[:1], "pct"),
        ("cfo", lambda: V("cfo0") > 0, [V("cfo0")], "usd"),
        ("droa", lambda: roa[0] > roa[1], roa, "pct"),
        ("accr", lambda: V("cfo0") > V("ni0"), [V("cfo0"), V("ni0")], "usd"),
        ("lev", lambda: lev[0] <= lev[1], lev, "pct"),
        ("liq", lambda: cr[0] > cr[1], cr, "x"),
        ("sh", lambda: V("sh0") <= V("sh1"), [V("sh0"), V("sh1")], "qty"),
        ("gm", lambda: gm[0] > gm[1], gm, "pct"),
        ("at", lambda: at[0] > at[1], at, "x"),
    ]
    out = []
    for cid, test, vals, kind in crit:
        miss = need(*PIO_IN[cid])
        out.append({"id": cid, "ok": None if miss or None in vals else test(), "vals": vals, "kind": kind, "miss": miss})
    return out


def _altman(V, need, price, shares):
    """Z with market value of equity (price x shares), else Z'' on book equity; any missing input -> no score."""
    a = {"miss": need(*_alt_keys(price))}
    ta, tl = V("ta0"), V("tl0")
    if not a["miss"] and ta and tl:
        parts = [(V("ca0") - V("cl0")) / ta, V("re0") / ta, V("ebit0") / ta]
        if price:
            a["mve"] = price * shares
            parts, w = parts + [a["mve"] / tl, V("rev0") / ta], (1.2, 1.4, 3.3, 0.6, 1.0)
        else:
            parts, w = parts + [V("eq0") / tl], (6.56, 3.26, 6.72, 1.05)
        a.update(parts=parts, z=sum(p * k for p, k in zip(w, parts)))
    return a


def _quarter(g, fy):
    """Latest 10-Q balance-sheet date after the last 10-K year end (within Q_MAX_DAYS: a 10-Q that follows a missing
    10-K cannot be bridged to TTM), plus its TTM/YTD/instant picks; None when there is none."""
    ends = [d for d in _facts(g, INST["ta"][0], QFORMS, False) if 0 < _days(d, fy[0]) <= Q_MAX_DAYS]
    if not ends:
        return None
    e = max(ends)
    xq = {}
    for f, tags in FLOW.items():  # weighted-average shares are not additive: the YTD value itself
        xq[f] = _pick([(t, _ttm(g, t, fy, e)[f == "sh"]) for t in tags], [0, 1, 2])
    xq.update({f: _pick([(t, _inst_q(g, t, e)) for t in tags], [0, 1, 2]) for f, tags in INST.items()})
    v, src, meta = _values(xq)
    gone = {f"{f}{i}" for f in v for i in range(3) if v[f][i] is None}  # before the long-term-debt rule
    if None in v["ltd"][:2]:  # same rule as the annual figures: missing long-term debt counts as 0
        v["ltd"] = [0 if a is None else a for a in v["ltd"]]
    need = lambda *keys: sorted({TAGS[k[:-1]][0] for k in keys if _getter(v)(k) is None})
    return {"end": e, "x": xq, "v": v, "src": src, "meta": meta, "gone": gone, "need": need}


def analyze(cik, ticker, sic):
    """companyfacts -> raw inputs + Piotroski/Altman/Beneish results (numbers only; Hebrew is in format_he)."""
    res = {"ticker": ticker, "sic": sic, "notes": []}
    cf = common.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json")
    g = (cf or {}).get("facts", {}).get("us-gaap")
    if not g:
        return {**res, "error": "nofacts" if cf is None else "nogaap"}
    flows = {f: [(t, _series(g, t, True)) for t in tags] for f, tags in FLOW.items()}
    ends = sorted({d for f in ("rev", "ni", "cfo") for _, s in flows[f] for d in s}, reverse=True)
    if not ends:
        return {**res, "error": "no10k"}
    fy = ends[:1]  # fiscal-year spine: t, then the latest annual end 330-400 days before, twice
    for d in ends:
        if len(fy) < 3 and 330 <= (_d(fy[-1]) - _d(d)).days <= 400:
            fy.append(d)
    dates = fy + [None] * (3 - len(fy))
    x = {f: _pick(ser, dates) for f, ser in flows.items()}
    x.update({f: _pick([(t, _series(g, t, False)) for t in tags], dates) for f, tags in INST.items()})
    v, src, meta = _values(x)
    gone = {f"{f}{i}" for f in v for i in range(3) if v[f][i] is None}  # before the long-term-debt rule
    if None in v["ltd"][:2]:  # spec: missing long-term debt counts as 0
        res["notes"].append("ltd0")
        v["ltd"] = [0 if a is None else a for a in v["ltd"]]
    res["notes"] += [f for f in ("gp", "tl", "sga") if src[f] in ("rev-cogs", "lse-eq", "sm+ga")]
    res.update(fy=fy, v=v, src=src, form=next((x[f][0][2] for f in ("ni", "rev", "cfo") if x[f][0][2]), "10-K"))
    today = _today()
    first = min((e["filed"] for tax in cf["facts"].values() for c in tax.values()
                 for es in c.get("units", {}).values() for e in es), default=None)
    res["first_filed"] = first

    def need(*keys):
        """Missing tag names for keys like 'ni0' (field + year index: 0 = t, 1 = t-1, 2 = t-2)."""
        return sorted({TAGS[k[:-1]][0] if dates[int(k[-1])] else "דוח שנתי קודם" for k in keys
                       if v[k[:-1]][int(k[-1])] is None})

    V = _getter(v)
    q = _quarter(g, fy)
    res["ttm"] = q and {"end": q["end"], "v": q["v"], "src": q["src"]}
    # Piotroski on TTM only when that loses nothing: every input and criterion the FY computation has must exist
    res["pio"], res["basis"] = _piotroski(V, need), {"pio": _basis("FY", fy[0], PIO_KEYS, meta)}
    if q:
        pq = _piotroski(_getter(q["v"]), q["need"])
        if (any(c["ok"] is not None for c in pq) and not (set(PIO_KEYS) - gone) & q["gone"]
                and all(b["ok"] is not None for a, b in zip(res["pio"], pq) if a["ok"] is not None)):
            res["pio"], res["basis"]["pio"] = pq, _basis("TTM", q["end"], PIO_KEYS, q["meta"])
            _qnote(res, q, "gp", "rev-cogs")
    if sic and 6000 <= sic <= 6799:
        return _fresh({**res, "fin": True}, today)

    # Altman Z: classic with market value of equity (Yahoo price x cover-page shares), else Z'' on book equity
    # price the common stock (not the warrant/preferred the user may have typed); why = reason for Z''
    alt = {"ebit_tag": x["ebit"][0][1], "px": common.cik_tickers().get(int(cik), ticker), "price": None, "split": 1}
    es = cf["facts"].get("dei", {}).get(SHARES, {}).get("units", {}).get("shares", [])
    last = max(es, key=lambda e: (e["end"], e["filed"], e["accn"]), default=None)
    if last:  # multi-class: sum the classes that filing reports at that date
        alt.update(shares=sum(e["val"] for e in es if e["accn"] == last["accn"] and e["end"] == last["end"]),
                   shares_date=last["end"])
    if not last or alt["shares"] <= 0:
        alt["why"] = "noshares"
    elif (_d(fy[0]) - _d(last["end"])).days > 365:
        alt["why"] = "stale"
    elif common.noncommon(alt["px"]):
        alt["why"] = "noncommon"
    else:
        alt["price"], alt["split"] = quote(alt["px"], last["end"])
        src = QUOTED.get(alt["px"], {})
        if src.get("source") == "cache":  # Yahoo down: a saved price, but splits since the cover date are unknown
            alt.update(price=None, split=1, why="cached", price_asof=src["asof"])
        else:
            if src.get("source") == "stale":  # Yahoo's last trade is old (halted / not trading): shown with its date
                alt["price_asof"] = src["asof"]
            alt["shares"] *= alt["split"]  # a split after the cover-page date (BYND 1:30) must not scale MVE 30x
            alt["why"] = None if alt["price"] else "noprice"
    alt["kind"] = "Z" if alt["price"] else "Z''"
    keys = _alt_keys(alt["price"])
    zq = q and _altman(_getter(q["v"]), q["need"], alt["price"], alt.get("shares"))
    if zq and "z" in zq:  # Z needs every input, so a TTM score means nothing was lost: the fresher figure wins
        alt.update(zq, ebit_tag=q["x"]["ebit"][0][1])
        res["basis"]["alt"] = _basis("TTM", q["end"], keys, q["meta"])
        _qnote(res, q, "tl", "lse-eq")
    else:
        alt.update(_altman(V, need, alt["price"], alt.get("shares")))
        res["basis"]["alt"] = _basis("FY", fy[0], keys, meta)

    # Beneish M (8 variables, annual indices -> always FY): any missing input -> no score (never a neutral 1.0)
    ben = {"miss": need(*BEN_IN)}
    res["basis"]["ben"] = _basis("FY", fy[0], BEN_IN + ("ltd0",), meta)
    if not ben["miss"]:
        r = lambda f, i: _div(V(f"{f}{i}"), V(f"rev{i}"))
        aq = [_div(V(f"ta{i}") - V(f"ca{i}") - V(f"ppe{i}"), V(f"ta{i}")) for i in (0, 1)]
        dp = [_div(V(f"dep{i}"), V(f"dep{i}") + V(f"ppe{i}")) for i in (0, 1)]
        lv = [_div(V(f"cl{i}") + V(f"ltd{i}"), V(f"ta{i}")) for i in (0, 1)]
        ben["idx"] = idx = {
            "DSRI": _div(r("ar", 0), r("ar", 1)), "GMI": _div(r("gp", 1), r("gp", 0)), "AQI": _div(*aq),
            "SGI": _div(V("rev0"), V("rev1")), "DEPI": _div(dp[1], dp[0]), "SGAI": _div(r("sga", 0), r("sga", 1)),
            "TATA": _div(V("cont0") - V("cfo0"), V("ta0")), "LVGI": _div(*lv)}
        if None not in idx.values():
            ben["m"] = -4.84 + sum(BENEISH[k] * idx[k] for k in idx)
            # fast growers and young companies score high without any manipulation: say which applies. "Young" is
            # approximated by the earliest filing date in companyfacts (the IPO date itself is not in the data)
            growth = idx["SGI"] > SGI_HIGH
            young = bool(first) and (today - _d(first)).days < YOUNG_DAYS
            if growth or young:
                ben["caveat"] = {"growth": idx["SGI"] - 1 if growth else None, "first": first[:4] if young else None}
    return _fresh({**res, "fin": False, "alt": alt, "ben": ben}, today)


def _alt_keys(price):
    """Altman inputs: Z uses revenue (and market value), Z'' uses book equity instead."""
    return "ca0", "cl0", "re0", "ebit0", "ta0", "tl0", "rev0" if price else "eq0"


def _qnote(res, q, f, how):
    """A derived TTM input (gross profit, liabilities) is noted like the annual one."""
    if q["src"][f] == how and f not in res["notes"]:
        res["notes"].append(f)


def _fresh(res, today):
    """The newest filing behind any score, flagged when it was filed more than STALE_DAYS before today."""
    newest = max((b["filed"] for b in res["basis"].values() if b["filed"]), default=None)
    return {**res, "newest": newest, "stale": bool(newest) and (today - _d(newest)).days > STALE_DAYS}


# ---------- Hebrew ----------
LABEL = {"roa": "תשואה על הנכסים חיובית", "cfo": "תזרים מפעילות שוטפת חיובי",
         "droa": "תשואה על הנכסים השתפרה", "accr": "תזרים מפעילות גבוה מהרווח הנקי",
         "lev": "מינוף לא עלה (חוב לזמן ארוך/נכסים)", "liq": "יחס שוטף עלה",
         "sh": "לא הונפקו מניות (ממוצע משוקלל)", "gm": "שיעור הרווח הגולמי עלה", "at": "מחזור הנכסים עלה"}
ERR = {"nofacts": "אין לחברה נתונים כספיים ב־SEC.",
       "nogaap": "אין עדיין דוח שנתי אמריקאי (חברה חדשה בבורסה, או חברה זרה שמדווחת אחרת).",
       "no10k": "לא נמצא דוח שנתי אמריקאי (ייתכן שזו חברה זרה)."}


def altman_zone(kind, z):
    """Altman Z (public company) or Z'' (book equity) -> its zone in words."""
    hi, lo = (2.99, 1.81) if kind == "Z" else (2.60, 1.10)
    return "אזור בטוח" if z > hi else "אזור אפור" if z >= lo else "אזור מצוקה"


def beneish_zone(m):
    return "סיכון גבוה לתמרון רווחים" if m > -1.78 else "אזור ביניים" if m > -2.22 else "סיכון נמוך לתמרון רווחים"


def _flow(name, x, neg=None):
    """A yearly figure in words; a negative one says so in words ("הפסד נקי"), never with a minus sign."""
    if x is None:
        return f"{name}: חסר"
    return f"{neg if x < 0 and neg else name}: {common.amount(x)}" + (" (שלילי)" if x < 0 and not neg else "")


def format_he(res):
    """Result dict -> short Hebrew lines: the year's figures, then one plain verdict per score (details stay in the
    numbers behind them, not in the message)."""
    out = ["🧮 <b>בריאות פיננסית</b>"]
    if res.get("error"):
        return out + ["ℹ️ " + ERR[res["error"]]]
    ttm = res.get("ttm")
    when = (f"ב־4 הרבעונים עד {code(common.il_date(ttm['end']))}" if ttm else
            f"בשנת הכספים שהסתיימה ב־{code(common.il_date(res['fy'][0]))}")

    def fig(f, name, neg=None):  # the 4-quarter figure, else the yearly one (said so)
        x = ttm["v"][f][0] if ttm else None
        if x is None and ttm and res["v"][f][0] is not None:
            return _flow(name, res["v"][f][0], neg) + " (שנתי)"
        return _flow(name, x if ttm else res["v"][f][0], neg)
    out.append(f"{when}: " + " · ".join((fig("rev", "הכנסות"), fig("ni", "רווח נקי", "הפסד נקי"),
                                         fig("cfo", "תזרים מפעילות"))))
    if res.get("stale"):
        out.append(f"⚠️ הדוח האחרון ישן: הוגש ב־{code(common.il_date(res['newest']))}.")
    p = res["pio"]
    n, k = sum(x["ok"] is not None for x in p), sum(x["ok"] is True for x in p)
    level = ", " + ("חזק" if k >= 7 else "חלש" if k <= 3 else "בינוני") if n == 9 else ""  # no verdict on part of it
    out.append(f"פיוטרוסקי (איכות): {code(k)} מתוך {code(n)}{level}" + (f" ({code(9 - n)} לא חושבו)" if n < 9 else ""))
    bad = [LABEL[x["id"]] for x in p if x["ok"] is False]
    if bad:
        out.append("❌ לא עמדה ב: " + ", ".join(bad) + ".")
    if res["fin"]:
        return out + ["ℹ️ אלטמן ובנייש לא מתאימים לבנק, לחברת ביטוח או לחברת השקעות."]
    a, b = res["alt"], res["ben"]
    out.append("אלטמן (סיכון פשיטת רגל): " + (altman_zone(a["kind"], a["z"]) if "z" in a else "לא חושב, חסרים נתונים"))
    out.append("בנייש (תמרון רווחים): " + (beneish_zone(b["m"]) if "m" in b else "לא חושב, חסרים נתונים"))
    if "m" in b and b.get("caveat"):
        out.append("⚠️ בחברה צעירה או שצומחת מהר, בנייש מתריע לא פעם לשווא.")
    return out
