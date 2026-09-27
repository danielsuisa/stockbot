"""Live short-squeeze list - phases 2-3 of docs/superpowers/specs/2026-09-27-squeeze-screener-design.md.

Once a trading day before the US open (.github/workflows/squeeze.yml): rank the market with the variant the
backtest froze, send the top 10 to Telegram with the backtest's spike AND crash rates, journal the names, and fill
in the 10-session outcome of earlier lists. Borrow (IBKR) and options (CBOE) numbers are shown as unvalidated.

    python -m bot.squeeze_live          # run, send, write data/squeeze_last.json and data/squeeze_journal.json
    python -m bot.squeeze_live --dry    # print the message only, write nothing"""
import argparse
import bisect
import concurrent.futures as cf
import datetime as dt
import json
import os
import statistics
import traceback
from pathlib import Path

from bot import borrow, common, market, options, shorts, squeeze
from bot.common import code

VARIANT = {"w": 0.7, "g_si": 0.20, "g_rv": 2.0}  # frozen by the backtest: docs/backtest/squeeze-2026-09-27.md
NAME = "w0.7/si0.2/rv2"
BACKTEST = {"hit": 0.068, "crash": 0.071, "base": 0.010, "years": "2024–2026"}  # out-of-sample, run 3
CRASH = 1 / squeeze.HIT - 1  # -33%: the fall that mirrors the +50% spike
LAST, JOURNAL = "squeeze_last.json", "squeeze_journal.json"
FRAMES = 6  # quarters of SEC frames: facts sit within ~45 days of a quarter end, so 6 cover the 400-day age limit
BORROW_TIMEOUT = 10  # seconds: an unreachable FTP must not stall the list or the Telegram listener for long
FIELDS = ("score", "si_pct", "dtc", "chg", "rvol", "ret5", "brk", "price", "fuel", "trigger", "shares")


# ---------- files ----------
def path(name):
    return Path(common.env("SQUEEZE_DIR", str(common.DATA))) / name


def load(name, default):
    p = path(name)
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def save(name, obj):
    p = path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)


def _round(x):
    return round(x, 4) if isinstance(x, float) else x


# ---------- data ----------
def calendar(today):
    """Completed US sessions before `today` (ISO, ascending), from SPY's daily bars ([] when Yahoo fails)."""
    b = squeeze.to_bars(market.chart("SPY", range="6mo", interval="1d"))
    return [d for d in (b or {}).get("d", []) if d < today]


def latest_report(cal, today):
    """(settlement date, {FINRA symbol: row}) of the newest FINRA report usable on `today` (its 8th session after
    settlement, `today` counted as a session), or (None, {})."""
    for settle in reversed(shorts.settlement_dates()[-3:]):
        use = shorts.usable_from(settle, cal + [today])
        if use and use <= today:
            return settle, shorts.report(settle)
    return None, {}


def frames_now(today):
    """SEC shares frames of the current calendar quarter and the FRAMES - 1 before it (today: a date) - enough for
    shorts.SHARES_MAX_AGE, so the live candidate set matches the backtest's."""
    y, q, out = today.year, (today.month - 1) // 3 + 1, []
    for _ in range(FRAMES):
        out.append(shorts.frame(y, q))
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    return out


def bars_for(tickers, threads=8):
    """{ticker: bars} with 3 months of Yahoo daily bars, for each ticker Yahoo answers for."""
    def one(t):
        return squeeze.to_bars(market.chart(t, range="3mo", interval="1d"))
    with cf.ThreadPoolExecutor(threads) as ex:
        got = dict(zip(tickers, ex.map(one, tickers)))
    return {t: b for t, b in got.items() if b}


def features_at(b, today, prev):
    """Features of a ticker for session `today` from its bars through `prev` (= D-1); None without a bar on D-1."""
    if not b:
        return None
    i = bisect.bisect_left(b["d"], today) - 1
    return squeeze.features(b, i) if i >= 0 and b["d"][i] == prev else None


