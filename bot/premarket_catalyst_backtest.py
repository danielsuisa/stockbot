"""Pre-registered premarket-catalyst replay. Locked on 2026-10-15, before this run.

Nothing in this module places or transmits an order. A GO is a paper-trade result, not a merge and not
permission to trade. The listener is unchanged.

In-sample is 2016-01-01 through 2022-12-31. Out-of-sample is 2023-01-01 through the last Alpaca session
before the run's New York date. Only that window is gated. An incomplete download is NO-GO. Prices are
split-adjusted. The rules are in bot/premarket_catalyst.py and docs/backtest/premarket-catalyst-prereg-2026-10-15.md.
"""
import csv
import datetime as dt
import json
import re
import statistics
import time
import traceback
import urllib.error
import urllib.parse
from collections import defaultdict
from pathlib import Path

from bot import alpaca, common
from bot.desk_gate import YEARS, judge, tstat
from bot.premarket_catalyst import (
    CHEAP_SENS, CHEAP_SLIP, DELISTED, DESK_MIN_POSITION, GAPS, blacklist_summary, desk_summary, ex_dates,
    filing_blocks, flat_minute, halt_exit, halt_summary, index_assets, opening_low, pick, premarket,
    price_ok, primary_source, quote_spread, rvol_ok, scale_quote, score, setup, shares_ok, source_name,
    split_sane, universe,
)
from bot.tax import TAX, after_tax_terminal

START, OOS, WARM = "2016-01-01", "2023-01-01", "2015-06-01"
NY = alpaca.NY
CHUNK = 100
DATA = "https://data.alpaca.markets/v2/stocks"
ACTIONS = "https://data.alpaca.markets/v1/corporate-actions"
NEWS = "https://data.alpaca.markets/v1beta1/news"
TICKERS = "https://www.sec.gov/files/company_tickers.json"
BROWSE = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={symbol}&count=1&output=atom"
CACHE = alpaca.CACHE / "t3"
SEC = common.ROOT / ".cache" / "sec-t3"
CIK_RE = re.compile(r"(?:CIK=|<cik>)0*(\d{1,10})", re.I)


def _get(url, query, tries=8):
    """alpaca._get with extra waits. A 400/401/403/404 is the request, not the rate limit."""
    for i in range(tries):
        try:
            return alpaca._get(url, query)
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404) or i == tries - 1:
                raise
            time.sleep(min(90, 5 * (i + 1)))
        except RuntimeError:
            raise
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(min(90, 5 * (i + 1)))


def _bars(symbols, start, end, timeframe):
    out, token = {s: [] for s in symbols}, None
    while True:
        q = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "end": end, "limit": 10000,
             "adjustment": "split", "feed": "sip", "sort": "asc", **({"page_token": token} if token else {})}
        js = _get(f"{DATA}/bars", q)
        for s, items in (js.get("bars") or {}).items():
            out.setdefault(s, []).extend(items or [])
        token = js.get("next_page_token")
        if not token:
            return out


def _raw_bars(symbols, start, end):
    """Raw daily bars. Separate from the split cache, and from the other tests' raw cache."""
    out, token = {s: [] for s in symbols}, None
    while True:
        q = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start, "end": end, "limit": 10000,
             "adjustment": "raw", "feed": "sip", "sort": "asc", **({"page_token": token} if token else {})}
        js = _get(f"{DATA}/bars", q)
        for s, items in (js.get("bars") or {}).items():
            out.setdefault(s, []).extend(items or [])
        token = js.get("next_page_token")
        if not token:
            return out


def _day(bar):
    return (alpaca._ny(bar["t"]).strftime("%Y-%m-%d"), bar["o"], bar["h"], bar["l"], bar["c"], bar.get("v") or 0)


def daily(symbols, start, end, adjustment):
    """{symbol: [(date, o, h, l, c, v)]}. `adjustment` is `split` or `raw`."""
    root = CACHE / adjustment / f"{start}_{end}"
    out, todo = {}, []
    for s in dict.fromkeys(symbols):
        got = alpaca._read(root / f"{s}.json.gz")
        if got is None:
            todo.append(s)
        else:
            out[s] = [tuple(r) for r in got]
    for i in range(0, len(todo), CHUNK):
        part = todo[i:i + CHUNK]
        if adjustment == "split":
            got = _bars(part, start, end, "1Day")
        else:
            got = _raw_bars(part, start, end)
        for s in part:
            rows = [_day(b) for b in got.get(s, [])]
            alpaca._write(root / f"{s}.json.gz", rows)
            out[s] = [tuple(r) for r in rows]
        print(f"daily {adjustment} {min(i + CHUNK, len(todo))}/{len(todo)} requests {alpaca.REQUESTS[0]}",
              flush=True)
    return out


