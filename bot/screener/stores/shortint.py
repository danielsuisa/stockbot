"""FINRA consolidated short interest (bot/shorts): the newest report usable today - its 8th session after settlement
(shorts.usable_from, the squeeze's point-in-time rule) - kept until a newer one becomes usable."""
from bot import common, shorts
from bot.screener import stores


def usable_settlement(settles, cal_done, today):
    """Newest settlement date usable on `today` (cal_done: completed sessions ascending; today counts as a
    session) -> ISO or None."""
    cal = [d for d in cal_done if d < today] + [today]
    for s in reversed(settles):
        u = shorts.usable_from(s, cal)
        if u and u <= today:
            return s
    return None


def update(cal_done, today):
    """The shortint store, refreshed when a newer report became usable -> (data {"settle", "rows": {FINRA symbol:
    {"si", "dtc", "adv"}}}, meta)."""
    st = stores.load("shortint")
    try:
        settles = shorts.settlement_dates()[-4:]
        stores.count("finra")
    except Exception:
        common.trace()
        return (st["data"], st["meta"]) if st else ({}, None)
    want = usable_settlement(settles, cal_done, today)
    if st and st["data"].get("settle") == want:
        return st["data"], st["meta"]
    if want is None:
        return (st["data"], st["meta"]) if st else ({}, None)
    rep = shorts.report(want)
    stores.count("finra", 1 + len(rep) // shorts.PAGE)
    rows = {k: {"si": r["si"], "dtc": r.get("dtc"), "adv": r.get("adv")} for k, r in rep.items()}
    data = {"settle": want, "rows": rows}
    meta = stores.save("shortint", data, "finra-consolidated-short-interest", rows=len(rows), settle=want)
    return data, meta


def lookup(data, symbol):
    """The FINRA row of an Alpaca-spelled symbol (BRK.B -> FINRA's BRKB) or None."""
    rows = (data or {}).get("rows") or {}
    return rows.get(symbol) or rows.get(symbol.replace(".", "")) or rows.get(symbol.replace(".", "-"))
