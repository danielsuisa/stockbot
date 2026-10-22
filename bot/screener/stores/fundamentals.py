"""SEC XBRL frames -> per-company facts for the fundamental metrics (metrics/fundamental.py).

One frame = one value per company for one concept and one calendar period (CY2025Q2: ~3-month durations; CY2025:
~12-month durations; CY2025Q2I: instants). Each concept has tag fallbacks in priority order: a company's value comes
from the first tag that has one for that period. A full refresh reads QUARTERS quarterly frames, YEARS annual frames
and INSTANTS instant frames per tag (~500 calls, weekly); a light refresh only the newest 2 quarters and instants
(daily). Shares outstanding come from the dei frames through bot/shorts (the squeeze's rules)."""
import datetime as dt

from bot import common, shorts
from bot.screener import stores

FRAME = "https://data.sec.gov/api/xbrl/frames/{tax}/{tag}/{unit}/{period}.json"
QUARTERS, YEARS, INSTANTS = 10, 7, 3
# concept -> (kind "D" duration / "I" instant, unit, [(taxonomy, tag), ...] in priority order)
CONCEPTS = {
    "revenue": ("D", "USD", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                             "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                             "RevenuesNetOfInterestExpense"]),
    "cost_revenue": ("D", "USD", ["CostOfRevenue", "CostOfGoodsAndServicesSold"]),
    "gross_profit": ("D", "USD", ["GrossProfit"]),
    "oper_income": ("D", "USD", ["OperatingIncomeLoss"]),
    "net_income": ("D", "USD", ["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"]),
    "eps_diluted": ("D", "USD-per-shares", ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",
                                            "EarningsPerShareBasic"]),
    "cfo": ("D", "USD", ["NetCashProvidedByUsedInOperatingActivities",
                         "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    "capex": ("D", "USD", ["PaymentsToAcquirePropertyPlantAndEquipment"]),
    "da": ("D", "USD", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                        "DepreciationAmortizationAndAccretionNet"]),
    "dividends_paid": ("D", "USD", ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]),
    "dps": ("D", "USD-per-shares", ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"]),
    "assets": ("I", "USD", ["Assets"]),
    "cur_assets": ("I", "USD", ["AssetsCurrent"]),
    "cur_liab": ("I", "USD", ["LiabilitiesCurrent"]),
    "equity": ("I", "USD", ["StockholdersEquity",
                            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
    "cash": ("I", "USD", ["CashAndCashEquivalentsAtCarryingValue",
                          "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"]),
    "inventory": ("I", "USD", ["InventoryNet"]),
    "lt_debt": ("I", "USD", ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"]),
    "lt_debt_current": ("I", "USD", ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"]),
    "st_debt": ("I", "USD", ["ShortTermBorrowings", "DebtCurrent", "CommercialPaper"]),
}


def periods(today, kind, light=False):
    """Frame period names to read for `kind` as of `today` (date), newest first."""
    y, q = today.year, (today.month - 1) // 3 + 1
    qs = []
    for _ in range(2 if light else max(QUARTERS, INSTANTS)):
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)  # the current quarter has not ended
        qs.append((y, q))
    if kind == "I":
        return [f"CY{y}Q{q}I" for y, q in qs[:2 if light else INSTANTS]]
    out = [f"CY{y}Q{q}" for y, q in qs[:2 if light else QUARTERS]]
    if not light:
        out += [f"CY{today.year - k}" for k in range(1, YEARS + 1)]
    return out


def frame(tag, unit, period, tax="us-gaap"):
    """One frame -> [{"cik", "start", "end", "val"}] ([] when not published)."""
    d = common.get_json(FRAME.format(tax=tax, tag=tag, unit=unit, period=period)) or {}
    stores.count("sec")
    return d.get("data") or []


def merge(facts, concept, kind, period, rows, rank):
    """Add one frame's rows to facts {cik: {concept: {"q"|"a"|"i": [...]}}}; a company keeps the value of the
    best-ranked tag per period (rank 0 = first fallback)."""
    slot = "i" if kind == "I" else ("a" if "Q" not in period else "q")
    for r in rows:
        cik = str(r.get("cik"))
        if r.get("val") is None:
            continue
        c = facts.setdefault(cik, {}).setdefault(concept, {})
        key = c.setdefault("_rank", {})
        if key.get(f"{slot}{period}", 99) < rank:
            continue
        lst = c.setdefault(slot, [])
        lst[:] = [x for x in lst if x[-1] != period]
        key[f"{slot}{period}"] = rank
        lst.append([r["end"], r["val"], period] if slot == "i" else [r.get("start"), r["end"], r["val"], period])


def strip(facts):
    """Drop the bookkeeping (ranks, period names) -> {cik: {concept: {"q", "a", "i"}}} for metrics/fundamental."""
    out = {}
    for cik, cs in facts.items():
        out[cik] = {k: {s: [x[:-1] for x in v[s]] for s in ("q", "a", "i") if v.get(s)} for k, v in cs.items()}
    return out


def refresh(today, light=False, old=None):
    """Read the frames -> raw facts (with bookkeeping, so a light refresh can merge into `old`)."""
    facts = old if (light and old) else {}
    for concept, (kind, unit, tags) in CONCEPTS.items():
        for period in periods(today, kind, light):
            for rank, tag in enumerate(tags):
                try:
                    merge(facts, concept, kind, period, frame(tag, unit, period), rank)
                except Exception:
                    common.trace()
    return facts


def shares(today):
    """{cik (int): [(usable from, fact date, shares)]} from the dei frames (bot/shorts, 6 quarters)."""
    y, q, out = today.year, (today.month - 1) // 3 + 1, []
    for _ in range(6):
        out.append(shorts.frame(y, q))
        stores.count("sec")
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    return shorts.shares_index(out)


def update(today, force=False):
    """The fundamentals store: full refresh when missing or older than 7 days (or forced), else a light refresh of
    the newest periods once a day -> (data {"facts": raw, "shares": {cik: [...]}}, meta)."""
    st = stores.load("fundamentals")
    age = stores.age_days(st["meta"]) if st else None
    if st and not force and age is not None and age < 1 and st["meta"].get("day") == today.isoformat():
        return st["data"], st["meta"]
    full = force or st is None or (st["meta"].get("full_day") or "") < (today - dt.timedelta(7)).isoformat()
    facts = refresh(today, light=not full, old=st["data"]["facts"] if st and not full else None)
    data = {"facts": facts, "shares": {str(k): v for k, v in shares(today).items()}}
    meta = stores.save("fundamentals", data, "sec-xbrl-frames", rows=len(facts), day=today.isoformat(),
                       full_day=today.isoformat() if full else st["meta"].get("full_day"))
    return data, meta


def company(data, cik):
    """One company's stripped facts {concept: {"q", "a", "i"}} ({} when unknown)."""
    raw = (data or {}).get("facts", {}).get(str(cik))
    return strip({"x": raw})["x"] if raw else {}


def shares_at(data, cik, day):
    """Shares outstanding usable on `day` (ISO) -> (shares, fact date) or (None, None) (bot/shorts rules)."""
    idx = {int(cik): [tuple(x) for x in (data or {}).get("shares", {}).get(str(cik), [])]} if cik else {}
    n = shorts.shares_at(idx, int(cik), day) if cik else None
    if not n:
        return None, None
    facts = [f for f in idx[int(cik)] if f[0] <= day]
    return n, facts[-1][1] if facts else None