def _page_actions(types, start, end, symbols=None):
    rows, token = [], None
    while True:
        q = {"types": types, "start": start, "end": end, "limit": 1000, "sort": "asc",
             **({"page_token": token} if token else {}),
             **({"symbols": ",".join(symbols)} if symbols else {})}
        try:
            js = _get(ACTIONS, q)
        except urllib.error.HTTPError as e:
            if e.code == 400 and q["limit"] == 1000:
                q["limit"] = 100
                js = _get(ACTIONS, q)
            else:
                raise
        block = js.get("corporate_actions") or {}
        for items in block.values():
            if isinstance(items, list):
                rows.extend(items)
        token = js.get("next_page_token")
        if not token:
            return rows


def reverse_splits(start, end, symbols):
    """Reverse-split ex-dates only. A separate file, so the combined split cache stays unlabeled."""
    path = CACHE / f"reverse_{start}_{end}.json.gz"
    got = alpaca._read(path)
    if got is None:
        got = []
        try:
            got.extend(_page_actions("reverse_split", start, end))
            print(f"reverse splits {len(got)}", flush=True)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            print("reverse-split market-wide query rejected; chunking symbols", flush=True)
            got = []
            for i in range(0, len(symbols), CHUNK):
                got.extend(_page_actions("reverse_split", start, end, symbols[i:i + CHUNK]))
            print(f"reverse splits {len(got)}", flush=True)
        alpaca._write(path, got)
    return ex_dates(got)


def corporate_actions(start, end, symbols):
    """Forward and reverse splits. A failed download is an incomplete file, not a silent skip."""
    path = CACHE / f"splits_{start}_{end}.json.gz"
    got = alpaca._read(path)
    if got is None:
        got = []
        try:
            for types in ("forward_split", "reverse_split"):
                got.extend(_page_actions(types, start, end))
                print(f"actions {types} {len(got)}", flush=True)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            print("actions market-wide query rejected; chunking symbols", flush=True)
            got = []
            for types in ("forward_split", "reverse_split"):
                for i in range(0, len(symbols), CHUNK):
                    got.extend(_page_actions(types, start, end, symbols[i:i + CHUNK]))
                print(f"actions {types} {len(got)}", flush=True)
        alpaca._write(path, got)
    return ex_dates(got)


def _end():
    today = dt.datetime.now(NY).date().isoformat()
    cal = alpaca.calendar(WARM, today)
    days = [d for d in sorted(cal) if d < today]
    return today, days, {d: cal[d] for d in days}


def _candidates(adj, sessions, splits, accept=price_ok):
    """Days whose previous session printed a split-adjusted close in the band, and that are not an ex-date.

    `accept` defaults to the $0.50–$5 test. The $3–$20 book passes its own test and does not change this one.
    """
    index = {d: i for i, d in enumerate(sessions)}
    out, dropped = [], 0
    for sym, rows in adj.items():
        by = {r[0]: r for r in rows}
        banned = splits.get(sym) or set()
        for day, i in index.items():
            if day < START or i < 1:
                continue
            prev = by.get(sessions[i - 1])
            if not prev or not accept(prev[4]):
                continue
            if day in banned:
                dropped += 1
                continue
            out.append((day, sym, i))
    return out, dropped


def _premarket(day, symbols):
    """{symbol: (volume, high, opening low)} from split-adjusted 5-minute bars. Cached per day."""
    path = CACHE / "pm5" / f"{day}.json.gz"
    have = alpaca._read(path) or {}
    todo = [s for s in dict.fromkeys(symbols) if s not in have]
    for i in range(0, len(todo), CHUNK):
        part = todo[i:i + CHUNK]
        got = _bars(part, alpaca.utc(day, "04:00"), alpaca.utc(day, "09:35"), "5Min")
        for s in part:
            bars = []
            for b in got.get(s, []):
                hh = alpaca._ny(b["t"]).strftime("%H:%M")
                if "04:00" <= hh <= "09:30":
                    bars.append((hh, b["o"], b["h"], b["l"], b["c"], b.get("v") or 0))
            vol, high = premarket(bars)
            have[s] = [vol, high, opening_low(bars)]
    if todo:
        alpaca._write(path, have)
    return {s: tuple(have[s]) for s in dict.fromkeys(symbols) if s in have}


