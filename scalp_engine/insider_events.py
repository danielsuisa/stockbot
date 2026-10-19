"""I1 Stage 3 (ADR 0009 sections 2-4): run the reimplemented stockbot rule (insider_rule) over every acceptance time,
check parity against the live journal, and write the event table. Computes no outcome: nothing here reads a price
after an event's entry decision.

    python -m scalp_engine.insider_events raw      # events and live-alert emulation from SEC data only (JSON)
    python -m scalp_engine.insider_events parity   # parity table against data/journal.json
    python -m scalp_engine.insider_events table    # final insider_events table (needs insider_prices first)
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import polars as pl

from scalp_engine import insider_data as D
from scalp_engine import insider_rule as R
from scalp_engine import storage
from scalp_engine.data_access import ET

UTC = dt.timezone.utc
LATENCY = dt.timedelta(minutes=5)
EVENT_FIRST, EVENT_LAST = dt.date(2022, 2, 1), dt.date(2026, 7, 20)
PARITY_FIRST, PARITY_LAST = dt.date(2026, 9, 24), dt.date(2026, 10, 16)
JOURNAL_START = dt.datetime(2026, 9, 24, 20, 30, tzinfo=UTC)  # first journal commit d6f1158 (I0 audit I1.7)
PRE_JOURNAL_STATE = "d0902d3"  # last data/state.json before the journal (2026-09-24 10:10 UTC): its alert record


def derived(name):
    p = storage.data_root() / "derived" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- inputs

def load_docs():
    """-> (list of (accepted_at, accession, issuer_cik, filed, doc, filed_symbol), stats). Only required filings with a
    header acceptance time; missing ones are counted."""
    fil, lin, acc = D.filings_frame(), D.lines_frame(), D.acceptance_frame()
    req, rst = D.required_filings(fil, lin)
    hdr = acc.filter((pl.col("method") == "header") & (pl.col("status") == "ok")).select("accession", "event_at")
    at = dict(zip(hdr["accession"].to_list(), hdr["event_at"].to_list()))
    oct_req = fil.filter(pl.col("dataset_quarter").str.starts_with("edgar-") & (pl.col("owner_seq") == 0)
                         & pl.col("document_type").is_in(["4", "4/A"]))["accession"].to_list()
    want = set(req["accession"].to_list()) | set(oct_req)
    owners = defaultdict(list)
    meta = {}
    for r in fil.filter(pl.col("accession").is_in(sorted(want))).sort(["accession", "owner_seq"]).iter_rows(named=True):
        owners[r["accession"]].append((r["owner_cik"], r["owner_name"], r["relationship"], r["owner_title"]))
        if r["owner_seq"] == 0:
            meta[r["accession"]] = r
    lines = defaultdict(list)
    for r in lin.filter(pl.col("accession").is_in(sorted(want))).sort(["accession", "line_seq"]).iter_rows(named=True):
        lines[r["accession"]].append(r)
    out, missing = [], 0
    for a in sorted(want):
        if a not in at:
            missing += 1
            continue
        m = meta[a]
        doc = R.make_doc(m["document_type"], m["issuer_cik"], owners[a], lines[a], symbol=m["issuer_symbol"],
                         original=m["date_of_orig_sub"], box=m["aff10b5one"], notes=m["notes_10b5"] or ())
        out.append((at[a], a, m["issuer_cik"], m["filing_date"].isoformat(), doc, m["issuer_symbol"]))
    out.sort(key=lambda x: (x[0], x[1]))
    return out, {**rst, "october_form4": len(oct_req), "with_acceptance": len(out), "missing_acceptance": missing}


# ---------------------------------------------------------------- the clock

def run_clock(docs, journal_start=JOURNAL_START, stop=None, seed=None):
    """Apply filings in acceptance order. -> (events, live alerts after journal_start, applied stats).
    At journal_start every issuer's alert record is replaced by `seed` ({cik: accessions}, the live bot's own record
    from before the journal began; empty if None) and every issuer with a counted row not in it alerts once, as the
    first journaled run did."""
    issuers, events, alerts = {}, [], []
    stats = defaultdict(int)
    started = False
    for t, acc, cik, filed, doc, sym in docs:
        if stop and t >= stop:
            break
        if journal_start and not started and t >= journal_start:
            started = True
            today = journal_start.astimezone(ET).date()
            for c, st in sorted(issuers.items()):
                st.prune(today)
                st.alerted = set((seed or {}).get(c, ()))
                snap = st.live_alert(st.evaluate())
                if snap:
                    alerts.append({"t": journal_start, "cik": c, "trigger_acc": None, "first_run": True, **snap})
        today = t.astimezone(ET).date()
        st = issuers.setdefault(cik, R.Issuer(cik))
        res = st.apply(acc, doc, filed, today)
        stats[res] += 1
        ev = st.evaluate()
        for trigger, accs, people, total in st.events(acc, ev):
            events.append({"trigger": trigger, "cik": cik, "accepted_at": t, "completing": acc, "accessions": accs,
                           "people": people, "total_usd": total, "filed_symbol": sym})
        if started:
            snap = st.live_alert(ev)
            if snap:
                alerts.append({"t": t, "cik": cik, "trigger_acc": acc, "first_run": False, **snap})
        else:
            st.live_alert(ev)  # keeps the alert record as the live bot would have (not reported)
    return events, alerts, dict(stats)


def snapshots(docs, ciks, lo, hi):
    """Every state of the given issuers after each of their filings in [lo, hi): {cik: [(t, kind, accs)]}, where kind is
    the live alert kind if the issuer qualifies, else ''. Used to match journal alerts whose run time is unknown."""
    issuers, out = {}, defaultdict(list)
    for t, acc, cik, filed, doc, sym in docs:
        if t >= hi:
            break
        st = issuers.setdefault(cik, R.Issuer(cik))
        st.apply(acc, doc, filed, t.astimezone(ET).date())
        if cik in ciks and t >= lo:
            ev = st.evaluate()
            kind = "+".join(k for k, v in (("cluster", ev["cluster"]), ("big", ev["big"])) if v)
            out[cik].append((t, kind, sorted({a for b in ev["uniq"] for a in R.accs_of(b)})))
    return out


# ---------------------------------------------------------------- parity (ADR 0008 E5)

def pre_journal_alerted(ref=PRE_JOURNAL_STATE):
    """{cik: set(accessions)} the live bot had alerted before the journal existed (read-only git show)."""
    repo = Path(__file__).resolve().parent.parent
    raw = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:data/state.json"], capture_output=True, check=True).stdout
    return {str(c): set(v) for c, v in json.loads(raw.decode("utf-8")).get("alerted", {}).items()}


def journal(ref="origin/main"):
    """The live journal, read-only, from the repository (git show; nothing is checked out or written)."""
    repo = Path(__file__).resolve().parent.parent
    raw = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:data/journal.json"], capture_output=True, check=True).stdout
    return json.loads(raw.decode("utf-8"))["alerts"]


def live_rows(ref="d6f1158"):
    """The live bot's purchase rows at the first journaled run, by accession (read-only git show)."""
    repo = Path(__file__).resolve().parent.parent
    raw = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:data/state.json"], capture_output=True, check=True).stdout
    return {b["acc"]: b for b in json.loads(raw.decode("utf-8"))["buys"]}


