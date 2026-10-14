"""Pre-registered overnight momentum book (Lou, Polk, Skouras), locked 2026-10-14.

The rules live in docs/backtest/overnight-mom-prereg-2026-10-14.md. This file is the executable copy.
Nothing here places or transmits an order. Thresholds stay as they were before the first download.

    python -m bot.overnight_mom
"""
import datetime as dt
import json
import math
import random
import re
import statistics
import time
import traceback
import urllib.error
from collections import Counter, defaultdict
from pathlib import Path

from bot import alpaca, common

PREREG = "docs/backtest/overnight-mom-prereg-2026-10-14.md"
START, WARMUP, OOS = "2016-01-01", "2015-10-01", "2023-01-01"
PRICE_MIN, ADV_MIN = 10.0, 20_000_000.0
ADV_DAYS, SIGNAL_DAYS, TOP_N = 20, 21, 5
COSTS = (0.0005, 0.0010, 0.0020)
GATE_COST = 0.0010
DRAWS, SEED, PCTL = 1000, 20261014, 0.95
HALT_SESSIONS, NW_LAG = 5, 5
EXCHANGES = ("NYSE", "NASDAQ", "AMEX")
ACCOUNT, COMMISSION, MIN_ORDER = 460.0, 0.0035, 0.35
NEWS_PAGE_CAP = 40
SPY_COVERAGE = 0.99
SPLIT_BAND = 0.25
CHUNK = 80
DATA = "https://data.alpaca.markets/v2/stocks"
ACTIONS = "https://data.alpaca.markets/v1/corporate-actions"
NEWS = "https://data.alpaca.markets/v1beta1/news"
SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
SKIP_NAME = re.compile(
    r"\bETF\b|\bETN\b|\bETFs\b|\bFund\b|\bFunds\b|iShares|ProShares|Direxion|\bSPDR\b|"
    r"Invesco QQQ|VanEck|WisdomTree|Global X|GraniteShares|\bLeveraged\b|"
    r"\b\d(\.\d)?[xX]\b|Trust, Series|\bWarrant\b|\bWarrants\b|\bPreferred\b|"
    r"\bRights\b|\bUnits?\b|\bNotes\b|\bDebentures?\b|Exchange[- ]Traded",
    re.I)
EARNINGS_RE = re.compile(r"(?i)\bearnings\b|\bEPS\b|financial results|quarterly results|reports results")
DELISTED = (("LNKD", "2016-12-07"), ("YHOO", "2017-06-16"), ("WFM", "2017-08-25"), ("MON", "2018-06-06"),
            ("TWX", "2018-06-14"), ("SHLD", "2018-10-15"), ("SPLS", "2017-09-12"), ("PNRA", "2017-07-17"),
            ("CAB", "2017-09-22"))
TERMINAL = ("merger", "worthless", "delist")


def is_common(asset):
    """One Alpaca asset row -> whether the symbol may enter the universe (listing status is not a filter)."""
    name, sym, exch = asset.get("name") or "", asset.get("symbol") or "", (asset.get("exchange") or "").upper()
    return exch in EXCHANGES and bool(SYMBOL_RE.fullmatch(sym)) and bool(name) and not SKIP_NAME.search(name)


def index_assets(assets):
    """Asset rows -> {symbol: row}, one row each. Inactive wins when both statuses are present."""
    out = {}
    for a in assets:
        sym = a.get("symbol")
        if not sym:
            continue
        if sym not in out:
            out[sym] = dict(a)
        elif a.get("status") == "inactive":
            out[sym]["status"] = "inactive"
    return out


def last_completed(today, sessions):
    """The last session strictly before New York `today` (ISO). That session is already closed."""
    done = [s for s in sessions if s < today]
    if not done:
        raise RuntimeError("no completed session")
    return done[-1]


def entry_dates(sessions):
    """Sessions that can be an entry: on/after START, with a later session still inside the file."""
    return [d for i, d in enumerate(sessions) if d >= START and i + 1 < len(sessions)]


def portfolio_return(rets, cost):
    """Equal-weight mean of gross stock returns, minus one round trip. Cash (no names) costs nothing."""
    if not rets:
        return 0.0
    return sum(rets) / len(rets) - cost


def name_return(pick, variant):
    """One held name's gross return. Variant 2 uses the opening VWAP, except a terminal exit, which has no print."""
    if variant == "open":
        return pick["ret"]
    if pick["exit_kind"] in TERMINAL:
        return pick["ret"]
    return pick.get("vwap_ret")


def book_return(picks, variant, cost):
    """The night's portfolio return, or None when every name lacked the variant's exit (the night is dropped)."""
    if not picks:
        return 0.0
    xs = [r for r in (name_return(p, variant) for p in picks) if r is not None]
    if not xs:
        return None
    return portfolio_return(xs, cost)


def t_stat(xs):
    """One-sample t of the nightly returns. A zero standard deviation has no t (the check then fails)."""
    n = len(xs)
    if n < 2:
        return None
    m = statistics.fmean(xs)
    sd = statistics.stdev(xs)
    if sd == 0:
        return None
    return m / (sd / math.sqrt(n))


