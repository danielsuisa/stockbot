"""Premarket relative-volume catalyst. Locked 2026-10-15 before any data request.

Long only, cash, $460, one name a day. This module does not place or transmit an order.

The screen, all required, uses split-adjusted prices and split-adjusted volume (`adjustment=split`).
A reverse split then does not look like a gap between the prior close and the next session, and it does
not look like a volume spike. The prior close is the previous session's split-adjusted close, and only
when that session itself printed. It has to be at least $0.50 and under $5. A later split moves an old
day into or out of that band; that lookahead is accepted and stated, not corrected after the run.

Premarket is 04:00 through 09:29 New York. Volume and the high come from the 5-minute bars that start
at 04:00 through 09:25, which cover that window. Relative volume is that volume divided by the median
premarket volume of the prior 20 sessions. At least 10 of those 20 must have printed, and the ratio
must be at least 5.

A headline is required in the 24 hours ending 09:30 New York, and its Alpaca `source` must be a
primary wire. A missing source does not qualify. An S-1, a 424B, or an at-the-market shelf (S-3 /
S-3ASR) accepted in the 30 calendar days ending 09:30 blocks the name. Float is not in Alpaca.
Shares outstanding (`dei:EntityCommonStockSharesOutstanding`) stand in for it and must be under 20
million. The entry is the first 1-minute close above the premarket high. The stop is one cent under
the low of the opening 5-minute bar, so the close has to be from 09:35 through 10:30: earlier than
09:35 the stop is not known, and later than 10:30 is outside the window. The exit is 2R or 15:55.
"""
import datetime as dt
import re
import statistics

from bot.desk_gate import ACCOUNT, net_r, shares_for, walk

PRICE_LO, PRICE_HI = 0.50, 5.0
RVOL_MIN = 5.0
RVOL_DAYS, RVOL_MIN_HISTORY = 20, 10
FLOAT_MAX = 20_000_000.0
MIN_SHARES = 100_000.0
SHARES_MAX_AGE = 400
TARGET_R = 2.0
TICK = 0.01
ENTRY_FROM, ENTRY_TO = "09:35", "10:30"
SPLIT_BAND = 0.25
EXCHANGES = ("NYSE", "NASDAQ", "AMEX")
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
SKIP_NAME = re.compile(
    r"\bETF\b|\bETN\b|\bETFs\b|\bFund\b|\bFunds\b|iShares|ProShares|Direxion|\bSPDR\b|"
    r"Invesco QQQ|VanEck|WisdomTree|Global X|GraniteShares|\bLeveraged\b|"
    r"\b\d(\.\d)?[xX]\b|Trust, Series|\bWarrant\b|\bWarrants\b|\bPreferred\b|"
    r"\bRights\b|\bUnits?\b|\bNotes\b|\bDebentures?\b|Exchange[- ]Traded",
    re.I)
# Alpaca `source`, compared with spaces removed and case folded. A bare "newswire" is not enough.
PRIMARY_SOURCES = ("businesswire", "prnewswire", "cision", "globenewswire", "accesswire",
                   "accessnewswire", "newsfile")
BLOCK_EXACT = {"S-1", "S-1/A", "S-3", "S-3/A", "S-3ASR", "S-3ASR/A"}
# The nine names from the earlier survivorship probe. Counted, not used to change a threshold.
DELISTED = (("LNKD", "2016-12-07"), ("YHOO", "2017-06-16"), ("WFM", "2017-08-25"), ("MON", "2018-06-06"),
            ("TWX", "2018-06-14"), ("SHLD", "2018-10-15"), ("SPLS", "2017-09-12"), ("PNRA", "2017-07-17"),
            ("CAB", "2017-09-22"))
GAPS = (
    "Tickers Alpaca never stored are absent. The nine-name probe counts that hole for those names only.",
    "A reused ticker is one symbol. The two companies are not split apart.",
    "A delisting with no inactive asset row is absent.",
    "A name with no CIK, no usable shares-outstanding fact, or an incomplete filing history is excluded. "
    "Shares outstanding are not float.",
    "An intraday halt or a delist after the entry exits at the last print. There is no CRSP delisting return.",
    "The $0.50–$5 band is split-adjusted, so a later split moves an old day into or out of the band.",
    "OTC names and symbols outside NYSE, NASDAQ, and AMEX are out.",
)