def build(today):
    """Today's list (today: ISO) -> {"date", "prev", "si_date", "rows" (the top list), "gated" (short interest past
    the gate), "candidates" (also past price / liquidity), "missing" (no Yahoo bars), "recent" (the last WINDOW
    sessions incl. today)}. RuntimeError when Yahoo's calendar or a usable FINRA report is missing."""
    cal = calendar(today)
    if not cal:
        raise RuntimeError("Yahoo: no SPY sessions")
    settle, raw = latest_report(cal, today)
    if not raw:
        raise RuntimeError("FINRA: no usable short-interest report")
    primary = {t: cik for cik, t in common.cik_tickers().items()}
    if not primary:
        raise RuntimeError("SEC: no ticker map")
    frames = frames_now(dt.date.fromisoformat(today))
    if not any(frames):  # SEC down must alarm, not read as "no stock passes today"
        raise RuntimeError("SEC: no shares-outstanding frames")
    shares = shorts.shares_index(frames)
    gated = []
    for t, si in shorts.remap(raw, primary).items():
        n = shorts.shares_at(shares, primary[t], today) if t in primary else None
        if n and VARIANT["g_si"] <= si["si"] / n <= squeeze.MAX_SI_PCT:
            gated.append((t, si, n))
    bars = bars_for([t for t, _, _ in gated])
    rows = [r for t, si, n in gated if (r := squeeze.make_row(t, si, n, features_at(bars.get(t), today, cal[-1])))]
    return {"date": today, "prev": cal[-1], "si_date": settle, "rows": squeeze.rank(rows, VARIANT),
            "gated": len(gated), "candidates": len(rows), "missing": len(gated) - len(bars),
            "recent": cal[-(squeeze.WINDOW - 1):] + [today]}


def enrich(rows, today):
    """Unvalidated extras for the listed names: IBKR borrow fee / availability and CBOE gamma fuel (None = missing).
    today: a date. No rows -> no downloads."""
    if not rows:
        return rows
    fees = borrow.fetch(timeout=BORROW_TIMEOUT)
    for r in rows:
        r["borrow"] = fees.get(r["t"])
        chain = options.chain(r["t"])
        r["gamma"] = options.gamma(chain, r["shares"], today) if chain else None
    return rows


# ---------- message ----------
def _pct(x, signed=False):
    return code(f"{x * 100:+.0f}%" if signed else f"{x * 100:.1f}%")


def _num(x, fmt):
    return code(format(x, fmt))


def line(k, r):
    """One listed stock (Hebrew first, RTL): rank, ticker, score and the numbers behind it."""
    brk = " · פריצה ⬆️" if r["brk"] >= 1 else ""
    return (f"מקום {code(k)}: {code(r['t'])} · ציון {_num(r['score'], '.2f')} · שורט {_pct(r['si_pct'])} מהמניות · "
            f"{_num(r['dtc'], '.1f')} ימי כיסוי · מחזור פי {_num(r['rvol'], '.1f')} · 5 ימים {_pct(r['ret5'], True)}"
            f" · מחיר {code(common.price(r['price']))}{brk}")


def extras(r):
    """The unvalidated options / borrow numbers of one listed stock, or '' when both sources are missing."""
    out, g, b = [], r.get("gamma"), r.get("borrow")
    if g:
        out.append(f"חשיפת קולים עד {code(options.NEAR)} יום {_pct(g['exposure'])} מהמניות")
        if g.get("iv30") is not None:
            out.append(f"תנודתיות גלומה {code(format(g['iv30'], '.0f') + '%')}")
    if b:
        fee = "חסרה" if b["fee"] is None else code(format(b["fee"], ".1f") + "%")
        avail = "חסרה" if b["avail"] is None else code((">" if b["more"] else "") + format(b["avail"], ","))
        out.append(f"עמלת השאלה {fee} בשנה · זמינות להשאלה {avail}")
    return f"   ↳ לא מאומת: {' · '.join(out)}" if out else ""


def footer():
    """The backtest line every squeeze message carries: spikes AND crashes, so a pick never reads as a buy signal."""
    return (f"📊 בבדיקה ההיסטורית ({code(BACKTEST['years'])}): {_pct(BACKTEST['hit'])} מהמניות ברשימה עלו {code('50%+')} "
            f"תוך {code(squeeze.WINDOW)} ימי מסחר, ו־{_pct(BACKTEST['crash'])} ירדו {code('33%')} ומעלה; בכל השוק "
            f"{_pct(BACKTEST['base'])} עלו {code('50%+')}. זה סימן לתנודה חדה צפויה, לא לכיוון.")


def message(res):
    """The daily Hebrew list for Telegram."""
    head = [f"🚀 <b>רשימת סקוויז ליום {code(res['date'])}</b>",
            f"דירוג בשיטה שנקבעה בבדיקה ההיסטורית ({code(NAME)}): שורט של {code('20%')} מהמניות לפחות, ומחזור ביום "
            f"המסחר האחרון של פי {code(2)} לפחות מהממוצע."]
    body = []
    for k, r in enumerate(res["rows"], 1):
        x = extras(r)
        body += [line(k, r)] + ([x] if x else [])
    missing = f", ל־{code(res['missing'])} אין נתוני Yahoo" if res["missing"] else ""
    foot = [footer(),
            f"🗓️ שורט לפי דוח FINRA מ־{code(res['si_date'])} · מחירים עד {code(res['prev'])} · "
            f"{code(res['gated'])} מניות עם שורט גבוה, {code(res['candidates'])} עברו סינון מחיר ונזילות{missing}.",
            "ℹ️ שורות ״לא מאומת״ (אופציות, השאלה) עוד לא נבדקו היסטורית ואינן משפיעות על הדירוג."]
    return "\n".join(head + [""] + (body or ["🤷 אין היום מניה שעוברת את הסינון."]) + [""] + foot)