def newey_west_t(xs, lag=NW_LAG):
    """t with a Bartlett kernel. Reported only; the gate uses t_stat."""
    n = len(xs)
    if n < lag + 2:
        return None
    m = statistics.fmean(xs)
    e = [x - m for x in xs]
    var = sum(z * z for z in e) / n
    for ell in range(1, lag + 1):
        gamma = sum(e[i] * e[i - ell] for i in range(ell, n)) / n
        var += 2 * (1 - ell / (lag + 1)) * gamma
    if var <= 0:
        return None
    return m / math.sqrt(var / n)


def percentile(xs, p):
    """Nearest-rank percentile: sorted[ceil(p * n) - 1]."""
    if not xs:
        return None
    s = sorted(xs)
    k = min(max(math.ceil(p * len(s)) - 1, 0), len(s) - 1)
    return s[k]


def select(rows, n=TOP_N):
    """Highest signal, then higher dollar volume, then symbol. At most `n` names."""
    ordered = sorted(rows, key=lambda r: (-r["signal"], -r["adv"], r["symbol"]))
    return ordered[:n]


def random_means(nights, cost, draws=DRAWS, seed=SEED):
    """Mean nightly net return of `draws` random books from each night's eligible list."""
    out = []
    for i in range(draws):
        xs = []
        for night in nights:
            elig = night["eligible_rets"]
            if not elig:
                xs.append(0.0)
                continue
            rng = random.Random(f"{seed}|{i}|{night['date']}")
            pick = elig if len(elig) <= TOP_N else rng.sample(elig, TOP_N)
            xs.append(portfolio_return(pick, cost))
        out.append(statistics.fmean(xs) if xs else 0.0)
    return out


def series(nights, variant, cost):
    """[(date, net)] for nights the variant could price. A None book return drops the night."""
    out = []
    for night in nights:
        r = book_return(night["picks"], variant, cost)
        if r is not None:
            out.append((night["date"], r))
    return out


def compound(rets):
    """Growth of $1. Stops at zero if a night loses the whole book."""
    g = 1.0
    for r in rets:
        g *= 1 + r
        if g <= 0:
            return 0.0
    return g


def max_drawdown(rets):
    """Peak-to-trough of compounded equity. A negative number."""
    g, peak, worst = 1.0, 1.0, 0.0
    for r in rets:
        g *= 1 + r
        if g <= 0:
            return -1.0
        peak = max(peak, g)
        worst = min(worst, g / peak - 1)
    return worst


def cagr(rets, nights_per_year=252):
    g = compound(rets)
    years = len(rets) / nights_per_year
    if years <= 0 or g <= 0:
        return None
    return g ** (1 / years) - 1


def year_means(pairs):
    """{year: {n, mean}} from [(date, net)]."""
    buckets = defaultdict(list)
    for day, r in pairs:
        buckets[day[:4]].append(r)
    return {y: {"n": len(v), "mean": statistics.fmean(v)} for y, v in sorted(buckets.items())}


def summarize(pairs):
    """The descriptive block for one sample. t is None when it is not defined."""
    xs = [r for _, r in pairs]
    if not xs:
        return {"n": 0, "mean": None, "t": None, "nw_t": None, "hit": None, "ann": None, "sharpe": None,
                "total": None, "cagr": None, "maxdd": None, "years": {}}
    m = statistics.fmean(xs)
    sd = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return {"n": len(xs), "mean": m, "t": t_stat(xs), "nw_t": newey_west_t(xs),
            "hit": sum(r > 0 for r in xs) / len(xs), "ann": m * 252,
            "sharpe": (m / sd * math.sqrt(252)) if sd else None,
            "total": compound(xs) - 1, "cagr": cagr(xs), "maxdd": max_drawdown(xs), "years": year_means(pairs)}


def gate(oos, draw_means, spy, strategy_on_spy, all_years, oos_years):
    """The locked checks. SPY is compared on the nights both books have a return. Returns [(label, ok, detail)]."""
    p95 = percentile(draw_means, PCTL)
    mean, t = oos.get("mean"), oos.get("t")
    years_pos = [y for y, v in all_years.items() if v["mean"] is not None and v["mean"] > 0]
    year_frac = (len(years_pos) / len(all_years)) if all_years else 0.0
    oos_ok = bool(oos_years) and all(v["n"] > 0 and v["mean"] > 0 for v in oos_years.values())
    return [
        ("OOS mean net > 0 and t > 2", bool(mean is not None and mean > 0 and t is not None and t > 2),
         {"mean": mean, "t": t}),
        ("OOS mean above the random 95th percentile", bool(mean is not None and p95 is not None and mean > p95),
         {"mean": mean, "p95": p95}),
        ("OOS mean above SPY close-to-open on the same nights",
         bool(strategy_on_spy is not None and spy is not None and strategy_on_spy > spy),
         {"strategy": strategy_on_spy, "spy": spy}),
        ("every OOS year mean net > 0", oos_ok, oos_years),
        ("at least 70% of years positive", year_frac >= 0.70, {"fraction": year_frac, "positive": len(years_pos),
                                                               "years": len(all_years)}),
    ]


