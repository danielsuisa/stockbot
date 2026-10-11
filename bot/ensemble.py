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
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

from bot import (alpaca, blind, common, ensemble_backtest as bt, ensemble_counts as ec, ensemble_data as ed,
                 ensemble_stats as st, ensemble_verdict as ev, levels, signals)

BARS_END = "2026-07-31"  # outcomes only (ruling C17); the universe, the detectors and the signal days end on SIGNAL_END
REPORT_PATH = "ensemble-blind-report.json"
VERDICT_PATH, TICKETS_PATH = ev.VERDICT_PATH, ev.TICKETS_PATH
TODAY = ev.today
K_REPORT = pathlib.Path(__file__).resolve().parent.parent / "docs/backtest/ensemble-phaseC-measurements-2026-10-11.json"


class MissingInput(BaseException):
    """An input is not in the cache during a blind or verdict run (the network is closed). A BaseException, so no
    retry loop or broad `except Exception` can swallow it."""


class RsMismatch(Exception):
    """The r_s recomputed in the run differ from the K report (no values in the message)."""


def _refuse(*a, **k):
    raise MissingInput("an input is not cached")


DUMMY_ENV = ("SEC_UA", "ALPACA_KEY_ID", "ALPACA_SECRET_KEY")  # the blind job has no secrets: a miss must reach urlopen


class offline:
    """Close the network: every urllib request raises MissingInput (cached inputs are read without it). Without the
    secrets (the blind job), placeholder values let a cache miss reach the closed network and surface as
    MissingInput instead of a missing-key error."""

    def __enter__(self):
        self.saved = urllib.request.urlopen
        self.env = {k: os.environ.get(k) for k in DUMMY_ENV}
        for k in DUMMY_ENV:
            if not os.environ.get(k):
                os.environ[k] = "offline"
        urllib.request.urlopen = _refuse
        return self

    def __exit__(self, *exc):
        urllib.request.urlopen = self.saved
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


FINGERPRINT = [None]


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


def prefetch(deadline_min, require_complete=False):
    """Fetch and cache the inputs; counts only. With require_complete (the last prefetch job) it fails unless every
    wanted quote was read and no index page is missing, so the blind job never starts on incomplete inputs."""
    t0 = time.monotonic()
    inp, got = prepare()
    FINGERPRINT[0] = ed.fingerprint(inp)
    wanted = candidates(got["firings"], inp, got["renamed"])
    read = 0
    for sym, at in wanted:
        if time.monotonic() - t0 >= deadline_min * 60:
            break
        alpaca.quote_full(sym, at)
        read += 1
    missing = getattr(inp.releases, "missing", 0)
    print(f"prefetch: {len(wanted)} quotes wanted, {read} read, {missing} index pages missing; "
          f"inputs {FINGERPRINT[0]}")
    return 4 if require_complete and (read < len(wanted) or missing) else 0


RS_TOLERANCE = 0.01


def check_r_s(r_s, path=K_REPORT):
    """Spec 4.2: for each signal, max(1, r_s) of the run within 1% (relative) of the K report's max(1, r_s) --
    bars fetched again can move a firing for a benign reason. The run's own r_s and K are the ones used."""
    want = json.loads(pathlib.Path(path).read_text("utf-8"))["k"]["k_counts"]
    for s in signals.SIGNALS:
        run, rep = max(1.0, r_s[s]), max(1.0, want[s]["r_s"])
        if abs(run - rep) > RS_TOLERANCE * rep:
            raise RsMismatch("max(1, r_s) moved more than 1% from the K report")


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
    FINGERPRINT[0] = ed.fingerprint(inp)
    tickets, refusals, kept, pools, small, K, r_s, w_s, blocks10, idx, days = _run(inp, got)
    if not kept:
        raise ValueError("no ticket kept")
    x = [(t["g"] - t["m"]) - t["c"] for t in kept]
    return blind.report(x, [t["c"] for t in kept], [t["filled"] for t in kept], [t["signal"] for t in kept], blocks10,
                        _counts(got["firings"], tickets, refusals, small, idx, len(days)), K, r_s, w_s)


class FingerprintMismatch(Exception):
    """The verdict's inputs are not the blind run's (6.4 point 4)."""


def verdict_report(expected_fp=None, commit=""):
    """The single verdict run (phase D; never run in phase C) -> (report, kept tickets or None). Stops before any
    return statistic when b > 0.03 R, check 1 or comparability fails (6.4); otherwise every number of
    ensemble_verdict.numbers."""
    inp, got = prepare()
    FINGERPRINT[0] = ed.fingerprint(inp)
    if expected_fp and FINGERPRINT[0] != expected_fp:
        raise FingerprintMismatch("the inputs' fingerprint differs from the blind run's")
    tickets, refusals, kept, pools, small, K, r_s, w_s, blocks10, idx, days = _run(inp, got)
    if not kept:
        raise ValueError("no ticket kept")
    x = [(t["g"] - t["m"]) - t["c"] for t in kept]
    s10, s5 = st.ses(x, blocks10)
    b, mu = st.b_allowance(K), st.mu_min(s10, s5)
    counts = _counts(got["firings"], tickets, refusals, small, idx, len(days))
    comp = bt.comparability(counts["quote_refused"], counts["passing_quote_free"], small, len(tickets))
    confirm = st.confirmable(mu, b)
    rep = {"inputs": FINGERPRINT[0], "commit": commit, "K": K, "r_s": r_s, "w_s": w_s,
           "check_1": {"pass": confirm, "mu_min": mu, "b_R": b, "se_t10": s10, "se_t5": s5},
           "comparability": comp, "counts": counts}
    gates = None
    if b <= st.B_DATA_MAX and confirm and comp["comparable"]:
        for t, blk in zip(kept, blocks10):
            t["block"] = blk
        rep.update(ev.numbers(kept, bt.draws(kept, pools), b, blocks10, counts["refusals"]))
        gates = {k: g["pass"] for k, g in rep["gates"].items()}
    rep["outcome"] = st.outcome(confirm, comp["comparable"], gates, b=b)
    return rep, (kept if gates is not None else None)


def head():
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description="The ensemble's runs (inputs, blind, verdict)")
    ap.add_argument("--prefetch", action="store_true")
    ap.add_argument("--deadline-min", type=int, default=300)
    ap.add_argument("--require-complete", action="store_true")
    ap.add_argument("--blind", action="store_true")
    ap.add_argument("--verdict", action="store_true")
    ap.add_argument("--blind-commit", default="")
    ap.add_argument("--owner-go-ahead", action="store_true")
    ap.add_argument("--fingerprint", default="", help="the blind run's input fingerprint (the verdict refuses others)")
    a = ap.parse_args(argv)
    common.install_excepthooks()
    try:
        if a.prefetch:
            return prefetch(a.deadline_min, a.require_complete)
        if a.verdict:
            if not (a.owner_go_ahead and a.blind_commit and a.blind_commit == head()):
                print("verdict refused: it needs --blind-commit <the checked-out commit of the blind run> and "
                      "--owner-go-ahead")
                return 2
            try:
                with offline():
                    rep, kept = verdict_report(a.fingerprint, a.blind_commit)
            except FingerprintMismatch:
                print("verdict refused: the inputs' fingerprint differs from the blind run's; nothing was computed")
                return 2
            paths = ev.write(rep, kept, TODAY())
            print(f"verdict written: {', '.join(paths)}")
            return 0
        if a.blind:
            with offline():
                rep = blind_report()
            blind.write(rep, REPORT_PATH)
            print(f"blind report written: {REPORT_PATH}; inputs {FINGERPRINT[0]}")
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