def is_common(asset):
    """One Alpaca asset row. Listing status is not a filter: inactive names stay in."""
    name, sym, exch = asset.get("name") or "", asset.get("symbol") or "", (asset.get("exchange") or "").upper()
    return exch in EXCHANGES and bool(SYMBOL_RE.fullmatch(sym)) and bool(name) and not SKIP_NAME.search(name)


def index_assets(assets):
    """{symbol: row}. Inactive wins when the file has both statuses."""
    out = {}
    for a in assets:
        sym = a.get("symbol")
        if not sym:
            continue
        if sym not in out:
            out[sym] = dict(a)
        elif a.get("status") == "inactive":
            out[sym]["status"] = "inactive"
    return out


def universe(assets):
    indexed = index_assets(assets)
    return sorted(s for s, a in indexed.items() if is_common(a)), indexed


def flat_minute(close_hhmm):
    """15:55, or the minute before an early close when that minute is earlier."""
    h, m = (int(x) for x in close_hhmm.split(":")[:2])
    t = h * 60 + m - 1
    earlier = f"{t // 60:02d}:{t % 60:02d}"
    return earlier if earlier < "15:55" else "15:55"


def price_ok(prev_close):
    return prev_close is not None and PRICE_LO <= prev_close < PRICE_HI


def rvol_ok(today_vol, prior_vols):
    """`prior_vols` is the prior 20 sessions. A missing premarket print is None."""
    usable = [v for v in prior_vols if v]
    if today_vol is None or today_vol <= 0 or len(usable) < RVOL_MIN_HISTORY:
        return False
    med = statistics.median(usable)
    return med > 0 and today_vol / med >= RVOL_MIN


def premarket(bars):
    """5-minute bars `(minute, o, h, l, c, v)`. Through 09:25 is 04:00–09:29.

    Returns `(volume, high)` or `(None, None)` when that window did not print.
    """
    pm = [b for b in bars if "04:00" <= b[0] <= "09:25"]
    if not pm:
        return None, None
    return sum(b[5] or 0 for b in pm), max(b[2] for b in pm)


def opening_low(bars):
    """Low of the 09:30 five-minute bar, which is 09:30–09:34. None when that bar is missing."""
    hit = [b for b in bars if b[0] == "09:30"]
    return None if not hit else hit[0][3]


def blocks_form(form):
    """S-1, any 424B, and the S-3 / S-3ASR shelf an at-the-market sale is filed under."""
    f = (form or "").upper()
    return f in BLOCK_EXACT or f.startswith("424B")


def filing_blocks(events, day):
    """`events` are `(New York 'YYYY-MM-DDTHH:MM', form)`. The window ends at 09:30 and is 30 calendar days."""
    end = dt.datetime.fromisoformat(f"{day}T09:30")
    start = end - dt.timedelta(days=30)
    for stamp, form in events:
        if not blocks_form(form):
            continue
        t = dt.datetime.fromisoformat(stamp[:16])
        if start <= t < end:
            return True
    return False


def shares_ok(facts, day):
    """`(filed, period end, shares)` -> `(passes, reason)`.

    The latest fact filed strictly before `day`. A figure under 100,000 shares is a scale error.
    A period end more than 400 days old is unused. The proxy for float is shares outstanding under
    20 million. This is not float.
    """
    use = [f for f in facts if f[0] < day]
    if not use:
        return False, "none"
    filed, end, val = max(use, key=lambda f: (f[0], f[1]))
    age = (dt.date.fromisoformat(day) - dt.date.fromisoformat(end)).days
    if val < MIN_SHARES:
        return False, "scale"
    if age > SHARES_MAX_AGE:
        return False, "stale"
    if val >= FLOAT_MAX:
        return False, "large"
    return True, "ok"


def primary_source(source):
    """True when Alpaca's source string is a locked primary wire. Empty is not a wire."""
    if isinstance(source, dict):
        source = source.get("name") or source.get("id") or ""
    key = re.sub(r"[^a-z0-9]", "", (source or "").lower())
    return any(token in key for token in PRIMARY_SOURCES)


