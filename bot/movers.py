"""Extended-hours movers - docs/superpowers/specs/2026-09-28-extended-hours-scans-design.md, section 6: stocks past the
squeeze list's short-interest gate that are up >= 10 % in the current session on real volume. Unvalidated: every
mover is logged for a later backtest, and none of it changes the squeeze ranking."""
import bisect
import concurrent.futures as cf
import datetime as dt
import json
import re

from bot import common
from bot.common import code

EXT = "https://api.nasdaq.com/api/quote/{t}/extended-trading?markettype={m}&assetclass=stocks&time=0"
INFO = "https://api.nasdaq.com/api/quote/{t}/info?assetclass=stocks"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
MIN_GAIN, MIN_VOLUME, REALERT, EPS = 0.10, 0.10, 0.10, 1e-9
THREADS = 4  # Nasdaq calls at a time
KEEP_DAYS, LOG_DAYS = 7, 400
ICON = {"pre": "🌅", "post": "🌙", "regular": "📈"}
NAME = {"pre": "לפני המסחר", "post": "אחרי המסחר", "regular": "במסחר הרגיל"}


def _number(s):
    """'$23.42 -1.60 (-6.39%)' / '198,895' -> its first number (23.42 / 198895.0), or None."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
    return float(m.group().replace(",", "")) if m else None


def _date(s):
    """The first 'Sep 25, 2026' in a Nasdaq text -> '2026-09-25', or None."""
    m = re.search(r"[A-Z][a-z]{2} \d{1,2}, \d{4}", s or "")
    return dt.datetime.strptime(m.group(), "%b %d, %Y").date().isoformat() if m else None


def quote(t, session):
    """Nasdaq's numbers for ticker t in `session` -> {"price", "volume", "date"}; None when the session has no trades
    for it; False when Nasdaq could not be read. pre / post: the extended session, dated by its "Data last updated"
    line; regular: last sale and today's volume so far, dated by the last trade."""
    sym = t.replace("-", ".")
    try:
        if session == "regular":
            p = json.loads(common.fetch(INFO.format(t=sym), headers=HEADERS, tries=1, timeout=10))["data"]["primaryData"]
            return {"price": _number(p.get("lastSalePrice")), "volume": _number(p.get("volume")),
                    "date": _date(p.get("lastTradeTimestamp"))}
        d = json.loads(common.fetch(EXT.format(t=sym, m=session), headers=HEADERS, tries=1, timeout=10))["data"]
        rows = (d.get("infoTable") or {}).get("rows") or []
        if not rows:
            return None
        info = d.get("lastUpdateInfo")
        return {"price": _number(rows[0].get("consolidated")), "volume": _number(rows[0].get("volume")),
                "date": _date(info[0] if isinstance(info, list) and info else info)}
    except Exception as e:  # an unofficial source: any failure means "could not read"
        print(f"nasdaq {t} {session}: {type(e).__name__} {e}")
        return False


def quotes(tickers, session):
    """{ticker: quote(t, session)} for every ticker, THREADS at a time."""
    with cf.ThreadPoolExecutor(THREADS) as ex:
        return dict(zip(tickers, ex.map(lambda t: quote(t, session), tickers)))


def reference(b, session, today):
    """The close a move is measured from (bars from squeeze.to_bars): pre / regular -> the last close before
    `today`; post -> today's regular close; None without it."""
    if not b:
        return None
    i = bisect.bisect_left(b["d"], today)
    if session == "post":
        return b["c"][i] if i < len(b["d"]) and b["d"][i] == today else None
    return b["c"][i - 1] if i > 0 else None


def find(cands, qs, today):
    """cands: [{"t", "ref" (reference close), "adv" (FINRA average daily volume), ...}], qs: {t: quote} -> movers up
    >= MIN_GAIN on session volume >= MIN_VOLUME x adv with the quote dated `today`, biggest gain first. A missing
    input or a quote from another day is never a mover."""
    out = []
    for c in cands:
        q = qs.get(c["t"])
        if not q or q.get("date") != today or not (q.get("price") and c.get("ref") and c.get("adv")) \
                or q.get("volume") is None:
            continue
        gain, ratio = q["price"] / c["ref"] - 1, q["volume"] / c["adv"]
        if gain >= MIN_GAIN - EPS and ratio >= MIN_VOLUME - EPS:
            out.append({**c, "price": q["price"], "volume": q["volume"], "gain": gain, "vol_ratio": ratio})
    return sorted(out, key=lambda m: (-m["gain"], m["t"]))


def fresh(found, state, today, session, manual=False):
    """Only new or changed: movers not alerted yet in this session, or up >= REALERT above their last alerted gain
    (state["movers"]["<date>:<session>"]); a manual run returns everything. Marks what it returns."""
    book = state.setdefault("movers", {})
    seen = book.setdefault(f"{today}:{session}", {})
    out = [m for m in found if manual or m["t"] not in seen or m["gain"] >= seen[m["t"]] + REALERT - EPS]
    for m in out:
        seen[m["t"]] = max(seen.get(m["t"], m["gain"]), m["gain"])
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(KEEP_DAYS)).isoformat()
    for k in [k for k in book if k[:10] < cutoff]:
        del book[k]
    return out


def log(book, found, today, session, manual):
    """Append the movers to the log (data/movers_log.json) for a later backtest; entries older than LOG_DAYS go."""
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(LOG_DAYS)).isoformat()
    keep = [e for e in book.get("entries", []) if e["date"] >= cutoff]
    new = [{"date": today, "session": session, "t": m["t"], "manual": manual,
            **{k: round(m[k], 4) if isinstance(m.get(k), float) else m.get(k)
               for k in ("price", "ref", "gain", "vol_ratio", "si_pct")}} for m in found]
    book["entries"] = keep + new
    return book


def _pct(x, signed=False):
    return code(f"{x * 100:+.0f}%" if signed else f"{x * 100:.0f}%")


def text(found, session, today, ranks=None, failed=0, manual=False):
    """The Hebrew movers message; '' on a scheduled run with nothing to send."""
    ranks = ranks or {}
    if not found and not manual:
        return ""
    head = f"{ICON[session]} <b>מזנקות {NAME[session]} · {code(today)}</b>" if session else \
        f"🌙 <b>השוק סגור כרגע · {code(today)}</b>"
    lines = [("▶️ הרצה ידנית · " if manual else "") + head]
    for m in found:
        rank = f" · מקום {code(ranks[m['t']])} ברשימת הסקוויז" if m["t"] in ranks else ""
        lines.append(f"מזנקת: {code(m['t'])} {_pct(m['gain'], True)} · מחזור {_pct(m['vol_ratio'])} מיום רגיל · "
                     f"שורט {_pct(m['si_pct'])} מהמניות · {code(format(m.get('dtc') or 0.0, '.1f'))} ימי כיסוי{rank}")
    if not found:
        lines.append(f"🤷 אין כרגע מניה עם שורט גבוה שעלתה {code('10%')} במחזור אמיתי." if session else
                     "אין עכשיו מסחר, גם לא לפני או אחרי השעות הרגילות.")
    lines.append(f"⚠️ הכלל (עלייה של {code('10%')} לפחות ומחזור של {code('10%')} לפחות מיום רגיל) לא נבדק "
                 "היסטורית, ולכן לא מאומת."
                 + (f" · ל־{code(failed)} מניות לא התקבל ציטוט מ־Nasdaq" if failed else ""))
    return "\n".join(lines)
