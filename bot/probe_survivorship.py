"""Phase A.1 of the recommendation algorithm (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md
revision 6, section 7 A.1; plan docs/superpowers/plans/2026-10-10-recommendation-algorithm-phase-a1-claude.md):
item 2.02 coverage with 8-K only, universe-like stock-months defined without bars (FINRA average daily share volume x
the latest Form 4/5 transaction price), how many the rename rule recovers, f and b = f x 1 R.

Counts and coverage only. No return of a ticket or a stock is computed; the only files written are caches of raw
data (FINRA reports, Alpaca bars, SEC zips)."""
from bot import alpaca, common, shorts

CACHE = common.ROOT / ".cache" / "finra"


# ---------- FINRA ----------
def finra_report(date):
    """FINRA's consolidated short-interest report settled on `date` (exchange-listed classes) -> {FINRA symbol: slim
    row}, cached as gzip JSON."""
    path = CACHE / f"{date}.json.gz"
    got = alpaca._read(path)
    if got is None:
        got = shorts.report(date)
        alpaca._write(path, got)
    return got


def finra_spellings(symbol):
    """An insider-data symbol (BRK.B) -> its two FINRA spellings: shorts.norm's (BRK-B) and FINRA's own class-share
    form without a separator (BRKB)."""
    return symbol.replace(".", "-"), symbol.replace(".", "")


def finra_row(report, symbol):
    """The report's row for an insider-data symbol, or None."""
    return next((report[s] for s in finra_spellings(symbol) if s in report), None)


def volume_check(reports):
    """{date: report} -> how many rows carry FINRA's average daily share volume, and the reports where none does (the
    spec's stop condition: no volume field)."""
    rows = [r for rep in reports.values() for r in rep.values()]
    return {"reports": len(reports), "rows": len(rows), "rows with volume": sum(1 for r in rows if r.get("adv")),
            "reports without any volume": sorted(d for d, rep in reports.items()
                                                 if not any(r.get("adv") for r in rep.values()))}