def _in(day, start, end):
    return start <= day <= end


def exit_return(symbol, sessions, i, adj, raw_close, mergers, worthless):
    """Gross price return from the close of sessions[i]. (return, kind, exit date) or (None, 'censored', None)."""
    entry = adj[sessions[i]][1]
    if entry <= 0:
        return None, "bad", None
    last = min(i + HALT_SESSIONS, len(sessions) - 1)
    window = sessions[i + 1:last + 1]
    for step, nxt in enumerate(window, start=1):
        bar = adj.get(nxt)
        if bar and bar[0] > 0:
            return bar[0] / entry - 1, ("open" if step == 1 else "halt"), nxt
    if not window:
        return None, "censored", None
    start, end = window[0], window[-1]
    cash = _cash(mergers.get(symbol, ()), start, end)
    if cash is not None and raw_close > 0:
        return cash / raw_close - 1, "merger", end
    if any(_in(d, start, end) for d in worthless.get(symbol, ())):
        return -1.0, "worthless", end
    if i + HALT_SESSIONS >= len(sessions):
        return None, "censored", None
    return -1.0, "delist", end


def _cash(events, start, end):
    """The cash per share of a merger whose effective date falls inside [start, end], or None."""
    hit = [rate for day, rate in events if rate > 0 and _in(day, start, end)]
    return hit[0] if hit else None


def build_rows(symbol, sessions, raw, adj, mergers, worthless):
    """One symbol's eligible entry days. `raw` and `adj` map a session to (open, close, volume). Volume is unused on adj."""
    n = len(sessions)
    log_r = [None] * n
    for i in range(1, n):
        a, b = adj.get(sessions[i]), adj.get(sessions[i - 1])
        if a and b and a[0] > 0 and b[1] > 0:
            log_r[i] = math.log(a[0] / b[1])
    rows = []
    for i, day in enumerate(sessions):
        if day < START or i + 1 >= n or i < SIGNAL_DAYS - 1:
            continue
        window = log_r[i - (SIGNAL_DAYS - 1):i + 1]
        if len(window) != SIGNAL_DAYS or any(x is None for x in window):
            continue
        vols, raw_close, ok = [], None, True
        for j in range(i - (ADV_DAYS - 1), i + 1):
            bar = raw.get(sessions[j])
            if not bar or bar[1] <= 0 or bar[2] is None or bar[2] < 0:
                ok = False
                break
            vols.append(bar[1] * bar[2])
            if j == i:
                raw_close = bar[1]
        if not ok or raw_close is None or not raw_close > PRICE_MIN:
            continue
        adv = sum(vols) / ADV_DAYS
        if not adv > ADV_MIN:
            continue
        ret, kind, exit_day = exit_return(symbol, sessions, i, adj, raw_close, mergers, worthless)
        if ret is None:
            continue
        rows.append({"date": day, "symbol": symbol, "signal": sum(window), "adv": adv, "ret": ret,
                     "raw_close": raw_close, "exit_kind": kind, "exit_date": exit_day, "vwap_ret": None,
                     "adj_close": adj[day][1]})
    return rows


def apply_rename(raw, adj, changes):
    """Drop or truncate the old symbol of a rename so a stitched history is not held twice. Returns the skip set
    and mutates the old symbol's bars when they must end at the change."""
    skip = set()
    for old, new, day in changes:
        if not day:
            continue
        new_bars = raw.get(new) or {}
        if any(d < day for d in new_bars):
            skip.add(old)
            continue
        if old in raw:
            raw[old] = {d: b for d, b in raw[old].items() if d < day}
        if old in adj:
            adj[old] = {d: b for d, b in adj[old].items() if d < day}
    return skip


def nights_from(sessions, rows, flags, spy, drop_earnings):
    """One book: every entry night, cash when nobody qualifies. `flags` is {entry date: {symbols}}."""
    by = defaultdict(list)
    for row in rows:
        if drop_earnings and row["symbol"] in flags.get(row["date"], ()):
            continue
        by[row["date"]].append(row)
    out = []
    for day in entry_dates(sessions):
        cands = by.get(day, [])
        out.append({"date": day, "oos": day >= OOS, "picks": select(cands),
                    "eligible_rets": [r["ret"] for r in cands], "spy": spy.get(day)})
    return out


def split_sane(adj, sessions):
    """AAPL's 4-for-1 split on 2020-08-31 must not survive as a price jump. -> (ok, detail)."""
    before = [d for d in sessions if d <= "2020-08-28" and d in adj]
    after = [d for d in sessions if d >= "2020-08-31" and d in adj]
    if not before or not after:
        return False, "missing bars around 2020-08-31"
    c0, c1 = adj[before[-1]][1], adj[after[0]][1]
    if c0 <= 0 or c1 <= 0:
        return False, "non-positive close"
    jump = c1 / c0 - 1
    return abs(jump) < SPLIT_BAND, {"from": before[-1], "to": after[0], "jump": jump}


