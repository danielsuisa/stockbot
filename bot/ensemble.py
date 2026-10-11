"""The ensemble's run driver (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md 6.2-6.4, 4.2):

  --prefetch   fetch and cache INPUTS only (bars, filings, index pages, quotes for every firing that passes the
               quote-free refusals: the guard quote and one quote per session the order could fill on); resumable;
               prints counts only.
  --blind      the blind run (needs the owner's go-ahead, spec 1.1): with the network closed (every input must be
               cached), issue the tickets, simulate, build the pools, and write ONE file, the blind report.
  --verdict    the verdict run (phase D): refuses unless given the blind run's commit (checked out) and the owner's
               go-ahead flag.

Results live in memory only. A crash prints the exception's type and frames only (common.trace)."""
import argparse
import collections
import datetime as dt
import json
import math
import pathlib
import subprocess
import sys
import time
import urllib.request

from bot import (alpaca, blind, common, ensemble_backtest as bt, ensemble_counts as ec, ensemble_data as ed,
                 ensemble_stats as st, levels, signals)

BARS_END = "2026-07-31"  # outcomes only (ruling C17); the universe, the detectors and the signal days end on SIGNAL_END
REPORT_PATH = "ensemble-blind-report.json"
VERDICT_PATH = "ensemble-verdict-report.json"
K_REPORT = pathlib.Path(__file__).resolve().parent.parent / "docs/backtest/ensemble-phaseC-measurements-2026-10-11.json"


class MissingInput(BaseException):
    """An input is not in the cache during a blind or verdict run (the network is closed). A BaseException, so no
    retry loop or broad `except Exception` can swallow it."""


class RsMismatch(Exception):
    """The r_s recomputed in the run differ from the K report (no values in the message)."""


def _refuse(*a, **k):
    raise MissingInput("an input is not cached")


class offline:
    """Close the network: every urllib request raises MissingInput (cached inputs are read without it)."""

    def __enter__(self):
        self.saved = urllib.request.urlopen
        urllib.request.urlopen = _refuse
        return self

    def __exit__(self, *exc):
        urllib.request.urlopen = self.saved
        return False


def prepare(today=None):
    inp = ed.load(today or dt.date.today(), bars_end=BARS_END)
    return inp, signals.firings(inp, ed.SIGNAL_START, ed.SIGNAL_END)


def candidates(fired, inp, renamed):
    """The quotes a run can need, decided without any outcome: for every firing that passes the quote-free refusals
    and is inside the data, the guard quote and the quote 5 minutes after the open of every session its order could
    fill on (1 for open, 3 for breakout)."""
    out, seen = [], set()
    for d, sig, sym in sorted(fired):
        bars_of = renamed.get((d, sym), sym)
        s = inp.series[bars_of]
        mode, horizon = signals.MODES[sig]
        t = levels.plan(sym, bt.plan_rows(s, d), d, mode=mode, horizon=horizon, risk=bt.RISK)
        if not t["ok"] or bt.beyond_data(d, mode, horizon, inp.sessions):
            continue
        wanted = [levels.guard_time(d, inp.cal)]
        wanted += [levels.entry_time(r[0], inp.cal) for r in bt.rows_after(s, d)[:bt._valid(mode)]]
        for at in wanted:
            if (bars_of, at) not in seen:
                seen.add((bars_of, at))
                out.append((bars_of, at))
    return out


def prefetch(deadline_min):
    t0 = time.time()
    inp, got = prepare()
    wanted = candidates(got["firings"], inp, got["renamed"])
    read = 0
    for sym, at in wanted:
        if time.time() - t0 > deadline_min * 60:
            break
        alpaca.quote_full(sym, at)
        read += 1
    print(f"prefetch: {len(wanted)} quotes wanted, {read} read")
    return 0


def check_r_s(r_s, path=K_REPORT):
    """The run's r_s must equal the K report's (spec 4.2, C6)."""
    want = json.loads(pathlib.Path(path).read_text("utf-8"))["k"]["k_counts"]
    if any(not math.isclose(r_s[s], want[s]["r_s"], rel_tol=1e-9, abs_tol=1e-12) for s in signals.SIGNALS):
        raise RsMismatch("r_s differ from the K report")


def _run(inp, got):
    """Everything the blind and verdict runs share, in memory -> (tickets, refusals, kept, pools, small, K, r_s, w_s,
    blocks10)."""
    fired, renamed = got["firings"], got["renamed"]
    tickets, refusals = bt.issue(fired, inp, renamed, alpaca.quote_full)
    fired_by_day = collections.defaultdict(set)
    for d, _, sym in fired:
        fired_by_day[d].add(sym)
    kept, pools, small = bt.compare(tickets, inp, renamed, got["deciles"], fired_by_day)
    days = [d for d in inp.sessions if ed.SIGNAL_START <= d <= ed.SIGNAL_END]
    idx = {d: i for i, d in enumerate(days)}
    blocks10 = [st.block(idx[t["day"]], len(days)) for t in kept]
    counts = ec.k_counts(fired, got["members"], ec.ended(inp, start=ed.SIGNAL_START, end=ed.SIGNAL_END), inp.sessions,
                         start=ed.SIGNAL_START, end=ed.SIGNAL_END)
    r_s = {s: counts[s]["r_s"] for s in signals.SIGNALS}
    check_r_s(r_s)
    issued = collections.Counter(t["signal"] for t in tickets)
    w_s = {s: issued[s] / len(tickets) for s in signals.SIGNALS} if tickets else {s: 0.0 for s in signals.SIGNALS}
    K = ec.k_value(r_s, w_s)[0]
    return tickets, refusals, kept, pools, small, K, r_s, w_s, blocks10, idx, days


