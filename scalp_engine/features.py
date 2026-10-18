"""L1 features, regime and L2 screener filters (ADR 0003). Pure functions over input frames: every function takes the
decision time explicitly and filters its inputs by availability itself, so rows that become available later cannot
change a value (tests/test_features.py, leakage tests 1-3).

Pipeline (scalp_engine.screener drives it): day_inputs() loads one session through data_access; snapshot_frame()
computes every snapshot feature, the regime and the filter results for a list of snapshot times and one variant.
"""
import datetime as dt
import hashlib
import inspect
import json
import math
import re
from pathlib import Path

import polars as pl

from scalp_engine.contracts import CONTRACTS
from scalp_engine.data_access import ET

UTC = dt.timezone.utc
REGISTRY_FILE = CONTRACTS / "features" / "registry_p2.json"
BAR_AVAIL = dt.timedelta(seconds=62)  # bar open + 60 s + publish delay 2 s (ADR 0002 section 4)
NBBO_DELAY = dt.timedelta(minutes=15)  # sip_delayed_15m
FILING_DELAY = dt.timedelta(seconds=60)  # public_realtime
EOD_DELAY = dt.timedelta(minutes=15)  # sip_eod: prior session's 20:00 ET close + 15 min
SPREAD_WINDOW, SPREAD_MIN_BARS = 30, 10
RVOL_SESSIONS, RVOL_MIN_SESSIONS = 20, 10
SNAPSHOT_TIMES = [dt.time(8 + (30 + 5 * i) // 60, (30 + 5 * i) % 60) for i in range(37)]  # 08:30 .. 11:30 ET
SAMPLE_TIMES = [dt.time(9 + (30 + 5 * i) // 60, (30 + 5 * i) % 60) for i in range(25)]  # 09:30 .. 11:30 ET
SECTOR_ETFS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
DILUTION_FORMS = r"^(S-1|S-3)(/A|ASR|MEF)?$|^424B\d$"
SQRT_ANNUAL = math.sqrt(390 * 252)
VARIANTS = {"iex_live": "iex", "sip_research": "sip"}

# L2 table (ARCHITECTURE.md), unchanged
MIN_PRICE, MAX_PRICE, MIN_ADV, MIN_RVOL, MIN_ATR, MAX_SPREAD = 5.0, 200.0, 2_000_000, 2.0, 0.02, 0.0010
# regime thresholds (ADR 0003 section 5)
VIX_EVENT, RV_EVENT, RV_FROM, TREND_RET = 30.0, 0.40, dt.time(9, 45), 0.03


def et(day, t):
    return dt.datetime.combine(day, t, ET).astimezone(UTC)


# ---------------------------------------------------------------- daily features

def split_factors(actions, d):
    """symbol, ex_date, price_factor (old/new) for forward and reverse splits with ex date <= d."""
    rows = []
    for r in actions.iter_rows(named=True):
        if r["action_type"] not in ("forward_splits", "reverse_splits"):
            continue
        raw = json.loads(r["raw"])
        new, old = raw.get("new_rate"), raw.get("old_rate")
        ex = r["event_at"].astimezone(ET).date()
        if not new or not old or ex > d:
            continue
        rows.append({"symbol": r["symbol"], "ex_date": ex, "price_factor": float(old) / float(new)})
    return pl.DataFrame(rows, schema={"symbol": pl.Utf8, "ex_date": pl.Date, "price_factor": pl.Float64})


def adjusted_daily(bars, splits):
    """Daily bars adjusted as of the decision day: bars dated before a split's ex date are scaled (price x old/new,
    volume x new/old). splits must already be restricted to ex date <= decision day."""
    b = bars.with_columns(pl.col("event_at").dt.convert_time_zone("America/New_York").dt.date().alias("bar_date"))
    if splits.height:
        f = (b.select("symbol", "bar_date").join(splits, on="symbol", how="inner")
             .filter(pl.col("bar_date") < pl.col("ex_date"))
             .group_by(["symbol", "bar_date"]).agg(pl.col("price_factor").product().alias("f")))
        b = b.join(f, on=["symbol", "bar_date"], how="left").with_columns(pl.col("f").fill_null(1.0))
    else:
        b = b.with_columns(pl.lit(1.0).alias("f"))
    return b.select("symbol", "bar_date", *[(pl.col(c) * pl.col("f")).alias(c) for c in ("open", "high", "low", "close")],
                    (pl.col("volume") / pl.col("f")).alias("volume")).sort(["symbol", "bar_date"])


def daily_window_features(adj, sessions_before):
    """prev_close, adv20, atr14_pct per symbol from adjusted bars of the sessions before the decision day."""
    last20 = set(sessions_before[-20:])
    w = adj.filter(pl.col("bar_date").is_in(sorted(last20)))
    tr = (w.with_columns(pl.col("close").shift(1).over("symbol").alias("pc"))
          .with_columns(pl.max_horizontal("high", "pc").sub(pl.min_horizontal("low", "pc")).alias("tr")))
    agg = tr.group_by("symbol").agg(
        pl.col("close").last().alias("prev_close"), pl.col("bar_date").last().alias("last_bar"),
        pl.len().alias("n"), pl.col("volume").mean().alias("adv"),
        pl.col("tr").drop_nulls().tail(14).mean().alias("atr"), pl.col("tr").drop_nulls().tail(14).len().alias("n_tr"))
    return agg.select(
        "symbol", "prev_close",
        pl.when(pl.col("n") >= 15).then(pl.col("adv")).alias("adv20"),
        pl.when(pl.col("n_tr") >= 14).then(pl.col("atr") / pl.col("prev_close")).alias("atr14_pct"))


def market_daily(spy_adj, vix, macro_dates, d, sessions_before):
    """spy_ret20, spy_sma20_dev (SPY adjusted closes), vix_prev_close, macro_day for decision day d."""
    closes = dict(spy_adj.filter(pl.col("symbol") == "SPY").select("bar_date", "close").iter_rows())
    last, first = sessions_before[-1], sessions_before[-21] if len(sessions_before) >= 21 else None
    c_last = closes.get(last)
    w = [closes[s] for s in sessions_before[-20:] if s in closes]
    ret20 = c_last / closes[first] - 1 if c_last and first in closes else None
    sma_dev = c_last / (sum(w) / len(w)) - 1 if c_last and len(w) == 20 else None
    v = vix.filter(pl.col("trading_date") < d).sort("trading_date")
    vix_prev = v["close"][-1] if v.height else None
    return {"spy_ret20": ret20, "spy_sma20_dev": sma_dev, "vix_prev_close": vix_prev, "macro_day": d in macro_dates}


def sector_assignment(adj, members, sessions_before_month):
    """Monthly sector ETF: highest correlation of daily log returns over the last 60 sessions before the month."""
    days = sessions_before_month[-61:]
    r = (adj.filter(pl.col("bar_date").is_in(days) & pl.col("symbol").is_in(sorted(set(members) | set(SECTOR_ETFS))))
         .sort(["symbol", "bar_date"])
         .with_columns((pl.col("close").log() - pl.col("close").shift(1).over("symbol").log()).alias("r"))
         .with_columns(pl.col("bar_date").shift(1).over("symbol").alias("prev_date")))
    # a return counts only between consecutive sessions
    pos = {d: i for i, d in enumerate(days)}
    r = r.filter(pl.col("prev_date").is_not_null()).filter(
        pl.struct(["bar_date", "prev_date"]).map_elements(lambda s: pos.get(s["bar_date"], -9) - pos.get(s["prev_date"], -99) == 1,
                                                          return_dtype=pl.Boolean))
    wide = r.pivot(on="symbol", index="bar_date", values="r")
    out = {}
    etfs = [e for e in SECTOR_ETFS if e in wide.columns]
    for s in members:
        if s not in wide.columns:
            out[s] = None
            continue
        best, best_c = None, None
        for e in etfs:
            pair = wide.select(s, e).drop_nulls()
            if pair.height < 40:
                continue
            c = pair.select(pl.corr(s, e)).item()
            if c is not None and not math.isnan(c) and (best_c is None or c > best_c or (c == best_c and e < best)):
                best, best_c = e, c
        out[s] = best
    return out


# ---------------------------------------------------------------- snapshot features

def _cs_pair(h0, l0, h1, l1):
    k = 3 - 2 * math.sqrt(2)
    beta = (h1 / l1).log() ** 2 + (h0 / l0).log() ** 2
    gamma = (pl.max_horizontal(h0, h1) / pl.min_horizontal(l0, l1)).log() ** 2
    alpha = (((2 * beta).sqrt() - beta.sqrt()) / k) - (gamma / k).sqrt()
    s = 2 * (alpha.exp() - 1) / (1 + alpha.exp())
    return pl.when(s > 0).then(s).otherwise(0.0)


def bar_table(bars, open_at):
    """Per-bar running state for one feed and one session: availability, cumulative volumes and VWAP sums,
    pre-market high/low, and the rolling spread estimators over the last SPREAD_WINDOW real bars."""
    sym = "symbol"
    b = bars.sort([sym, "event_at"]).with_columns(
        (pl.col("event_at") + BAR_AVAIL).alias("avail"),
        (pl.col("event_at") >= open_at).alias("rth"),
        pl.coalesce(pl.col("vwap"), pl.col("close")).alias("vw"))
    h0, l0 = pl.col("high").shift(1).over(sym), pl.col("low").shift(1).over(sym)
    c0 = pl.col("close").shift(1).over(sym)
    eta0 = (h0.log() + l0.log()) / 2
    eta1 = (pl.col("high").log() + pl.col("low").log()) / 2
    w = SPREAD_WINDOW - 1
    b = b.with_columns(
        pl.when(pl.col("rth")).then(0).otherwise(pl.col("volume")).cum_sum().over(sym).alias("cum_pre"),
        pl.when(pl.col("rth")).then(pl.col("volume")).otherwise(0).cum_sum().over(sym).alias("cum_rth"),
        pl.when(pl.col("rth")).then(pl.col("vw") * pl.col("volume")).otherwise(0.0).cum_sum().over(sym).alias("pv_rth"),
        pl.when(pl.col("rth")).then(0.0).otherwise(pl.col("vw") * pl.col("volume")).cum_sum().over(sym).alias("pv_pre"),
        pl.when(pl.col("rth")).then(None).otherwise(pl.col("high")).cum_max().over(sym).alias("pm_high"),
        pl.when(pl.col("rth")).then(None).otherwise(pl.col("low")).cum_min().over(sym).alias("pm_low"),
        pl.col("rth").cast(pl.Int64).cum_sum().over(sym).alias("n_rth"),
        _cs_pair(h0, l0, pl.col("high"), pl.col("low")).alias("cs_term"),
        ((c0.log() - eta0) * (c0.log() - eta1)).alias("ar_term"))
    b = b.with_columns(
        pl.col("pm_high").forward_fill().over(sym), pl.col("pm_low").forward_fill().over(sym),
        pl.col("cs_term").rolling_mean(window_size=w, min_samples=SPREAD_MIN_BARS - 1).over(sym).alias("spread_cs"),
        pl.col("ar_term").rolling_mean(window_size=w, min_samples=SPREAD_MIN_BARS - 1).over(sym).alias("ar_s2"))
    return b.with_columns(
        pl.when(pl.col("ar_s2") > 0).then((4 * pl.col("ar_s2")).sqrt()).otherwise(
            pl.when(pl.col("ar_s2").is_not_null()).then(0.0)).alias("spread_ar"))


def asof_state(table, symbols, times):
    """State of each symbol at each snapshot time: the latest bar with avail <= t (backward as-of join)."""
    grid = pl.DataFrame({"symbol": [s for s in symbols for _ in times], "t": [t for _ in symbols for t in times]},
                        schema={"symbol": pl.Utf8, "t": pl.Datetime("us", "UTC")}).sort(["symbol", "t"])
    right = table.select("symbol", "avail", "close", "cum_pre", "cum_rth", "pv_rth", "pv_pre", "pm_high", "pm_low",
                         "n_rth", "spread_cs", "spread_ar").sort(["symbol", "avail"])
    return grid.join_asof(right, left_on="t", right_on="avail", by="symbol", strategy="backward")


def nbbo_at(samples, symbols, times, delay):
    """Relative NBBO spread of the latest 'ok' sample with event_at <= t - delay (same session)."""
    ok = (samples.filter(pl.col("status") == "ok")
          .select("symbol", pl.col("event_at").alias("s_at"),
                  ((pl.col("ask_price") - pl.col("bid_price")) / ((pl.col("ask_price") + pl.col("bid_price")) / 2)).alias("nbbo"))
          .with_columns((pl.col("s_at") + delay).alias("s_avail")).sort(["symbol", "s_avail"]))
    grid = pl.DataFrame({"symbol": [s for s in symbols for _ in times], "t": [t for _ in symbols for t in times]},
                        schema={"symbol": pl.Utf8, "t": pl.Datetime("us", "UTC")}).sort(["symbol", "t"])
    return grid.join_asof(ok, left_on="t", right_on="s_avail", by="symbol", strategy="backward")


def filing_flags(filings, cik_of, symbols, times, prev_close_at):
    """earnings_8k and dilution_filing per (symbol, t), and the latest availability of a filing used; None without a CIK."""
    f = filings.with_columns((pl.col("event_at") + FILING_DELAY).alias("f_avail"),
                             pl.col("items").fill_null("").str.contains(r"(^|,)\s*2\.02").alias("is_202"),
                             pl.col("form").str.contains(DILUTION_FORMS).alias("is_dil"))
    earn, dil = {}, {}
    for cik, at, fa, form, is202, isdil in f.select("cik", "event_at", "f_avail", "form", "is_202", "is_dil").iter_rows():
        if form == "8-K" and is202 and at > prev_close_at:
            earn.setdefault(cik, []).append(fa)
        if isdil:
            dil.setdefault(cik, []).append((at, fa))
    rows = []
    for s in symbols:
        cik = cik_of.get(s)
        for t in times:
            if cik is None:
                rows.append((s, t, None, None, None))
                continue
            e = [fa for fa in earn.get(cik, ()) if fa <= t]
            d = [fa for at, fa in dil.get(cik, ()) if fa <= t and at > t - dt.timedelta(days=30)]
            used = e + d
            rows.append((s, t, bool(e), bool(d), max(used) if used else None))
    return pl.DataFrame(rows, schema={"symbol": pl.Utf8, "t": pl.Datetime("us", "UTC"), "earnings_8k": pl.Boolean,
                                      "dilution_filing": pl.Boolean, "filing_avail": pl.Datetime("us", "UTC")}, orient="row")


def spy_rv(table, times):
    """Annualized realized volatility of SPY 1-minute log returns over regular-session bars available by t."""
    s = table.filter((pl.col("symbol") == "SPY") & pl.col("rth")).sort("event_at")
    out = {}
    for t in times:
        c = s.filter(pl.col("avail") <= t)["close"]
        if c.len() < 11:
            out[t] = (None, None)
            continue
        x = c.to_list()
        r = [math.log(b) - math.log(a) for a, b in zip(x, x[1:])]
        mean = math.fsum(r) / len(r)  # exact sums: the value does not depend on how the frame is chunked
        sd = math.sqrt(math.fsum((v - mean) ** 2 for v in r) / (len(r) - 1))
        out[t] = (sd * SQRT_ANNUAL, s.filter(pl.col("avail") <= t)["avail"].max())
    return out


def regime(macro_day, vix_prev, spy_ret20, spy_sma20_dev, rv, t_et):
    """ADR 0003 section 5."""
    if macro_day or (vix_prev is not None and vix_prev >= VIX_EVENT) or \
            (t_et >= RV_FROM and rv is not None and rv >= RV_EVENT):
        return "event"
    if spy_ret20 is not None and spy_sma20_dev is not None and abs(spy_ret20) >= TREND_RET and \
            (spy_ret20 > 0) == (spy_sma20_dev > 0) and spy_sma20_dev != 0:
        return "trend"
    return "chop"


def rvol_denominators(prior_profiles, symbols, n_clock=len(SNAPSHOT_TIMES)):
    """Mean cumulative pre-market / regular volume at each clock time over the prior sessions in which the symbol's
    minute bars were fetched (a fetched session without a bar counts 0); null below RVOL_MIN_SESSIONS or when 0."""
    rows = []
    for s in symbols:
        for i in range(n_clock):
            pre = [p[s][0][i] if s in p else 0.0 for p, fetched in prior_profiles if s in fetched]
            rth = [p[s][1][i] if s in p else 0.0 for p, fetched in prior_profiles if s in fetched]
            ok = len(pre) >= RVOL_MIN_SESSIONS
            mp = sum(pre) / len(pre) if ok else None
            mr = sum(rth) / len(rth) if ok else None
            rows.append((s, i, mp if mp else None, mr if mr else None))
    return pl.DataFrame(rows, schema={"symbol": pl.Utf8, "ti": pl.Int64, "den_pre": pl.Float64, "den_rth": pl.Float64},
                        orient="row")


def day_profile(table, symbols, times):
    """{symbol: (cum_pre list, cum_rth list)} at each clock time: the RVOL numerators of this session, stored for
    later sessions' denominators."""
    st = asof_state(table, symbols, times).with_columns(pl.col("cum_pre").fill_null(0), pl.col("cum_rth").fill_null(0))
    out = {}
    for s, g in st.partition_by("symbol", as_dict=True, maintain_order=True).items():
        out[s[0]] = ([float(x) for x in g["cum_pre"]], [float(x) for x in g["cum_rth"]])
    return out


def snapshot_frame(inp, variant, times, spread_choice):
    """Every snapshot feature, the regime and the screener filters for one variant at the given UTC times.

    inp keys: day, open_at, prev_close_at, members (symbol -> rank), bars ({feed: frame} of this session, every fetched
    symbol), samples, filings, cik_of, daily (symbol features), market (dict), sector (symbol -> etf),
    prior_profiles ({feed: [(profile, fetched symbols)] of the prior sessions}), daily_avail, market_avail.
    times must be snapshot clock times of inp["day"] (SNAPSHOT_TIMES)."""
    feed = VARIANTS[variant]
    members = sorted(inp["members"])
    table = bar_table(inp["bars"][feed], inp["open_at"])
    st = asof_state(table, members, times)
    ti = {t: SNAPSHOT_TIMES.index(t.astimezone(ET).time()) for t in times}
    den = rvol_denominators(inp["prior_profiles"][feed], members)
    st = st.with_columns(pl.col("t").replace_strict(ti, return_dtype=pl.Int64).alias("ti"),
                         pl.col("cum_pre").fill_null(0), pl.col("cum_rth").fill_null(0))  # no bar yet = no volume yet
    st = st.join(den, on=["symbol", "ti"], how="left")
    delayed = nbbo_at(inp["samples"], members, times, NBBO_DELAY).select("symbol", "t", pl.col("nbbo").alias("spread_nbbo_delayed"),
                                                                         pl.col("s_avail").alias("nbbo_delayed_avail"))
    rt = nbbo_at(inp["samples"], members, times, dt.timedelta(0)).select("symbol", "t", pl.col("nbbo").alias("spread_nbbo_rt"),
                                                                         pl.col("s_avail").alias("nbbo_rt_avail"))
    if variant == "iex_live":  # research_only: not available live, so never stored in live rows
        rt = rt.with_columns(pl.lit(None, dtype=pl.Float64).alias("spread_nbbo_rt"),
                             pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("nbbo_rt_avail"))
    fl = inp["flags"] if "flags" in inp else filing_flags(inp["filings"], inp["cik_of"], members, times, inp["prev_close_at"])
    daily = inp["daily"]
    st = (st.join(delayed, on=["symbol", "t"], how="left").join(rt, on=["symbol", "t"], how="left")
          .join(fl, on=["symbol", "t"], how="left").join(daily, on="symbol", how="left"))
    rv = spy_rv(table, times)
    m = inp["market"]
    open_et = dt.time(9, 30)
    est = f"spread_{spread_choice}"
    use_nbbo = "spread_nbbo_delayed" if variant == "iex_live" else "spread_nbbo_rt"
    nbbo_avail = "nbbo_delayed_avail" if variant == "iex_live" else "nbbo_rt_avail"
    st = st.with_columns(
        pl.col("close").alias("last_price"),
        pl.col("t").replace_strict({t: (t.astimezone(ET).time() <= open_et) for t in times}, return_dtype=pl.Boolean).alias("pre_open"),
        pl.col("t").replace_strict({t: (t - et(inp["day"], open_et)).total_seconds() / 60 for t in times},
                                   return_dtype=pl.Float64).alias("minutes_since_open"),
        pl.col("t").replace_strict({t: rv[t][0] for t in times}, return_dtype=pl.Float64, default=None).alias("spy_rv_intraday"),
        pl.col("t").replace_strict({t: rv[t][1] for t in times}, return_dtype=pl.Datetime("us", "UTC"), default=None).alias("spy_avail"))
    st = st.with_columns(
        (pl.col("last_price") / pl.col("prev_close") - 1).alias("gap_pct"),
        pl.when(pl.col("den_pre").is_not_null()).then(pl.col("cum_pre") / pl.col("den_pre")).alias("rvol_pre"),
        pl.when(pl.col("den_rth").is_not_null()).then(pl.col("cum_rth") / pl.col("den_rth")).alias("rvol_open"),
        pl.when(pl.col("n_rth") > 0).then(pl.col("close") / (pl.col("pv_rth") / pl.col("cum_rth")) - 1)
          .otherwise(pl.when(pl.col("cum_pre") > 0).then(pl.col("close") / (pl.col("pv_pre") / pl.col("cum_pre")) - 1)).alias("vwap_dev_pct"),
        ((pl.col("pm_high") - pl.col("pm_low")) / pl.col("prev_close")).alias("pm_range_pct"),
        pl.coalesce(pl.col(use_nbbo), pl.col(est)).alias("spread_used"),
        pl.when(pl.col(use_nbbo).is_not_null()).then(pl.lit("nbbo")).when(pl.col(est).is_not_null()).then(pl.lit(spread_choice))
          .otherwise(pl.lit("none")).alias("spread_source"))
    st = st.with_columns(
        pl.when(pl.col("pre_open")).then(pl.col("rvol_pre")).otherwise(pl.col("rvol_open")).alias("rvol"),
        pl.coalesce(pl.col("last_price"), pl.col("prev_close")).alias("px"))
    macro = bool(m["macro_day"])
    st = st.with_columns(
        pl.col("px").is_between(MIN_PRICE, MAX_PRICE).fill_null(False).alias("pass_price"),
        (pl.col("adv20") > MIN_ADV).fill_null(False).alias("pass_adv"),
        (pl.col("rvol") > MIN_RVOL).fill_null(False).alias("pass_rvol"),
        (pl.col("atr14_pct") > MIN_ATR).fill_null(False).alias("pass_atr"),
        (pl.col("spread_used") < MAX_SPREAD).fill_null(False).alias("pass_spread"),
        (~pl.col("dilution_filing").fill_null(False)).alias("pass_dilution"),
        pl.when(pl.col("dilution_filing").is_null()).then(pl.lit("unknown")).when(pl.col("dilution_filing"))
          .then(pl.lit("true")).otherwise(pl.lit("false")).alias("dilution"),
        pl.when(pl.lit(macro) | pl.col("earnings_8k").fill_null(False)).then(pl.lit("true"))
          .when(pl.col("earnings_8k").is_null()).then(pl.lit("unknown")).otherwise(pl.lit("false")).alias("catalyst"))
    st = st.with_columns(pl.all_horizontal("pass_price", "pass_adv", "pass_rvol", "pass_atr", "pass_spread",
                                           "pass_dilution").alias("candidate"))
    regimes = {t: regime(macro, m["vix_prev_close"], m["spy_ret20"], m["spy_sma20_dev"], rv[t][0], t.astimezone(ET).time())
               for t in times}
    avail_cols = [pl.col("avail"), pl.when(pl.col(use_nbbo).is_not_null()).then(pl.col(nbbo_avail)),
                  pl.col("filing_avail"), pl.col("spy_avail"),
                  pl.lit(inp["daily_avail"]).dt.cast_time_unit("us"), pl.lit(inp["market_avail"]).dt.cast_time_unit("us")]
    st = st.with_columns(
        pl.col("t").replace_strict(regimes, return_dtype=pl.Utf8).alias("regime"),
        pl.max_horizontal(*avail_cols).alias("inputs_available_at"),
        pl.col("symbol").replace_strict(inp["members"], return_dtype=pl.Int32, default=None).alias("member_rank"),
        pl.col("symbol").replace_strict(inp["sector"], return_dtype=pl.Utf8, default=None).alias("sector_etf"),
        pl.lit(m["vix_prev_close"], dtype=pl.Float64).alias("vix_prev_close"),
        pl.lit(m["spy_ret20"], dtype=pl.Float64).alias("spy_ret20"),
        pl.lit(m["spy_sma20_dev"], dtype=pl.Float64).alias("spy_sma20_dev"),
        pl.lit(macro).alias("macro_day"),
        pl.lit(variant).alias("variant"), pl.lit(feed).alias("feed"))
    return st.select(SNAPSHOT_COLUMNS)


SNAPSHOT_COLUMNS = [
    "t", "symbol", "variant", "feed", "member_rank", "regime", "candidate",
    "pass_price", "pass_adv", "pass_rvol", "pass_atr", "pass_spread", "pass_dilution", "catalyst", "dilution",
    "px", "last_price", "prev_close", "gap_pct", "adv20", "atr14_pct", "rvol", "rvol_pre", "rvol_open", "vwap_dev_pct",
    "pm_range_pct", "minutes_since_open", "spread_used", "spread_source", "spread_cs", "spread_ar",
    "spread_nbbo_delayed", "spread_nbbo_rt", "earnings_8k", "dilution_filing", "macro_day", "sector_etf",
    "spy_rv_intraday", "vix_prev_close", "spy_ret20", "spy_sma20_dev", "inputs_available_at"]


# ---------------------------------------------------------------- registry

FEATURES = {
    # name: (version, defining functions, inputs, window, feed, live_profile, availability rule, description)
    "prev_close": ("1.0.0", (split_factors, adjusted_daily, daily_window_features), ["raw_bars_1d", "corporate_actions"],
                   "last session before d", "sip", "sip_eod", "prior session 20:00 ET + 15 min", "Split-adjusted last close"),
    "adv20": ("1.0.0", (split_factors, adjusted_daily, daily_window_features), ["raw_bars_1d", "corporate_actions"],
              "20 sessions", "sip", "sip_eod", "prior session 20:00 ET + 15 min", "Mean split-adjusted daily volume"),
    "atr14_pct": ("1.0.0", (split_factors, adjusted_daily, daily_window_features), ["raw_bars_1d", "corporate_actions"],
                  "14 true ranges", "sip", "sip_eod", "prior session 20:00 ET + 15 min", "ATR14 / prev_close"),
    "sector_etf": ("1.0.0", (sector_assignment,), ["raw_bars_1d"], "60 sessions before the month", "sip", "sip_eod",
                   "prior session 20:00 ET + 15 min", "Most correlated SPDR sector ETF"),
    "spy_ret20": ("1.0.0", (market_daily,), ["raw_bars_1d"], "21 sessions", "sip", "sip_eod",
                  "prior session 20:00 ET + 15 min", "SPY 20-session return"),
    "spy_sma20_dev": ("1.0.0", (market_daily,), ["raw_bars_1d"], "20 sessions", "sip", "sip_eod",
                      "prior session 20:00 ET + 15 min", "SPY close vs its 20-session mean"),
    "vix_prev_close": ("1.0.0", (market_daily,), ["vix_daily"], "1 session", "none", "public_eod",
                       "session date 18:00 ET", "Cboe VIX close of the prior session"),
    "macro_day": ("1.0.0", (market_daily,), ["macro_events"], "the session", "none", "public_eod",
                  "00:00 ET of the session", "FOMC, CPI or NFP day"),
    "last_price": ("1.0.0", (bar_table, asof_state), ["raw_bars_1m"], "session to t", "variant", "variant",
                   "bar open + 62 s", "Close of the latest available bar"),
    "gap_pct": ("1.0.0", (bar_table, asof_state, snapshot_frame), ["raw_bars_1m", "raw_bars_1d"], "session to t",
                "variant", "variant", "bar open + 62 s", "last_price / prev_close - 1"),
    "rvol_pre": ("1.0.0", (bar_table, asof_state, day_profile, rvol_denominators), ["raw_bars_1m"],
                 "pre-market to t vs 20 sessions", "variant", "variant", "bar open + 62 s", "Pre-market relative volume"),
    "rvol_open": ("1.0.0", (bar_table, asof_state, day_profile, rvol_denominators), ["raw_bars_1m"],
                  "09:30 to t vs 20 sessions", "variant", "variant", "bar open + 62 s", "Regular-session relative volume"),
    "vwap_dev_pct": ("1.0.0", (bar_table, asof_state, snapshot_frame), ["raw_bars_1m"], "session to t", "variant",
                     "variant", "bar open + 62 s", "last_price / VWAP - 1"),
    "pm_range_pct": ("1.0.0", (bar_table, asof_state, snapshot_frame), ["raw_bars_1m", "raw_bars_1d"],
                     "pre-market to t", "variant", "variant", "bar open + 62 s", "Pre-market range / prev_close"),
    "minutes_since_open": ("1.0.0", (snapshot_frame,), [], "-", "none", "public_realtime", "t",
                           "Minutes since 09:30 ET"),
    "spread_cs": ("1.0.0", (_cs_pair, bar_table), ["raw_bars_1m"], "last 30 real bars", "variant", "variant",
                  "bar open + 62 s", "Corwin-Schultz high-low spread"),
    "spread_ar": ("1.0.0", (bar_table,), ["raw_bars_1m"], "last 30 real bars", "variant", "variant",
                  "bar open + 62 s", "Abdi-Ranaldo close-high-low spread"),
    "spread_nbbo_delayed": ("1.0.0", (nbbo_at,), ["raw_quote_samples"], "latest sample <= t - 15 min", "sip",
                            "sip_delayed_15m", "sample time + 15 min", "Delayed NBBO relative spread"),
    "spread_nbbo_rt": ("1.0.0", (nbbo_at,), ["raw_quote_samples"], "latest sample <= t", "sip", "research_only",
                       "sample time", "Real-time NBBO relative spread"),
    "earnings_8k": ("1.0.0", (filing_flags,), ["edgar_filings", "cik_map"], "since prior regular close", "none",
                    "public_realtime", "acceptance + 60 s", "8-K item 2.02 since the prior close"),
    "dilution_filing": ("1.0.0", (filing_flags,), ["edgar_filings", "cik_map"], "30 calendar days", "none",
                        "public_realtime", "acceptance + 60 s", "S-1 / S-3 / 424B filing in 30 days"),
    "spy_rv_intraday": ("1.0.0", (bar_table, spy_rv), ["raw_bars_1m"], "09:30 to t", "variant", "variant",
                        "bar open + 62 s", "SPY intraday realized volatility, annualized"),
    "regime": ("1.0.0", (regime,), ["macro_day", "vix_prev_close", "spy_ret20", "spy_sma20_dev", "spy_rv_intraday"],
               "-", "variant", "variant", "max of its inputs", "trend / chop / event (rules)"),
}


def code_hash(funcs):
    src = "\n".join(inspect.getsource(f).replace("\r\n", "\n") for f in funcs)
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


def registry():
    out = []
    for name, (ver, funcs, inputs, window, feed, profile, avail, desc) in FEATURES.items():
        h = code_hash(funcs)
        out.append({"schema_version": 2, "name": name, "version": ver, "code_hash": h,
                    "feature_id": f"{name}@{ver}+{h[:12]}", "inputs": inputs or ["clock"], "window": window,
                    "feed": feed, "live_profile": profile, "availability_rule": avail, "description": desc})
    return out


def feature_ids(variant):
    feed_profile = {"iex_live": ("iex", "iex_realtime"), "sip_research": ("sip", "research_only")}[variant]
    ids = []
    for e in registry():
        if e["name"] == "spread_nbbo_rt" and variant == "iex_live":
            continue  # research_only: never used by the live variant
        if e["name"] == "spread_nbbo_delayed" and variant == "sip_research":
            continue
        ids.append(e["feature_id"])
    return sorted(ids), feed_profile


def write_registry(path=REGISTRY_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry(), indent=2) + "\n", encoding="utf-8")