def small_account(nights, equity=ACCOUNT):
    """$460, whole shares, five equal slots, commission and the gate-cost spread. Variant 1 picks."""
    eq, peak, worst = equity, equity, 0.0
    held, valued = [], []
    for night in nights:
        slot = eq / TOP_N
        pnl, n_held = 0.0, 0
        for p in night["picks"]:
            px = p["raw_close"]
            if px <= 0:
                continue
            shares = int(slot // px)
            if shares < 1:
                continue
            notional = shares * px
            comm = max(COMMISSION * shares, MIN_ORDER) * 2
            pnl += notional * p["ret"] - comm - GATE_COST * notional
            n_held += 1
        eq = max(0.0, eq + pnl)
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1 if peak else -1)
        if night["oos"]:
            valued.append(eq)
        held.append(n_held)
    return {"start": equity, "end": eq, "total": eq / equity - 1 if equity else None, "maxdd": worst,
            "avg_names": statistics.fmean(held) if held else 0.0,
            "oos_end": valued[-1] if valued else None}


def _bps(x):
    return "—" if x is None else f"{x * 1e4:.2f}"


def _num(x, spec="{:.4f}"):
    return "—" if x is None else spec.format(x)


def _pct(x):
    return "—" if x is None else f"{100 * x:.2f}%"


def render(result):
    """The markdown report. Numbers only; no price history."""
    g = result["go"]
    oos = result["primary_open"]["oos"]
    checks = result["checks"]
    lines = [
        f"# Overnight momentum, SIP close-to-open — {result['today']}",
        "",
        f"**{'GO' if g else 'NO-GO'}.** Primary book: {result['primary']}. Variant 1, exit at the next open, "
        f"0.10% round trip. Window {result['start']} → {result['end']}. Complete: {result['complete']}.",
        "",
        "No order was sent. The rules were locked in `docs/backtest/overnight-mom-prereg-2026-10-14.md` before this run.",
        "",
        "## Verdict",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for label, ok, _ in checks:
        lines.append(f"| {label} | {'pass' if ok else 'fail'} |")
    lines += ["", "## Out of sample, variant 1, 0.10%", "",
              "| Nights | Mean (bps) | t | Newey-West t | Beat random p95 | SPY (bps) | Strategy on SPY nights (bps) |",
              "|---:|---:|---:|---:|---|---:|---:|",
              f"| {oos['n']} | {_bps(oos['mean'])} | {_num(oos['t'], '{:.2f}')} | {_num(oos['nw_t'], '{:.2f}')} | "
              f"{_bps(result['p95'])} | {_bps(result['spy'])} | {_bps(result['strategy_on_spy'])} |",
              "",
              "| Hit rate | Simple annualized | Sharpe | Total | CAGR | Max drawdown |",
              "|---:|---:|---:|---:|---:|---:|",
              f"| {_pct(oos['hit'])} | {_pct(oos['ann'])} | {_num(oos['sharpe'], '{:.2f}')} | {_pct(oos['total'])} | "
              f"{_pct(oos['cagr'])} | {_pct(oos['maxdd'])} |",
              "",
              "## By year (variant 1, 0.10%, primary book)",
              "",
              "| Year | Sample | Nights | Mean (bps) |",
              "|---|---|---:|---:|"]
    for sample, block in (("in-sample", result["primary_open"]["is"]), ("out-of-sample", oos)):
        for year, v in block["years"].items():
            lines.append(f"| {year} | {sample} | {v['n']} | {_bps(v['mean'])} |")
    lines += ["", "## Cost sensitivity, out of sample, variant 1", "",
              "| Round trip | Mean (bps) | t | Above random p95 | Above SPY | OOS years all positive |",
              "|---:|---:|---:|---|---|---|"]
    for cost, row in result["costs"].items():
        lines.append(f"| {100 * float(cost):.2f}% | {_bps(row['mean'])} | {_num(row['t'], '{:.2f}')} | "
                     f"{'yes' if row['beats_random'] else 'no'} | {'yes' if row['beats_spy'] else 'no'} | "
                     f"{'yes' if row['years_ok'] else 'no'} |")
    v2 = result["primary_vwap"]["oos"]
    lines += ["", "## Variant 2, exit at the 09:30–09:45 VWAP (not the gate)", "",
              "The random-draw test is not run here: it would need an opening VWAP for every eligible name, and only "
              "the names the book actually held were downloaded.",
              "",
              "| Sample | Nights | Dropped nights | Mean (bps) | t | Above SPY |",
              "|---|---:|---:|---:|---:|---|"]
    for label, block in (("in-sample", result["primary_vwap"]["is"]), ("out-of-sample", v2)):
        lines.append(f"| {label} | {block['n']} | {block['dropped']} | {_bps(block['mean'])} | "
                     f"{_num(block['t'], '{:.2f}')} | {'yes' if block.get('beats_spy') else 'no'} |")
    acct = result["account"]
    lines += ["", "## $460 account (reported, not gated)", "",
              f"Whole shares, five slots, $0.0035 a share with a $0.35 minimum each side, plus 0.10% round trip. "
              f"Ending equity ${acct['end']:.2f} from ${acct['start']:.0f} ({_pct(acct['total'])}). "
              f"Max drawdown {_pct(acct['maxdd'])}. Average names held {acct['avg_names']:.2f}.",
              "",
              "## Other book",
              "",
              f"Primary book is `{result['primary']}` because the earliest AAPL news item is {result['news_first']}. "
              f"The other book, out of sample, variant 1, 0.10%: mean {_bps(result['other_oos']['mean'])} bps, "
              f"t {_num(result['other_oos']['t'], '{:.2f}')}, nights {result['other_oos']['n']}.",
              "",
              "## Survivorship",
              ""]
    lines += [f"- {line}" for line in result["survivorship"]]
    lines += ["", "## Data", "",
              f"- Requests {result['requests']} · runtime {result['runtime']:.0f}s · git {result['git']} · "
              f"prereg sha256 {result['prereg_sha']}",
              f"- Asset-file exchanges: {result['exchanges']}. "
              f"Universe symbols {result['universe']} ({result['inactive']} inactive in the asset file). "
              f"Eligible at least once: {result['ever_eligible']} ({result['ever_inactive']} of them inactive).",
              f"- Exit kinds: {result['exit_kinds']}",
              f"- AAPL split check: {result['aapl']}",
              f"- Earnings flags: {result['earnings_nights']} nights, {result['earnings_names']} name-nights. "
              f"Truncated news days: {result['news_truncated']}.",
              "",
              "In-sample is not part of the verdict. Variant 2 and the 0.05% / 0.20% columns are not part of it either.",
              ""]
    return "\n".join(lines)


# ---------- download ----------
def _get(url, query, tries=8):
    """alpaca._get with extra waits, so a shared 200-request minute does not abort the file.
    A 400/401/403/404 is the request, not the rate limit: it is not retried."""
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


def _bars(symbols, start, end, timeframe, adjustment):
    out, token = {s: [] for s in symbols}, None
    while True:
        q = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "end": end, "limit": 10000,
             "adjustment": adjustment, "feed": "sip", "sort": "asc", **({"page_token": token} if token else {})}
        js = _get(f"{DATA}/bars", q)
        for s, items in (js.get("bars") or {}).items():
            out.setdefault(s, []).extend(items or [])
        token = js.get("next_page_token")
        if not token:
            return out


