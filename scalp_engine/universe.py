"""Point-in-time monthly universe (method top500_v3, P1 report).

Candidate pool: every us_equity asset Alpaca lists (active and inactive) on NYSE, NASDAQ or AMEX,
plus pre-rename tickers from name-change corporate actions, plus delisted tickers from merger and
worthless-removal corporate actions (Alpaca's asset list omits most acquired companies). Tickers are
queried literally (asof='-'), so a ticker's bars are whatever traded under it.
Delisted tickers: candidates only for months starting before the delisting; excluded when the acquirer
is an ETP (a fund reorganization). Renames bound validity:
  - a pre-rename ticker is a candidate only if the company it became passes the common-stock check,
    and only for months starting before the rename date (valid_until);
  - a post-rename ticker is a candidate only for months starting on or after the rename date (valid_from),
    because before it the ticker may have belonged to another issuer.
Superseded methods stay in the append-only store, unused: top500_v1 let pre-rename tickers skip the
check (three ETP tickers and a reused preferred ticker got in); top500_v2 had no delisted companies.

Common-stock check (no vendor security-type field exists), applied in order:
  1. venue: NYSE, NASDAQ, AMEX. NYSE Arca and Cboe BZX (ARCA, BATS) list almost only ETPs and are excluded.
  2. symbol: 1-5 letters, optionally .A / .B share class. Suffixes .U .WS .RT .WI .PR* are units,
     warrants, rights, when-issued and preferreds; 5-letter NASDAQ symbols ending W / R / U are
     warrants / rights / units.
  3. name: no fund, ETP, note, warrant, right, unit or preferred keyword. Assets without a name
     (many inactive ones) pass on 1-2 alone and are flagged name_missing.
Monthly reconstitution for month M, using only daily bars dated before M's first session:
last close in [$5, $200], >= 15 bars among the last 20 sessions, rank by the median of vwap*volume
over those sessions, keep the top 500 (ties by symbol).
"""
import re

import polars as pl

