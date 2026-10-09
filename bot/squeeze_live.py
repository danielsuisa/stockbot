"""Live short-squeeze list: the owner's screen (bot/screen.py, 2026-10-08, not backtested) and the extended-hours
movers, started by the listener's clock (bot/clock.py) at the session slots (bot/sessions.py) or by /squeeze now.

Hourly in the regular session and after the close the screen runs; the first list of a day goes out in full, later
runs send only who entered and who left. Pre-market runs send movers only (today's relative volume means nothing
yet). Every listed name is journaled once per WINDOW sessions with the next regular open as its entry, and its
10-session outcome is filled in later. The backtested list this screen replaced (NAME) keeps its journal entries.

    python -m bot.squeeze_live          # run, send, write data/squeeze_*.json and data/movers_log.json
    python -m bot.squeeze_live --dry    # print the messages only, write nothing"""
import argparse
import bisect
import concurrent.futures as cf
import datetime as dt
import json
import os
import statistics
import traceback
from pathlib import Path

from bot import common, market, movers, screen, sessions, shorts, squeeze
from bot.common import code

SCREEN = "screen-2026-10-08"  # the journal's name for the owner's screen
SCREEN_PRE = "screen-pre-2026-10-08"  # ... and for its pre-market list (pre-market volume instead of relative volume)
NAME = "w0.7/si0.2/rv2"  # the backtested list the screen replaced on 2026-10-08 (its journal entries keep this name)
BACKTEST = {"hit": 0.068, "crash": 0.071, "base": 0.010, "years": "2024–2026"}  # that list's out-of-sample rates
CRASH = 1 / squeeze.HIT - 1  # -33%: the fall that mirrors the +50% spike
MOVERS_SI = 0.20  # the movers' stocks: short interest of at least 20% of shares outstanding (SEC)
LAST, JOURNAL = "squeeze_last.json", "squeeze_journal.json"
STATE, MOVERS_LOG = "squeeze_state.json", "movers_log.json"
FRAMES = 6  # quarters of SEC frames: facts sit within ~45 days of a quarter end, so 6 cover the 400-day age limit
FIELDS = ("float", "short_float", "short_ratio", "avg_vol", "rel_vol", "pre_vol", "price", "chg")


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


def build(today):
    """FINRA + SEC + Yahoo for one run (today: ISO New York date) -> {"date", "prev", "si_date", "short" ({ticker:
    FINRA short shares}, for the screen), "recent" (the last WINDOW sessions incl. today), "universe" (stocks whose
    short interest is MOVERS_SI-150% of shares outstanding, with their bars: the movers scan), "gated", "missing"
    (gated, no Yahoo bars)}. RuntimeError when Yahoo's calendar, a usable FINRA report, SEC's ticker map or its
    share frames are missing."""
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
    short = shorts.remap(raw, primary)
    gated = []
    for t, si in short.items():
        n = shorts.shares_at(shares, primary[t], today) if t in primary else None
        if n and MOVERS_SI <= si["si"] / n <= squeeze.MAX_SI_PCT:
            gated.append((t, si, n))
    bars = bars_for([t for t, _, _ in gated])
    return {"date": today, "prev": cal[-1], "si_date": settle, "short": {t: si["si"] for t, si in short.items()},
            "gated": len(gated), "missing": len(gated) - len(bars), "recent": cal[-(squeeze.WINDOW - 1):] + [today],
            "universe": [{"t": t, "si_pct": si["si"] / n, "dtc": si.get("dtc") or 0.0, "adv": si.get("adv"),
                          "bars": bars.get(t)} for t, si, n in gated]}


# ---------- message ----------
def _count(x):
    return f"{x / 1e6:.1f}M" if x >= 1e6 else f"{x / 1e3:.0f}K"


FMT = {"float": _count, "avg_vol": _count, "short_float": lambda v: f"{v * 100:.1f}%", "short_ratio": "{:.1f}".format,
       "rel_vol": "{:.1f}".format, "pre_vol": "{:.0%}".format, "price": common.price, "options": lambda v: "יש" if v else "אין"}
LABEL = {"float": ("מניות צפות", f"פחות מ־{code(50)} מיליון"), "short_float": ("שורט מהמניות הצפות", f"יותר מ־{code('20%')}"),
         "short_ratio": ("ימים לכיסוי", f"יותר מ־{code(5)}"),
         "avg_vol": ("מחזור ממוצע ב־3 חודשים", f"יותר מ־{code(500)} אלף מניות"), "price": ("מחיר", f"יותר מ־{code('2$')}"),
         "rel_vol": ("מחזור יחסי", f"יותר מפי {code(2)}"), "options": ("אופציות", "שיהיו")}
