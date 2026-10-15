"""Pre-registered $3–$20 premarket replay. Locked on 2026-10-15, before this run.

Nothing in this module places or transmits an order. A GO is a paper-trade result, not a merge and not
permission to trade. The listener is unchanged. This report is not the sub-$5 book.

In-sample is 2016-01-01 through 2022-12-31. Out-of-sample is 2023-01-01 through the last Alpaca session
before the run's New York date. Only that window is gated, and the gate is the $460 whole-share book.
An incomplete download is NO-GO. The rules are in bot/premarket_mid.py and
docs/backtest/premarket-mid-prereg-2026-10-15.md.
"""
import csv
import datetime as dt
import json
import traceback
from pathlib import Path

from bot import alpaca
from bot.desk_gate import YEARS, judge, tstat
from bot.premarket_catalyst import (
    DESK_MIN_POSITION, GAPS, blacklist_summary, desk_summary, halt_summary, index_assets, split_sane, universe,
)
from bot.gates import verdict as gate_verdict
from bot.premarket_catalyst_backtest import (
    NY, OOS, START, WARM, _candidates, _end, _fee_side, _fmt, _pass_rvol, _play, _probe, _queue,
    _result, _screen, corporate_actions, daily, reverse_splits,
)
from bot.premarket_mid import MID_HI, MID_LO, MID_LULD, mid_price_ok, score_priced
NEWS_CACHE = "news-mid"


def _gaps():
    """The shared holes, with this book's price band named in place of the $0.50–$5 line."""
    note = (
        "This book is the prior close from $3 through $20, both ends inclusive. "
        "The $0.50–$5 book is a separate report. The band is split-adjusted, so a later split moves an old "
        "day into or out of it."
    )
    return [note if gap.startswith("The $0.50") else gap for gap in GAPS]


def _sens_table(rows):
    """Out-of-sample mean net R. Under $5 the side rates differ. At $5 and above they repeat."""
    oos = [r for r in rows if r["day"] >= OOS]
    lines = ["| Fallback | Out-of-sample trades | Mean net R | t |", "|---|---:|---:|---:|"]
    for label, key in (("1% under $5, or the $5+ model (the gate)", "net_r"),
                       ("0.5% under $5", "net_r_0_5"), ("2% under $5", "net_r_2")):
        xs = [r[key] for r in oos if r.get(key) is not None]
        mean = sum(xs) / len(xs) if xs else None
        lines.append(f"| {label} | {len(xs)} | {_fmt(mean)} | {_fmt(tstat(xs), '{:.2f}')} |")
    if not rows:
        lines += ["", "This download had no fills, so the three columns are the same empty book."]
    return "\n".join(lines)


def _variant_lines(result, rows):
    """Reverse-split book and the flags that were not tested."""
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
                  f"The {counts.get('rvol', 0):,} relative-volume passers were not joined to the reverse-split "
                  "file. The recorded trade list has nothing to exclude."]
    news = counts.get("news") or 0
    lines += ["", "## ATM, S-3 full text, and going concern", ""]
    if news:
        lines.append(
            "Not tested. The locked submissions screen for S-1, 424B, and S-3 / S-3ASR did run. An "
            "at-the-market flag beyond that form list, and a going-concern flag, need the EDGAR full-text "
            "search. That search was not run."
        )
    else:
        lines.append(
            "Not tested. An at-the-market flag and a going-concern flag need the EDGAR full-text search. "
            "That search was not run. No name had a primary-wire headline, so the locked submissions screen "
            "was not reached either. The form list is unchanged."
        )
    return lines


def _halt_lines(rows):
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
        lines += ["", "The recorded book has no fills, so there is no realized loss to set against the planned stop."]
    else:
        lines.append("No open trade contained a gap of 5 minutes, so there is no halt fill to compare "
                     "with the planned stop.")
    return lines


def _desk_lines(rows):
    """Desk size beside the gate. Every name in this book uses the 10% band, including a $3 print."""
    summary = desk_summary(rows, band=MID_LULD)
    pct = "not defined on an empty signal list" if summary["pct"] is None else f"{summary['pct']:.1%}"
    lines = [
        "## Desk size",
        "",
        "Reported beside the main book. The gate still buys the whole shares of $460. This size is the "
        "smaller of 1% of the account at the stop and 2% of the account at the worst case. Worst case per "
        f"share is the larger of the stop distance and 10% of the price. The 10% band is forced for every "
        f"name in this book, including a print of exactly $3. A position under ${DESK_MIN_POSITION:.0f} is skipped. "
        "This count cannot flip the verdict.",
        "",
        f"Signals that survive: {summary['kept_n']} of {summary['n']} ({pct}).",
        "",
        "When the 10% band binds, the position is 2% of $460 divided by 10%, which is $92. When the stop "
        "is wider than 10%, the 1% leg binds and the position is $4.60 divided by the stop as a fraction of "
        "price. That is under $50 once the stop is wider than 9.2% of the entry.",
    ]
    if summary["n"] == 0:
        lines += ["",
                  "The recorded book has no entry signals, so this percentage is not defined."]
    else:
        lines += ["",
                  "The desk size is a count of signals. It is not a second filled book."]
    return lines


