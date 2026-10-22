"""The filter catalog (catalog.yaml, JSON syntax - DECISION D-001) and finviz-style codes -> Predicate objects.

A code is <prefix>_<option>. The option is one of the catalog's named options (OBSERVED from finviz's own filter
definitions, or INFERRED from the build prompt), or a custom value in the filter family's grammar:
  numeric  u<N> under N (strict), o<N> over N (strict), <a>to<b> inclusive, <a>to at least, to<b> at most
  updown   u<N> up more than N%, d<N> down more than N%, u-<N> under -N%, <a>to<b> in %
Predicate numbers are in the code's units (catalog "units"); metric values are converted with UNIT_FACTOR."""
import dataclasses
import functools
import json
import re
from pathlib import Path

PATH = Path(__file__).with_name("catalog.yaml")
EXACT, APPROX, UNAVAILABLE = "IMPLEMENTED_EXACT", "IMPLEMENTED_APPROX", "UNAVAILABLE"
NUM = r"-?\d+(?:\.\d+)?"

# metric id -> unit of its value as the engine computes it (natural units: dollars, shares, fractions)
UNITS = {
    "market_cap": "usd", "price": "usd", "atr14": "usd", "ah_close": "usd",
    "avg_volume": "shares", "volume": "shares", "float": "shares", "shares_outstanding": "shares", "trades": "count",
    "rel_volume": "x", "beta": "x", "rsi14": "x", "short_ratio": "x",
    **{k: "x" for k in ("pe", "forward_pe", "peg", "ps", "pb", "pc", "pfcf", "ev_ebitda", "ev_sales", "current_ratio",
                        "quick_ratio", "lt_debt_eq", "debt_eq")},
    **{k: "frac" for k in (
        "dividend_yield", "short_float", "float_pct", "volume_pct_float", "change", "change_open", "gap", "ah_change",
        "perf_w", "perf_m", "perf_q", "perf_h", "perf_ytd", "perf_y", "perf_3y", "perf_5y", "perf_10y",
        "volatility_w", "volatility_m", "eps_qoq", "eps_yoy_ttm", "eps_3y", "eps_5y", "sales_qoq", "sales_yoy_ttm",
        "sales_3y", "sales_5y", "roa", "roe", "roi", "gross_margin", "oper_margin", "net_margin", "payout",
        "insider_own", "insider_trans", "inst_own", "inst_trans", "eps_growth_this_y", "eps_growth_next_y",
        "eps_next_5y", "div_growth_1y", "div_growth_3y", "div_growth_5y",
        "price_vs_sma20", "price_vs_sma50", "price_vs_sma200", "sma20_vs_sma50", "sma20_vs_sma200", "sma50_vs_sma20",
        "sma50_vs_sma200", "sma200_vs_sma20", "sma200_vs_sma50",
        *(f"from_{hl}_{w}" for hl in ("high", "low") for w in ("20d", "50d", "52w", "all")))},
    "div_growth_years": "count",
}
# code unit (catalog filter "unit") -> factor from the metric's natural unit to the code's unit
UNIT_FACTOR = {("usd", "usd_b"): 1e-9, ("shares", "shares_k"): 1e-3, ("shares", "shares_m"): 1e-6,
               ("frac", "pct"): 100.0}


class UnknownCode(ValueError):
    """A code no catalog filter recognizes."""


@dataclasses.dataclass(frozen=True)
class Predicate:
    """One resolved filter code (alts: the other options of a '|' OR, all on the same prefix)."""
    code: str
    prefix: str
    option: str
    metric: str
    pred: tuple | None
    status: str
    reason: str
    evidence: str
    label: str
    elite: bool = False
    custom: bool = False
    alts: tuple = ()

    @property
    def approx(self):
        return self.status == APPROX or any(a.status == APPROX for a in self.alts)

    @property
    def available(self):
        return self.status != UNAVAILABLE and self.pred is not None

    def metrics(self):
        """Every metric this predicate reads (compound predicates read several)."""
        out = []
        for p in (self, *self.alts):
            if p.pred and p.pred[0] == "all":
                out += [part["metric"] for part in p.pred[1]]
            else:
                out.append(p.metric)
        return list(dict.fromkeys(out))


def _freeze(x):
    if isinstance(x, list):
        return tuple(_freeze(i) for i in x)
    if isinstance(x, dict):
        return {k: _freeze(v) for k, v in x.items()}
    return x


@functools.lru_cache(None)
def load(path=None):
    """catalog.yaml -> dict (comment lines starting with '#' are YAML comments)."""
    text = Path(path or PATH).read_text("utf-8")
    return json.loads("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")))


@functools.lru_cache(None)
def filters():
    return {f["prefix"]: f for f in load()["filters"]}


@functools.lru_cache(None)
def signals():
    return {s["code"]: s for s in load()["signals"]}


@functools.lru_cache(None)
def _prefixes():
    return sorted(filters(), key=len, reverse=True)


def split(code):
    """'sh_float_u50' -> ('sh_float', 'u50'); the longest catalog prefix wins (ta_perf2 before ta_perf)."""
    code = code.strip().lower()
    for p in _prefixes():
        if code.startswith(p + "_") and len(code) > len(p) + 1:
            return p, code[len(p) + 1:]
    raise UnknownCode(code)


def _num(s):
    v = float(s)
    return int(v) if v.is_integer() else v