def source_name(item):
    source = item.get("source")
    if isinstance(source, dict):
        return source.get("name") or source.get("id") or ""
    return source or ""


def ex_dates(rows):
    """Corporate-action rows -> `{symbol: {ex dates}}`. `process_date` is used when `ex_date` is absent."""
    out = {}
    for row in rows:
        sym = row.get("symbol")
        day = (row.get("ex_date") or row.get("process_date") or "")[:10]
        if sym and len(day) == 10:
            out.setdefault(sym, set()).add(day)
    return out


def split_sane(closes, sessions):
    """AAPL's 4-for-1 split on 2020-08-31 must not survive as a jump. `(ok, detail)`."""
    before = [d for d in sessions if d <= "2020-08-28" and d in closes]
    after = [d for d in sessions if d >= "2020-08-31" and d in closes]
    if not before or not after:
        return False, "missing bars around 2020-08-31"
    c0, c1 = closes[before[-1]], closes[after[0]]
    if c0 <= 0 or c1 <= 0:
        return False, "non-positive close"
    jump = c1 / c0 - 1
    return abs(jump) < SPLIT_BAND, {"from": before[-1], "to": after[0], "jump": jump}


def quote_spread(raw_spread, raw_close, adj_close):
    """Put a raw SIP spread into split-adjusted dollars. None keeps the 1% haircut."""
    if raw_spread is None or not raw_close or not adj_close or raw_close <= 0:
        return None
    return raw_spread * (adj_close / raw_close)


def setup(minutes, pm_high, or_low, flat_at):
    """The first 1-min close above the premarket high, from 09:35 through 10:30, or None.

    `minutes` are `(minute, o, h, l, c, v)` through `flat_at`. The fill is that close. The path
    starts on the next bar, because the signal bar's low printed before the close.
    """
    if pm_high is None or or_low is None:
        return None
    stop = or_low - TICK
    if stop <= 0:
        return None
    signal = None
    for b in minutes:
        if b[0] < ENTRY_FROM or b[0] > ENTRY_TO or b[0] > flat_at:
            continue
        if b[4] > pm_high:
            signal = b
            break
    if signal is None:
        return None
    entry = signal[4]
    if entry <= stop:
        return None
    path = [(b[0], b[1], b[2], b[3], b[4]) for b in minutes if signal[0] < b[0] <= flat_at]
    target = entry + TARGET_R * (entry - stop)
    risk = entry - stop
    if path:
        long = walk(path, stop, target, "long", True)
        short = walk(path, entry + risk, entry - TARGET_R * risk, "short", True)
    else:
        long, short = (entry, "last", signal[0]), (entry, "last", signal[0])
    if not long or not short:
        return None
    if path and long[2] < flat_at and long[1] == "time":
        long = (long[0], "last", long[2])
    return {"entry": entry, "stop": stop, "fill_minute": signal[0], "exit": long[0], "kind_exit": long[1],
            "exit_minute": long[2], "short_exit": short[0], "target": target, "pm_high": pm_high,
            "or_low": or_low}


def score(row, spread, account=ACCOUNT):
    """Add the $460 share count and both net R figures. None when one share costs more than $460."""
    shares = shares_for(row["entry"], account)
    if shares < 1 or row.get("short_exit") is None:
        return None
    pnl, r, mode = net_r(row["entry"], row["exit"], row["stop"], shares, spread, "long")
    spnl, sr, _ = net_r(row["entry"], row["short_exit"], row["stop"], shares, spread, "short")
    if r is None or sr is None:
        return None
    bare = r + (2 * max(0.35, 0.0035 * shares)) / (abs(row["entry"] - row["stop"]) * shares)
    out = dict(row)
    out.update(shares=shares, pnl=pnl, net_r=r, net_r_short=sr, short_pnl=spnl, cost_mode=mode,
               net_r_no_commission=bare)
    return out


def pick(rows):
    """One name: the highest premarket relative volume, then the symbol."""
    if not rows:
        return None
    return min(rows, key=lambda r: (-r["rvol"], r["t"]))