def _day(bar):
    return (alpaca._ny(bar["t"]).strftime("%Y-%m-%d"), bar["o"], bar["c"], bar.get("v") or 0)


def daily(symbols, start, end, adjustment):
    """{symbol: {date: (open, close, volume)}}. Cached per symbol. Empty is an answer."""
    root = alpaca.CACHE / "overnight" / adjustment / f"{start}_{end}"
    out, todo = {}, []
    for s in symbols:
        got = alpaca._read(root / f"{s}.json.gz")
        if got is None:
            todo.append(s)
        else:
            out[s] = {r[0]: (r[1], r[2], r[3]) for r in got}
    for n, i in enumerate(range(0, len(todo), CHUNK)):
        part = todo[i:i + CHUNK]
        got = _bars(part, start, end, "1Day", adjustment)
        for s in part:
            rows = [_day(b) for b in got.get(s, [])]
            alpaca._write(root / f"{s}.json.gz", rows)
            out[s] = {r[0]: (r[1], r[2], r[3]) for r in rows}
        print(f"daily {adjustment} {min(i + CHUNK, len(todo))}/{len(todo)}", flush=True)
    return out


def _page_actions(types, start, end, symbols=None):
    """Every corporate-action row of `types` in [start, end], following next_page_token.
    `symbols` is omitted when the endpoint can answer for the whole market."""
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


def corporate_actions(start, end, symbols=()):
    """Mergers, worthless removals, and renames. Cached for the window.
    A 400 on the market-wide query is retried in symbol chunks (the endpoint then requires `symbols`)."""
    path = alpaca.CACHE / "overnight" / f"actions_{start}_{end}.json.gz"
    got = alpaca._read(path)
    if got is None:
        got = []
        try:
            for types in ("cash_merger,stock_and_cash_merger", "worthless_removal", "name_change"):
                got.extend(_page_actions(types, start, end))
                print(f"actions {types} {len(got)}", flush=True)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            print("actions market-wide query rejected; chunking symbols", flush=True)
            got = []
            for types in ("cash_merger,stock_and_cash_merger", "worthless_removal", "name_change"):
                for i in range(0, len(symbols), CHUNK):
                    got.extend(_page_actions(types, start, end, symbols[i:i + CHUNK]))
                print(f"actions {types} {len(got)}", flush=True)
        alpaca._write(path, got)
    mergers, worthless, changes = defaultdict(list), defaultdict(list), []
    for row in got:
        if "old_symbol" in row and "new_symbol" in row:
            changes.append((row["old_symbol"], row["new_symbol"], row.get("process_date") or ""))
            continue
        sym = row.get("symbol") or row.get("acquiree_symbol")
        if not sym:
            continue
        if "rate" in row and "acquiree_symbol" in row:
            mergers[sym].append((row.get("effective_date") or row.get("process_date") or "", float(row["rate"])))
        elif "cash_rate" in row and "acquiree_symbol" in row:
            mergers[sym].append((row.get("effective_date") or "", float(row["cash_rate"])))
        elif row.get("process_date") and "acquiree_symbol" not in row and "new_symbol" not in row:
            worthless[sym].append(row["process_date"])
    return mergers, worthless, changes