METHOD = "top500_v3"
VENUES = {"NYSE", "NASDAQ", "AMEX"}
MARKET_SET = ("SPY", "QQQ", "IWM", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
TOP_N, MIN_PRICE, MAX_PRICE, WINDOW, MIN_BARS = 500, 5.0, 200.0, 20, 15

_SYMBOL = re.compile(r"^[A-Z]{1,5}(\.[AB])?$")
# Kept narrow on purpose: issuer names such as Invesco Ltd., WisdomTree Inc., Build-A-Bear Workshop,
# Ultra Clean Holdings, Unit Corp. or U.S. Physical Therapy are common stocks. ADRs are kept.
_NAME_EXCLUDE = re.compile(
    r"\b(ETFs?|ETNs?|ETP|Funds?|Index|iShares|SPDR|ProShares|Direxion|Select Sector|UltraPro|UltraShort|"
    r"2X|3X|Daily Target|Exchange[- ]Traded|Notes|Debentures?|Warrants?|Rights|Units?,? each|Preferred|Pfd|"
    r"Trust Units|Trust,? Series|Tracking Stock|Closed[- ]End|Municipal)\b|%",
    re.I)


def normalize_symbol(symbol):
    return symbol[: -len("_DELISTED")] if symbol.endswith("_DELISTED") else symbol


def classify(asset):
    """(is_common, reason) for one Alpaca asset record."""
    sym = normalize_symbol(asset["symbol"])
    if sym in MARKET_SET:
        return False, "market set ETF"
    if asset.get("exchange") not in VENUES:
        return False, f"venue {asset.get('exchange')}"
    if not _SYMBOL.match(sym):
        return False, "symbol pattern"
    if asset.get("exchange") == "NASDAQ" and len(sym) == 5 and sym[-1] in "WRU":
        return False, "nasdaq 5th letter"
    name = (asset.get("name") or "").strip()
    if name and _NAME_EXCLUDE.search(name):
        return False, "name keyword"
    return True, "common" if name else "common (name missing)"


def pool(assets, renames=(), delistings=()):
    """{symbol: info} of common-stock candidates. Duplicate symbols (active + inactive records) collapse
    to one ticker; it is common if any record is, and name_missing only if no record has a name.
    renames: (old_symbol, new_symbol, rename_date) from name-change actions.
    delistings: (symbol, delisting_date, acquirer_symbol or None) from merger / worthless-removal actions."""
    assets = [dict(a, id=a.get("id") or a.get("asset_id")) for a in assets]  # vendor record or stored snapshot row
    listed = {normalize_symbol(a["symbol"]) for a in assets}
    out = {}
    for a in sorted(assets, key=lambda a: (normalize_symbol(a["symbol"]), a["status"] != "active", a["id"])):
        ok, _ = classify(a)
        if not ok:
            continue
        sym = normalize_symbol(a["symbol"])
        cur = out.get(sym)
        if cur is None:
            out[sym] = {"asset_id": a["id"], "exchange": a["exchange"], "status": a["status"],
                        "tradable": a.get("tradable"), "shortable": a.get("shortable"),
                        "easy_to_borrow": a.get("easy_to_borrow"), "name_missing": not (a.get("name") or "").strip(),
                        "valid_from": None, "valid_until": None}
        elif (a.get("name") or "").strip():
            cur["name_missing"] = False
    for old, new, day in sorted(renames):
        if old == new:
            continue  # CUSIP-only change
        if new in out:  # the post-rename ticker belongs to this issuer only from the rename on
            vf = out[new]["valid_from"]
            out[new]["valid_from"] = day if vf is None else max(vf, day)
        if old in listed or not _SYMBOL.match(old) or new not in out:
            continue  # old ticker has its own record (classified above), or the issuer is not a common stock
        prev = out.get(old)
        out[old] = {"asset_id": out[new]["asset_id"], "exchange": out[new]["exchange"], "status": "renamed",
                    "tradable": None, "shortable": None, "easy_to_borrow": None, "name_missing": True,
                    "valid_from": None, "valid_until": day if prev is None else min(prev["valid_until"], day)}
    last = {}
    for sym, day, acquirer in sorted(delistings, key=lambda x: (x[0], x[1], x[2] or "")):
        if sym in listed or not _SYMBOL.match(sym):
            continue  # a listed record decides, or not an equity ticker
        if acquirer and acquirer in listed and acquirer not in out:
            continue  # merged into an ETP or other non-common security: a fund reorganization
        last[sym] = max(last.get(sym, day), day)
    for sym, day in last.items():
        if sym in out:
            continue
        out[sym] = {"asset_id": None, "exchange": None, "status": "delisted", "tradable": None, "shortable": None,
                    "easy_to_borrow": None, "name_missing": True, "valid_from": None, "valid_until": day}
    return out


def candidates(pool_, month_start):
    """Symbols eligible for the month starting on month_start (a session date)."""
    return {s for s, i in pool_.items()
            if (i["valid_from"] is None or i["valid_from"] <= month_start)
            and (i["valid_until"] is None or month_start < i["valid_until"])}


def reconstitute(daily, sessions_before, candidates):
    """Members of one month.

    daily: DataFrame[symbol, event_at, close, volume, vwap] holding daily bars of the WINDOW sessions
    before the month (and nothing later). sessions_before: those session dates (ET), ascending.
    candidates: set of eligible symbols. Returns DataFrame[symbol, rank, last_close,
    median_dollar_volume, bars_in_window] sorted by rank."""
    if len(sessions_before) != WINDOW:
        raise ValueError(f"need exactly {WINDOW} prior sessions, got {len(sessions_before)}")
    d = (daily.filter(pl.col("symbol").is_in(sorted(candidates)))
         .with_columns(dv=pl.coalesce(pl.col("vwap"), pl.col("close")) * pl.col("volume"))
         .sort(["symbol", "event_at"]))
    agg = (d.group_by("symbol")
           .agg(pl.len().alias("bars_in_window"), pl.col("close").sort_by("event_at").last().alias("last_close"),
                pl.col("dv").median().alias("median_dollar_volume"))
           .filter((pl.col("bars_in_window") >= MIN_BARS) & pl.col("last_close").is_between(MIN_PRICE, MAX_PRICE))
           .sort(["median_dollar_volume", "symbol"], descending=[True, False])
           .head(TOP_N))
    return agg.with_columns(rank=pl.int_range(1, agg.height + 1, dtype=pl.Int32)).select(
        "symbol", "rank", "last_close", "median_dollar_volume", pl.col("bars_in_window").cast(pl.Int32))