UNTESTED = "⚠️ הסינון לא נבדק היסטורית, ואינו סימן לכיוון."
SOURCE = "⚠️ מידע לצורכי מחקר בלבד ואינו ייעוץ השקעות.\nמקור: FINRA (שורט), SEC (מניות), Yahoo ו־Nasdaq (מחירים), CBOE (אופציות)."


def _v(key, v):
    if v is None:
        return "חסר"
    if key in ("float", "avg_vol"):
        return common.count(v)
    if key == "options":
        return FMT[key](v)
    if key == "rel_vol":
        return f"פי {code(FMT[key](v))}"
    return code(FMT[key](v))


def line(k, r):
    """One listed stock in two lines: rank, ticker, price and today's move; then short interest, days to cover and
    volume against a normal day (before the open, the pre-market volume)."""
    vol = (f"מחזור לפני הפתיחה {_v('pre_vol', r['pre_vol'])} מיום רגיל" if "pre_vol" in r
           else f"מחזור {_v('rel_vol', r['rel_vol'])} מהרגיל")
    return (f"מקום {code(k)}: {code(r['t'])} · {_v('price', r['price'])} {common.arrow(r['chg']) if r.get('chg') is not None else ''}"
            .rstrip() + f"\nשורט {_v('short_float', r['short_float'])} מהמניות הצפות · {_v('short_ratio', r['short_ratio'])}"
            f" ימים לכיסוי · {vol}")


def _when(scr, at):
    """'יום ב׳ <code>28.9.2026</code> · מצב ב־<code>19:30</code>' (Israel time)."""
    il = at.astimezone(common.IL)
    day = scr.get("date") or f"{at:%Y-%m-%d}"
    return f"יום {common.weekday(day)} {code(common.il_date(day))} · מצב ב־{code(f'{il:%H:%M}')}"


def _foot(res):
    return [UNTESTED, f"🗓️ נתוני השורט מדוח FINRA של {code(common.il_date(res['si_date'], year=False))}."]


def message(res, scr, at, static=None):
    """The full list (res: build(), scr: screen.screen() or screen.premarket(), at: New York time of the run,
    static: the pre-market's fixed set, unused in the text). The criteria are in /squeeze TICKER, not here."""
    pre = scr.get("kind") == "pre"
    head = [f"{'🌅' if pre else '🚀'} <b>רשימת סקוויז{' לפני הפתיחה' if pre else ''}</b>", _when(scr, at)]
    body = "\n\n".join(line(k, r) for k, r in enumerate(scr["rows"], 1)) or "🤷 אף מניה לא עומדת כרגע בכל התנאים."
    return "\n".join(head + ["", body, ""] + _foot(res))


def change_text(scr, old, at):
    """Who entered the list (with their lines) and who left it since the last list of the same day."""
    was, now = {r["t"] for r in old}, [r["t"] for r in scr["rows"]]
    lines = [f"🔄 <b>שינוי ברשימת הסקוויז</b>", _when(scr, at)]
    new = [line(k, r) for k, r in enumerate(scr["rows"], 1) if r["t"] not in was]
    if new:
        lines += ["", "נכנסו:"] + new
    left = [t for t in sorted(was) if t not in now]
    if left:
        lines += ["", "יצאו: " + ", ".join(map(code, left)) + "."]
    return "\n".join(lines + ["", UNTESTED])


# ---------- journal ----------
def record(j, scr, entry, res):
    """Journal the listed names with `entry` (the next regular open) as their entry session; a ticker journaled
    within the last WINDOW sessions counts once -> the number added."""
    seen = {e["t"] for e in j["entries"] if e["date"] >= res["recent"][0]}
    added = 0
    for k, r in enumerate(scr["rows"], 1):
        if r["t"] in seen:
            continue
        seen.add(r["t"])
        j["entries"].append({"date": entry, "seen": scr["date"] or res["date"], "t": r["t"], "rank": k,
                             "variant": SCREEN_PRE if scr.get("kind") == "pre" else SCREEN, "si_date": res["si_date"],
                             **{f: _round(r.get(f)) for f in FIELDS},
                             "outcome": None})
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


def _summary(entries):
    """Closed outcomes of some journal entries -> Hebrew lines."""
    done = [e["outcome"] for e in entries if e.get("outcome") and e["outcome"].get("trade")]
    waiting = sum(1 for e in entries if not e.get("outcome"))
    if not done:
        return [f"עוד אין מניות שעברו {code(squeeze.WINDOW)} ימי מסחר מאז שהופיעו ברשימה ({code(waiting)} ממתינות)."]
    n = len(done)
    small = f" (מדגם קטן, פחות מ־{code(30)})" if n < 30 else ""
    pct = lambda x: code(f"{x * 100:.0f}%")  # noqa: E731
    med = statistics.median(o["r10"] for o in done)
    return [f"הושלמו {code(n)} מניות{small}, {code(waiting)} ממתינות: {pct(sum(o['hit'] for o in done) / n)} עלו "
            f"{code('50%')} ומעלה, ו־{pct(sum(o['crash'] for o in done) / n)} ירדו {code('33%')} ומעלה.",
            f"חציון אחרי {code(squeeze.WINDOW)} ימי מסחר: {common.change(med, 0)}."]