def news_first(end):
    """The date of AAPL's earliest Alpaca news item, or None when the feed has none through `end`."""
    js = _get(NEWS, {"symbols": "AAPL", "start": "2010-01-01T00:00:00Z", "end": alpaca.utc(end, "20:00"),
                     "limit": 1, "sort": "asc"})
    items = js.get("news") or []
    if not items:
        return None
    return (items[0].get("created_at") or "")[:10] or None


def news_flags(entry, exit_day):
    """Symbols with an earnings-like headline in (16:00 entry, 09:30 exit]. Too many pages -> (set(), True)."""
    path = alpaca.CACHE / "overnight" / "news" / f"{entry}.json.gz"
    got = alpaca._read(path)
    if got is None:
        got, token, truncated = [], None, False
        for _ in range(NEWS_PAGE_CAP):
            js = _get(NEWS, {"start": alpaca.utc(entry, "16:00"), "end": alpaca.utc(exit_day, "09:30"),
                             "limit": 50, "sort": "asc", **({"page_token": token} if token else {})})
            got.extend(js.get("news") or [])
            token = js.get("next_page_token")
            if not token:
                break
        else:
            truncated = True
        alpaca._write(path, {"news": got, "truncated": truncated})
        got = {"news": got, "truncated": truncated}
    symbols = set()
    if not got.get("truncated"):
        for item in got.get("news") or []:
            if EARNINGS_RE.search(item.get("headline") or ""):
                symbols.update(item.get("symbols") or [])
    return symbols, bool(got.get("truncated"))


def opening_vwap(symbols, day):
    """{symbol: 09:30–09:45 VWAP} for the names that have a 15-minute bar. Split-adjusted, same basis as the close."""
    path = alpaca.CACHE / "overnight" / "vwap" / f"{day}.json.gz"
    have = alpaca._read(path) or {}
    todo = [s for s in symbols if s not in have]
    if todo:
        at = alpaca.utc(day, "09:30")
        got = _bars(todo, at, at, "15Min", "split")
        for s in todo:
            bars = got.get(s) or []
            vw = None
            for b in bars:
                if alpaca._ny(b["t"]).strftime("%H:%M") == "09:30" and b.get("vw"):
                    vw = b["vw"]
                    break
            have[s] = vw
        alpaca._write(path, have)
    return {s: have.get(s) for s in symbols}


def fill_vwap(nights):
    """Set vwap_ret on open/halt picks from the exit session's opening VWAP. One request per exit day."""
    need = defaultdict(set)
    for night in nights:
        for p in night["picks"]:
            if p["exit_kind"] in ("open", "halt") and p.get("exit_date"):
                need[p["exit_date"]].add(p["symbol"])
    vwap = {}
    for i, (day, syms) in enumerate(sorted(need.items())):
        vwap[day] = opening_vwap(sorted(syms), day)
        if i % 50 == 0:
            print(f"vwap days {i}/{len(need)}", flush=True)
    for night in nights:
        for p in night["picks"]:
            px = vwap.get(p.get("exit_date"), {}).get(p["symbol"])
            if px and p["adj_close"] > 0 and p["exit_kind"] in ("open", "halt"):
                p["vwap_ret"] = px / p["adj_close"] - 1


def probe_delisted(assets, end):
    """The nine known 2016–2018 delistings: in the asset file, and how many raw bars in the prior 30 days."""
    rows = []
    have = index_assets(assets)
    for sym, last in DELISTED:
        start = (dt.date.fromisoformat(last) - dt.timedelta(days=40)).isoformat()
        bars = daily([sym], start, min(last, end), "raw").get(sym, {})
        a = have.get(sym)
        rows.append({"symbol": sym, "last": last, "in_assets": bool(a), "status": a.get("status") if a else None,
                     "bars": len([d for d in bars if d <= last])})
    return rows


def _git():
    import subprocess
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True)
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True)
        return rev.stdout.strip() + (" dirty" if dirty.stdout.strip() else "")
    except Exception:
        return "unknown"


def _sha_prereg():
    import hashlib
    return hashlib.sha256(Path(PREREG).read_bytes()).hexdigest()[:16]


def _block(nights, variant, cost):
    """In-sample and out-of-sample summaries, plus how many nights variant 2 could not price."""
    def take(oos):
        pairs, dropped = [], 0
        for night in nights:
            if night["oos"] != oos:
                continue
            r = book_return(night["picks"], variant, cost)
            if r is None:
                dropped += 1
            else:
                pairs.append((night["date"], r))
        sm = summarize(pairs)
        sm["dropped"] = dropped
        return sm, pairs
    ins, is_pairs = take(False)
    oos, pairs = take(True)
    return {"is": ins, "oos": oos, "pairs": pairs, "is_pairs": is_pairs}


