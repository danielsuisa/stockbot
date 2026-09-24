"""Forensic scores from SEC XBRL companyfacts: Piotroski F, Altman Z, Beneish M -> Hebrew lines.
Annual 10-K figures; Piotroski and Altman are refreshed to trailing-12-months (TTM) when a newer 10-Q exists."""
import datetime as dt

from bot import common, market
from bot.common import code, money

FORMS = {"10-K", "10-K/A", "10-KT", "10-KT/A"}
QFORMS = {"10-Q", "10-Q/A"}
Q_MAX_DAYS = 300  # a 10-Q ends at most ~9 months (Q3 of a 53-week year: ~280 days) after the FY end it follows
STALE_DAYS = 274  # ~9 months: older than this, the newest filing used is flagged
BUDGET = 1500  # visible chars for the whole section; past it the optional TTM summary line is dropped
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
TTM_LINE = "ארבעת הרבעונים האחרונים (TTM)"
LABEL_SHQ = f"לא הונפקו מניות (ממוצע משוקלל מתחילת השנה ב־{code('10-Q')}, לא TTM)"  # not additive
FMT = {"pct": lambda a: f"{a * 100:.1f}%", "usd": money, "x": lambda a: f"{a:.2f}",
       "qty": lambda a: f"{a / 1e9:.3f}B" if a >= 1e9 else f"{a / 1e6:.2f}M"}
ERR = {"nofacts": "אין נתוני XBRL של SEC לחברה זו (companyfacts לא פורסם).",
       "nogaap": f"אין עדיין נתוני {code('us-gaap')} מדוח שנתי (חברה חדשה בבורסה, או חברה זרה המדווחת לפי IFRS"
                 f" בטופס {code('20-F')}/{code('40-F')}).",
       "no10k": f"לא נמצאו נתונים שנתיים מדוחות {code('10-K')} (ייתכן חברה זרה המגישה {code('20-F')})."}


def _c(a, f=money):
    return "חסר" if a is None else code(f(a))


def _tags(ts, cap=4):
    more = f" ועוד {code(len(ts) - cap)}" if len(ts) > cap else ""
    return "חסר " + ", ".join(code(t) if t.isascii() else t for t in ts[:cap]) + more


def _basis_he(b):
    """' (10-K FY 2025-09-27, הוגש 2025-10-31)' or ' (TTM עד 2026-06-27, 10-Q הוגש 2026-07-31)'."""
    filed = f"הוגש {code(b['filed'])}" if b["filed"] else ""
    if b["kind"] == "TTM":
        return f" (TTM עד {code(b['end'])}" + (f", {code(b['form'])} {filed}" if filed else "") + ")"
    return f" ({code(b['form'] or '10-K')} FY {code(b['end'])}" + (f", {filed}" if filed else "") + ")"


def _caveat(c):
    """Beneish false-positive warning: revenue growth above 30% and/or a company public for under ~5 years."""
    why = []
    if c["growth"] is not None:
        why.append(f"צמיחת הכנסות של {code('%+.0f%%' % (c['growth'] * 100))} בשנה")
    if c["first"]:
        why.append(f"כנראה פחות מ־5 שנים בבורסה (דיווח ראשון ב־XBRL של SEC: {code(c['first'])}, הערכה)")
    return "⚠️ סיכון גבוה לחיובי־שגוי בבנייש: " + " · ".join(why)