def stats_text(j):
    """/squeeze stats: the screen's closed outcomes, and the replaced list's apart, next to its own backtest."""
    new = [e for e in j["entries"] if e.get("variant") == SCREEN]
    pre = [e for e in j["entries"] if e.get("variant") == SCREEN_PRE]
    old = [e for e in j["entries"] if e.get("variant") not in (SCREEN, SCREEN_PRE)]
    lines = ["📒 <b>תוצאות רשימת הסקוויז</b>", "🔎 <b>הרשימה הנוכחית</b> (לא נבדקה היסטורית):"] + _summary(new)
    lines += ["", "🌅 <b>הרשימה לפני הפתיחה</b> (לא נבדקה היסטורית):"] + _summary(pre)
    if old:
        lines += ["", f"🗂️ <b>השיטה הקודמת</b> (עד {code(common.il_date('2026-10-08'))}):"] + _summary(old)
        hit, crash = (code(format(BACKTEST[k], ".1%")) for k in ("hit", "crash"))
        lines.append(f"בבדיקה ההיסטורית שלה ({code(BACKTEST['years'])}): {hit} עלו {code('50%')} ומעלה, ו־{crash} ירדו"
                     f" {code('33%')} ומעלה.")
    return "\n".join(lines)


# ---------- /squeeze TICKER ----------
def ticker_text(t, si, m, rank=None, note=""):
    """/squeeze TICKER reply: every criterion with the stock's value (si: slim FINRA row or None; m: screen.metrics)."""
    lines = [f"🔎 <b>בדיקת סקוויז: {code(t)}</b>",
             f"שורט: {common.count(si['si'])} מניות (דוח FINRA של {code(common.il_date(si['date'], year=False))})" if si
             else "שורט: אין נתון ב־FINRA."]
    if m.get("chg") is not None:
        lines.append(f"היום: {common.change(m['chg'])}")
    bad = screen.fails(m)
    for key, _, _ in screen.CRITERIA:
        label, need = LABEL[key]
        val = "חסר" if m.get(key) is None else _v(key, m[key])
        lines.append(f"{'❌' if key in bad else '✅'} {label}: {val} (צריך {need})")
    if bad:
        lines.append(f"❌ לא עומדת ב־{code(len(bad))} מהתנאים.")
    else:
        lines.append("✅ עומדת בכל התנאים" + (f", מקום {code(rank)} ברשימה האחרונה." if rank else "."))
    return "\n".join(lines + ([note] if note else []) + [UNTESTED])


def ticker_report(t):
    """/squeeze TICKER: the stock's numbers now (FINRA, Yahoo, CBOE) against every criterion of the screen."""
    today = sessions.now_ny().date().isoformat()
    cal = calendar(today)
    usable = [r for r in shorts.symbol_rows(t.replace("-", ""))
              if (u := shorts.usable_from(r["date"], cal + [today])) and u <= today]
    si = usable[-1] if usable else None
    q = flt = None
    note = ""
    try:
        get = screen.connect()
        q = screen.quotes(get, [t]).get(t) or None
        flt = screen.float_shares(get, t) or None
    except Exception as e:  # an unofficial source: say so, still show what FINRA and CBOE give
        print(f"yahoo {t}: {type(e).__name__} {e}")
        note = "⚠️ לא התקבלו נתונים מ־Yahoo; נסו שוב בעוד כמה דקות."
    m = screen.metrics(si["si"] if si else None, q, flt, screen.optionable(t))
    rank = next((k for k, r in enumerate(load(LAST, {}).get("rows", []), 1) if r["t"] == t), None)
    return ticker_text(t, si, m, rank, note)


# ---------- run ----------
def run_movers(res, session, today, state, manual, rows=()):
    """The movers part of a run -> (message or '', movers found). Nasdaq down for every stock -> one warning per
    session (always on a manual run)."""
    if not session:
        return (movers.text([], None, today, manual=True) if manual else ""), []
    cands = [{**u, "ref": movers.reference(u["bars"], session, today)} for u in res["universe"]]
    qs = movers.quotes([c["t"] for c in cands], session)
    failed = sum(q is False for q in qs.values())
    if cands and failed == len(cands):
        warn = manual or sessions.first(state, f"nasdaq:{session}", today)
        return (f"⚠️ Nasdaq לא החזיר מחירים ({movers.NAME[session]}). אנסה שוב בריצה הבאה."
                if warn else ""), []
    found = movers.fresh(movers.find(cands, qs, today), state, today, session, manual)
    ranks = {r["t"]: k for k, r in enumerate(rows, 1)}
    return movers.text(found, session, today, ranks, failed, manual), found