def _counts(fired, tickets, refusals, small, idx, n_days):
    blk = lambda d: str(st.block(idx[d], n_days)) if d in idx else "outside"  # noqa: E731
    firings, issued, by_reason = {}, {}, {}
    for d, sig, _ in fired:
        firings.setdefault(sig, collections.Counter())[blk(d)] += 1
    for t in tickets:
        issued.setdefault(t["signal"], collections.Counter())[blk(t["day"])] += 1
    for (sig, reason), n in refusals.items():
        by_reason.setdefault(sig, {})[reason] = n
    quote_refused = sum(n for (sig, reason), n in refusals.items() if reason in bt.QUOTE_REASONS)
    return {"firings": {s: dict(v) for s, v in firings.items()}, "refusals": by_reason,
            "issued": {s: dict(v) for s, v in issued.items()}, "pools_dropped_small": small,
            "quote_refused": quote_refused, "passing_quote_free": len(tickets) + quote_refused,
            "issued_total": len(tickets)}


def blind_report():
    """The blind run: everything in memory; returns only the whitelisted report."""
    inp, got = prepare()
    tickets, refusals, kept, pools, small, K, r_s, w_s, blocks10, idx, days = _run(inp, got)
    if not kept:
        raise ValueError("no ticket kept")
    x = [(t["g"] - t["m"]) - t["c"] for t in kept]
    return blind.report(x, [t["c"] for t in kept], [t["filled"] for t in kept], [t["signal"] for t in kept], blocks10,
                        _counts(got["firings"], tickets, refusals, small, idx, len(days)), K, r_s, w_s)


def verdict_report():
    """The single verdict run (phase D; never run in phase C): check 1, comparability, the five gates, the outcome."""
    inp, got = prepare()
    tickets, refusals, kept, pools, small, K, r_s, w_s, blocks10, idx, days = _run(inp, got)
    x = [(t["g"] - t["m"]) - t["c"] for t in kept]
    x_stress = [(t["g"] - t["m"]) - t["cost_r_stress"] for t in kept]
    s10, s5 = st.ses(x, blocks10)
    b = st.b_allowance(K)
    counts = _counts(got["firings"], tickets, refusals, small, idx, len(days))
    comp = bt.comparability(counts["quote_refused"], counts["passing_quote_free"], small, len(tickets))
    confirm = st.confirmable(st.mu_min(s10, s5), b)
    gates = None
    out = {"check_1": confirm, "comparable": comp["comparable"]}
    if confirm and comp["comparable"]:
        A, A_k = bt.A(kept), bt.draws(kept, pools)
        g1 = st.gate1(x, b, blocks10)
        gates = {"1": g1["pass"], "2": st.above_950th(A, A_k), "3": st.gate3(x, blocks10)["pass"],
                 "4": st.gate4(x, [t["signal"] for t in kept])["pass"], "5": st.gate1(x_stress, b, blocks10)["pass"]}
        out.update(A=A, p=st.p_value(A, A_k), gates=gates, t10=g1["t10"], t5=g1["t5"])
    out["outcome"] = st.outcome(confirm, comp["comparable"], gates)
    return out


def head():
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description="The ensemble's runs (inputs, blind, verdict)")
    ap.add_argument("--prefetch", action="store_true")
    ap.add_argument("--deadline-min", type=int, default=300)
    ap.add_argument("--blind", action="store_true")
    ap.add_argument("--verdict", action="store_true")
    ap.add_argument("--blind-commit", default="")
    ap.add_argument("--owner-go-ahead", action="store_true")
    a = ap.parse_args(argv)
    common.install_excepthooks()
    try:
        if a.prefetch:
            return prefetch(a.deadline_min)
        if a.verdict:
            if not (a.owner_go_ahead and a.blind_commit and a.blind_commit == head()):
                print("verdict refused: it needs --blind-commit <the checked-out commit of the blind run> and "
                      "--owner-go-ahead")
                return 2
            with offline():
                rep = verdict_report()
            with open(VERDICT_PATH, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=1)
            print(f"verdict report written: {VERDICT_PATH}")
            return 0
        if a.blind:
            with offline():
                rep = blind_report()
            blind.write(rep, REPORT_PATH)
            print(f"blind report written: {REPORT_PATH}")
            return 0
        ap.print_usage()
        return 2
    except MissingInput:
        print("blind: an input is missing from the cache; nothing was computed")
        return 3
    except Exception:  # noqa: BLE001 - the type and frames only, never a value
        common.trace()
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
