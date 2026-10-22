"""Metric registry: metric id -> calculator over a per-symbol Ctx, with its source, approx flag and evidence tag
(docs/screener/metric-spec.md). Ctx computes lazily and caches; a metric whose inputs are missing or stale is None.

Series: B = the completed daily bars; S = B plus today's bar while today's regular session has started (RTH or
POST). In PRE the technicals use B; `price` is the pre-market price and change / gap compare it with the last close."""
import dataclasses
import datetime as dt

from bot.screener import catalog
from bot.screener.metrics import fundamental as F
from bot.screener.metrics import technical as T
from bot.screener.stores import bars as BARS
from bot.screener.stores import fundamentals as FUND
from bot.screener.stores import holders as HOLD
from bot.screener.stores import ownership as OWN
from bot.screener.stores import publicfloat as PF
from bot.screener.stores import shortint as SI

AVG_N = 63
INSIDER_TRANS_DAYS = 182


@dataclasses.dataclass(frozen=True)
class MetricValue:
    value: object
    as_of: str | None
    source: str
    approx: bool
    evidence: str


class Ctx:
    """One symbol's inputs (env: evaluate.Env) and its computed metrics."""

    def __init__(self, sym, env):
        self.sym, self.env = sym, env
        self.uni = env.universe.get(sym) or {}
        self.cik = self.uni.get("cik")
        self.B = env.bars.get(sym)
        self.today = env.today.get(sym)
        self._c = {}
        self._S = None

    # ----- series -----
    @property
    def live(self):
        return self.env.session in ("RTH", "POST") and bool(self.today and self.today.get("c") is not None)

    def S(self):
        """Daily series with today's bar appended while today's regular session runs -> dict of lists or None."""
        if self._S is None:
            if self.B is None or not len(self.B):
                self._S = False
            else:
                s = {k: list(getattr(self.B, k)) for k in ("o", "h", "l", "c", "v", "n")}
                s["d"] = self.B.dates()
                if self.live:
                    t = self.today
                    for k, v in (("o", t["o"]), ("h", t["h"]), ("l", t["l"]), ("c", t["c"]), ("v", t["v"]),
                                 ("n", t.get("n") or 0)):
                        s[k].append(v)
                    s["d"].append(self.env.day)
                self._S = s
        return self._S or None

    def get(self, metric):
        if metric not in self._c:
            entry = METRICS.get(metric)
            try:
                self._c[metric] = entry[0](self) if entry else None
            except (ZeroDivisionError, TypeError, ValueError, IndexError, KeyError):
                self._c[metric] = None
        return self._c[metric]

    def value(self, metric):
        entry = METRICS.get(metric)
        if not entry:
            return MetricValue(None, None, "none", False, "UNKNOWN")
        return MetricValue(self.get(metric), self.env.as_of.get(entry[1]), entry[1], entry[2], entry[3])

    # ----- shared inputs -----
    def facts(self):
        if "_facts" not in self._c:
            self._c["_facts"] = FUND.company(self.env.fund, self.cik) if self.cik else {}
        return self._c["_facts"]

    def ttm(self, concept):
        return F.ttm(self.facts().get(concept))[0]

    def inst(self, concept):
        return F.latest(self.facts().get(concept))[0]

    def ref(self):
        return (self.env.reference or {}).get(str(self.cik)) or {} if self.cik else {}

    def short(self):
        return SI.lookup(self.env.short, self.sym)


# ---------- price / volume ----------
def _price(c):
    if c.env.session == "PRE" and c.today and c.today.get("pre") is not None:
        return c.today["pre"]
    if c.live:
        return c.today["c"]
    return c.B.c[-1] if c.B is not None and len(c.B) else None


def _prev_close(c):
    """The close before the bar `price` belongs to."""
    if c.B is None or not len(c.B):
        return None
    if c.live or (c.env.session == "PRE" and c.today and c.today.get("pre") is not None):
        return c.B.c[-1]
    return c.B.c[-2] if len(c.B) >= 2 else None


def _change(c):
    p, q = c.get("price"), _prev_close(c)
    return p / q - 1 if p is not None and q else None


def _change_open(c):
    if c.env.session == "PRE":
        return None
    if c.live:
        return c.today["c"] / c.today["o"] - 1 if c.today.get("o") else None
    return c.B.c[-1] / c.B.o[-1] - 1 if c.B is not None and len(c.B) and c.B.o[-1] else None