def main(argv=None):
    ap = argparse.ArgumentParser(description="Short-squeeze screen and extended-hours movers -> Telegram and data/")
    ap.add_argument("--dry", action="store_true", help="print the messages only, write nothing")
    ap.add_argument("--manual", action="store_true", help="run now, whatever the time (also SQUEEZE_MODE=manual)")
    a = ap.parse_args(argv)
    manual = a.manual or common.env("SQUEEZE_MODE") == "manual"
    now = sessions.now_ny()
    st = sessions.status(now) or sessions.fallback(now)
    today = now.date().isoformat()
    state = load(STATE, {"v": 1})
    if manual:
        session = sessions.session_now(now, st)
    else:
        name = sessions.slot(now, st, sessions.SQUEEZE)
        if not name or not sessions.claim(state, name, today):
            print(f"squeeze {now:%Y-%m-%d %H:%M} New York: no session slot to run")
            return 0
        session = sessions.SLOTS[name][1]
    try:
        res = build(today)
    except Exception as e:  # the owner hears about it once per session (every time on a manual run)
        traceback.print_exc()
        if not a.dry:
            if manual or sessions.first(state, f"build:{session}", today):
                common.send(f"⚠️ רשימת הסקוויז לא רצה: {code(common.esc(str(e)[:120]))}", source=SOURCE)
            save(STATE, state)
        return 0  # reported above; exit 1 would add the workflow alarm on every run of an outage
    j = load(JOURNAL, {"v": 1, "entries": []})
    try:
        filled = followup(j, today)
    except Exception:  # a follow-up problem must not hold back the list; it is retried next run
        traceback.print_exc()
        filled = 0
    scr, list_text, added, static = None, "", 0, None
    kind = "pre" if session == "pre" else "day" if manual or session in ("regular", "post") else None
    if kind:
        try:
            if kind == "pre":  # before the open: the fixed set (once a day) + pre-market price and volume
                static = state.get("static")
                if not static or static.get("date") != today:
                    static = state["static"] = {"date": today, **screen.candidates(res["short"], screen.connect())}
                scr = screen.premarket(static, lambda t: movers.quote(t, "pre"), today)
            else:
                scr = screen.screen(res["short"], screen.connect())
        except Exception as e:
            traceback.print_exc()
            if manual or sessions.first(state, f"screen:{session}", today):
                list_text = f"⚠️ רשימת הסקוויז לא רצה: {code(common.esc(str(e)[:120]))}. אנסה שוב בריצה הבאה."
    if scr:
        date = scr["date"] or today
        full, last = message(res, scr, now, static), load(LAST, {})
        old = last.get("rows") if (last.get("date"), last.get("kind")) == (date, kind) else None  # no kind: replaced method
        if manual or old is None:
            list_text = full
        elif {r["t"] for r in scr["rows"]} != {r["t"] for r in old}:
            list_text = change_text(scr, old, now)
        added = record(j, scr, sessions.next_open(now, st), res)
    rows = scr["rows"] if scr else ()
    with_movers = manual or session != "regular"  # the hourly runs are the screen's; movers stay pre/post-market
    note = (f"📒 התעדכנו התוצאות של {code(filled)} מניות מהרשימה. לכל התוצאות: {code('/squeeze stats')}"
            if filled else "")
    if a.dry:
        movers_text = run_movers(res, session, today, state, manual, rows)[0] if with_movers else ""
        print("\n\n".join(t for t in (list_text, movers_text, note) if t) or "nothing new")
        return 0
    # each part is saved right after it goes out, so a failure later in the run (or the twin's retry) never re-sends it
    if list_text:
        common.send(list_text, signal=bool(scr), source=SOURCE)
    if scr:
        save(LAST, {"date": date, "kind": kind, "at": f"{now:%H:%M}", "text": full,
                    "rows": [{k: _round(v) for k, v in r.items()} for r in scr["rows"]]})
    save(JOURNAL, j)
    save(STATE, state)
    movers_text, found = run_movers(res, session, today, state, manual, rows) if with_movers else ("", [])
    if movers_text:
        common.send(movers_text, signal=True, source=SOURCE)
    if found:
        save(MOVERS_LOG, movers.log(load(MOVERS_LOG, {"v": 1, "entries": []}), found, today, session, manual))
    save(STATE, state)
    if note:
        common.send(note, source=SOURCE)
    common.log("squeeze", session=session, manual=manual, rows=len(rows), screened=scr and scr.get("screened", scr.get("checked")),
               movers=len(found), sent=sum(bool(t) for t in (list_text, movers_text, note)), added=added,
               filled=filled)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