def _pass_rvol(cands, sessions):
    need = defaultdict(set)
    for day, sym, i in cands:
        if i < 20:
            continue
        for j in range(i - 20, i + 1):
            need[sessions[j]].add(sym)
    stats = {}
    for n, day in enumerate(sorted(need)):
        stats[day] = _premarket(day, sorted(need[day]))
        if n % 25 == 0:
            print(f"premarket {day} {n}/{len(need)} requests {alpaca.REQUESTS[0]}", flush=True)
    passed = []
    for day, sym, i in cands:
        if i < 20:
            continue
        today = stats.get(day, {}).get(sym)
        if not today:
            continue
        prior = []
        for j in range(i - 20, i):
            prev = stats.get(sessions[j], {}).get(sym)
            prior.append(None if not prev else prev[0])
        if not rvol_ok(today[0], prior):
            continue
        usable = [v for v in prior if v]
        med = statistics.median(usable)
        passed.append({"day": day, "t": sym, "rvol": today[0] / med, "pm_high": today[1], "or_low": today[2],
                       "i": i})
    return passed


def _news(symbols, day, cache_name="news"):
    """Symbols in `symbols` with a primary-source headline in the 24 hours ending 09:30. Fail closed.

    `cache_name` is the cache directory. The default is this book's file. A parallel book uses its own
    directory, because a hit is the headlines already stored for the symbols in that file.
    """
    if not symbols:
        return set(), False
    path = CACHE / cache_name / f"{day}.json.gz"
    got = alpaca._read(path)
    end = dt.datetime.fromisoformat(f"{day}T09:30").replace(tzinfo=NY)
    start = end - dt.timedelta(hours=24)
    if got is None:
        rows, truncated = [], False
        for i in range(0, len(symbols), 30):
            part = symbols[i:i + 30]
            token = None
            pages = 0
            while pages < 40:
                js = _get(NEWS, {"symbols": ",".join(part),
                                 "start": start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "end": end.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "limit": 50, "sort": "asc", **({"page_token": token} if token else {})})
                rows.extend(js.get("news") or [])
                token = js.get("next_page_token")
                pages += 1
                if not token:
                    break
            else:
                truncated = True
        got = {"news": rows, "truncated": truncated}
        alpaca._write(path, got)
    if got.get("truncated"):
        return set(), True
    want, hit = set(symbols), set()
    for item in got.get("news") or []:
        if not primary_source(source_name(item)):
            continue
        stamp = item.get("created_at") or ""
        if not stamp:
            continue
        t = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if not (start <= t < end):
            continue
        hit |= set(item.get("symbols") or []) & want
    return hit, False


def _sec_json(url, name):
    path = SEC / name
    got = alpaca._read(path) if path.exists() else None
    if got is None:
        body = common.fetch(url)
        got = None if body is None else json.loads(body)
        alpaca._write(path, got if got is not None else {"_missing": True})
        return got
    if isinstance(got, dict) and got.get("_missing"):
        return None
    return got


def _ticker_map():
    path = SEC / "company_tickers.json.gz"
    got = alpaca._read(path) if path.exists() else None
    if got is None:
        d = common.get_json(TICKERS) or {}
        got = {}
        for v in d.values():
            ticker = (v.get("ticker") or "").upper()
            if ticker:
                got[ticker] = int(v["cik_str"])
        alpaca._write(path, got)
    return {k: int(v) for k, v in got.items()}


def _cik(symbol, known, cache):
    if symbol in cache:
        return cache[symbol]
    for key in (symbol, symbol.replace(".", "-"), symbol.replace("-", ".")):
        if key in known:
            cache[symbol] = known[key]
            return known[key]
    found = None
    for key in (symbol, symbol.replace(".", "-")):
        try:
            text = common.fetch(BROWSE.format(symbol=urllib.parse.quote(key)))
        except (urllib.error.HTTPError, OSError, TimeoutError):
            text = None
        if text:
            m = CIK_RE.search(text.decode("utf-8", "replace"))
            if m:
                found = int(m.group(1))
                break
    cache[symbol] = found
    return found


def _events(cik):
    """(events, complete). Older submission files that 404 make the history incomplete."""
    path = SEC / "events" / f"{cik}.json.gz"
    if path.exists():
        got = alpaca._read(path)
        return got["events"], got["complete"]
    doc = _sec_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", f"sub_{cik}.json.gz")
    if not doc:
        alpaca._write(path, {"events": [], "complete": False})
        return [], False
    events, complete = [], True
    recent = (doc.get("filings") or {}).get("recent") or {}
    events.extend(_columns(recent))
    for extra in (doc.get("filings") or {}).get("files") or []:
        name = extra.get("name")
        if not name:
            continue
        older = _sec_json(f"https://data.sec.gov/submissions/{name}", f"sub_{name}.gz")
        if not older:
            complete = False
            continue
        events.extend(_columns(older))
    alpaca._write(path, {"events": events, "complete": complete})
    return events, complete