def seen_without_row(accs, after, ref="origin/main"):
    """True if the live bot's first state after `after` + 1 day lists every accession as read ('seen') but stores no
    purchase row for any of them: bot/scan.scan_day skips a filing whose issuer is not in SEC's ticker map."""
    repo = Path(__file__).resolve().parent.parent
    log = subprocess.run(["git", "-C", str(repo), "log", "--reverse", "--format=%h", f"--after={(after + dt.timedelta(days=1)).isoformat()}",
                          ref, "--", "data/state.json"], capture_output=True, check=True).stdout.decode().split()
    if not log:
        return False
    raw = subprocess.run(["git", "-C", str(repo), "show", f"{log[0]}:data/state.json"], capture_output=True, check=True).stdout
    st = json.loads(raw.decode("utf-8"))
    seen = {a for v in st.get("seen", {}).values() for a in v}
    rows = {b["acc"] for b in st["buys"]}
    return all(a in seen for a in accs) and not (set(accs) & rows)


def parity(docs, alerts, sic, entries=None, live_state=True):
    """Journal alerts dated PARITY_FIRST..PARITY_LAST vs the reimplementation. A journal alert is *matched* if the
    reimplementation's state for that issuer had exactly the alert's accessions while qualifying, within its run
    window; otherwise it is *explained* only by a documented cause, or *unexplained*."""
    entries = journal() if entries is None else entries
    js = [e for e in entries if PARITY_FIRST.isoformat() <= e["date"] <= PARITY_LAST.isoformat()]
    known = {a for _, a, *_ in docs}
    lo = dt.datetime.combine(PARITY_FIRST - dt.timedelta(days=35), dt.time(), ET).astimezone(UTC)
    hi = dt.datetime.combine(PARITY_LAST + dt.timedelta(days=2), dt.time(), ET).astimezone(UTC)
    snaps = snapshots(docs, {str(e["cik"]) for e in js}, lo, hi)
    rows, matched_alerts = [], set()
    for e in js:
        cik, A = str(e["cik"]), sorted(set(e.get("accs") or []))
        d = dt.date.fromisoformat(e["date"])
        w_lo = dt.datetime.combine(d - dt.timedelta(days=4), dt.time(), ET).astimezone(UTC)
        w_hi = dt.datetime.combine(d + dt.timedelta(days=1), dt.time(), ET).astimezone(UTC)
        cand = [(t, k, accs) for t, k, accs in snaps.get(cik, []) if w_lo <= t < w_hi]
        status, cause = "unexplained", ""
        if any(accs == A and k for t, k, accs in cand):
            status = "matched"
        else:
            missing = [a for a in A if a not in known]
            near = min(cand, key=lambda c: len(set(c[2]) ^ set(A)), default=None)
            if missing:
                status, cause = "explained", f"accessions not in the reimplementation's inputs: {missing}"
            elif near and set(near[2]) < set(A) | set(near[2]) and near[1]:
                extra, lack = sorted(set(near[2]) - set(A)), sorted(set(A) - set(near[2]))
                if lack and not extra:
                    status, cause = "explained", f"live rows outside the acceptance-date window (aged out here): {lack}"
                elif extra and not lack:
                    status, cause = "explained", f"rows the live run had not read yet or had aged out: {extra}"
            elif near and not near[1]:
                cause = f"issuer does not qualify in the reimplementation (nearest state {near[2]})"
        for t, k, accs in cand:
            if k:
                matched_alerts.add((cik, tuple(accs)))
        rows.append({"journal_id": e["id"], "date": e["date"], "ticker": e["ticker"], "cik": cik, "kind": e["kind"],
                     "accs": A, "status": status, "cause": cause})
    jkeys = {(str(e["cik"])) for e in js}
    fpi = {r["cik"] for r in sic.iter_rows(named=True) if r["annual_form"] in ("20-F", "40-F")}
    unlisted = {r["cik"] for r in sic.iter_rows(named=True) if not r["tickers"]}
    live = live_rows() if live_state else {}
    only = []
    for a in alerts:
        d = a["t"].astimezone(ET).date()
        if not (PARITY_FIRST <= d <= PARITY_LAST) or a["cik"] in jkeys:
            continue
        cause = ("foreign private issuer: the live alert is name-only and not journaled" if a["cik"] in fpi else
                 "issuer has no current ticker: not in SEC's ticker map" if a["cik"] in unlisted else "")
        lrows = [live[x] for x in a["accs"] if x in live]
        if not cause and a.get("first_run") and len(lrows) == len(a["accs"]):
            ev = R.evaluate([dict(r, cik=a["cik"]) for r in lrows])
            if not (ev["cluster"] or ev["big"]):
                cause = ("window anchor: the live first run built these rows with its scan date as today, so only lines "
                         f"dated from the scan date - 30 counted (live values {[r['value'] for r in lrows]})")
        if not cause and live_state and seen_without_row(a["accs"], a["t"]):
            cause = "the live scan read the filing and stored no row: issuer not in SEC's ticker map at scan time"
        only.append({"t": a["t"].isoformat(), "cik": a["cik"], "kind": a["kind"], "accs": a["accs"], "cause": cause,
                     "first_run": a.get("first_run", False)})
    counts = {"journal_alerts": len(js), "matched": sum(r["status"] == "matched" for r in rows),
              "explained": sum(r["status"] == "explained" for r in rows),
              "unexplained": sum(r["status"] == "unexplained" for r in rows),
              "reimplementation_only": len(only), "reimplementation_only_explained": sum(bool(o["cause"]) for o in only)}
    return {"counts": counts, "journal": rows, "reimplementation_only": only}


def _ser(x):
    if isinstance(x, (dt.datetime, dt.date)):
        return x.isoformat()
    raise TypeError(type(x))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scalp_engine.insider_events")
    ap.add_argument("step", choices=["raw", "parity", "table"])
    a = ap.parse_args(argv)
    docs, st = load_docs()
    print(json.dumps(st))
    if a.step == "raw":
        events, alerts, stats = run_clock(docs)
        derived("i1_events_raw.json").write_text(json.dumps({"stats": st, "apply": stats, "events": events,
                                                             "alerts": alerts}, default=_ser) + "\n", encoding="utf-8")
        print(json.dumps({"events": len(events), "alerts": len(alerts), "apply": stats}))
    elif a.step == "parity":
        events, alerts, _ = run_clock(docs, seed=pre_journal_alerted())
        res = parity(docs, alerts, D.sic_frame())
        derived("i1_parity.json").write_text(json.dumps(res, indent=1, default=_ser) + "\n", encoding="utf-8")
        print(json.dumps(res["counts"], indent=1))
    else:
        from scalp_engine import insider_prices as P
        P.write_event_table(docs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