def _gap(c):
    if c.env.session == "PRE":
        pre = c.today.get("pre") if c.today else None
        return pre / c.B.c[-1] - 1 if pre is not None and c.B is not None and len(c.B) else None
    if c.live:
        return c.today["o"] / c.B.c[-1] - 1 if c.today.get("o") and len(c.B) else None
    return c.B.o[-1] / c.B.c[-2] - 1 if c.B is not None and len(c.B) >= 2 and c.B.c[-2] else None


def _volume(c):
    if c.env.session in ("PRE", "RTH", "POST"):
        return c.today["v"] if c.today else (0.0 if c.env.stage2 and c.sym in c.env.stage2 else None)
    return c.B.v[-1] if c.B is not None and len(c.B) else None


def _trades(c):
    if c.env.session in ("PRE", "RTH", "POST"):
        return c.today.get("n") if c.today else None
    return c.B.n[-1] if c.B is not None and len(c.B) else None


def _measured_last(c):
    """Is the last completed bar the session being measured (CLOSED on the evening of a session)?"""
    return c.env.session == "CLOSED" and c.B is not None and len(c.B) and c.B.last_date() == c.env.day


def _avg_volume(c):
    """Mean volume of the AVG_N sessions before the measured one (calibrated in parity, D-043: finviz's 3-month
    average excludes the session it is compared with)."""
    if c.B is None:
        return None
    v = c.B.v[:-1] if _measured_last(c) else c.B.v
    return sum(v[-AVG_N:]) / AVG_N if len(v) >= AVG_N else None


def _rel_volume(c):
    if c.env.session in ("PRE", "RTH", "POST"):
        v = _volume(c)
        if v is None:
            return None
        exp = BARS.expected_volume(BARS.profile_mean(c.env.profile, c.sym) or c.env.generic_profile(c),
                                   c.env.now.strftime("%H:%M"))
        return v / exp if exp else None
    base = _avg_volume(c)
    return c.B.v[-1] / base if base and c.B is not None and len(c.B) else None


# ---------- technical on S ----------
def _s(f):
    def g(c):
        s = c.S()
        return f(s, c) if s else None
    return g


def _sma_rel(n):
    return _s(lambda s, c: (s["c"][-1] / T.sma(s["c"], n) - 1) if T.sma(s["c"], n) else None)


def _sma_vs(a, b):
    return _s(lambda s, c: (T.sma(s["c"], a) / T.sma(s["c"], b) - 1) if T.sma(s["c"], a) and T.sma(s["c"], b) else None)


def _anchor_perf(key):
    def g(c):
        done = c.env.sessions_done
        n = BARS.ANCHORS[key]
        idx = len(done) - n if c.live else len(done) - 1 - n
        if idx < 0 or not c.env.long:
            return None
        day = done[idx]
        closes = (((c.env.long.get("a") or {}).get(key) or {}).get("closes") or {}).get(c.sym) or {}
        base = closes.get(day)
        s = c.S()
        return s["c"][-1] / base - 1 if s and base else None
    return g


def _monthly(c, sym=None):
    key = f"_m:{sym or c.sym}"
    if key not in c._c:
        c._c[key] = BARS.monthly(c.env.long, sym or c.sym)
    return c._c[key]


def _all_high(c):
    s, m = c.S(), _monthly(c)
    if not s:
        return None
    return max([*s["h"], *((m or {}).get("h") or [])])


def _all_low(c):
    s, m = c.S(), _monthly(c)
    if not s:
        return None
    return min([*s["l"], *((m or {}).get("l") or [])])


def _beta(c):
    m, spy = _monthly(c), _monthly(c, "SPY")
    if not m or not spy:
        return None
    sp = dict(zip(spy["t"], spy["c"]))
    pairs = [(x, sp[t]) for t, x in zip(m["t"], m["c"]) if t in sp]
    return T.beta([p[0] for p in pairs], [p[1] for p in pairs])


def _candle(c):
    s = c.S()
    if not s:
        return None
    return T.candlestick(s["o"][-1], s["h"][-1], s["l"][-1], s["c"][-1], s["c"][:-1])


def _ssr(c):
    s = c.S()
    if not s or len(s["c"]) < 3:
        return None
    return T.ssr(s["c"][-2], s["l"][-1], s["c"][-3], s["l"][-2])


