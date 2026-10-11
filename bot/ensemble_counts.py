"""Phase C's two measurements (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md 6.4, 6.2 and 4.2),
counts only:

1a. K: how often each signal fires on a stock in the 12 months before its bars end for good, against how often it
    fires on any stock (r_s), the provisional K with phase A's firing shares, K_max and b.
1b. Quote coverage: on a seeded sample of firings, section 3's plan and guard at $100 risk on bars through D; the
    share of quote-based refusals against the 5% of 6.2.

No fill, exit or outcome of any ticket is computed; every comparison with a threshold is made on unrounded values."""
import bisect
import collections
import datetime as dt
import json
import math
import random
import sys
import time

from bot import alpaca, common, ensemble_data as ed, levels, probe_ensemble as pe, probe_insider as pi, signals

F = 0.0041  # spec 6.4: f, the survivorship share
B_MAX = 0.03
QUOTE_SHARE_MAX = 0.05
ENDED_BEFORE = "2025-04-01"
PHASE_A_FIRINGS = {"S1": 1511, "S2": 7979, "S3": 13355, "S4": 14679}
W_PROVISIONAL = {s: n / sum(PHASE_A_FIRINGS.values()) for s, n in PHASE_A_FIRINGS.items()}
SAMPLE_SEED, SAMPLE_PER = 20261011, 50
K_REPORT_KEYS = ("firings_per_signal", "phase_a_firings", "difference_vs_phase_a", "diagnostics", "warm_up",
                 "ended_symbols", "k_counts", "w_provisional", "K", "K_max", "f", "b_R", "b_max_R", "limit_R",
                 "continue_to_quotes")
QUOTE_REPORT_KEYS = ("sample_size", "strata", "weighted_share", "weighted_share_se", "per_signal", "per_year",
                     "entry_quote_missing", "limit", "stop", "quote_requests")


# ---------- 1a: K ----------
def ended(inp, start=ed.SIGNAL_START, end=ed.SIGNAL_END, before=ENDED_BEFORE):
    """Symbols whose bars end for good (spec 4.2, C3) -> {symbol: L}: L, the last bar through `end`, is before
    `before` and not before `start`, and on the first session after L the rename rule does not carry the company on
    (its CIK's one other symbol has a bar that session within a factor of 2 of the CIK's latest market price)."""
    out = {}
    for sym, s in inp.series.items():
        k = bisect.bisect_right(s["dates"], end)
        if not k:
            continue
        L = s["dates"][k - 1]
        if not start <= L < before:
            continue
        j = bisect.bisect_right(inp.sessions, L)
        if j >= len(inp.sessions):
            continue
        nxt = inp.sessions[j]
        cik = signals.cik_at(inp, sym, nxt)
        others = [o for o in inp.of_cik.get(cik, ()) if o != sym and signals._has_bar(inp, o, nxt)]
        if cik is not None and len(others) == 1:
            price, o = signals.cik_price(inp, cik, nxt), inp.series[others[0]]
            close = o["raw_close"][pe._i(o, nxt)]
            if price is not None and price / signals.RENAME_FACTOR <= close <= price * signals.RENAME_FACTOR:
                continue  # renamed: the company goes on under the other symbol
        out[sym] = L
    return out


def window(L, sessions, start=ed.SIGNAL_START, end=ed.SIGNAL_END):
    """The sessions in (L - 365 days, L], clipped to the signal period."""
    lo = (dt.date.fromisoformat(L) - dt.timedelta(365)).isoformat()
    return [d for d in sessions if lo < d <= L and start <= d <= end]


def _ratio(f_end, d_end, f_all, d_all):
    if not (f_end and d_end and f_all and d_all):
        return 0.0
    return (f_end / d_end) / (f_all / d_all)