def _spy_side(nights, pairs):
    by = {n["date"]: n["spy"] for n in nights}
    strat, spy = [], []
    for day, r in pairs:
        s = by.get(day)
        if s is None:
            continue
        strat.append(r)
        spy.append(s)
    if not strat:
        return None, None
    return statistics.fmean(strat), statistics.fmean(spy)


def evaluate_book(nights, cost):
    """Variant 1 numbers, the random means, and the SPY comparison, all at one cost."""
    block = _block(nights, "open", cost)
    draws = random_means([n for n in nights if n["oos"]], cost)
    strat, spy = _spy_side(nights, block["pairs"])
    oos_years = block["oos"]["years"]
    years_ok = bool(oos_years) and all(v["n"] > 0 and v["mean"] > 0 for v in oos_years.values())
    p95 = percentile(draws, PCTL)
    return {"block": block, "draws": draws, "p95": p95, "strategy_on_spy": strat, "spy": spy,
            "beats_random": bool(block["oos"]["mean"] is not None and p95 is not None and block["oos"]["mean"] > p95),
            "beats_spy": bool(strat is not None and spy is not None and strat > spy),
            "years_ok": years_ok}


def survivorship_lines(meta, probe):
    covered = sum(1 for r in probe if r["in_assets"] and r["bars"] > 0)
    lines = [
        "Universe: Alpaca assets with status active and inactive. A name is eligible on a day only if it has a bar "
        "that day, a raw close above $10, and 20 sessions of dollar volume above $20M.",
        f"Known 2016–2018 delistings found with bars: {covered} of {len(probe)}. "
        + ", ".join(f"{r['symbol']} {'in' if r['in_assets'] else 'NOT in'} assets, {r['bars']} bars" for r in probe),
        "A name that stops printing inside the file is kept in the average: cash merger at the cash rate, "
        "stock-and-cash at the cash part only, otherwise −100% after five sessions with no print. "
        "That −100% is harsh for a stock-only take-out Alpaca did not mark, and it is the locked choice.",
        "Renames: if the new symbol has bars before the name_change process date, the old symbol is dropped.",
        "Not fixed: tickers Alpaca never listed, a ticker reused by a later company, and dividends (the signal is the "
        "price ratio, so an ex-dividend morning is a loss even though the holder is paid).",
        f"Exit counts: {meta['exit_kinds']}. Inactive symbols eligible at least once: {meta['ever_inactive']}.",
    ]
    return lines


def write_report(result):
    day = result["today"]
    md = common.ROOT / "docs" / "backtest" / f"overnight-mom-{day}.md"
    js = md.with_suffix(".json")
    md.write_text(render(result), encoding="utf-8")
    js.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(md, flush=True)
    return md