def format_he(res):
    """Result dict -> Hebrew Telegram HTML lines (RTL-safe, <= ~1500 visible chars)."""
    basis = res.get("basis", {})
    ttm = any(b["kind"] == "TTM" for b in basis.values())
    out = [f"<b>🧮 ציונים פורנזיים מדוחות {code('10-K')}" + (f" ו־{code('10-Q')}" if ttm else "") + "</b>"]
    if res.get("error"):
        return out + ["ℹ️ " + ERR[res["error"]]]
    fy, v, p = res["fy"], res["v"], res["pio"]
    out.append(f"שנות כספים: {code(fy[0])}" + (f" מול {code(fy[1])}" if len(fy) > 1 else " (אין שנה קודמת)")
               + f" ({code(res['form'])})")
    out.append(f"הכנסות {_c(v['rev'][0])} · רווח נקי {_c(v['ni'][0])} · תזרים מפעילות {_c(v['cfo'][0])}"
               f" · נכסים {_c(v['ta'][0])}")
    if ttm:
        t = res["ttm"]["v"]
        out.append(f"{TTM_LINE} עד {code(res['ttm']['end'])}: הכנסות {_c(t['rev'][0])} · רווח נקי {_c(t['ni'][0])}"
                   f" · תזרים מפעילות {_c(t['cfo'][0])}")
    if res.get("stale"):
        out.append(f"⚠️ נתונים פונדמנטליים ישנים (הדוח האחרון הוגש {code(res['newest'])})")
    n, k = sum(x["ok"] is not None for x in p), sum(x["ok"] is True for x in p)
    out.append(f"<b>פיוטרוסקי F: {code(f'{k}/{n}')}</b>" + _basis_he(basis["pio"])
               + (f" · {code(9 - n)} קריטריונים חסרים" if n < 9 else ""))
    for x in p:
        label = LABEL_SHQ if x["id"] == "sh" and basis["pio"]["kind"] == "TTM" else LABEL[x["id"]]
        if x["ok"] is None:
            out.append(f"➖ {label}: " + (_tags(x["miss"]) if x["miss"] else "לא ניתן לחישוב (מכנה אפס)"))
        else:
            vals = " מול ".join(code(FMT[x["kind"]](a)) for a in x["vals"])
            out.append(f"{'✅' if x['ok'] else '❌'} {label}: {vals}")
    if res["fin"]:
        sic = f"SIC {res['sic']}"
        out.append(f"⏭️ אלטמן ובנייש לא חושבו: חברה פיננסית ({code(sic)}) – המודלים לא מתאימים למאזן של בנקים,"
                   " חברות ביטוח וחברות השקעה.")
        return _fit(out + _notes(res))
    a, b = res["alt"], res["ben"]
    if "z" not in a:
        out.append("<b>אלטמן Z:</b> " + (_tags(a["miss"]) if a["miss"] else "לא ניתן לחישוב (מכנה אפס)")
                   + _basis_he(basis["alt"]))
    else:
        hi, lo = (2.99, 1.81) if a["kind"] == "Z" else (2.60, 1.10)
        zone = "אזור בטוח" if a["z"] > hi else "אזור אפור" if a["z"] >= lo else "אזור מצוקה"
        z, e = f"{a['z']:.2f}", a["ebit_tag"]
        out.append(f"<b>אלטמן {code(a['kind'])}: {code(z)}</b> · {zone}" + _basis_he(basis["alt"]))
        ebit = (f"רווח תפעולי: {code(e)}" if e == TAGS["ebit"][0] else
                f"רווח תפעולי ({code(TAGS['ebit'][0])}) חסר, שימש רווח לפני מס: {code(e)}")
        if a["kind"] == "Z":
            px = common.price(a["price"]) + (f" ({a['px']})" if a["px"] != res["ticker"] else "")
            split = f" (מותאם לפיצול מניות: {code('×%.4g' % a['split'])})" if a["split"] != 1 else ""
            split += f" (⚠ מחיר מ־{code(a['price_asof'])})" if a.get("price_asof") else ""
            out.append(f"שווי שוק {_c(a['mve'])} = מחיר {code(px)} × "
                       f"{_c(a['shares'], FMT['qty'])} מניות ({code(a['shares_date'])}){split} · {ebit}")
        else:
            why = {"stale": f"מספר המניות בעמוד השער ישן ({code(a.get('shares_date'))})",
                   "noncommon": f"לחברה אין מניה רגילה ברשימת SEC ({code(a['px'])})",
                   "noprice": "מחיר המניה מ־Yahoo לא זמין",
                   "cached": f"מחיר חי מ־Yahoo לא זמין (המחיר השמור מ־{code(a.get('price_asof'))} לא משמש לשווי שוק,"
                             " כי לא ניתן לבדוק אם היה פיצול מניות מאז)",
                   "noshares": f"מספר המניות בעמוד השער חסר ({code('dei:' + SHARES)})"}[a["why"]]
            out.append(f"⚠️ {why} – לכן חושב {code(a['kind'])} על בסיס הון עצמי בספרים במקום שווי שוק · {ebit}")
    if "m" in b:
        zone = "סיכון גבוה לתמרון רווחים" if b["m"] > -1.78 else "אזור ביניים" if b["m"] > -2.22 else "סיכון נמוך"
        m = f"{b['m']:.2f}"
        out.append(f"<b>בנייש M: {code(m)}</b> · {zone}" + _basis_he(basis["ben"]))
        out.append("מדדים: " + " ".join(code(f"{i} {val:.2f}") for i, val in b["idx"].items()))
        if b.get("caveat"):
            out.append(_caveat(b["caveat"]))
    elif b["miss"]:
        out.append("<b>בנייש M:</b> " + _tags(b["miss"]) + _basis_he(basis["ben"]))
    else:
        zero = [i for i, val in b["idx"].items() if val is None]
        out.append("<b>בנייש M:</b> לא ניתן לחישוב (מכנה אפס ב־" + ", ".join(code(i) for i in zero) + ")"
                   + _basis_he(basis["ben"]))
    return _fit(out + _notes(res))


def _fit(out):
    """Keep the section within BUDGET visible chars: the TTM summary line is the one optional line."""
    if common.visible("\n".join(out)) > BUDGET:
        out = [s for s in out if not s.startswith(TTM_LINE)]
    return out


def _notes(res):
    q = (res.get("ttm") or {}).get("src") or {}
    s = {k: t or q.get(k) for k, t in res["src"].items()}  # a note raised by the TTM figures names their tags
    msg = {"ltd0": f"חוב לזמן ארוך לא דווח ({code(TAGS['ltd'][0])}) – נחשב כ־{code(0)} לפי הכללים",
           "gp": f"רווח גולמי חושב: הכנסות פחות {code(s['cogs'] or '')}",
           "tl": f"התחייבויות חושבו: {code('LiabilitiesAndStockholdersEquity')} פחות הון עצמי",
           "sga": f"הוצאות מכירה והנהלה חושבו: {code(s['sm'] or '')} ועוד {code(s['ga'] or '')}"}
    return [f"ℹ️ {msg[k]}" for k in res["notes"]]