def _ah_close(c):
    return c.today.get("post") if c.env.session == "POST" and c.today else None


def _ah_change(c):
    a = _ah_close(c)
    return a / c.today["c"] - 1 if a is not None and c.today.get("c") else None


# ---------- descriptive / ownership ----------
def _shares(c):
    return FUND.shares_at(c.env.fund, c.cik, c.env.day)[0] if c.cik else None


def _mcap(c):
    p, n = c.get("price"), c.get("shares_outstanding")
    return p * n if p is not None and n else None


def _insiders(c, roles, day=None):
    return OWN.holdings(c.env.own, c.cik, day, roles)[0] if c.cik else None


def _float(c):
    """Shares outstanding - officers and directors - 10% owners (Forms 3/4/5) - other >= 5% holders (Schedules 13D and
    13G, qualified institutions under Rule 13d-1(b) kept, a holder that also files Forms 3/4/5 counted once at the
    larger amount), as of
    the evaluated day. float_method: "insiders+5pct" (13D/13G coverage complete for the issuer), "insiders" (Forms
    3/4/5, plus any 13D/13G holders read so far), "issuer_public_float" (no holdings data: the 10-K cover's
    non-affiliate count carried forward, D-053), "outstanding_proxy" (none of these). Never <= 0: the next method is
    used and float_flag says why. Rule 144 restricted shares are not subtracted (no free source)."""
    n = c.get("shares_outstanding")
    if not n:
        return None
    if not c.cik:
        c._c["float_method"] = "outstanding_proxy"
        return float(n)
    day = c.env.day
    held, k = OWN.holdings(c.env.own, c.cik, day, "dot")
    big, m = HOLD.holders(c.env.holders, c.cik, OWN.owner_holdings(c.env.own, c.cik, day), day)
    tries = []
    if k or m:
        tries.append(("insiders+5pct" if HOLD.covered(c.env.holders, c.cik) else "insiders", (held or 0) + big))
    if k and m:
        tries.append(("insiders", held))
    for method, sub in tries:
        if n - sub > 0:
            c._c["float_method"] = method
            return n - sub
        c._c["float_flag"] = "holdings >= shares outstanding"
    if not k and not m:  # no holdings at all: the issuer's own non-affiliate count, carried forward (D-053)
        v = PF.carried((c.env.public_float or {}).get(str(c.cik)), n)
        if v and v > 0:
            c._c["float_method"] = "issuer_public_float"
            return v
    c._c["float_method"] = "outstanding_proxy"
    return float(n)


def _insider_trans(c):
    day = dt.date.fromisoformat(c.env.day)
    then = _insiders(c, "do", (day - dt.timedelta(INSIDER_TRANS_DAYS)).isoformat())
    now = _insiders(c, "do")
    return now / then - 1 if now is not None and then else None


def _ipo(c):
    m = _monthly(c)
    if m and m.get("t"):
        first = m["t"][0]
        if first > BARS.HISTORY_START[:7]:
            return first + "-01"
    if c.B is not None and len(c.B) and len(c.B) < BARS.KEEP:
        return c.B.dates()[0]
    return None  # on or before the history start (2016-01): "more than N years" decided by the window function


def _earnings(c):
    from bot.screener.stores import reference as REF
    stamps = c.ref().get("earnings") or []
    return [REF.accepted_ny(x) for x in stamps if x] or None


# ---------- fundamentals ----------
def _debt(c):
    parts = [c.inst(k) for k in ("lt_debt", "lt_debt_current", "st_debt")]
    return sum(x for x in parts if x is not None) if any(x is not None for x in parts) else None


def _ev(c):
    cap, debt, cash = c.get("market_cap"), _debt(c), c.inst("cash")
    if cap is None:
        return None
    return cap + (debt or 0) - (cash or 0)


def _has_fundamentals(c):
    f = c.facts()
    return bool(f.get("revenue") or f.get("net_income") or f.get("assets"))


def _dps_ttm(c):
    v = c.ttm("dps")
    if v is None and _has_fundamentals(c):
        return 0.0
    return v


def _gross(c):
    gp, rev = c.ttm("gross_profit"), c.ttm("revenue")
    if gp is None and rev is not None and c.ttm("cost_revenue") is not None:
        gp = rev - c.ttm("cost_revenue")
    return F.ratio(gp, rev)


