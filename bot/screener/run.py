"""Screener runs: CLI, the screener workflow's modes and the listener's in-process commands.

    python -m bot.screener.run --filters "cap_small,sh_float_u50" --sort -change --signal ta_unusualvolume --dry
    python -m bot.screener.run --mode query --query "<text>"     # /screen <codes> (dispatched by the listener)
    python -m bot.screener.run --mode preset --name NAME          # /screen run NAME (and /screen NAME)
    python -m bot.screener.run --mode save --query "NAME CODES"   # /screen save
    python -m bot.screener.run --mode delete --name NAME          # /screen del
    python -m bot.screener.run --mode slot --slot reg1            # scheduled presets (bot/screener/schedule.py)
    python -m bot.screener.run --mode refresh [--full]            # maintenance: every store
    python -m bot.screener.run --mode verify                      # D5: a dry run of every default preset, counts only

Every run logs its request counts per source. Nothing is sent with --dry (or without TG_TOKEN)."""
import argparse
import dataclasses
import datetime as dt
import json
import sys

from bot import alpaca, common
from bot.common import code
from bot.screener import evaluate, format as fmt, parser, presets, signals, stores
from bot.screener.metrics import registry
from bot.screener.stores import bars as BARS
from bot.screener.stores import fundamentals as FUND
from bot.screener.stores import ownership as OWN
from bot.screener.stores import reference as REF
from bot.screener.stores import shortint as SI

NY = alpaca.NY
SLOT_BARS_CALLS = 60  # a scheduled run may spend this much catching up the daily bars (the rest: maintenance)
RUN_ALPACA_MAX, ALPACA_RESERVE = 150, 10  # a run's Alpaca budget; stage 2 is sized to what is left (D-040)
NEWS_PAGES = 40
CHANGED = [False]  # a store changed this run (the workflow saves the cache then)


# ---------- environment ----------
def sources(queries):
    """The stores a list of parsed queries reads (registry sources), price display included."""
    ms = {"price", "change", "volume", "rel_volume", "market_cap", "avg_volume"}
    for q in queries:
        for p in q.filters:
            ms |= set(p.metrics())
        if q.sort:
            ms.add(q.sort[0])
        if q.signal:
            rule = signals.RULES.get(q.signal["code"])
            if rule:
                ms.add(rule[0])
            if q.signal["code"] in ("n_earningsbefore", "n_earningsafter"):
                ms.add("earnings_date")
            if q.signal["code"] in ("it_latestbuys", "it_latestsales"):
                ms.add("insider_own")
        ms |= set(fmt.extra_metrics(q))
    out = {registry.METRICS[m][1] for m in ms if m in registry.METRICS}
    if "news_date" in ms:  # a window predicate (evaluate.news_window), not a registry metric
        out.add("news")
    if out & {"ownership", "shortint"}:
        out |= {"fundamentals"}  # float needs shares outstanding
    if "reference" in out or any(m in ms for m in ("earnings_date",)):
        out.add("reference")
    return out


def clock_now(now=None):
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(NY)
    cal, meta = REF.sessions(now.date().isoformat())
    ck = REF.clock(now, cal)
    return now, ck, [r[0] for r in cal], REF.completed(cal, ck), meta


def news(since_utc, until_utc):
    """Alpaca news items between two UTC ISO times -> ({symbol: [created_at, ...]}, pages read)."""
    out, token, pages = {}, None, 0
    while pages < NEWS_PAGES:
        js = alpaca._get(alpaca.NEWS, {"start": since_utc, "end": until_utc, "limit": 50, "sort": "desc",
                                       **({"page_token": token} if token else {})})
        stores.count("alpaca")
        pages += 1
        for x in js.get("news") or []:
            for s in x.get("symbols") or []:
                out.setdefault(s, []).append(x.get("created_at"))
        token = js.get("next_page_token")
        if not token:
            break
    return out, pages


def alpaca_left():
    """Alpaca calls this run may still spend (stage-2 planner, D-040)."""
    return RUN_ALPACA_MAX - ALPACA_RESERVE - max(stores.COUNTS["alpaca"], alpaca.REQUESTS[0])


