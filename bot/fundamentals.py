"""Forensic scores from SEC XBRL companyfacts (10-K only): Piotroski F, Altman Z, Beneish M -> Hebrew lines."""
import datetime as dt
import json
import time

from bot import common
from bot.common import code, money

FORMS = {"10-K", "10-K/A", "10-KT", "10-KT/A"}
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
_d = dt.date.fromisoformat


def _series(g, tag, flow):
    """{end: (val, form)} from 10-K facts; flows must span 350-380 days; the latest filing wins per period end."""
    best = {}
    for unit in ("USD", "shares"):
        for e in g.get(tag, {}).get("units", {}).get(unit, []):
            if e["form"] not in FORMS or ("start" in e) != flow:
                continue
            if flow and not 350 <= (_d(e["end"]) - _d(e["start"])).days <= 380:
                continue
            k = (e["filed"], e["accn"])
            if e["end"] not in best or k > best[e["end"]][0]:
                best[e["end"]] = (k, e["val"], e["form"])
    return {end: (v, f) for end, (_, v, f) in best.items()}


def _pick(ser, dates):
    """Per date (val, tag, form): the tag covering most of the two compared years wins (ties -> list order),
    remaining gaps are filled per year from the other tags."""
    ser = sorted(ser, key=lambda ts: -sum(d in ts[1] for d in dates[:2]))
    return [next(((s[d][0], t, s[d][1]) for t, s in ser if d in s), (None, None, None)) for d in dates]


def quote(ticker, since):
    """(last price, split factor after date `since`) from Yahoo's public chart endpoint (unofficial -> may fail;
    the caller degrades to Z''). shares_on_since x factor = shares on today's basis (1:30 reverse split -> 1/30)."""
    p1 = int(dt.datetime.combine(_d(since) + dt.timedelta(1), dt.time(), dt.timezone.utc).timestamp())
    now = int(time.time())
    for host in ("query1", "query2"):
        try:
            body = common.fetch(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}?period1="
                                f"{min(p1, now - 5 * 86400)}&period2={now}&interval=1d&events=split", tries=2, timeout=20)
            r = json.loads(body)["chart"]["result"][0]
            m, f = r["meta"], 1.0
            for s in r.get("events", {}).get("splits", {}).values():
                f *= s["numerator"] / s["denominator"] if s["date"] >= p1 else 1
            if m.get("currency") in (None, "USD") and m["regularMarketPrice"] > 0:
                return float(m["regularMarketPrice"]), f
        except Exception as e:
            print(f"yahoo {host} {ticker}: {type(e).__name__} {e}")
    return None, 1.0