def _columns(cols):
    out = []
    for form, stamp in zip(cols.get("form") or [], cols.get("acceptanceDateTime") or []):
        if not stamp:
            continue
        ny = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(NY)
        out.append([ny.strftime("%Y-%m-%dT%H:%M"), form])
    return out


def _facts(cik):
    path = SEC / "shares" / f"{cik}.json.gz"
    if path.exists():
        return alpaca._read(path)
    doc = _sec_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json", f"cf_{cik}.json.gz")
    facts = []
    dei = ((doc or {}).get("facts") or {}).get("dei") or {}
    rows = (((dei.get("EntityCommonStockSharesOutstanding") or {}).get("units") or {}).get("shares") or [])
    for row in rows:
        if row.get("filed") and row.get("end") and row.get("val") is not None:
            facts.append([row["filed"][:10], row["end"][:10], float(row["val"])])
    alpaca._write(path, facts)
    return facts


def _minutes(symbols, day, flat_at):
    path = CACHE / "m1" / day
    have, todo = {}, []
    for s in dict.fromkeys(symbols):
        got = alpaca._read(path / f"{s}.json.gz")
        if got is None:
            todo.append(s)
        else:
            have[s] = got
    for i in range(0, len(todo), CHUNK):
        part = todo[i:i + CHUNK]
        got = _bars(part, alpaca.utc(day, "09:30"), alpaca.utc(day, _next_minute(flat_at)), "1Min")
        for s in part:
            rows = []
            for b in got.get(s, []):
                hh = alpaca._ny(b["t"]).strftime("%H:%M")
                if "09:30" <= hh <= flat_at:
                    rows.append([hh, b["o"], b["h"], b["l"], b["c"], b.get("v") or 0])
            alpaca._write(path / f"{s}.json.gz", rows)
            have[s] = rows
    return {s: [tuple(r) for r in have.get(s, [])] for s in symbols}


def _quote_end(minute):
    h, m = (int(x) for x in minute.split(":")[:2])
    t = h * 60 + m + 1
    return f"{t // 60:02d}:{t % 60:02d}"


def _spread(sym, day, minute, raw_close, adj_close):
    q = alpaca.quote_at(sym, alpaca.utc(day, _quote_end(minute)))
    if not q:
        return None
    return quote_spread(q[1] - q[0], raw_close, adj_close)


def _nbbo(sym, day, minute, raw_close, adj_close):
    """Scaled `(bid, ask)` at `minute`, or None. Used for sub-$5 entry and exit fills."""
    if not minute:
        return None
    q = alpaca.quote_at(sym, alpaca.utc(day, _quote_end(minute)))
    if not q:
        return None
    return scale_quote(q, raw_close, adj_close)


def _next_minute(hhmm):
    h, m = (int(x) for x in hhmm.split(":")[:2])
    t = h * 60 + m + 1
    return f"{t // 60:02d}:{t % 60:02d}"


def _screen(passed, listed, news_cache="news"):
    """News, filings, and shares outstanding. Returns the rows that may be played, plus counts."""
    counts = {"rvol": len(passed), "news": 0, "news_truncated": 0, "no_cik": 0, "filings_blocked": 0,
              "filings_incomplete": 0, "shares_none": 0, "shares_scale": 0, "shares_stale": 0,
              "shares_large": 0, "ready": 0}
    by_day = defaultdict(list)
    for row in passed:
        by_day[row["day"]].append(row)
    known, cik_cache = _ticker_map(), {}
    events_cache, facts_cache = {}, {}
    ready = []
    for n, day in enumerate(sorted(by_day)):
        rows = by_day[day]
        hits, truncated = _news([r["t"] for r in rows], day, news_cache)
        if truncated:
            counts["news_truncated"] += 1
            rows = []
        else:
            rows = [r for r in rows if r["t"] in hits]
        counts["news"] += len(rows)
        for row in rows:
            cik = _cik(row["t"], known, cik_cache)
            if not cik:
                counts["no_cik"] += 1
                continue
            if cik not in events_cache:
                events_cache[cik] = _events(cik)
            events, complete = events_cache[cik]
            if not complete:
                counts["filings_incomplete"] += 1
                continue
            if filing_blocks(events, day):
                counts["filings_blocked"] += 1
                continue
            if cik not in facts_cache:
                facts_cache[cik] = _facts(cik)
            ok, reason = shares_ok(facts_cache[cik], day)
            if not ok:
                counts[f"shares_{reason}"] += 1
                continue
            row = dict(row)
            row["listed"] = listed.get(row["t"])
            ready.append(row)
        if n % 25 == 0:
            print(f"screen {day} ready {len(ready)} requests {alpaca.REQUESTS[0]}", flush=True)
    counts["ready"] = len(ready)
    return ready, counts


