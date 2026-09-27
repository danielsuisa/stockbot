"""CBOE delayed option chains (free, no key) -> the gamma-fuel read for the squeeze list: the shares dealers would
hold against near-dated calls, relative to the company's shares outstanding. Display and journal only -
unvalidated (there is no free history to backtest it)."""
import datetime as dt
import json
import re

from bot import common

URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{t}.json"
NEAR = 21  # days to expiry that count as near-dated
OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")  # adjusted series (GME1...) do not match and are skipped


def chain(t):
    """CBOE's delayed chain of ticker t (class shares as BRK.B) -> its "data" dict, or None (none / unreachable)."""
    try:
        body = common.fetch(URL.format(t=t.replace("-", ".")), tries=2, timeout=30)
        return json.loads(body)["data"] if body else None
    except Exception as e:  # a display-only source: any failure just means "missing"
        print(f"cboe {t}: {type(e).__name__} {e}")
        return None


def gamma(data, shares, today):
    """{"exposure": sum(call OI x 100 x delta, expiry within NEAR days) / shares, "vol_oi": near call volume / near
    call OI, "pc_oi": put OI / call OI (all expiries), "iv30": CBOE's 30-day implied volatility in %}; None
    without calls or shares. today: a date."""
    near_oi = near_vol = expo = call_oi = put_oi = 0.0
    for o in data.get("options") or []:
        m = OCC.match(o.get("option", ""))
        if not m:
            continue
        oi = o.get("open_interest") or 0.0
        if m[3] == "P":
            put_oi += oi
            continue
        call_oi += oi
        days = (dt.datetime.strptime(m[2], "%y%m%d").date() - today).days
        if 0 <= days <= NEAR:
            near_oi += oi
            near_vol += o.get("volume") or 0.0
            expo += oi * 100 * (o.get("delta") or 0.0)
    if not call_oi or not shares:
        return None
    return {"exposure": expo / shares, "vol_oi": near_vol / near_oi if near_oi else None,
            "pc_oi": put_oi / call_oi, "iv30": data.get("iv30")}