def _pfcf(c):
    cfo, capex = c.ttm("cfo"), c.ttm("capex")
    if cfo is None:
        return None
    fcf = cfo - (capex or 0)
    return F.ratio(c.get("market_cap"), fcf)


def _ev_ebitda(c):
    oi, da = c.ttm("oper_income"), c.ttm("da")
    if oi is None:
        return None
    return F.ratio(_ev(c), oi + (da or 0))


def _quick(c):
    ca, cl, inv = c.inst("cur_assets"), c.inst("cur_liab"), c.inst("inventory")
    return F.ratio(ca - (inv or 0), cl) if ca is not None else None


def _eps_ttm(c):
    return c.ttm("eps_diluted")


def _pe(c):
    e = _eps_ttm(c)
    return c.get("price") / e if e and e > 0 and c.get("price") is not None else None


BARS_SRC, FUND_SRC = "bars", "fundamentals"
X, A = False, True  # approx flag
# metric -> (calculator, source store, approx, evidence)
METRICS = {
    "price": (_price, BARS_SRC, X, "OBSERVED"),
    "change": (_change, BARS_SRC, X, "OBSERVED"),
    "change_open": (_change_open, BARS_SRC, X, "OBSERVED"),
    "gap": (_gap, BARS_SRC, X, "OBSERVED"),
    "volume": (_volume, BARS_SRC, X, "OBSERVED"),
    "trades": (_trades, BARS_SRC, A, "ASSUMED"),
    "avg_volume": (_avg_volume, BARS_SRC, X, "INFERRED"),
    "rel_volume": (_rel_volume, "profile", X, "INFERRED"),
    "volume_pct_float": (lambda c: F.ratio(c.get("volume"), c.get("float")), "ownership", A, "INFERRED"),
    **{f"perf_{k}": (_s(lambda s, c, n=n: T.perf(s["c"], n)), BARS_SRC, X, "OBSERVED")
       for k, n in (("w", 5), ("m", 21), ("q", 63), ("h", 126), ("y", 252))},
    "perf_ytd": (_s(lambda s, c: T.perf_ytd(s["d"], s["c"])), BARS_SRC, X, "INFERRED"),
    "perf_3y": (_anchor_perf("perf_3y"), "long", X, "OBSERVED"),
    "perf_5y": (_anchor_perf("perf_5y"), "long", X, "OBSERVED"),
    "perf_10y": (_anchor_perf("perf_10y"), "long", A, "OBSERVED"),
    "volatility_w": (_s(lambda s, c: T.volatility(s["h"], s["l"], s["c"], 5)), BARS_SRC, X, "DECISION"),
    "volatility_m": (_s(lambda s, c: T.volatility(s["h"], s["l"], s["c"], 21)), BARS_SRC, X, "DECISION"),
    "rsi14": (_s(lambda s, c: T.rsi(s["c"])), BARS_SRC, X, "INFERRED"),
    "atr14": (_s(lambda s, c: T.atr(s["h"], s["l"], s["c"])), BARS_SRC, X, "INFERRED"),
    **{f"price_vs_sma{n}": (_sma_rel(n), BARS_SRC, X, "INFERRED") for n in (20, 50, 200)},
    **{f"sma{a}_vs_sma{b}": (_sma_vs(a, b), BARS_SRC, X, "INFERRED")
       for a, b in ((20, 50), (20, 200), (50, 20), (50, 200), (200, 20), (200, 50))},
    **{f"cross_price_sma{n}": (_s(lambda s, c, n=n: T.price_cross_sma(s["c"], n)), BARS_SRC, X, "INFERRED")
       for n in (20, 50, 200)},
    **{f"cross_sma{a}_sma{b}": (_s(lambda s, c, a=a, b=b: T.sma_cross(s["c"], a, b)), BARS_SRC, X, "INFERRED")
       for a, b in ((20, 50), (20, 200), (50, 20), (50, 200), (200, 20), (200, 50))},
    # distance from the high/low of the n sessions before the measured bar (calibrated in parity, D-044: finviz shows
    # a positive distance for a stock above its prior 52-week high)
    **{f"from_high_{w}": (_s(lambda s, c, n=n: T.from_high(s["c"][-1], s["h"][:-1], n)), BARS_SRC, X, "OBSERVED")
       for w, n in (("20d", 20), ("50d", 50), ("52w", 252))},
    **{f"from_low_{w}": (_s(lambda s, c, n=n: T.from_low(s["c"][-1], s["l"][:-1], n)), BARS_SRC, X, "OBSERVED")
       for w, n in (("20d", 20), ("50d", 50), ("52w", 252))},
    **{f"new_high_{w}": (_s(lambda s, c, n=n: T.new_high(s["h"], n)), BARS_SRC, X, "INFERRED")
       for w, n in (("20d", 20), ("50d", 50), ("52w", 252))},
    **{f"new_low_{w}": (_s(lambda s, c, n=n: T.new_low(s["l"], n)), BARS_SRC, X, "INFERRED")
       for w, n in (("20d", 20), ("50d", 50), ("52w", 252))},
    "from_high_all": (lambda c: c.S()["c"][-1] / _all_high(c) - 1 if _all_high(c) else None, "long", A, "DECISION"),
    "from_low_all": (lambda c: c.S()["c"][-1] / _all_low(c) - 1 if _all_low(c) else None, "long", A, "DECISION"),
    "new_high_all": (lambda c: c.S()["h"][-1] >= _all_high(c) if _all_high(c) else None, "long", A, "INFERRED"),
    "new_low_all": (lambda c: c.S()["l"][-1] <= _all_low(c) if _all_low(c) else None, "long", A, "INFERRED"),
    "beta": (_beta, "long", A, "DECISION"),
    "candlestick": (_candle, BARS_SRC, A, "ASSUMED"),
    "ssr": (_ssr, BARS_SRC, A, "OBSERVED"),
    "ah_close": (_ah_close, BARS_SRC, A, "INFERRED"),
    "ah_change": (_ah_change, BARS_SRC, A, "INFERRED"),
    "day_range": (_s(lambda s, c: T.day_range(s["h"][-1], s["l"][-1])), BARS_SRC, X, "OBSERVED"),
    "news_count": (lambda c: c.env.news_count.get(c.sym), "news", A, "DECISION"),
    # descriptive
    "exchange": (lambda c: c.uni.get("exch"), "universe", X, "OBSERVED"),
    "is_etf": (lambda c: c.uni.get("etf"), "universe", X, "OBSERVED"),
    "is_shell": (lambda c: c.ref().get("sic") == "6770" if c.ref() else None, "reference", A, "INFERRED"),
    "optionable": (lambda c: c.uni.get("optionable"), "universe", X, "OBSERVED"),
    "shortable": (lambda c: c.uni.get("shortable"), "universe", A, "DECISION"),
    "sector": (lambda c: c.ref().get("sector"), "reference", A, "DECISION"),
    "industry": (lambda c: c.ref().get("industry"), "reference", A, "DECISION"),
    "country": (lambda c: c.ref().get("country"), "reference", A, "INFERRED"),
    "shares_outstanding": (_shares, FUND_SRC, A, "OBSERVED"),
    "market_cap": (_mcap, FUND_SRC, A, "OBSERVED"),
    "float": (_float, "ownership", A, "DECISION"),
    "float_pct": (lambda c: F.ratio(c.get("float"), c.get("shares_outstanding")), "ownership", A, "INFERRED"),
    "short_float": (lambda c: F.ratio((c.short() or {}).get("si"), c.get("float")), "shortint", A, "OBSERVED"),
    "short_ratio": (lambda c: F.ratio((c.short() or {}).get("si"), c.get("avg_volume")), "shortint", A, "INFERRED"),
    "insider_own": (lambda c: F.ratio(_insiders(c, "do"), c.get("shares_outstanding")), "ownership", A, "OBSERVED"),
    "insider_trans": (_insider_trans, "ownership", A, "DECISION"),
    "earnings_date": (_earnings, "reference", A, "DECISION"),
    "ipo_date": (_ipo, "long", A, "DECISION"),
    # fundamentals
    "pe": (_pe, FUND_SRC, A, "OBSERVED"),
    "eps_ttm": (_eps_ttm, FUND_SRC, A, "OBSERVED"),
    "ps": (lambda c: F.ratio(c.get("market_cap"), c.ttm("revenue")), FUND_SRC, A, "OBSERVED"),
    "pb": (lambda c: F.ratio(c.get("market_cap"), c.inst("equity")), FUND_SRC, A, "OBSERVED"),
    "pc": (lambda c: F.ratio(c.get("market_cap"), c.inst("cash")), FUND_SRC, A, "OBSERVED"),
    "pfcf": (_pfcf, FUND_SRC, A, "DECISION"),
    "ev_ebitda": (_ev_ebitda, FUND_SRC, A, "INFERRED"),
    "ev_sales": (lambda c: F.ratio(_ev(c), c.ttm("revenue")), FUND_SRC, A, "INFERRED"),
    "dividend_yield": (lambda c: F.ratio(_dps_ttm(c), c.get("price")), FUND_SRC, A, "OBSERVED"),
    "payout": (lambda c: F.ratio(c.ttm("dividends_paid"), c.ttm("net_income")), FUND_SRC, A, "OBSERVED"),
    "div_growth_1y": (lambda c: F.cagr(c.facts().get("dps"), 1), FUND_SRC, A, "INFERRED"),
    "div_growth_3y": (lambda c: F.cagr(c.facts().get("dps"), 3), FUND_SRC, A, "INFERRED"),
    "div_growth_5y": (lambda c: F.cagr(c.facts().get("dps"), 5), FUND_SRC, A, "INFERRED"),
    "div_growth_years": (lambda c: F.streak(c.facts().get("dps")) if c.facts().get("dps") else None, FUND_SRC, A, "INFERRED"),
    "eps_qoq": (lambda c: F.quarter_yoy(c.facts().get("eps_diluted")), FUND_SRC, A, "OBSERVED"),
    "eps_yoy_ttm": (lambda c: F.ttm_yoy(c.facts().get("eps_diluted")), FUND_SRC, A, "INFERRED"),
    "eps_3y": (lambda c: F.cagr(c.facts().get("eps_diluted"), 3), FUND_SRC, A, "DECISION"),
    "eps_5y": (lambda c: F.cagr(c.facts().get("eps_diluted"), 5), FUND_SRC, A, "DECISION"),
    "sales_qoq": (lambda c: F.quarter_yoy(c.facts().get("revenue")), FUND_SRC, A, "OBSERVED"),
    "sales_yoy_ttm": (lambda c: F.ttm_yoy(c.facts().get("revenue")), FUND_SRC, A, "INFERRED"),
    "sales_3y": (lambda c: F.cagr(c.facts().get("revenue"), 3), FUND_SRC, A, "INFERRED"),
    "sales_5y": (lambda c: F.cagr(c.facts().get("revenue"), 5), FUND_SRC, A, "DECISION"),
    "roa": (lambda c: F.ratio(c.ttm("net_income"), c.inst("assets")), FUND_SRC, A, "OBSERVED"),
    "roe": (lambda c: F.ratio(c.ttm("net_income"), c.inst("equity")), FUND_SRC, A, "OBSERVED"),
    "roi": (lambda c: F.ratio(c.ttm("net_income"), (c.inst("equity") or 0) + (c.inst("lt_debt") or 0))
            if c.inst("equity") is not None else None, FUND_SRC, A, "INFERRED"),
    "current_ratio": (lambda c: F.ratio(c.inst("cur_assets"), c.inst("cur_liab")), FUND_SRC, A, "OBSERVED"),
    "quick_ratio": (_quick, FUND_SRC, A, "OBSERVED"),
    "lt_debt_eq": (lambda c: F.ratio(c.inst("lt_debt") or (0.0 if c.inst("equity") else None), c.inst("equity")),
                   FUND_SRC, A, "OBSERVED"),
    "debt_eq": (lambda c: F.ratio(_debt(c) or (0.0 if c.inst("equity") else None), c.inst("equity")), FUND_SRC, A,
                "DECISION"),
    "gross_margin": (_gross, FUND_SRC, A, "OBSERVED"),
    "oper_margin": (lambda c: F.ratio(c.ttm("oper_income"), c.ttm("revenue")), FUND_SRC, A, "OBSERVED"),
    "net_margin": (lambda c: F.ratio(c.ttm("net_income"), c.ttm("revenue")), FUND_SRC, A, "OBSERVED"),
}


def available(metric):
    return metric in METRICS


def approx(metric):
    return bool(METRICS.get(metric, (None, None, True))[2])


def unit_factor(metric, prefix):
    return catalog.factor(metric, prefix)