# ---------- journal ----------
def record(j, res):
    """Journal today's names; a ticker journaled within the last WINDOW sessions counts once (as in the backtest)
    -> the number added."""
    seen = {e["t"] for e in j["entries"] if e["date"] >= res["recent"][0]}  # by date order: a holiday run counts too
    added = 0
    for k, r in enumerate(res["rows"], 1):
        if r["t"] in seen:
            continue
        g = r.get("gamma")
        j["entries"].append({"date": res["date"], "t": r["t"], "rank": k, "variant": NAME, "si_date": res["si_date"],
                             **{f: _round(r.get(f)) for f in FIELDS}, "borrow": r.get("borrow"),
                             "gamma": {x: _round(v) for x, v in g.items()} if g else None, "outcome": None})
        added += 1
    return added


def followup(j, today):
    """Fill the outcome of every journal entry whose 10 sessions have closed: entry = the open of the first session
    on/after the list date, hit = high >= 1.5x entry, crash = low <= entry / 1.5 -> the number filled. Missing data
    is retried on the next run, never estimated; no bar on the entry session -> no trade."""
    todo = [e for e in j["entries"] if e.get("outcome") is None]
    if not todo:
        return 0
    p1 = int(dt.datetime.fromisoformat(min(e["date"] for e in todo)).replace(tzinfo=dt.timezone.utc).timestamp())
    p1 -= 7 * 86400
    p2 = int(dt.datetime.now(dt.timezone.utc).timestamp())
    spy = squeeze.to_bars(market.chart("SPY", period1=p1, period2=p2, interval="1d"))
    cal = [d for d in (spy or {}).get("d", []) if d < today]
    filled = 0
    for e in todo:
        k = bisect.bisect_left(cal, e["date"])
        if k + squeeze.WINDOW - 1 >= len(cal):
            continue
        b = squeeze.to_bars(market.chart(e["t"], period1=p1, period2=p2, interval="1d"))
        if not b:
            continue
        start, end = cal[k], cal[k + squeeze.WINDOW - 1]
        i = bisect.bisect_left(b["d"], start)
        o = squeeze.outcome(b, i, end) if i < len(b["d"]) and b["d"][i] == start else None
        e["outcome"] = {"trade": False, "start": start} if o is None else {
            "trade": True, "start": start, "end": end, "hit": o["hit"], "crash": o["dd"] <= CRASH,
            **{x: round(o[x], 4) for x in ("maxup", "dd", "r10")}}
        filled += 1
    return filled


def stats_text(j):
    """/squeeze stats: the journal's closed outcomes against the backtest's out-of-sample rates."""
    done = [e["outcome"] for e in j["entries"] if e.get("outcome") and e["outcome"].get("trade")]
    waiting = sum(1 for e in j["entries"] if not e.get("outcome"))
    head = "📒 <b>יומן רשימת הסקוויז</b>"
    if not done:
        return (f"{head}\nעוד אין מניות שעברו {code(squeeze.WINDOW)} ימי מסחר מאז שהופיעו ברשימה "
                f"({code(waiting)} בהמתנה).")
    n = len(done)
    small = f" (מדגם קטן, פחות מ־{code(30)})" if n < 30 else ""
    return "\n".join([
        head,
        f"הושלמו {code(n)} מניות{small}, {code(waiting)} בהמתנה: {_pct(sum(o['hit'] for o in done) / n)} עלו "
        f"{code('50%+')} ו־{_pct(sum(o['crash'] for o in done) / n)} ירדו {code('33%')} ומעלה.",
        f"חציון התשואה אחרי {code(squeeze.WINDOW)} ימי מסחר: {_pct(statistics.median(o['r10'] for o in done), True)}.",
        footer()])


# ---------- /squeeze TICKER ----------
def reasons(si_pct, feat, cls=None):
    """Why a stock is not on the list (Hebrew); [] when it passes every gate. cls: FINRA market class."""
    out = []
    if cls and cls not in shorts.CLASSES:
        out.append(f"לא נסחרת בבורסה (FINRA: {code(cls)})")
    if si_pct is None:
        out.append("אין נתוני שורט או מספר מניות")
    elif si_pct < VARIANT["g_si"]:
        out.append(f"שורט {_pct(si_pct)} נמוך מ־{code('20%')}")
    elif si_pct > squeeze.MAX_SI_PCT:
        out.append(f"שורט גבוה מ־{code('150%')} מהמניות, כנראה נתון שגוי")
    if feat is None:
        out.append("אין מספיק נתוני מסחר")
        return out
    if feat["rvol"] < VARIANT["g_rv"]:
        out.append(f"מחזור פי {_num(feat['rvol'], '.1f')} נמוך מפי {code(2)}")
    if feat["price"] < squeeze.MIN_PRICE:
        out.append(f"מחיר {code(common.price(feat['price']))} נמוך מ־{code('1$')}")
    if feat["dollar"] < squeeze.MIN_DOLLAR:
        out.append(f"מחזור דולרי {code(common.money(feat['dollar']))} נמוך מ־{code('2.0M$')}")
    return out


