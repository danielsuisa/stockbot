"""ZBA v2, STEP 0: one decisive probe per data question before section 14 is designed (numbers only, no keys).

    python -m bot.probe_v2          # prints a JSON report and appends it to the Actions job summary

a) Alpaca news: earliest item, and the share of 2016-2021 ZBA top-20 ticker-days (a sample of days) with an item
   between the previous close (16:00 New York) and 09:35.
b) Historical SIP quotes: do they carry bid/ask sizes (bs/as) back to 2016?
c) Survivorship: do Alpaca's assets (active + inactive) cover known 2016-2018 delistings, with daily bars then?
d) SIP trades for 2016 (the tick-level fill minute needs them).
e) SEC SIC codes for the sampled top-20 tickers (2-digit sector proxy), via today's SEC ticker map."""
import datetime as dt
import json
import statistics

from bot import alpaca, common, ticket_backtest as tb

NEWS = "https://data.alpaca.markets/v1beta1/news"
# well-known delistings 2016-2018 (acquired or bankrupt): (symbol then, last trading day, what happened)
DELISTED = [("LNKD", "2016-12-07", "acquired by Microsoft"), ("YHOO", "2017-06-16", "became Altaba"),
            ("WFM", "2017-08-25", "acquired by Amazon"), ("MON", "2018-06-06", "acquired by Bayer"),
            ("TWX", "2018-06-14", "acquired by AT&T"), ("SHLD", "2018-10-15", "Sears, bankrupt"),
            ("SPLS", "2017-09-12", "Staples, taken private"), ("PNRA", "2017-07-17", "Panera, taken private"),
            ("CAB", "2017-09-22", "Cabela's, acquired by Bass Pro")]


def sample_days(sessions):
    """Two sessions a year 2016-2021: the first on/after March 15 and September 15."""
    out = []
    for y in range(2016, 2022):
        for m in (3, 9):
            d = f"{y}-{m:02d}-15"
            out.append(next(s for s in sessions if s >= d))
    return out


def top20(day, sessions, syms):
    k = sessions.index(day)
    prev, look = sessions[k - 1], (dt.date.fromisoformat(day) - dt.timedelta(60)).isoformat()
    daily = {}
    for i in range(0, len(syms), alpaca.CHUNK):
        daily.update(alpaca.daily(syms[i:i + alpaca.CHUNK], look, day))
    pre = tb.prefilter(day, daily, prev)
    names = sorted(pre)
    prior = [alpaca.opening_bars(names, s) for s in sessions[k - tb.Z_DAYS:k]]
    today = alpaca.opening_bars(names, day)
    top = tb.stocks_in_play({t: today.get(t) for t in names}, prior)
    with_bar = sum(1 for t in syms if any(b[0] == prev for b in daily.get(t, [])))
    return prev, [t for t, _, _ in top], len(names), with_bar


def news_items(sym, start, end, limit=1, sort="desc"):
    js = alpaca._get(NEWS, {"symbols": sym, "start": start, "end": end, "limit": limit, "sort": sort})
    return js.get("news") or []


def main():
    out = {}
    # a) earliest news item overall (AAPL is among the most covered symbols)
    first = news_items("AAPL", "2010-01-01T00:00:00Z", "2026-01-01T00:00:00Z", sort="asc")
    out["a_news_earliest_AAPL"] = first[0]["created_at"] if first else None
    cal = alpaca.calendar("2015-10-01", "2021-12-31")
    sessions = sorted(cal)
    syms = tb.asset_universe(alpaca.assets())
    days, hits, n = [], 0, 0
    sectors, mapped = {}, 0
    cik = {t: c for c, t in common.cik_tickers().items()}
    for day in sample_days(sessions):
        prev, top, prefiltered, with_bar = top20(day, sessions, syms)
        got = 0
        for t in top:
            items = news_items(t, alpaca.utc(prev, "16:00"), alpaca.utc(day, "09:35"))
            got += bool(items)
            if t in cik:
                mapped += 1
                sic = (common.submissions(cik[t]) or {}).get("sic")
                sectors[t] = str(sic)[:2] if sic else None
        hits, n = hits + got, n + len(top)
        days.append({"day": day, "top": len(top), "with_news": got, "prefiltered": prefiltered,
                     "universe_with_a_bar_on_prev": with_bar})
    out["a_sample_days"] = days
    out["a_share_with_news"] = round(hits / n, 3) if n else None
    out["e_top20_mapped_to_sec_ticker_map"] = f"{mapped}/{n}"
    out["e_with_sic"] = sum(1 for v in sectors.values() if v)
    # b) quote sizes in 2016
    q = alpaca._get(f"{alpaca.DATA}/AAPL/quotes", {"start": "2016-03-01T15:00:00Z", "end": "2016-03-01T15:00:05Z",
                                                   "feed": "sip", "limit": 5})
    qs = q.get("quotes") or []
    out["b_quotes_2016_sample"] = [{k: x.get(k) for k in ("t", "bp", "bs", "ap", "as")} for x in qs[:3]]
    out["b_sizes_present"] = bool(qs) and all(x.get("bs") is not None and x.get("as") is not None for x in qs)
    # d) trades in 2016
    tr = alpaca._get(f"{alpaca.DATA}/AAPL/trades", {"start": "2016-03-01T15:00:00Z", "end": "2016-03-01T15:00:05Z",
                                                    "feed": "sip", "limit": 5})
    out["d_trades_2016_count_in_5s"] = len(tr.get("trades") or [])
    # c) survivorship: in the asset list? daily bars around the last day?
    assets = {a["symbol"]: a for a in alpaca.assets()}
    out["c_universe_size"] = {"assets": len(assets), "zba_universe": len(syms)}
    rows = []
    for sym, last, what in DELISTED:
        d0 = (dt.date.fromisoformat(last) - dt.timedelta(30)).isoformat()
        bars = alpaca.daily([sym], d0, last).get(sym, [])
        a = assets.get(sym)
        rows.append({"sym": sym, "last": last, "what": what, "in_assets": bool(a),
                     "status": a and a.get("status"), "name_now": a and a.get("name"), "bars_last_30d": len(bars)})
    out["c_delisted"] = rows
    out["requests"] = alpaca.REQUESTS[0]
    text = json.dumps(out, ensure_ascii=False, indent=1)
    print(text)
    common.summary("### ZBA v2 STEP 0 probe\n\n```json\n" + text + "\n```")
    print("median top size:", statistics.median(d["top"] for d in days) if days else None)


if __name__ == "__main__":
    main()
