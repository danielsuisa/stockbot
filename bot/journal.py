"""Alert journal - data/journal.json, written by the scan workflow only: every alert with its context (price,
quality, regime, forensic scores, liquidity), then its 30/90/180-day return against SPY for /stats and the weekly
summary. Informational research statistics, not advice."""
import datetime as dt
import json
import os
import statistics
from pathlib import Path

from bot import common, market
from bot.common import code, money

HORIZONS = (30, 90, 180)
OPEN_DAYS = 180
BUCKETS = ((0, 40, "0–39"), (40, 70, "40–69"), (70, 101, "70–100"))
SCHEMA = 1


def path():
    """JOURNAL_FILE, else journal.json next to the state file (so a STATE_FILE override moves both together)."""
    return Path(common.env("JOURNAL_FILE") or Path(common.env("STATE_FILE", str(common.DATA / "state.json")))
                .with_name("journal.json"))


def load(p=None):
    """journal.json -> {"v", "alerts": [...]} (missing file -> empty; older entries gain the fields they lack)."""
    p = p or path()
    j = json.loads(p.read_text("utf-8")) if p.exists() else {}
    j = {"v": SCHEMA, "alerts": [], **j}
    for e in j["alerts"]:
        e.setdefault("returns", {})
        e.setdefault("sic", None)
    return j


def save(j, p=None):
    p = p or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(j, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)


def record(j, entry):
    """Append an alert (its id made unique for the day) -> the stored entry."""
    base, n = entry["id"], 2
    ids = {e["id"] for e in j["alerts"]}
    while entry["id"] in ids:
        entry["id"], n = f"{base}-{n}", n + 1
    j["alerts"].append({**entry, "returns": {}})
    return j["alerts"][-1]


def _age(e, today):
    return (today - dt.date.fromisoformat(e["date"])).days


def concentration(j, sic, today):
    """Open journal entries (< 180 days) in the same SIC major group (first two digits) as `sic`."""
    if not sic:
        return 0
    return sum(1 for e in j["alerts"] if e.get("sic") and str(e["sic"])[:2] == str(sic)[:2] and _age(e, today) < OPEN_DAYS)


def followup(j, today):
    """Fill every reached 30/90/180-day horizon: price vs price at alert, and SPY over the same dates.
    -> entries updated. Missing prices leave the horizon empty (retried next week), never estimated."""
    updated, spy = 0, {}
    for e in j["alerts"]:
        start = dt.date.fromisoformat(e["date"])
        for h in HORIZONS:
            if str(h) in e["returns"] or _age(e, today) < h:
                continue
            a0, a1 = market.close_on(e["ticker"], start), market.close_on(e["ticker"], start + dt.timedelta(h))
            if start not in spy:
                spy[start] = market.close_on("SPY", start)
            s1 = market.close_on("SPY", start + dt.timedelta(h))
            if not (a0 and a1 and spy[start] and s1):
                continue
            ret, sret = a1[1] / a0[1] - 1, s1[1] / spy[start][1] - 1
            e["returns"][str(h)] = {"end": a1[0].isoformat(), "ret": round(ret, 4), "spy": round(sret, 4),
                                    "excess": round(ret - sret, 4)}
            updated += 1
    return updated


def _bucket(q):
    return next(label for lo, hi, label in BUCKETS if lo <= (q or 0) < hi)


def stats(j):
    """Aggregate returns: per horizon n / hit rate (excess > 0) / mean and median excess; per regime and per
    quality bucket (at the longest horizon each alert has reached); best and worst alerts."""
    out = {"count": len(j["alerts"]), "horizons": {}, "regime": {}, "quality": {}}
    for h in HORIZONS:
        xs = [e["returns"][str(h)]["excess"] for e in j["alerts"] if str(h) in e["returns"]]
        out["horizons"][h] = {"n": len(xs), "hit": sum(x > 0 for x in xs) / len(xs) if xs else None,
                              "mean": statistics.mean(xs) if xs else None, "median": statistics.median(xs) if xs else None}
    latest = []
    for e in j["alerts"]:
        h = max((int(k) for k in e["returns"]), default=None)
        if h:
            latest.append((e, h, e["returns"][str(h)]["excess"]))
    for key, fn in (("regime", lambda e: e.get("regime") or "unknown"), ("quality", lambda e: _bucket(e.get("quality")))):
        groups = {}
        for e, _, x in latest:
            groups.setdefault(fn(e), []).append(x)
        out[key] = {k: {"n": len(v), "hit": sum(x > 0 for x in v) / len(v), "mean": statistics.mean(v)}
                    for k, v in groups.items()}
    ranked = sorted(latest, key=lambda t: t[2])
    out["best"], out["worst"] = ranked[::-1][:3], ranked[:3]
    return out


def _pct(x):
    return code(f"{x * 100:+.1f}%") if x is not None else "—"