def k_counts(fired, members, ended_syms, sessions, start=ed.SIGNAL_START, end=ed.SIGNAL_END):
    """Per signal: firings and universe stock-sessions and stock-months, in the ended symbols' windows and over all
    symbols, the two ratios and r_s = the larger (spec 4.2, C4)."""
    udays = collections.defaultdict(list)
    for d in sorted(members):
        if start <= d <= end:
            for sym in members[d]:
                udays[sym].append(d)
    ss_all = sum(len(v) for v in udays.values())
    m_all = sum(len({d[:7] for d in v}) for v in udays.values())
    f_all = collections.Counter(sig for d, sig, _ in fired if start <= d <= end)
    by_sym = collections.defaultdict(list)
    for d, sig, sym in fired:
        by_sym[sym].append((d, sig))
    f_end, ss_end, m_end = collections.Counter(), 0, 0
    for sym, L in ended_syms.items():
        w = set(window(L, sessions, start, end))
        inside = [d for d in udays.get(sym, ()) if d in w]
        ss_end += len(inside)
        m_end += len({d[:7] for d in inside})
        f_end.update(sig for d, sig in by_sym.get(sym, ()) if d in w)
    out = {}
    for sig in signals.SIGNALS:
        r_m = _ratio(f_end[sig], m_end, f_all[sig], m_all)
        r_ss = _ratio(f_end[sig], ss_end, f_all[sig], ss_all)
        out[sig] = {"firings_end": f_end[sig], "stock_sessions_end": ss_end, "stock_months_end": m_end,
                    "firings_all": f_all[sig], "stock_sessions_all": ss_all, "stock_months_all": m_all,
                    "r_month": r_m, "r_session": r_ss, "r_s": max(r_m, r_ss)}
    return out


def k_value(r, w):
    """K = sum of w_s x max(1, r_s); K_max = the largest max(1, r_s)."""
    return sum(w[s] * max(1.0, r[s]) for s in w), max(max(1.0, r[s]) for s in w)


def within(b):
    return b <= B_MAX


def k_report(got, inp):
    totals = collections.Counter(sig for _, sig, _ in got["firings"])
    end_syms = ended(inp)
    counts = k_counts(got["firings"], got["members"], end_syms, inp.sessions)
    K, K_max = k_value({s: counts[s]["r_s"] for s in signals.SIGNALS}, W_PROVISIONAL)
    return {"firings_per_signal": {s: totals[s] for s in signals.SIGNALS}, "phase_a_firings": PHASE_A_FIRINGS,
            "difference_vs_phase_a": {s: (totals[s] - n) / n for s, n in PHASE_A_FIRINGS.items()},
            "diagnostics": got["diagnostics"], "warm_up": got["warm_up"], "ended_symbols": len(end_syms),
            "k_counts": counts, "w_provisional": W_PROVISIONAL, "K": K, "K_max": K_max, "f": F, "b_R": K * F,
            "b_max_R": K_max * F, "limit_R": B_MAX, "continue_to_quotes": within(K_max * F)}


# ---------- 1b: quote coverage ----------
def sample(fired, seed=SAMPLE_SEED, per=SAMPLE_PER):
    """Up to `per` firings per (signal, year), drawn with one seeded generator over the strata in order (C7)."""
    strata = collections.defaultdict(list)
    for d, sig, sym in sorted(fired):
        strata[(sig, d[:4])].append((d, sig, sym))
    rng, out = random.Random(seed), []
    for key in sorted(strata):
        items = strata[key]
        out += items if len(items) <= per else sorted(rng.sample(items, per))
    return sorted(out)


def plan_rows(s, day, n=40):
    """The bars of the current segment through D, in D's raw money (section 2): split-adjusted prices rescaled by
    raw close(D) / split-adjusted close(D); volume rescaled the other way so price x volume stays the raw dollar
    volume. Rows (date, o, h, l, c, v); the open is not needed by the plan and is set to the close."""
    i = pe._i(s, day)
    scale = s["raw_close"][i] / s["close"][i]
    rows = []
    for j in range(max(s["_seg_start"][i], i - n + 1), i + 1):
        f = s["close"][j] / s["raw_close"][j] * scale
        c = s["raw_close"][j] * f
        rows.append((s["dates"][j], c, s["raw_high"][j] * f, s["raw_low"][j] * f, c, s["raw_volume"][j] / f))
    return rows


