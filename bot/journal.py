"""Alert journal - data/journal.json, written by the scan workflow only: every alert with its context (price,
quality, regime, forensic scores, liquidity), then its 30/90/180-day return against SPY for /stats and the weekly
summary. Informational research statistics, not advice."""
import datetime as dt
import json
import os
import statistics
from pathlib import Path

from bot import common, market
from bot.common import code

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
            if not (a0 and a1):
                continue
            for d in (a0[0], a1[0]):  # SPY over the stock's own sessions, so both returns cover the same dates
                if d not in spy:
                    spy[d] = market.close_on("SPY", d)
            s0, s1 = spy[a0[0]], spy[a1[0]]
            if not (s0 and s1):
                continue
            ret, sret = a1[1] / a0[1] - 1, s1[1] / s0[1] - 1
            e["returns"][str(h)] = {"start": a0[0].isoformat(), "end": a1[0].isoformat(), "ret": round(ret, 4),
                                    "spy": round(sret, 4),
                                    "excess": round(ret - sret, 4)}
            updated += 1
    return updated


def _bucket(q):
    return None if q is None else next(label for lo, hi, label in BUCKETS if lo <= q < hi)


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
            k = fn(e)
            if k is not None:  # an alert without a quality score is left out, not counted as low quality
                groups.setdefault(k, []).append(x)
        out[key] = {k: {"n": len(v), "hit": sum(x > 0 for x in v) / len(v), "mean": statistics.mean(v)}
                    for k, v in groups.items()}
    ranked = sorted(latest, key=lambda t: t[2])
    out["best"], out["worst"] = ranked[::-1][:3], ranked[:3]
    return out


def _vs(x):
    """Excess return over SPY in words: 'יותר ב־<code>2.0%</code> מ־SPY' (no sign glyph next to a number)."""
    if x is None:
        return "חסר"
    if round(abs(x) * 100, 1) == 0:
        return "כמו SPY"
    return f"{'יותר' if x > 0 else 'פחות'} ב־{code(f'{abs(x) * 100:.1f}%')} מ־SPY"


def stats_text(j, title="📒 <b>יומן ההתראות</b>"):
    """Hebrew /stats and weekly summary: per horizon how many alerts beat SPY and the median excess; by quality score;
    the best and worst."""
    s = stats(j)
    lines = [title, f"ביומן: {code(s['count'])} התראות."]
    if not any(v["n"] for v in s["horizons"].values()):
        return "\n".join(lines + [f"עוד אין התראות בנות {code(HORIZONS[0])} יום, אז אין עדיין תוצאות."])
    for h, v in s["horizons"].items():
        if v["n"]:
            lines.append(f"אחרי {code(h)} יום: {code(v['n'])} התראות, {code(format(v['hit'], '.0%'))} עברו את SPY"
                         f" · חציון: {_vs(v['median'])}")
    if s["quality"]:
        lines.append("לפי ציון: " + " · ".join(f"{code(k)}: {code(v['n'])} התראות, ממוצע {_vs(v['mean'])}"
                                              for k, v in sorted(s["quality"].items())))
    for label, rows in (("🏆 הטובות", s["best"]), ("🔻 החלשות", s["worst"])):
        lines.append(f"{label}: " + " · ".join(f"{code(e['ticker'])} {_vs(x)}" for e, h, x in rows))
    return "\n".join(lines)


def journal_text(j, n=5):
    """Hebrew /journal N: the last N alerts with their returns so far."""
    es = j["alerts"][-max(1, min(n, 20)):][::-1]
    if not es:
        return "📒 היומן ריק: עדיין לא נשלחו התראות."
    out = [f"📒 <b>ההתראות האחרונות ({code(len(es))})</b>"]
    for e in es:
        h = max((x for x in HORIZONS if str(x) in e["returns"]), default=None)
        px = e.get("price", {}).get("price")
        out.append(f"• התראה על {code(e['ticker'])} מ־{code(common.il_date(e['date'], year=False))} · ציון {code(e.get('quality'))}"
                   f" · מחיר אז {code(common.price(px)) if px else 'חסר'}"
                   + (f" · אחרי {code(h)} יום: {_vs(e['returns'][str(h)]['excess'])}" if h else " · עוד אין תוצאה"))
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


def score_words(s):
    """The journal's compact scores -> plain Hebrew parts (only the ones that exist)."""
    from bot import fundamentals
    out = []
    if s.get("piotroski"):
        k, n = s["piotroski"].split("/")
        out.append(f"פיוטרוסקי {code(k)} מתוך {code(n)}")
    if s.get("altman"):
        kind, z = s["altman"].split()
        out.append(f"אלטמן: {fundamentals.altman_zone(kind, float(z))}")
    if s.get("beneish") is not None:
        out.append(f"בנייש: {fundamentals.beneish_zone(s['beneish'])}")
    return out


def context_lines(e, conc):
    """Alert lines: price at alert, daily dollar volume (flagged when low), forensic scores, sector concentration."""
    px, lq, s = e["price"], e["liquidity"], e["scores"]
    price_txt = code(common.price(px["price"])) if px.get("price") else "חסר"
    if px.get("source") in ("cache", "stale") and px.get("asof"):  # Yahoo down (last saved price) / not trading lately
        price_txt += f" (שמור מ־{code(common.il_date(px['asof'], year=False))})"
    lines = [f"💵 מחיר: {price_txt}" + (f" · מחזור יומי ממוצע: {common.amount(lq)}"
                                         + (" ⚠️ נמוך" if lq < market.LOW_LIQUIDITY else "") if lq is not None else "")]
    words = score_words(s)
    if words:
        lines.append("🧮 " + " · ".join(words))
    if conc >= 3:
        lines.append(f"⚠️ כבר {code(conc)} התראות פתוחות באותו ענף.")
    return lines