def _play(ready, cal, raw, adj, scorer=score):
    books = []
    by_day = defaultdict(list)
    for row in ready:
        by_day[row["day"]].append(row)
    for n, day in enumerate(sorted(by_day)):
        rows = by_day[day]
        flat = flat_minute(cal[day][1])
        minutes = _minutes([r["t"] for r in rows], day, flat)
        done = []
        for row in rows:
            built = setup(minutes.get(row["t"], []), row["pm_high"], row["or_low"], flat)
            if not built:
                continue
            raw_c = _close(raw, row["t"], day)
            adj_c = _close(adj, row["t"], day)
            built.update(day=day, t=row["t"], rvol=row["rvol"], listed=row.get("listed"),
                         raw_close=raw_c, adj_close=adj_c)
            halt = halt_exit(minutes.get(row["t"], []), built["fill_minute"], built["entry"], built["stop"],
                             built["target"], flat)
            built["halt_affected"] = halt["affected"]
            if halt["affected"]:
                built.update(planned_loss=halt["planned_loss"], realized_loss=halt["realized_loss"],
                             halt_fill=halt["fill"], halt_minute=halt["minute"])
            done.append(built)
        chosen = pick(done)
        if chosen:
            raw_c, adj_c = chosen["raw_close"], chosen["adj_close"]
            entry_q = _nbbo(chosen["t"], day, chosen["fill_minute"], raw_c, adj_c)
            exit_q = _nbbo(chosen["t"], day, chosen["exit_minute"], raw_c, adj_c)
            pack = {slip: scorer(chosen, entry_q, exit_q, slip) for slip in (CHEAP_SLIP, *CHEAP_SENS)}
            scored = pack[CHEAP_SLIP]
            if scored:
                for slip, key in ((0.005, "net_r_0_5"), (0.02, "net_r_2")):
                    alt = pack[slip]
                    scored[key] = None if not alt else alt["net_r"]
                books.append(scored)
        if n % 25 == 0:
            print(f"entries {day} trades {len(books)} requests {alpaca.REQUESTS[0]}", flush=True)
    return books


def _close(daily_map, sym, day):
    for row in daily_map.get(sym) or []:
        if row[0] == day:
            return row[4]
    return None


def _probe(end):
    rows = []
    for sym, last in DELISTED:
        start = (dt.date.fromisoformat(last) - dt.timedelta(days=40)).isoformat()
        bars = daily([sym], start, min(last, end), "raw").get(sym, [])
        rows.append({"symbol": sym, "last": last, "bars": len([r for r in bars if r[0] <= last])})
    return rows


def _fmt(x, spec="{:+.3f}"):
    return "—" if x is None else spec.format(x)


def _sens_table(rows):
    """Out-of-sample mean net R at the gated 1% fallback and at the two sensitivity rates."""
    oos = [r for r in rows if r["day"] >= OOS]
    lines = ["| Fallback per side | Out-of-sample trades | Mean net R | t |",
             "|---|---:|---:|---:|"]
    for label, key in (("1% (the gate)", "net_r"), ("0.5%", "net_r_0_5"), ("2%", "net_r_2")):
        xs = [r[key] for r in oos if r.get(key) is not None]
        m = sum(xs) / len(xs) if xs else None
        lines.append(f"| {label} | {len(xs)} | {_fmt(m)} | {_fmt(tstat(xs), '{:.2f}')} |")
    if not rows:
        lines.append("")
        lines.append("This download had no fills, so the 1%, 0.5%, and 2% books are the same empty book. "
                     "The screen was not run again for the cost amendment.")
    return "\n".join(lines)