def quote_counts(sampled, inp, renamed, quote=None):
    """Per (signal, year): sampled; refused by a quote-free refusal (by reason); passing; of those, no usable guard
    quote; of those with one, the cost guard above 10%; separately, no usable quote 5 minutes after the next
    session's open (C8). Quotes are those of the symbol whose bars are used."""
    quote = quote or alpaca.quote_full
    out = collections.defaultdict(lambda: {"sampled": 0, "refused_quote_free": collections.Counter(), "passing": 0,
                                           "no_guard_quote": 0, "cost_guard": 0, "no_entry_quote": 0})
    for d, sig, sym in sampled:
        c = out[(sig, d[:4])]
        c["sampled"] += 1
        bars_of = renamed.get((d, sym), sym)
        mode, horizon = signals.MODES[sig]
        t = levels.plan(sym, plan_rows(inp.series[bars_of], d), d, mode=mode, horizon=horizon, risk=100.0)
        if not t["ok"]:
            c["refused_quote_free"][t["reason"]] += 1
            continue
        c["passing"] += 1
        g = levels.guard(t, quote(bars_of, levels.guard_time(d, inp.cal)))
        if g.get("reason") == "no usable quote":
            c["no_guard_quote"] += 1
        elif g.get("reason") == "cost above 10% of risk":
            c["cost_guard"] += 1
        k = bisect.bisect_right(inp.sessions, d)
        if k < len(inp.sessions):
            nxt = inp.sessions[k]
            if levels.spread(quote(bars_of, levels.entry_time(nxt, inp.cal))) is None:
                c["no_entry_quote"] += 1
    return dict(out)


def weighted_share(counts, population):
    """The population share of quote-based refusals among the firings passing the quote-free refusals: a ratio
    estimator over the strata, each weighted by its firing count, with its standard error (linearized, with the
    finite-population correction) -> (share, standard error)."""
    num = den = 0.0
    for h, c in counts.items():
        n, N = c["sampled"], population[h]
        if n:
            num += N * (c["no_guard_quote"] + c["cost_guard"]) / n
            den += N * c["passing"] / n
    if not den:
        return 0.0, 0.0
    R, var = num / den, 0.0
    for h, c in counts.items():
        n, N = c["sampled"], population[h]
        if n < 2:
            continue
        a = c["no_guard_quote"] + c["cost_guard"]
        b, z = c["passing"] - a, n - c["passing"]
        mean = (a * (1 - R) - b * R) / n
        s2 = (a * (1 - R - mean) ** 2 + b * (-R - mean) ** 2 + z * mean ** 2) / (n - 1)
        var += N * N * (1 - n / N) * s2 / n
    return R, math.sqrt(var) / den


def quote_stop(share):
    return share > QUOTE_SHARE_MAX


def quote_report(fired, inp, renamed, quote=None):
    population = collections.Counter((sig, d[:4]) for d, sig, _ in fired)
    sampled = sample(fired)
    before = alpaca.REQUESTS[0]
    counts = quote_counts(sampled, inp, renamed, quote)
    share, se = weighted_share(counts, population)
    sub = lambda keep: weighted_share({h: c for h, c in counts.items() if keep(h)}, population)  # noqa: E731
    passing = sum(c["passing"] for c in counts.values())
    return {"sample_size": len(sampled),
            "strata": {f"{sig} {y}": {**c, "refused_quote_free": dict(c["refused_quote_free"]),
                                      "population": population[(sig, y)]} for (sig, y), c in sorted(counts.items())},
            "weighted_share": share, "weighted_share_se": se,
            "per_signal": {s: sub(lambda h, s=s: h[0] == s)[0] for s in signals.SIGNALS},
            "per_year": {y: sub(lambda h, y=y: h[1] == y)[0] for y in sorted({h[1] for h in counts})},
            "entry_quote_missing": (sum(c["no_entry_quote"] for c in counts.values()) / passing) if passing else 0.0,
            "limit": QUOTE_SHARE_MAX, "stop": quote_stop(share), "quote_requests": alpaca.REQUESTS[0] - before}


# ---------- the run ----------
def run(today=None):
    t0 = time.time()
    inp = ed.load(today or dt.date.today())
    got = signals.firings(inp, ed.SIGNAL_START, ed.SIGNAL_END)
    k = k_report(got, inp)
    quotes = quote_report(got["firings"], inp, got["renamed"]) if k["continue_to_quotes"] else \
        {"skipped": "b_max above 0.03 R: stop after the K report"}
    return {"k": k, "quotes": quotes,
            "requests_and_runtime": {"alpaca": alpaca.REQUESTS[0], "sec zips": pi.SEC_REQUESTS[0],
                                     "index pages": len(getattr(inp.releases, "times", {})),
                                     "runtime_s": round(time.time() - t0)}}


def main(argv=None):
    common.install_excepthooks()
    try:
        out = run()
    except Exception:  # noqa: BLE001 - type and frames only
        common.trace()
        return 1
    text = json.dumps(out, indent=1, default=dict)
    print(text)
    common.summary(f"### Phase C measurements (counts only)\n\n```json\n{text}\n```")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
