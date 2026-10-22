"""Screen text -> Query. Accepts a finviz-style filter string ("cap_small,sh_float_u50 o=-change s=ta_unusualvolume"),
a query string ("f=...&o=...&s=...&v=111"), or a full screener URL pasted as text - parsed, never fetched (G1).

Codes are classified by lookup: a signal code -> the signal (one per screen; the first wins), a catalog code -> a
filter, anything else -> ignored with its reason. Unavailable codes are ignored too, and listed (E2): nothing is
dropped silently."""
import dataclasses
import re
import urllib.parse

from bot.screener import catalog

MAX_CODES = 25
ALLOWED = re.compile(r"^[a-z0-9_.|,\-=]+$")  # one token of Telegram input (H1)
KEYS = ("f", "o", "s", "v", "t", "r", "ft")


@dataclasses.dataclass
class Query:
    filters: list = dataclasses.field(default_factory=list)  # [catalog.Predicate] (available ones)
    signal: dict | None = None  # catalog signal entry
    sort: tuple | None = None  # (metric, descending, finviz key)
    view: str = "overview"
    tickers: list = dataclasses.field(default_factory=list)
    ignored: list = dataclasses.field(default_factory=list)  # [(code, reason)]
    codes: list = dataclasses.field(default_factory=list)  # every code as given, in order

    @property
    def approx(self):
        return any(p.approx for p in self.filters) or bool(self.signal and self.signal["status"] == catalog.APPROX)

    def key(self):
        """Canonical text of the query (presets, dedup)."""
        parts = [",".join(p.code for p in self.filters)]
        if self.signal:
            parts.append(f"s={self.signal['code']}")
        if self.sort:
            parts.append(f"o={'-' if self.sort[1] else ''}{self.sort[2]}")
        return " ".join(x for x in parts if x)


class InputError(ValueError):
    """User input outside the allowed alphabet or size (H1)."""


def _pairs(text):
    """Text -> [(key, value)]; a bare token is ("f", token). URLs and query strings are split on '&'."""
    text = text.strip()
    out = []
    for tok in re.split(r"\s+", text):
        if not tok:
            continue
        if "?" in tok and ("://" in tok or tok.split("?", 1)[0].endswith((".ashx", "screener"))):
            tok = tok.split("?", 1)[1]
        for part in tok.split("&"):
            key, vals = "f", []  # "cap_small,s=ta_x,sh_price_o5" -> f: cap_small; s: ta_x, sh_price_o5
            for seg in urllib.parse.unquote(part).split(","):
                if "=" in seg:
                    if vals:
                        out.append((key, ",".join(vals)))
                    key, seg = seg.split("=", 1)
                    key, vals = key.lower(), []
                if seg:
                    vals.append(seg)
            if vals or key != "f":
                out.append((key, ",".join(vals)))
    return out


def sort_key(value):
    """'-change' -> ('change', True, 'change'); unknown key -> None."""
    desc = value.startswith("-")
    key = value.lstrip("-").lower()
    metric = catalog.load()["sorts"].get(key)
    return (metric, desc, key) if metric else None


def parse(text, strict=False):
    """text -> Query. strict (Telegram input): every token must match ALLOWED and there are at most MAX_CODES
    codes, else InputError."""
    q = Query()
    pairs = _pairs(text)
    if strict:
        for k, v in pairs:
            if not ALLOWED.match(f"{k}={v}".lower()):
                raise InputError(f"characters outside {ALLOWED.pattern}")
    sigs = catalog.signals()
    for k, v in pairs:
        if k == "o":
            s = sort_key(v)
            if s:
                q.sort = s
            else:
                q.ignored.append((f"o={v}", "unknown sort key"))
            continue
        if k == "v":
            q.view = catalog.load()["views"].get(v, "overview")
            continue
        if k == "t":
            q.tickers += [t.strip().upper() for t in v.split(",") if t.strip()]
            continue
        if k in ("r", "ft"):
            continue  # row offset / filter tab: display only
        if k not in ("f", "s"):
            q.ignored.append((f"{k}={v}", "unknown parameter"))
            continue
        for code in [c.strip().lower() for c in v.split(",") if c.strip()]:
            q.codes.append(code)
            if code in sigs:
                s = sigs[code]
                if q.signal is not None:
                    q.ignored.append((code, "only one signal per screen"))
                elif s["status"] == catalog.UNAVAILABLE:
                    q.ignored.append((code, f"unavailable: {s['reason']}"))
                else:
                    q.signal = s
                continue
            try:
                p = catalog.resolve(code)
            except catalog.UnknownCode:
                q.ignored.append((code, "unknown code"))
                continue
            if not p.available:
                q.ignored.append((code, f"unavailable: {p.reason}"))
                continue
            old = next((x for x in q.filters if x.prefix == p.prefix), None)
            if old is not None:  # finviz keeps one value per filter: the later code wins
                q.filters.remove(old)
                q.ignored.append((old.code, f"replaced by {code}"))
            q.filters.append(p)
    if strict and len(q.codes) > MAX_CODES:
        raise InputError(f"more than {MAX_CODES} codes")
    return q