def build_env(queries, now=None, update_bars=True, max_bar_calls=SLOT_BARS_CALLS):
    """Load (and cheaply refresh) what `queries` need -> Env."""
    now, ck, cal_all, done, cal_meta = clock_now(now)
    need = sources(queries)
    uni, uni_meta = REF.universe()
    if uni_meta and uni_meta.get("as_of", "")[:10] == dt.datetime.now(dt.timezone.utc).date().isoformat():
        CHANGED[0] = True
    bars, bmeta = BARS.load()
    notes, partial = [], False
    if update_bars and done:
        checked = dict((bmeta or {}).get("checked") or {})
        stats = BARS.update(bars, sorted(uni), done, max_calls=max_bar_calls, checked=checked)
        if stats["fetched"] or stats["empty"]:
            bmeta = BARS.save(bars, {"last_session": done[-1], "checked": checked})
            CHANGED[0] = True
        if stats.get("budget_stop"):
            notes.append("stale: bars (catching up)")
            partial = True
        common.log("screener_bars", **stats)
    as_of = {"bars": done[-1] if done else None}
    env = evaluate.Env(now=now, session=ck["session"], day=ck["day"], sessions_done=done, universe=uni, bars=bars,
                       calendar=cal_all, partial=partial, budget_left=alpaca_left)
    if "long" in need:
        st = stores.load("long")
        env.long = st["data"] if st else {}
        as_of["long"] = st and st["meta"]["as_of"][:10]
    if "profile" in need and ck["session"] in ("PRE", "RTH", "POST"):
        st = stores.load("profile")
        env.profile = st["data"] if st else {}
    if need & {"fundamentals", "ownership", "shortint"}:
        st = stores.load("fundamentals")
        env.fund = st["data"] if st else {}
        as_of["fundamentals"] = st and st["meta"]["as_of"][:10]
        if not st:
            notes.append("stale: fundamentals")
    if "reference" in need:
        st = stores.load("reference")
        env.reference = st["data"] if st else {}
        as_of["reference"] = st and st["meta"]["as_of"][:10]
    if "ownership" in need:
        st = stores.load("ownership")
        env.own = st["data"] if st else {}
    if "shortint" in need and done:
        data, meta = SI.update(done, ck["day"])
        env.short = data
        as_of["shortint"] = data.get("settle") if data else None
    if "news" in need:
        start = alpaca.utc(done[-1] if done else ck["day"], "00:00")
        env.news, _ = news(start, alpaca.utc(ck["day"], BARS.end_time(now)) if ck["trading"] else
                           now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        env.news_count = {s: sum(1 for t in v if t and t[:10] == now.astimezone(dt.timezone.utc).date().isoformat())
                          for s, v in env.news.items()}
    if ck["session"] in ("PRE", "RTH", "POST"):
        end = BARS.end_time(now)
        env.fetch_today = lambda syms: BARS.today(syms, ck["day"], end, ck["open"] or "09:30", ck["close"] or "16:00")
    env.as_of = as_of
    for name, meta in (("bars", bmeta), ("universe", uni_meta)):
        if stores.stale(name, meta):
            notes.append(f"stale: {name}")
    env.notes = notes
    return env


def evaluate_text(text, env, strict=False):
    q = parser.parse(text, strict=strict)
    res = evaluate.run(q, env)
    res.notes += getattr(env, "notes", [])
    return q, res


# ---------- modes ----------
def send(text, dry):
    if dry:
        print(text, end="\n\n")
    else:
        common.send(text, source=fmt.SOURCE)


def query_mode(text, title=None, dry=False, now=None):
    try:
        q0 = parser.parse(text, strict=True)
    except parser.InputError:
        send(f"⚠️ הקודים לא תקינים. מותר: אותיות קטנות, ספרות ו־{code('_ . | , - =')}, עד {code(parser.MAX_CODES)} קודים."
             f" למשל {code('/screen cap_small,sh_float_u50 o=-change')}", dry)
        return 0
    if not q0.filters and not q0.signal:
        send(f"⚠️ לא זיהיתי קודי סינון. למשל {code('/screen cap_small,sh_float_u50 o=-change')}"
             + ("\n" + "\n".join(fmt.ignored_lines(q0.ignored)) if q0.ignored else ""), dry)
        return 0
    env = build_env([q0], now)
    q, res = evaluate_text(text, env, strict=True)
    send(fmt.screen_text(title or "סריקה", q, res, env.now), dry)
    log("query", res)
    return 0


def preset_mode(name, dry=False, now=None):
    p = presets.get(name or "")
    if not p:
        send(f"⚠️ אין סריקה שמורה בשם {code(name or '')}. לרשימה: {code('/screens')}", dry)
        return 0
    return query_mode(presets.query_text(p), title=p.get("title") or p["name"], dry=dry, now=now)


SAVE_ERR = {"usage": f"כתבו שם וקודים, למשל {code('/screen save mylist cap_small,sh_price_o5 sessions=rth every=60m')}",
            "name": "שם לא תקין: אותיות קטנות באנגלית, ספרות, _ או -, עד 30 תווים.",
            "sessions": f"מועדים מותרים: {code('pre,rth,post')}.", "every": f"תדירות: למשל {code('every=60m')} (30 דקות עד שבוע).",
            "codes": "הקודים לא תקינים.", "empty": "לא זיהיתי קודי סינון.", "builtin": "זה שם של סריקת ברירת מחדל; בחרו שם אחר.",
            "full": f"אפשר לשמור עד {code(presets.MAX_USER)} סריקות."}


def save_mode(text, dry=False):
    try:
        p = presets.parse_save(text)
        verb = presets.save(p)
    except presets.PresetError as e:
        send("⚠️ " + SAVE_ERR.get(str(e), str(e)), dry)
        return 0
    when = (f"תרוץ לבד: {', '.join(fmt.SESSION_HE[s] for s in p['sessions'])}, כל {code(p['every'])} דקות"
            if p["enabled"] else "לא תרוץ לבד (בלי sessions=)")
    send(f"💾 הסריקה {code(p['name'])} {'עודכנה' if verb == 'replaced' else 'נשמרה'}.\nקודים: {code(p['filters'])}\n{when}."
         f"\nלהרצה עכשיו: {code('/screen run ' + p['name'])}", dry)
    return 0


def delete_mode(name, dry=False):
    try:
        ok = presets.delete(name or "")
    except presets.PresetError as e:
        send("⚠️ " + SAVE_ERR.get(str(e), str(e)), dry)
        return 0
    send(f"🗑️ הסריקה {code(name)} נמחקה." if ok else f"⚠️ אין סריקה שמורה בשם {code(name)}.", dry)
    return 0


def slot_mode(slot, dry=False, now=None):
    """A scheduled slot: run every preset due in the current session; alert only on new or changed results (the
    first list of a day in full - never from a run whose daily bars stopped at the budget: that preset's first list
    waits for the next slot)."""
    state = presets.load_state()
    now0, ck, _, done, _ = clock_now(now)
    session = {"PRE": "pre", "RTH": "rth", "POST": "post"}.get(ck["session"])
    if not session:
        print(f"screener slot {slot}: market closed ({ck['session']})")
        return 0
    if not dry and not presets.claim(state, slot or session, ck["day"]):
        print(f"screener slot {slot}: already served today")
        return 0
    due = [p for p in presets.all_presets() if presets.due(p, session, now0, state)]
    if not due:
        print(f"screener slot {slot}: no preset due")
        if not dry:
            presets.save_state(state)
        return 0
    queries = [parser.parse(presets.query_text(p)) for p in due]
    env = build_env(queries, now)
    sent = 0
    for p, q in zip(due, queries):
        res = evaluate.run(q, dataclasses.replace(env, today=dict(env.today), stage2=set(env.stage2)))
        res.notes += env.notes
        rec = state.setdefault("presets", {}).get(p["name"]) or {}
        h = fmt.result_hash(res, q.sort[0] if q.sort else (signals.RULES.get(q.signal["code"], (None,))[0] if q.signal else None))
        first_today = rec.get("day") != ck["day"]
        if first_today and env.partial:  # the daily bars stopped at the budget: the next slot sends the first list
            log("slot", res, preset=p["name"], sent=False, deferred="bars catching up")
            continue
        text = ""
        if first_today:
            text = fmt.screen_text(p.get("title") or p["name"], q, res, env.now, p.get("max_results") or 15)
        elif h != rec.get("hash"):
            text = fmt.change_text(p.get("title") or p["name"], res, rec.get("tickers"), env.now,
                                   p.get("max_results") or 15)
        if text:
            send(text, dry)
            sent += 1
        state["presets"][p["name"]] = {"hash": h, "tickers": [r["t"] for r in res.rows], "day": ck["day"],
                                       "at": env.now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                       "total": res.total}
        log("slot", res, preset=p["name"], sent=bool(text))
    if not dry:
        presets.save_state(state)
    return 0


REFRESH_MINUTES = 20  # the step allows 25: later stores wait for the next night
PROFILE_CALLS, OWNERSHIP_FILINGS, REFERENCE_PER_RUN = 500, 800, 500  # SEC < 2000 a run


def refresh_mode(full=False, now=None):
    """Maintenance: every store, within the run's time and request budget (backfills continue on the next run)."""
    import time
    t0 = time.monotonic()
    late = lambda: (time.monotonic() - t0) / 60 > REFRESH_MINUTES  # noqa: E731
    now, ck, cal_all, done, _ = clock_now(now)
    today = dt.date.fromisoformat(ck["day"])
    uni, _ = REF.universe(refresh=True)
    syms = sorted(uni)
    stats = {}
    bars, bmeta = BARS.load()
    checked = dict((bmeta or {}).get("checked") or {})
    stats["bars"] = BARS.update(bars, syms, done, checked=checked)
    BARS.save(bars, {"last_session": done[-1] if done else None, "checked": checked})
    CHANGED[0] = True
    SI.update(done, ck["day"])
    FUND.update(today, force=full)
    ciks = sorted({u["cik"] for u in uni.values() if u.get("cik")})
    if not late():
        long = (stores.load("long") or {}).get("data") or {}
        stats["long"] = BARS.update_long(long, syms + ["SPY"], cal_all, ck["day"])
        stores.save("long", long, "alpaca-sip-1Month+anchors", rows=len(long.get("m", {})))
    if not late():
        eight = set()
        for d in done[-2:]:
            try:
                eight |= REF.eightk_ciks(d)
            except Exception:
                common.trace()
        prio = [c for c in ciks if c in eight][:REFERENCE_PER_RUN]
        _, _, stats["reference"] = REF.reference(ciks, ck["day"], per_run=REFERENCE_PER_RUN - len(prio), priority=prio)
    if not late():
        prof = (stores.load("profile") or {}).get("data") or {}
        stocks = [s for s in syms if not uni[s].get("etf")]
        stats["profile"] = BARS.update_profile(prof, stocks, done, max_calls=PROFILE_CALLS)
        stores.save("profile", prof, "alpaca-sip-1Hour", rows=len(prof.get("days", {})))
    if not late():
        _, _, stats["ownership"] = OWN.update({str(c) for c in ciks}, done, ck["day"], per_run=OWNERSHIP_FILINGS,
                                              late=late)
    stats["minutes"] = round((time.monotonic() - t0) / 60, 1)
    common.log("screener_refresh", **{k: v for k, v in stats.items()}, counts=dict(stores.COUNTS))
    return 0


def verify_mode(now=None):
    """D5: evaluate every default preset (dry) and log counts and the request budget."""
    qs = [(p, parser.parse(presets.query_text(p))) for p in presets.defaults()]
    env = build_env([q for _, q in qs], now)
    for p, q in qs:
        res = evaluate.run(q, dataclasses.replace(env, today=dict(env.today), stage2=set(env.stage2)))
        log("verify", res, preset=p["name"])
        print(fmt.screen_text(p.get("title") or p["name"], q, res, env.now))
    return 0


def log(kind, res, **extra):
    common.log("screener", kind=kind, session=res.session, rows=res.total, stage1=res.stage1, stage2=res.stage2,
               truncated=res.truncated, ignored=len(res.ignored), counts=dict(stores.COUNTS), **extra)


def output(**kv):
    if common.env("GITHUB_OUTPUT"):
        with open(common.env("GITHUB_OUTPUT"), "a", encoding="utf-8") as fh:
            fh.writelines(f"{k}={v}\n" for k, v in kv.items())


def main(argv=None):
    common.install_excepthooks()
    ap = argparse.ArgumentParser(description="finviz-style screener on free data")
    ap.add_argument("--mode", default="cli", choices=["cli", "query", "preset", "save", "delete", "slot", "refresh",
                                                      "verify"])
    ap.add_argument("--filters", default="")
    ap.add_argument("--sort", default="")
    ap.add_argument("--signal", default="")
    ap.add_argument("--query", default=common.env("SCREENER_QUERY"))
    ap.add_argument("--name", default=common.env("SCREENER_NAME"))
    ap.add_argument("--slot", default=common.env("SCREENER_SLOT"))
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--dry", action="store_true", help="print, send nothing, write no state")
    a = ap.parse_args(argv)
    mode = common.env("SCREENER_MODE") or a.mode
    try:
        if mode == "cli":
            text = " ".join(x for x in (a.filters, f"o={a.sort}" if a.sort else "", f"s={a.signal}" if a.signal else "") if x)
            rc = query_mode(text, dry=True)  # the CLI only prints
        elif mode == "query":
            rc = query_mode(a.query or "", dry=a.dry)
        elif mode == "preset":
            rc = preset_mode(a.name, dry=a.dry)
        elif mode == "save":
            rc = save_mode(a.query or "", dry=a.dry)
        elif mode == "delete":
            rc = delete_mode(a.name, dry=a.dry)
        elif mode == "slot":
            rc = slot_mode(a.slot, dry=a.dry)
        elif mode == "refresh":
            rc = refresh_mode(full=a.full)
        else:
            rc = verify_mode()
    finally:
        output(changed="true" if CHANGED[0] else "false")
        common.log("screener_budget", mode=mode, counts=dict(stores.COUNTS), alpaca_total=alpaca.REQUESTS[0])
        common.summary(f"Screener `{mode}`: requests {json.dumps(stores.COUNTS)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