def _div(a, b):
    return None if a is None or not b else a / b


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
    v = {f: [p[0] for p in ps] for f, ps in x.items()}
    src = {f: next((p[1] for p in ps if p[1]), None) for f, ps in x.items()}
    for i in range(3):  # derived fallbacks, flagged in src
        if v["gp"][i] is None and None not in (v["rev"][i], v["cogs"][i]):
            v["gp"][i], src["gp"] = v["rev"][i] - v["cogs"][i], "rev-cogs"
        if v["tl"][i] is None and None not in (v["lse"][i], v["eqn"][i]):
            v["tl"][i], src["tl"] = v["lse"][i] - v["eqn"][i], "lse-eq"
        if v["sga"][i] is None and None not in (v["sm"][i], v["ga"][i]):
            v["sga"][i], src["sga"] = v["sm"][i] + v["ga"][i], "sm+ga"
        if v["cont"][i] is None and v["ni"][i] is not None:
            v["cont"][i], src["cont"] = v["ni"][i], src["ni"]
    if None in v["ltd"][:2]:  # spec: missing long-term debt counts as 0
        res["notes"].append("ltd0")
        v["ltd"] = [0 if a is None else a for a in v["ltd"]]
    res["notes"] += [f for f in ("gp", "tl", "sga") if src[f] in ("rev-cogs", "lse-eq", "sm+ga")]
    res.update(fy=fy, v=v, src=src, form=next((x[f][0][2] for f in ("ni", "rev", "cfo") if x[f][0][2]), "10-K"))

    def need(*keys):
        """Missing tag names for keys like 'ni0' (field + year index: 0 = t, 1 = t-1, 2 = t-2)."""
        return sorted({TAGS[k[:-1]][0] if dates[int(k[-1])] else "דוח שנתי קודם" for k in keys
                       if v[k[:-1]][int(k[-1])] is None})

    V = lambda k: v[k[:-1]][int(k[-1])]
    roa = [_div(V("ni0"), V("ta1")), _div(V("ni1"), V("ta2"))]
    lev = [_div(V("ltd0"), V("ta0")), _div(V("ltd1"), V("ta1"))]
    cr = [_div(V("ca0"), V("cl0")), _div(V("ca1"), V("cl1"))]
    gm = [_div(V("gp0"), V("rev0")), _div(V("gp1"), V("rev1"))]
    at = [_div(V("rev0"), V("ta1")), _div(V("rev1"), V("ta2"))]
    crit = [  # (id, inputs, pass?, shown values, format)
        ("roa", ("ni0", "ta1"), lambda: roa[0] > 0, roa[:1], "pct"),
        ("cfo", ("cfo0",), lambda: V("cfo0") > 0, [V("cfo0")], "usd"),
        ("droa", ("ni0", "ta1", "ni1", "ta2"), lambda: roa[0] > roa[1], roa, "pct"),
        ("accr", ("cfo0", "ni0"), lambda: V("cfo0") > V("ni0"), [V("cfo0"), V("ni0")], "usd"),
        ("lev", ("ltd0", "ta0", "ltd1", "ta1"), lambda: lev[0] <= lev[1], lev, "pct"),
        ("liq", ("ca0", "cl0", "ca1", "cl1"), lambda: cr[0] > cr[1], cr, "x"),
        ("sh", ("sh0", "sh1"), lambda: V("sh0") <= V("sh1"), [V("sh0"), V("sh1")], "qty"),
        ("gm", ("gp0", "rev0", "gp1", "rev1"), lambda: gm[0] > gm[1], gm, "pct"),
        ("at", ("rev0", "ta1", "rev1", "ta2"), lambda: at[0] > at[1], at, "x"),
    ]
    res["pio"] = []
    for cid, keys, test, vals, kind in crit:
        miss = need(*keys)
        res["pio"].append({"id": cid, "ok": None if miss or None in vals else test(), "vals": vals, "kind": kind,
                           "miss": miss})
    if sic and 6000 <= sic <= 6799:
        return {**res, "fin": True}

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
        alt["shares"] *= alt["split"]  # a split after the cover-page date (BYND 1:30) must not scale MVE 30x
        alt["why"] = None if alt["price"] else "noprice"
    alt["kind"] = "Z" if alt["price"] else "Z''"
    alt["miss"] = need("ca0", "cl0", "re0", "ebit0", "ta0", "tl0", "rev0" if alt["price"] else "eq0")
    ta, tl = V("ta0"), V("tl0")
    if not alt["miss"] and ta and tl:
        parts = [(V("ca0") - V("cl0")) / ta, V("re0") / ta, V("ebit0") / ta]
        if alt["price"]:
            alt["mve"] = alt["price"] * alt["shares"]
            parts, w = parts + [alt["mve"] / tl, V("rev0") / ta], (1.2, 1.4, 3.3, 0.6, 1.0)
        else:
            parts, w = parts + [V("eq0") / tl], (6.56, 3.26, 6.72, 1.05)
        alt.update(parts=parts, z=sum(a * b for a, b in zip(w, parts)))

    # Beneish M (8 variables): any missing input -> no score (never a neutral 1.0 substitute)
    ben = {"miss": need(*(f"{f}{i}" for f in ("rev", "gp", "ar", "ca", "ppe", "ta", "dep", "sga", "cl")
                          for i in (0, 1)), "cont0", "cfo0")}
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
    return {**res, "fin": False, "alt": alt, "ben": ben}


# ---------- Hebrew ----------
LABEL = {"roa": "תשואה על הנכסים חיובית", "cfo": "תזרים מפעילות שוטפת חיובי",
         "droa": "תשואה על הנכסים השתפרה", "accr": "תזרים מפעילות גבוה מהרווח הנקי",
         "lev": "מינוף לא עלה (חוב לזמן ארוך/נכסים)", "liq": "יחס שוטף עלה",
         "sh": "לא הונפקו מניות (ממוצע משוקלל)", "gm": "שיעור הרווח הגולמי עלה", "at": "מחזור הנכסים עלה"}
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