def stats_text(j, title="📒 <b>יומן ההתראות</b>"):
    """Hebrew /stats and weekly summary."""
    s = stats(j)
    lines = [title, f"התראות ביומן: {code(s['count'])}"]
    if not any(v["n"] for v in s["horizons"].values()):
        return "\n".join(lines + ["עדיין אין התראות בנות 30 יום ומעלה, ולכן אין תשואות למדוד."])
    for h, v in s["horizons"].items():
        if v["n"]:
            lines.append(f"אחרי {code(h)} יום: {code(v['n'])} התראות · הצלחה {code(format(v['hit'], '.0%'))}"
                         f" · עודף תשואה על SPY: חציון {_pct(v['median'])}, ממוצע {_pct(v['mean'])}")
    names = {"panic": "פאניקה", "normal": "רגיל", "euphoria": "אופוריה", "unknown": "לא ידוע"}
    lines += [f"לפי מצב שוק: " + " · ".join(f"{names.get(k, k)} {code(v['n'])} ({_pct(v['mean'])})" for k, v in s["regime"].items()),
              f"לפי ציון איכות: " + " · ".join(f"{code(k)} {code(v['n'])} ({_pct(v['mean'])})"
                                                for k, v in sorted(s["quality"].items()))]
    for label, rows in (("הטובות", s["best"]), ("החלשות", s["worst"])):
        lines.append(f"{label}: " + " · ".join(f"{code(e['ticker'])} {_pct(x)} ({code(h)} יום)" for e, h, x in rows))
    return "\n".join(lines)


def journal_text(j, n=5):
    """Hebrew /journal N: the last N alerts with their context and returns so far."""
    es = j["alerts"][-max(1, min(n, 20)):][::-1]
    if not es:
        return "📒 היומן ריק: עדיין לא נשלחו התראות."
    out = [f"📒 <b>ההתראות האחרונות ביומן</b> ({code(len(es))})"]
    for e in es:
        rets = " · ".join(f"{code(h)} יום {_pct(e['returns'][str(h)]['excess'])}" for h in HORIZONS if str(h) in e["returns"])
        px = e.get("price", {}).get("price")
        out.append(f"• התראה {code(e['id'])} · {code(e['ticker'])} · איכות {code(e.get('quality'))}"
                   f" · מחיר {code(common.price(px)) if px else 'חסר'}"
                   + (f" · עודף על SPY: {rets}" if rets else " · עוד אין תשואה"))
    return "\n".join(out)


def entry_for(ev, snap, regime, quote, liquidity, scores, sic, company):
    """The journal entry for one sent alert."""
    return {"id": snap["id"], "date": snap["date"], "ticker": snap["ticker"], "cik": ev["open"][0]["cik"],
            "company": company, "sic": sic,
            "kind": "+".join(k for k, on in (("cluster", ev["cluster"]), ("big", ev["big"])) if on),
            "quality": ev["score"], "parts": ev["parts"], "regime": (regime or {}).get("tag", "unknown"),
            "price": {k: quote.get(k) for k in ("price", "asof", "source")}, "total": snap["total"],
            "insiders": len({b["insider_cik"] or b["insider"] for b in ev["open"]}), "liquidity": liquidity,
            "scores": scores, "accs": snap["accs"]}


def scores_of(res):
    """fundamentals.analyze() result -> compact {piotroski, altman, beneish} for the journal (None when missing)."""
    if not res or res.get("error"):
        return {"piotroski": None, "altman": None, "beneish": None}
    p = res.get("pio", [])
    ok, n = sum(x["ok"] is True for x in p), sum(x["ok"] is not None for x in p)
    alt, ben = res.get("alt") or {}, res.get("ben") or {}
    return {"piotroski": f"{ok}/{n}" if n else None,
            "altman": f"{alt['kind']} {alt['z']:.2f}" if "z" in alt else None,
            "beneish": round(ben["m"], 2) if "m" in ben else None}


def context_lines(e, conc):
    """Alert lines for block 5: price at alert, liquidity guard, forensic scores, sector concentration."""
    px, lq, s = e["price"], e["liquidity"], e["scores"]
    price_txt = code(common.price(px["price"])) if px.get("price") else "חסר"
    if px.get("source") == "cache":
        price_txt += f" (⚠ מחיר מ־{code(px['asof'])})"
    lines = [f"💵 מחיר בהתראה: {price_txt} · נזילות: "
             + (f"{code(money(lq))} ליום (ממוצע 30 יום)" + (" · ⚠ נזילות נמוכה" if lq < market.LOW_LIQUIDITY else "")
                if lq else "חסר")]
    lines.append(f"🧮 ציונים: פיוטרוסקי {code(s['piotroski']) if s['piotroski'] else 'חסר'} · אלטמן "
                 f"{code(s['altman']) if s['altman'] else 'חסר'} · בנייש {code(s['beneish']) if s['beneish'] is not None else 'חסר'}")
    if conc >= 3:
        group = f"SIC {str(e['sic'])[:2]}xx"
        lines.append(f"⚠ ריכוז סקטוריאלי: כבר {code(conc)} התראות פתוחות בענף {code(group)}")
    return lines