def _variant_lines(result, rows):
    """The reverse-split book, the flags that were not tested, and the after-tax line."""
    summary = blacklist_summary(rows, result.get("reverses") or {})
    counts = result.get("counts") or {}
    pct = "not defined on an empty book" if summary["pct"] is None else f"{summary['pct']:.1%}"
    lines = [
        "## Reverse-split blacklist",
        "",
        "This variant sits beside the main book. It does not replace it, and it does not change the gate.",
        "",
        "A trade is excluded when a reverse-split ex-date falls in the 365 calendar days strictly before "
        "the trade date. The trade date itself does not count. A split 366 calendar days earlier does not count.",
        "",
        f"Excluded {summary['excluded']} of {summary['n']} trades ({pct}). "
        f"The book that remains has {len(summary['kept'])} trades.",
    ]
    if summary["n"] == 0:
        lines += ["",
                  f"The {counts.get('rvol', 0):,} relative-volume passers were not joined to Alpaca's "
                  "reverse-split file. That would be a new download, and the recorded trade list has nothing "
                  "to exclude. Both books are the same empty book, and both are NO-GO."]
    lines += ["",
              "## ATM, S-3 full text, and going concern",
              "",
              "Not tested. An at-the-market flag and a going-concern flag need the EDGAR full-text search. "
              "That search was not run. The filing screen never started, because no name had a primary-wire "
              "headline. This run also has no SEC_UA, and a request to sec.gov without it stops the process. "
              "The locked 30-day submissions screen for S-1, 424B, and S-3 / S-3ASR is unchanged. It was not "
              "reached either.",
              "",
              "## After tax",
              "",
              f"Israeli capital-gains tax at {TAX:.0%} on realized gains. Losses offset gains inside the same "
              "calendar year. A losing year pays nothing and does not carry forward. There is no refund. "
              "The tax line does not change the pre-tax gate.",
              ""]
    if summary["n"] == 0:
        lines.append("This book never bought, so realized gains are zero and the tax is zero. Buy-and-hold "
                     "SPY, taxed once at the end, is not a comparison for a book that never bought.")
    else:
        realized = [(row["day"], row.get("pnl") or 0.0) for row in rows]
        terminal = sum(pnl for _day, pnl in realized)
        after, tax, _years = after_tax_terminal(terminal, realized)
        lines.append(f"Realized dollar profit ${terminal:,.2f}. Tax ${tax:,.2f}. After tax ${after:,.2f}. "
                     "The book does not compound, so this is the sum of trade profits. Buy-and-hold SPY is "
                     "not in this download, so the end-tax comparison is not computed here.")
    return lines


def _halt_lines(rows):
    """Halt gaps beside the main book. The gate still exits at the planned stop."""
    summary = halt_summary(rows)
    lines = [
        "## Trading halts",
        "",
        "A likely halt is a gap of at least 5 minutes between 1-minute bars from 09:30 through the flat "
        "minute. The fill is the open of the first bar after that gap. A stop or a target on an earlier "
        "bar means the halt did not affect the trade. A gap that never resumes by the flat minute is not "
        "counted. This exit sits beside the main book and does not change the gate.",
        "",
        f"Trades affected by a halt: {summary['affected']} of {summary['n']}.",
    ]
    if summary["affected"]:
        lines += ["",
                  f"Mean planned stop loss {summary['planned_loss']:.4f} per share. Mean realized loss at the "
                  f"halt open {summary['realized_loss']:.4f} per share. Extra loss versus the stop "
                  f"{summary['extra_loss']:.4f} per share.",
                  f"Dollar planned loss ${summary['planned_dollars']:.2f}. Dollar realized loss "
                  f"${summary['realized_dollars']:.2f}."]
    elif summary["n"] == 0:
        lines += ["",
                  "The recorded book has no fills, so there is no realized loss to set against the planned "
                  "stop. The minute bars were not downloaded again."]
    else:
        lines.append("No open trade contained a gap of 5 minutes, so there is no halt fill to compare "
                     "with the planned stop.")
    return lines


def _desk_lines(rows):
    """The desk size beside the $460 whole-share book."""
    summary = desk_summary(rows)
    pct = "not defined on an empty signal list" if summary["pct"] is None else f"{summary['pct']:.1%}"
    lines = [
        "## Desk size",
        "",
        "Reported beside the main book. The gate still buys the whole shares of $460. This size is the "
        "smaller of 1% of the account at the stop and 2% of the account at the worst case. Worst case per "
        f"share is the larger of the stop distance and the LULD band: 20% from $0.75 through $3, and 10% "
        f"above $3. Below $0.75 the desk rule names no band, so the worst case is the stop. A position "
        f"under ${DESK_MIN_POSITION:.0f} is skipped.",
        "",
        f"Signals that survive: {summary['kept_n']} of {summary['n']} ({pct}).",
        "",
        "On a $460 account the 20% band caps the position at $46, which is under $50, and a wider stop is "
        "smaller still. A signal priced from $0.75 through $3 does not survive. Above $3 the 10% band caps "
        "the position at $92.",
    ]
    if summary["n"] == 0:
        lines += ["",
                  "The recorded book has no entry signals, so this percentage is not defined. The screen "
                  "was not run again. The desk book never bought, so its tax is zero."]
    return lines