def format_he(res):
    """Result dict -> Hebrew Telegram HTML lines (RTL-safe, <= ~1300 visible chars)."""
    out = [f"<b>🧮 ציונים פורנזיים מדוחות {code('10-K')}</b>"]
    if res.get("error"):
        return out + ["ℹ️ " + ERR[res["error"]]]
    fy, v, p = res["fy"], res["v"], res["pio"]
    out.append(f"שנות כספים: {code(fy[0])}" + (f" מול {code(fy[1])}" if len(fy) > 1 else " (אין שנה קודמת)")
               + f" ({code(res['form'])})")
    out.append(f"הכנסות {_c(v['rev'][0])} · רווח נקי {_c(v['ni'][0])} · תזרים מפעילות {_c(v['cfo'][0])}"
               f" · נכסים {_c(v['ta'][0])}")
    n, k = sum(x["ok"] is not None for x in p), sum(x["ok"] is True for x in p)
    out.append(f"<b>פיוטרוסקי F: {code(f'{k}/{n}')}</b>" + (f" · {code(9 - n)} קריטריונים חסרים" if n < 9 else ""))
    for x in p:
        if x["ok"] is None:
            out.append(f"➖ {LABEL[x['id']]}: " + (_tags(x["miss"]) if x["miss"] else "לא ניתן לחישוב (מכנה אפס)"))
        else:
            vals = " מול ".join(code(FMT[x["kind"]](a)) for a in x["vals"])
            out.append(f"{'✅' if x['ok'] else '❌'} {LABEL[x['id']]}: {vals}")
    if res["fin"]:
        sic = f"SIC {res['sic']}"
        out.append(f"⏭️ אלטמן ובנייש לא חושבו: חברה פיננסית ({code(sic)}) – המודלים לא מתאימים למאזן של בנקים,"
                   " חברות ביטוח וחברות השקעה.")
        return out + _notes(res)
    a, b = res["alt"], res["ben"]
    if "z" not in a:
        out.append("<b>אלטמן Z:</b> " + (_tags(a["miss"]) if a["miss"] else "לא ניתן לחישוב (מכנה אפס)"))
    else:
        hi, lo = (2.99, 1.81) if a["kind"] == "Z" else (2.60, 1.10)
        zone = "אזור בטוח" if a["z"] > hi else "אזור אפור" if a["z"] >= lo else "אזור מצוקה"
        z, e = f"{a['z']:.2f}", a["ebit_tag"]
        out.append(f"<b>אלטמן {code(a['kind'])}: {code(z)}</b> · {zone}")
        ebit = (f"רווח תפעולי: {code(e)}" if e == TAGS["ebit"][0] else
                f"רווח תפעולי ({code(TAGS['ebit'][0])}) חסר, שימש רווח לפני מס: {code(e)}")
        if a["kind"] == "Z":
            px = common.price(a["price"]) + (f" ({a['px']})" if a["px"] != res["ticker"] else "")
            split = f" (מותאם לפיצול מניות: {code('×%.4g' % a['split'])})" if a["split"] != 1 else ""
            out.append(f"שווי שוק {_c(a['mve'])} = מחיר {code(px)} × "
                       f"{_c(a['shares'], FMT['qty'])} מניות ({code(a['shares_date'])}){split} · {ebit}")
        else:
            why = {"stale": f"מספר המניות בעמוד השער ישן ({code(a.get('shares_date'))})",
                   "noncommon": f"לחברה אין מניה רגילה ברשימת SEC ({code(a['px'])})",
                   "noprice": "מחיר המניה מ־Yahoo לא זמין",
                   "noshares": f"מספר המניות בעמוד השער חסר ({code('dei:' + SHARES)})"}[a["why"]]
            out.append(f"⚠️ {why} – לכן חושב {code(a['kind'])} על בסיס הון עצמי בספרים במקום שווי שוק · {ebit}")
    if "m" in b:
        zone = "סיכון גבוה לתמרון רווחים" if b["m"] > -1.78 else "אזור ביניים" if b["m"] > -2.22 else "סיכון נמוך"
        m = f"{b['m']:.2f}"
        out.append(f"<b>בנייש M: {code(m)}</b> · {zone}")
        out.append("מדדים: " + " ".join(code(f"{i} {val:.2f}") for i, val in b["idx"].items()))
    elif b["miss"]:
        out.append("<b>בנייש M:</b> " + _tags(b["miss"]))
    else:
        zero = [i for i, val in b["idx"].items() if val is None]
        out.append("<b>בנייש M:</b> לא ניתן לחישוב (מכנה אפס ב־" + ", ".join(code(i) for i in zero) + ")")
    return out + _notes(res)


def _notes(res):
    s = res["src"]
    msg = {"ltd0": f"חוב לזמן ארוך לא דווח ({code(TAGS['ltd'][0])}) – נחשב כ־{code(0)} לפי הכללים",
           "gp": f"רווח גולמי חושב: הכנסות פחות {code(s['cogs'] or '')}",
           "tl": f"התחייבויות חושבו: {code('LiabilitiesAndStockholdersEquity')} פחות הון עצמי",
           "sga": f"הוצאות מכירה והנהלה חושבו: {code(s['sm'] or '')} ועוד {code(s['ga'] or '')}"}
    return [f"ℹ️ {msg[k]}" for k in res["notes"]]