def _fail(today, end, exc):
    text = traceback.format_exc()
    print(text, flush=True)
    result = {"go": False, "complete": False, "today": today, "start": START, "end": end or "",
              "primary": "unfinished", "checks": [("download finished", False, {"error": str(exc)})],
              "error": text[-4000:]}
    path = common.ROOT / "docs" / "backtest" / f"overnight-mom-{today}.md"
    path.write_text(f"# Overnight momentum — {today}\n\n**NO-GO.** The download did not finish.\n\n```\n{text[-4000:]}\n```\n",
                    encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 1


def main():
    prereg = common.ROOT / PREREG
    if not prereg.exists():
        raise SystemExit(f"pre-registration missing: {PREREG}")
    today = dt.datetime.now(alpaca.NY).date().isoformat()
    end = None
    t0 = time.time()
    req0 = alpaca.REQUESTS[0]
    try:
        cal = alpaca.calendar(WARMUP, today)
        sessions = sorted(d for d in cal if d >= WARMUP)
        end = last_completed(today, sessions)
        sessions = [d for d in sessions if d <= end]
        print(f"sessions {sessions[0]} .. {end} ({len(sessions)})", flush=True)
        assets = alpaca.assets()
        indexed = index_assets(assets)
        universe = sorted(s for s, a in indexed.items() if is_common(a))
        inactive = {s for s in universe if indexed[s].get("status") == "inactive"}
        print(f"universe {len(universe)} inactive {len(inactive)}", flush=True)
        mergers, worthless, changes = corporate_actions(
            WARMUP, (dt.date.fromisoformat(today) + dt.timedelta(days=10)).isoformat(), universe)
        first = news_first(end)
        primary_ex = bool(first and first <= START)
        print(f"news first {first} primary {'ex_earnings' if primary_ex else 'all'}", flush=True)
        raw = daily(universe, WARMUP, end, "raw")
        alive = [s for s in universe if raw.get(s)]
        adj = daily(alive, WARMUP, end, "split")
        skip = apply_rename(raw, adj, changes)
        aapl_ok, aapl = split_sane(adj.get("AAPL", {}), sessions)
        probe = probe_delisted(assets, end)
        flags, truncated = {}, 0
        nxt = {sessions[i]: sessions[i + 1] for i in range(len(sessions) - 1)}
        if first:
            for n, day in enumerate(d for d in entry_dates(sessions) if d >= max(START, first)):
                symbols, cut = news_flags(day, nxt[day])
                flags[day] = set() if cut else symbols
                truncated += bool(cut)
                if n % 100 == 0:
                    print(f"news {n}", flush=True)
        rows, kinds = [], defaultdict(int)
        ever, ever_in = set(), set()
        for sym in alive:
            if sym in skip or sym not in adj:
                continue
            got = build_rows(sym, sessions, raw[sym], adj[sym], mergers, worthless)
            for row in got:
                kinds[row["exit_kind"]] += 1
                ever.add(sym)
                if sym in inactive:
                    ever_in.add(sym)
            rows.extend(got)
        spy = spy_returns(sessions, end)
        book_all = nights_from(sessions, rows, flags, spy, False)
        book_ex = nights_from(sessions, rows, flags, spy, True)
        primary = book_ex if primary_ex else book_all
        other = book_all if primary_ex else book_ex
        fill_vwap(primary)
        cost_rows = {}
        gate_eval = None
        for cost in COSTS:
            ev = evaluate_book(primary, cost)
            cost_rows[f"{cost:.4f}"] = {"mean": ev["block"]["oos"]["mean"], "t": ev["block"]["oos"]["t"],
                                        "beats_random": ev["beats_random"], "beats_spy": ev["beats_spy"],
                                        "years_ok": ev["years_ok"]}
            if abs(cost - GATE_COST) < 1e-12:
                gate_eval = ev
        vwap = _block(primary, "vwap", GATE_COST)
        for key, pairs in (("oos", vwap["pairs"]), ("is", vwap["is_pairs"])):
            strat_v, spy_v = _spy_side(primary, pairs)
            vwap[key]["beats_spy"] = bool(strat_v is not None and spy_v is not None and strat_v > spy_v)
        other_oos = _block(other, "open", GATE_COST)["oos"]
        spy_n = sum(1 for n in primary if n["oos"] and n["spy"] is not None)
        oos_n = sum(1 for n in primary if n["oos"])
        spy_ok = oos_n > 0 and spy_n / oos_n >= SPY_COVERAGE
        complete = bool(aapl_ok and spy_ok and gate_eval)
        checks = gate(gate_eval["block"]["oos"], gate_eval["draws"], gate_eval["spy"],
                      gate_eval["strategy_on_spy"],
                      {**gate_eval["block"]["is"]["years"], **gate_eval["block"]["oos"]["years"]},
                      gate_eval["block"]["oos"]["years"])
        checks.append(("file complete (AAPL split, SPY coverage, download)", complete,
                       {"aapl": aapl_ok, "spy_coverage": spy_n / oos_n if oos_n else 0}))
        go = complete and all(ok for _, ok, _ in checks)
        meta = {"exit_kinds": dict(kinds), "ever_inactive": len(ever_in)}
        result = {
            "go": go, "complete": complete, "today": today, "start": START, "end": end,
            "primary": "ex_earnings" if primary_ex else "all", "news_first": first,
            "checks": checks, "primary_open": {"is": gate_eval["block"]["is"], "oos": gate_eval["block"]["oos"]},
            "primary_vwap": {"is": vwap["is"], "oos": vwap["oos"]},
            "p95": gate_eval["p95"], "spy": gate_eval["spy"], "strategy_on_spy": gate_eval["strategy_on_spy"],
            "costs": cost_rows, "other_oos": other_oos, "account": small_account(primary),
            "survivorship": survivorship_lines(meta, probe), "requests": alpaca.REQUESTS[0] - req0,
            "runtime": time.time() - t0, "git": _git(), "prereg_sha": _sha_prereg(),
            "universe": len(universe), "inactive": len(inactive), "ever_eligible": len(ever),
            "exchanges": dict(Counter((a.get("exchange") or "").upper() for a in indexed.values())),
            "ever_inactive": len(ever_in), "exit_kinds": dict(kinds), "aapl": aapl, "probe": probe,
            "earnings_nights": sum(1 for s in flags.values() if s),
            "earnings_names": sum(len(s) for s in flags.values()), "news_truncated": truncated,
            "oos_nights": [{"date": d, "ret": r} for d, r in gate_eval["block"]["pairs"]],
        }
        # the in-sample pairs are not required in the JSON beyond the summary; drop the huge draw list
        write_report(result)
        print("GO" if go else "NO-GO", flush=True)
        return 0 if complete else 1
    except Exception as exc:
        return _fail(today, end, exc)


def spy_returns(sessions, end):
    """{entry date: split-adjusted close-to-next-open}. No $10 filter and no halt fiction: a missing open is missing."""
    adj = daily(["SPY"], WARMUP, end, "split").get("SPY", {})
    out = {}
    for i, day in enumerate(sessions[:-1]):
        if day < START:
            continue
        a, b = adj.get(day), adj.get(sessions[i + 1])
        if a and b and a[1] > 0 and b[0] > 0:
            out[day] = b[0] / a[1] - 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