def render(result):
    rows = result.get("trades") or []
    oos = [r for r in rows if r["day"] >= OOS]
    ins = [r for r in rows if r["day"] < OOS]
    g = judge(oos, YEARS)
    counts = result.get("counts") or {}
    _ignored, sample = _queue(rows, g["go"] and result.get("complete"), "T3 $3–$20 book")
    verdict = gate_verdict("t3-mid")
    lines = [
        f"# Premarket catalyst, $3–$20 — {result['run']}",
        "",
        "Separate from the sub-$5 book. The prior close is $3 through $20, both ends inclusive. The screen, "
        "the entry, the stop, and the four-check gate are otherwise the same rule. Long only, one name a day, "
        "$460, whole shares. This replay does not place or transmit an order.",
        "",
        "## Required sample",
        "",
        *sample,
        "",
        "The checks under the verdict are the book's own gate. When the sample is short they stay on the page "
        "and they are not the queue verdict.",
        "",
        f"Verdict: **{verdict}**.",
        "",
    ]
    for label, ok in g["checks"]:
        lines.append(f"- {'pass' if ok else 'fail'}: {label}")
    if not result.get("complete"):
        lines.append("- fail: the download finished")
    lines += ["",
              f"Window asked for: in-sample {START} through 2022-12-31, out-of-sample {OOS} through "
              f"{result.get('end')}. First split-adjusted daily bar: {result.get('first_bar')}.",
              "",
              f"Price band: prior close ${MID_LO:.2f} through ${MID_HI:.2f}, both ends inclusive. "
              f"$2.99 and $20.01 are out. The sub-$5 book remains $0.50 through under $5 and is a different report.",
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
        mean = sum(xs) / len(xs) if xs else None
        win = sum(r["net_r"] > 0 for r in part) / len(part) if part else None
        bare = [r["net_r_no_commission"] for r in part]
        bare_mean = sum(bare) / len(bare) if bare else None
        lines.append(
            f"| {name} | {len(part)} | {_fmt(mean)} | {_fmt(judge(part)['t'] if part else None, '{:.2f}')} | "
            f"{_fmt(show['p95'] if show else None)} | {_fmt(win, '{:.1%}')} | {_fmt(bare_mean)} |"
        )
    lines += ["", "| Year | Trades | Mean net R |", "|---|---:|---:|"]
    for year in ("2016", "2017", "2018", "2019", "2020", "2021", "2022", *YEARS):
        part = [r["net_r"] for r in rows if r["day"][:4] == year]
        lines.append(f"| {year} | {len(part)} | {_fmt(sum(part) / len(part) if part else None)} |")
    inactive = [r for r in rows if r.get("listed") == "inactive"]
    lines += ["",
              f"Trades in names Alpaca now marks inactive: {len(inactive)} of {len(rows)}.",
              "Commission is IBKR Pro Fixed on each order: $0.005 a share, minimum $1.00, maximum 1% of "
              f"that order's trade value. Orders on which the $1 minimum is the smaller fee: "
              f"{_fee_side(rows, True)} of {len(rows) * 2}. Orders on which the 1% cap is the smaller fee: "
              f"{_fee_side(rows, False)} of {len(rows) * 2}.",
              "",
              "## Cost model",
              "",
              "The cost follows the entry print, not the prior close. A print strictly under $5 uses the "
              "sub-$5 model: the entry fills at the ask and the exit at the bid when both SIP quotes exist, "
              "and a missing quote raises the entry print by 1% and lowers the exit print by 1%. That is 1% "
              "on each side. The share count is the whole shares of $460 at the price paid. The gate uses "
              "that 1% fallback. The same prints at 0.5% and at 2% per side are a sensitivity check. They "
              "do not flip the verdict.",
              "",
              "A print at $5 or above keeps the full quoted spread, charged once, from the entry minute. "
              "The exit quote is not a second spread. A missing entry quote uses the 1% round-trip haircut, "
              "half on the way in and half on the way out. It does not use 1% on each side. The share count "
              "is the whole shares of $460 at the printed entry. Those names repeat the same net R in the "
              "0.5% and 2% columns.",
              "",
              "Commission is IBKR Pro Fixed on each order, from the shares and that order's fill price: $0.005 a "
              "share, minimum $1.00, maximum 1% of trade value. A $50 to $100 penny order pays the 1% cap. "
              "A $460 order in a name above a few dollars pays the $1 minimum. It is not a flat percent. "
              "Net R divides the dollar result by the printed entry-to-stop risk.",
              "",
              _sens_table(rows),
              "",
              f"Quoted spread or quoted bid-ask: {sum(r.get('cost_mode') == 'quote' for r in rows)}. "
              f"Round-trip haircut: {sum(r.get('cost_mode') == 'haircut' for r in rows)}. "
              f"Percent each side: {sum(r.get('cost_mode') == 'slip' for r in rows)}.",
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
    lines.append("This count is the same nine-name probe. This book is still the $3–$20 band.")
    for row in probe:
        lines.append(f"- {row['symbol']} ({row['last']}): "
                     f"{'in' if row.get('in_assets') else 'NOT in'} the asset file"
                     f"{', status ' + row['status'] if row.get('status') else ''}, {row.get('bars')} raw bars.")
    lines.append("")
    for gap in _gaps():
        lines.append(f"- {gap}")
    lines += ["", "## Verdict", "", f"- Premarket $3–$20: {verdict}.", ""]
    return "\n".join(lines)


def _write(result):
    stem = Path(f"docs/backtest/premarket-mid-{result['run']}")
    stem.parent.mkdir(parents=True, exist_ok=True)
    text = render(result)
    if not result.get("complete", True):
        text = text.replace("**GO**", "**NO-GO**").replace("Premarket $3–$20: GO.", "Premarket $3–$20: NO-GO.")
    stem.with_suffix(".md").write_text(text, encoding="utf-8")
    fields = ["day", "t", "rvol", "entry", "stop", "exit", "kind_exit", "fill_minute", "shares", "net_r",
              "net_r_short", "net_r_no_commission", "pnl", "cost_mode", "listed", "raw_close", "adj_close",
              "halt_affected", "planned_loss", "realized_loss"]
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fields, extrasaction="ignore")
        writer.writeheader()
        for row in result.get("trades") or []:
            writer.writerow(row)
    slim = {k: v for k, v in result.items() if k != "trades"}
    slim["oos"] = judge([r for r in result.get("trades") or [] if r["day"] >= OOS])
    stem.with_suffix(".json").write_text(json.dumps(slim, indent=1, default=str), encoding="utf-8")
    print(f"wrote {stem}.md", flush=True)


def _used_reverses(trades, reverses):
    """Reverse-split dates for symbols that actually traded. Passers are not joined."""
    out = {}
    for row in trades or []:
        sym = row.get("symbol") or row.get("t")
        out[sym] = sorted(reverses.get(sym) or [])
    return out


def run():
    run_day, sessions, cal = _end()
    end = sessions[-1]
    assets = alpaca.assets()
    symbols, indexed = universe(assets)
    full = index_assets(assets)
    inactive = {s for s in symbols if indexed[s].get("status") == "inactive"}
    print(f"mid universe {len(symbols)} inactive {len(inactive)}", flush=True)
    splits = corporate_actions(WARM, end, symbols)
    reverses = reverse_splits(WARM, end, symbols)
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
    cands, dropped = _candidates(adj, sessions, splits, mid_price_ok)
    print(f"mid band {len(cands)} split days skipped {dropped} aapl {aapl_ok}", flush=True)
    if not aapl_ok:
        result = _result(run_day, end, first, symbols, inactive, with_bars, aapl, dropped, probe,
                         {"band": len(cands)}, [], False)
        result["reverses"] = {}
        return result
    passed = _pass_rvol(cands, sessions)
    print(f"mid rvol {len(passed)}", flush=True)
    ready, counts = _screen(passed, {s: indexed[s].get("status") for s in symbols}, NEWS_CACHE)
    counts["band"] = len(cands)
    trades = _play(ready, cal, raw, adj, score_priced)
    result = _result(run_day, end, first, symbols, inactive, with_bars, aapl, dropped, probe, counts, trades, True)
    result["reverses"] = _used_reverses(trades, reverses)
    return result


def main():
    try:
        result = run()
    except Exception:
        traceback.print_exc()
        run_day = dt.datetime.now(NY).date().isoformat()
        stub = {"run": run_day, "end": None, "first_bar": None, "universe": 0, "inactive_names": 0,
                "inactive_with_bars": 0, "aapl": None, "split_days": 0, "probe": [], "counts": {},
                "trades": [], "complete": False, "requests": alpaca.REQUESTS[0], "reverses": {}}
        _write(stub)
        return 1
    _write(result)
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