def render(result):
    rows = result.get("trades") or []
    oos = [r for r in rows if r["day"] >= OOS]
    ins = [r for r in rows if r["day"] < OOS]
    g = judge(oos, YEARS)
    counts = result.get("counts") or {}
    lines = [
        f"# Premarket catalyst — {result['run']}",
        "",
        "Long only, one name a day, $460. Split-adjusted prices. The headline had to be a primary wire in the "
        "24 hours ending 09:30. S-1, 424B, and S-3 / S-3ASR shelves in the prior 30 days were excluded. "
        "Shares outstanding stand in for float and are not float. This replay does not place or transmit an order.",
        "",
        f"Verdict: **{'GO' if g['go'] and result.get('complete') else 'NO-GO'}**.",
        "",
    ]
    for label, ok in g["checks"]:
        lines.append(f"- {'pass' if ok else 'fail'}: {label}")
    if not result.get("complete"):
        lines.append("- fail: the download finished")
    lines += ["", f"Window asked for: in-sample {START} through 2022-12-31, out-of-sample {OOS} through "
              f"{result.get('end')}. First split-adjusted daily bar: {result.get('first_bar')}.",
              "",
              f"Universe {result.get('universe')} names, {result.get('inactive_names')} inactive in the asset file, "
              f"{result.get('inactive_with_bars')} of those inactive names have a daily bar.",
              "",
              f"AAPL split check: {result.get('aapl')}. Split sessions skipped: {result.get('split_days')}.",
              "",
              f"Price-band days {counts.get('band')}. Relative-volume pass {counts.get('rvol')}. "
              f"Primary-source headlines {counts.get('news')}. News days cut off early {counts.get('news_truncated')}. "
              f"No CIK {counts.get('no_cik')}. Filing blocked {counts.get('filings_blocked')}. "
              f"Filing history incomplete {counts.get('filings_incomplete')}. "
              f"Shares missing {counts.get('shares_none')}, scale error {counts.get('shares_scale')}, "
              f"stale {counts.get('shares_stale')}, at or above 20 million {counts.get('shares_large')}. "
              f"Ready for the entry {counts.get('ready')}.",
              "",
              "| Sample | Trades | Mean net R | t | Random p95 | Win rate | Mean R without commission |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for name, part, show in (("2023–2026", oos, g), ("2016–2022", ins, None)):
        xs = [r["net_r"] for r in part]
        m = sum(xs) / len(xs) if xs else None
        win = sum(r["net_r"] > 0 for r in part) / len(part) if part else None
        bare = [r["net_r_no_commission"] for r in part]
        bm = sum(bare) / len(bare) if bare else None
        lines.append(f"| {name} | {len(part)} | {_fmt(m)} | {_fmt(judge(part)['t'] if part else None, '{:.2f}')} | "
                     f"{_fmt(show['p95'] if show else None)} | {_fmt(win, '{:.1%}')} | {_fmt(bm)} |")
    lines += ["", "| Year | Trades | Mean net R |", "|---|---:|---:|"]
    for y in ("2016", "2017", "2018", "2019", "2020", "2021", "2022", *YEARS):
        part = [r["net_r"] for r in rows if r["day"][:4] == y]
        lines.append(f"| {y} | {len(part)} | {_fmt(sum(part) / len(part) if part else None)} |")
    inactive = [r for r in rows if r.get("listed") == "inactive"]
    lines += ["",
              f"Trades in names Alpaca now marks inactive: {len(inactive)} of {len(rows)}.",
              f"The $0.35 minimum binds below 100 shares: {sum(r['shares'] < 100 for r in rows)} of {len(rows)}.",
              "",
              "## Cost model",
              "",
              "Every trade in this book is a sub-$5 name. When a SIP quote exists at the entry minute and at "
              "the exit minute, the entry fills at the ask and the exit fills at the bid. Both quotes are scaled "
              "by that day's split-adjusted close divided by the raw close. If either quote is missing, the entry "
              "print is raised by 1% and the exit print is lowered by 1%. That is 1% on each side. It is not a 1% "
              "round trip. Commission stays $0.0035 a share with a $0.35 minimum, both sides. The share count is "
              "the whole shares of $460 at the ask, or at the slipped entry when the quote is missing. Net R "
              "divides the dollar result by the printed entry-to-stop risk.",
              "",
              "The gate uses the 1% fallback. The same prints at 0.5% and at 2% per side are a sensitivity check. "
              "They do not flip the verdict. When both quotes exist, the three rates are the same fill.",
              "",
              _sens_table(rows),
              "",
              f"Quotes on both sides: {sum(r.get('cost_mode') == 'quote' for r in rows)}. "
              f"Percent slippage: {sum(r.get('cost_mode') == 'slip' for r in rows)}.",
              "",
              *_variant_lines(result, rows),
              "",
              *_halt_lines(rows),
              "",
              *_desk_lines(rows),
              "",
              "## Survivorship",
              ""]
    probe = result.get("probe") or []
    covered = sum(1 for r in probe if r.get("in_assets") and r.get("bars"))
    lines.append(f"Known 2016–2018 delistings found in the asset file with bars: {covered} of {len(probe)}.")
    for r in probe:
        lines.append(f"- {r['symbol']} ({r['last']}): "
                     f"{'in' if r.get('in_assets') else 'NOT in'} the asset file"
                     f"{', status ' + r['status'] if r.get('status') else ''}, {r.get('bars')} raw bars.")
    lines.append("")
    for gap in GAPS:
        lines.append(f"- {gap}")
    lines += ["", "## Verdict", "", f"- Premarket catalyst: {'GO' if g['go'] and result.get('complete') else 'NO-GO'}.",
              ""]
    return "\n".join(lines)


def _write(result):
    stem = Path(f"docs/backtest/premarket-catalyst-{result['run']}")
    stem.parent.mkdir(parents=True, exist_ok=True)
    text = render(result)
    if not result.get("complete", True):
        text = text.replace("**GO**", "**NO-GO**").replace(": GO.", ": NO-GO.")
    stem.with_suffix(".md").write_text(text, encoding="utf-8")
    fields = ["day", "t", "rvol", "entry", "stop", "exit", "kind_exit", "fill_minute", "shares", "net_r",
              "net_r_short", "net_r_no_commission", "pnl", "cost_mode", "listed", "raw_close", "adj_close"]
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fields, extrasaction="ignore")
        w.writeheader()
        for r in result.get("trades") or []:
            w.writerow(r)
    slim = {k: v for k, v in result.items() if k != "trades"}
    slim["oos"] = judge([r for r in result.get("trades") or [] if r["day"] >= OOS])
    stem.with_suffix(".json").write_text(json.dumps(slim, indent=1, default=str), encoding="utf-8")
    print(f"wrote {stem}.md", flush=True)