def ticker_text(t, si, shares, feat, rank=None, extra=""):
    """/squeeze TICKER reply from its parts (si: slim FINRA row or None; feat: squeeze.features or None)."""
    si_pct = si["si"] / shares if si and shares else None
    lines = [f"🔎 <b>בדיקת סקוויז: {code(t)}</b>"]
    if si:
        pct = f" · {_pct(si_pct)} מהמניות" if si_pct is not None else ""
        lines.append(f"שורט: {code(format(si['si'], ','))} מניות בדוח FINRA מ־{code(si['date'])}{pct} · "
                     f"{_num(si['dtc'] or 0.0, '.1f')} ימי כיסוי")
    else:
        lines.append("שורט: אין דוח FINRA לטיקר הזה.")
    if feat:
        brk = " · פריצה ⬆️" if feat["brk"] >= 1 else ""
        lines.append(f"מסחר: מחזור ביום האחרון פי {_num(feat['rvol'], '.1f')} מהממוצע · 5 ימים "
                     f"{_pct(feat['ret5'], True)} · מחיר {code(common.price(feat['price']))}{brk}")
    why = reasons(si_pct, feat, si.get("cls") if si else None)
    if why:
        lines.append("❌ לא עובר את הסינון: " + "; ".join(why) + ".")
    else:
        where = f" · מקום {code(rank)} ברשימה של היום" if rank else " (הדירוג נקבע מול כל המועמדים ברשימה היומית)"
        lines.append(f"✅ עובר את הסינון{where}.")
    if extra:
        lines.append(extra.strip())
    return "\n".join(lines + [footer()])


def ticker_report(t, today=None):
    """/squeeze TICKER: fetch the stock's numbers and explain where it stands against the list's gates."""
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    cal = calendar(today)
    usable = [r for r in shorts.symbol_rows(t.replace("-", ""))
              if (u := shorts.usable_from(r["date"], cal + [today])) and u <= today]
    cik = (common.tickers().get(t) or (None,))[0]
    shares = shorts.shares_at(shorts.shares_index(frames_now(dt.date.fromisoformat(today))), cik, today) if cik else None
    feat = features_at(squeeze.to_bars(market.chart(t, range="3mo", interval="1d")), today, cal[-1]) if cal else None
    last = load(LAST, {})
    rank = next((k for k, r in enumerate(last.get("rows", []), 1) if r["t"] == t), None) \
        if last.get("date") == today else None
    row = {"t": t, "shares": shares}
    enrich([row], dt.date.fromisoformat(today))
    return ticker_text(t, usable[-1] if usable else None, shares, feat, rank, extras(row))


# ---------- run ----------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Daily squeeze list -> Telegram and data/squeeze_*.json")
    ap.add_argument("--dry", action="store_true", help="print the message only, write nothing")
    a = ap.parse_args(argv)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    try:
        res = build(today)
    except Exception as e:  # the owner must hear about a failed list, not silence
        traceback.print_exc()
        if not a.dry:
            common.send(f"⚠️ רשימת הסקוויז של היום לא נבנתה ({code(type(e).__name__)}): {common.esc(str(e)[:200])}")
        return 1
    try:
        enrich(res["rows"], dt.date.fromisoformat(today))
    except Exception:  # unvalidated extras must never cost the day's list
        traceback.print_exc()
    j = load(JOURNAL, {"v": 1, "entries": []})
    try:
        filled = followup(j, today)
    except Exception:  # a follow-up problem must not hold back today's list; it is retried tomorrow
        traceback.print_exc()
        filled = 0
    added = record(j, res)
    text = message(res)
    if filled:
        text += f"\n📒 הושלם מעקב של {code(squeeze.WINDOW)} ימים ל־{code(filled)} מניות: {code('/squeeze stats')}"
    if a.dry:
        print(text)
        return 0
    common.send(text, signal=True)
    save(LAST, {"date": today, "text": text, "rows": [{k: _round(v) for k, v in r.items()} for r in res["rows"]]})
    save(JOURNAL, j)
    common.log("squeeze", rows=len(res["rows"]), gated=res["gated"], added=added, filled=filled)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
