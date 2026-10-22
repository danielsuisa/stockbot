"""The issuer's own non-affiliate float: dei:EntityPublicFloat, the 10-K cover's market value held by non-affiliates as
of the last day of the second fiscal quarter, from SEC frames (OBSERVED 2026-10-22: dei/EntityPublicFloat/USD/
CY2025Q2I.json, ~4300 companies, fields accn/cik/end/val). A float fallback only (float_method
"issuer_public_float", D-053) for issuers without any Form 3/4/5 or 13D/13G holding; issuer-reported, annual (up to
~15 months old), rounded, absent for 20-F filers, and "affiliate" is the issuer's own call (often officers, directors
and 10% owners only) - never ground truth.

Carried forward by affiliate count (D-053, measured in F1): non-affiliate shares then = val / the RAW close of the
last session on or before `end` (the issuer valued them at that day's actual price; `end` can be a weekend);
affiliates then = shares outstanding then (the dei cover fact nearest to `end`) - non-affiliate shares then; today's
float = shares outstanding today - affiliates then x the split factor since `end` (raw close / split-adjusted close
on that day). Dilution-safe; a later split is caught by refreshing the split-adjusted close monthly.

Store: {cik: {"end", "val", "accn", "day" (close date), "raw", "split", "so_then", "so_date", "split_at"}}."""
import datetime as dt
import json

from bot import alpaca, common
from bot.screener import stores

FRAME = "https://data.sec.gov/api/xbrl/frames/dei/EntityPublicFloat/USD/CY{year}Q2I.json"
SPLIT_REFRESH_DAYS = 30
MAX_SO_GAP_DAYS = 200  # the cover share count used must be dated within this many days of `end`


def frames(today):
    """{cik: {"end", "val", "accn"}}: the newest fact per company over this year's and last year's Q2 frames."""
    out = {}
    year = dt.date.fromisoformat(today).year
    for y in (year - 1, year):
        body = common.fetch(FRAME.format(year=y))
        stores.count("sec")
        for r in (json.loads(body) if body else {}).get("data", []):
            c = str(r["cik"])
            if c not in out or r["end"] > out[c]["end"]:
                out[c] = {"end": r["end"], "val": r["val"], "accn": r["accn"]}
    return out


def closes(symbols, end, adjustment):
    """{symbol: (date, close)} of the last session on or before `end` (ISO) -> Alpaca daily bars."""
    out = {}
    start = (dt.date.fromisoformat(end) - dt.timedelta(10)).isoformat()
    for i in range(0, len(symbols), 800):
        token = None
        while True:
            q = {"symbols": ",".join(symbols[i:i + 800]), "timeframe": "1Day", "start": start, "end": end,
                 "feed": "sip", "adjustment": adjustment, "limit": alpaca.LIMIT, "sort": "asc",
                 **({"page_token": token} if token else {})}
            js = alpaca._get(f"{alpaca.DATA}/bars", q)
            stores.count("alpaca")
            for s, items in (js.get("bars") or {}).items():
                items = [b for b in items or [] if alpaca._ny(b["t"]).date().isoformat() <= end]
                if items:
                    out[s] = (alpaca._ny(items[-1]["t"]).date().isoformat(), items[-1]["c"])
            token = js.get("next_page_token")
            if not token:
                break
    return out


def nearest_shares(fund, cik, end):
    """The dei cover share count dated nearest to `end` -> (shares, date) or (None, None)."""
    facts = ((fund or {}).get("shares") or {}).get(str(cik)) or []
    if not facts:
        return None, None
    f = min(facts, key=lambda x: abs((dt.date.fromisoformat(x[1]) - dt.date.fromisoformat(end)).days))
    gap = abs((dt.date.fromisoformat(f[1]) - dt.date.fromisoformat(end)).days)
    return (f[2], f[1]) if gap <= MAX_SO_GAP_DAYS else (None, None)


def update(universe, fund, today):
    """Refresh the store: new (cik, end) facts get their raw and split closes; split closes older than
    SPLIT_REFRESH_DAYS are fetched again -> (data, stats)."""
    st = stores.load("publicfloat")
    data = st["data"] if st else {}
    sym_of = {}
    for s, u in universe.items():
        if u.get("cik") and not u.get("etf"):
            sym_of.setdefault(str(u["cik"]), s)
    stats = {"facts": 0, "new": 0, "split_refresh": 0}
    facts = frames(today)
    stats["facts"] = len(facts)
    stale = (dt.date.fromisoformat(today) - dt.timedelta(SPLIT_REFRESH_DAYS)).isoformat()
    want = {}  # end -> [(cik, symbol, new)]
    for cik, f in facts.items():
        if cik not in sym_of:
            continue
        cur = data.get(cik)
        new = not cur or cur["end"] != f["end"] or cur["val"] != f["val"]
        if new or cur.get("split_at", "") < stale:
            want.setdefault(f["end"], []).append((cik, sym_of[cik], new))
    for end, items in sorted(want.items()):
        syms = sorted({s for _, s, _ in items})
        split = closes(syms, end, "split")
        raw = closes(sorted({s for _, s, new in items if new}), end, "raw") if any(n for *_, n in items) else {}
        for cik, sym, new in items:
            if new:
                so, so_date = nearest_shares(fund, cik, end)
                if sym in raw and sym in split and so:
                    data[cik] = {**facts[cik], "day": raw[sym][0], "raw": raw[sym][1], "split": split[sym][1],
                                 "so_then": so, "so_date": so_date, "split_at": today}
                    stats["new"] += 1
            elif sym in split:
                data[cik]["split"], data[cik]["split_at"] = split[sym][1], today
                stats["split_refresh"] += 1
    stores.save("publicfloat", data, "sec-frames-EntityPublicFloat+alpaca", rows=len(data))
    return data, stats


def carried(rec, shares_today):
    """Today's float from one store record by affiliate count x split factor -> shares or None."""
    if not rec or not shares_today or not rec.get("raw") or not rec.get("split"):
        return None
    non_aff = rec["val"] / rec["raw"]
    affiliates = max(0.0, rec["so_then"] - non_aff)
    return shares_today - affiliates * (rec["raw"] / rec["split"])
