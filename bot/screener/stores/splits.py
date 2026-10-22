"""Stock splits from Alpaca's corporate actions (market data, read-only; OBSERVED 2026-10-22: `/v1/corporate-actions`
with types forward_split,reverse_split returns {symbol, ex_date, old_rate, new_rate}, 1,000 a page - ~1,000 splits
over two years, 2 pages). Holdings and share counts filed before a split are in pre-split shares; the float scales
them to the evaluated day (D-057: BKYI's 1:10 reverse split of 2026-04-30 left a 10% owner's 595,000 pre-split
shares against 1.09M post-split shares outstanding, a float of 304,000).

Store: {symbol: [[ex date, factor = new_rate / old_rate], ...] ascending}."""
import datetime as dt

from bot import alpaca
from bot.screener import stores

URL = "https://data.alpaca.markets/v1/corporate-actions"
YEARS = 3


def update(today):
    """Read the last YEARS years of splits -> (data, calls)."""
    out, token, calls = {}, None, 0
    start = (dt.date.fromisoformat(today) - dt.timedelta(365 * YEARS)).isoformat()
    while True:
        js = alpaca._get(URL, {"types": "forward_split,reverse_split", "start": start, "end": today, "limit": 1000,
                               **({"page_token": token} if token else {})})
        stores.count("alpaca")
        calls += 1
        for kind in ("forward_splits", "reverse_splits"):
            for x in (js.get("corporate_actions") or {}).get(kind) or []:
                if x.get("symbol") and x.get("ex_date") and x.get("old_rate") and x.get("new_rate"):
                    out.setdefault(x["symbol"], []).append([x["ex_date"], x["new_rate"] / x["old_rate"]])
        token = js.get("next_page_token")
        if not token or calls >= 20:
            break
    data = {s: sorted(v) for s, v in out.items()}
    stores.save("splits", data, "alpaca-corporate-actions", rows=len(data))
    return data, calls


def factor(data, symbol, since, until):
    """Shares on `since` (ISO) x factor = shares on `until`: the product of the splits with since < ex date <=
    until (1.0 without one, or without a date)."""
    f = 1.0
    if not since:
        return f
    for ex, k in (data or {}).get(symbol) or []:
        if since < ex <= until:
            f *= k
    return f