def custom(family, option):
    """A custom value in the family's grammar -> predicate tuple, or None."""
    if family == "updown":
        if m := re.fullmatch(r"u(\d+(?:\.\d+)?)", option):
            n = _num(m.group(1))
            return (">=", 0) if n == 0 else (">", n)
        if m := re.fullmatch(r"d(\d+(?:\.\d+)?)", option):
            n = _num(m.group(1))
            return ("<=", 0) if n == 0 else ("<", -n)
        if m := re.fullmatch(r"u-(\d+(?:\.\d+)?)", option):
            return ("<", -_num(m.group(1)))
        if m := re.fullmatch(r"o(\d+(?:\.\d+)?)", option):
            return (">", _num(m.group(1)))
    if family in ("numeric", "updown"):
        if m := re.fullmatch(rf"u({NUM})", option):
            return ("<", _num(m.group(1)))
        if m := re.fullmatch(rf"o({NUM})", option):
            return (">", _num(m.group(1)))
        if m := re.fullmatch(rf"({NUM})to({NUM})", option):
            a, b = _num(m.group(1)), _num(m.group(2))
            return ("range", min(a, b), max(a, b))
        if m := re.fullmatch(rf"({NUM})to", option):
            return (">=", _num(m.group(1)))
        if m := re.fullmatch(rf"to({NUM})", option):
            return ("<=", _num(m.group(1)))
    return None


def _one(prefix, option, code):
    f = filters()[prefix]
    o = f["options"].get(option)
    if o is not None:
        pred = _freeze(o.get("pred"))
        status = o.get("status") or (UNAVAILABLE if pred is None else f["status"])
        return Predicate(code=code, prefix=prefix, option=option, metric=o.get("metric", f["metric"]), pred=pred,
                         status=status, reason=o.get("reason", f["reason"]), evidence=o.get("evidence", f["evidence"]),
                         label=o.get("label", option), elite=bool(o.get("elite") or f["elite"]))
    if f.get("custom") == "grammar":
        pred = custom(f["family"], option)
        if pred is not None:
            return Predicate(code=code, prefix=prefix, option=option, metric=f["metric"], pred=pred,
                             status=f["status"], reason=f["reason"], evidence="INFERRED",
                             label=f"custom {option}", elite=f["elite"], custom=True)
    if f["status"] == UNAVAILABLE:  # every value of an unavailable filter is unavailable, known option or not
        return Predicate(code=code, prefix=prefix, option=option, metric=f["metric"], pred=None, status=UNAVAILABLE,
                         reason=f["reason"], evidence="INFERRED", label=option, elite=f["elite"])
    raise UnknownCode(code)


def resolve(code):
    """One finviz-style code (options of one prefix may be joined with '|') -> Predicate. UnknownCode if any part
    is unknown."""
    prefix, option = split(code)
    parts = [o for o in option.split("|") if o]
    if not parts:
        raise UnknownCode(code)
    preds = [_one(prefix, o, f"{prefix}_{o}") for o in parts]
    head = preds[0]
    if len(preds) == 1:
        return head
    status = head.status if all(p.status == head.status for p in preds) else (
        UNAVAILABLE if any(p.status == UNAVAILABLE for p in preds) else APPROX)
    return dataclasses.replace(head, code=f"{prefix}_{'|'.join(parts)}", status=status, alts=tuple(preds[1:]))


def factor(metric, prefix):
    """Factor from `metric`'s natural unit to the unit the codes of filter `prefix` use."""
    unit = filters()[prefix]["unit"]
    return UNIT_FACTOR.get((UNITS.get(metric), unit), 100.0 if UNITS.get(metric) == "frac" and unit == "pct" else 1.0)


def check(pred, value, scale=1.0, fn=None, values=None):
    """Does `value` (natural units) satisfy predicate tuple `pred`? Missing value -> False. scale converts the value
    to the code's units; fn(name, arg, value) evaluates ("fn", ...) predicates; values(metric) feeds "all"."""
    if pred is None:
        return False
    op = pred[0]
    if op == "all":
        return all(check(part["pred"], values(part["metric"]) if values else None, 1.0, fn, values)
                   for part in pred[1])
    if op == "fn":
        return bool(fn and fn(pred[1], pred[2], value))
    if value is None:
        return False
    if op == "is":
        return bool(value) is pred[1]
    if op == "in":
        return value in pred[1]
    if op == "eq":
        return (value * scale if isinstance(value, (int, float)) and not isinstance(value, bool) else value) == pred[1]
    v = round(value * scale, 9)  # 0.07 * 100 = 7.000000000000001 must still equal 7 at a boundary
    if op == "<":
        return v < pred[1]
    if op == ">":
        return v > pred[1]
    if op == "<=":
        return v <= pred[1]
    if op == ">=":
        return v >= pred[1]
    if op == "range":
        return pred[1] <= v <= pred[2]
    raise ValueError(f"unknown predicate op {op!r}")


def counts():
    """Coverage of the catalog: {"filters": {status: n}, "options": {status: n}, "signals": {status: n},
    "evidence": {tag: n} (options)} for every public (non-Elite) filter and signal."""
    out = {"filters": {}, "options": {}, "signals": {}, "evidence": {}, "elite_filters": 0}
    for f in load()["filters"]:
        if f["elite"]:
            out["elite_filters"] += 1
            continue
        out["filters"][f["status"]] = out["filters"].get(f["status"], 0) + 1
        for name, o in f["options"].items():
            st = o.get("status") or (UNAVAILABLE if o.get("pred") is None else f["status"])
            out["options"][st] = out["options"].get(st, 0) + 1
            ev = o.get("evidence", f["evidence"])
            out["evidence"][ev] = out["evidence"].get(ev, 0) + 1
    for s in load()["signals"]:
        out["signals"][s["status"]] = out["signals"].get(s["status"], 0) + 1
    return out