def run():
    run_day, sessions, cal = _end()
    end = sessions[-1]
    assets = alpaca.assets()
    symbols, indexed = universe(assets)
    full = index_assets(assets)
    inactive = {s for s in symbols if indexed[s].get("status") == "inactive"}
    print(f"universe {len(symbols)} inactive {len(inactive)}", flush=True)
    splits = corporate_actions(WARM, end, symbols)
    adj = daily(symbols, WARM, end, "split")
    raw = daily([s for s in symbols if adj.get(s)], WARM, end, "raw")
    first = min((rows[0][0] for rows in adj.values() if rows), default=None)
    closes = {r[0]: r[4] for r in adj.get("AAPL", [])}
    aapl_ok, aapl = split_sane(closes, sessions)
    probe = _probe(end)
    for row in probe:
        asset = full.get(row["symbol"])
        row["in_assets"] = bool(asset)
        row["status"] = asset.get("status") if asset else None
    with_bars = {s for s, rows in adj.items() if rows}
    cands, dropped = _candidates(adj, sessions, splits)
    print(f"band {len(cands)} split days skipped {dropped} aapl {aapl_ok}", flush=True)
    if not aapl_ok:
        return _result(run_day, end, first, symbols, inactive, with_bars, aapl, dropped, probe,
                       {"band": len(cands)}, [], False)
    passed = _pass_rvol(cands, sessions)
    print(f"rvol {len(passed)}", flush=True)
    ready, counts = _screen(passed, {s: indexed[s].get("status") for s in symbols})
    counts["band"] = len(cands)
    trades = _play(ready, cal, raw, adj)
    return _result(run_day, end, first, symbols, inactive, with_bars, aapl, dropped, probe, counts, trades, True)


def _result(run_day, end, first, symbols, inactive, with_bars, aapl, dropped, probe, counts, trades, complete):
    return {"run": run_day, "end": end, "first_bar": first, "universe": len(symbols),
            "inactive_names": len(inactive), "inactive_with_bars": len(inactive & with_bars),
            "aapl": aapl, "split_days": dropped, "probe": probe, "counts": counts, "trades": trades,
            "complete": complete, "requests": alpaca.REQUESTS[0]}


def main():
    try:
        result = run()
    except Exception:
        traceback.print_exc()
        run_day = dt.datetime.now(NY).date().isoformat()
        stub = {"run": run_day, "end": None, "first_bar": None, "universe": 0, "inactive_names": 0,
                "inactive_with_bars": 0, "aapl": None, "split_days": 0, "probe": [], "counts": {},
                "trades": [], "complete": False, "requests": alpaca.REQUESTS[0]}
        _write(stub)
        return 1
    _write(result)
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
