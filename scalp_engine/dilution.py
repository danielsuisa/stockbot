"""dilution_filing@2.0.0 (ADR 0004 section 3): an S-1 / S-3 family registration or a 424B prospectus other than 424B2,
accepted in (t - 30 days, t - 60 s]. A symbol without a CIK is 'unknown' and FAILS the filter (fail closed).
v1 (scalp_engine.features.filing_flags, 424B2 included, unknown passed) is unchanged and still describes P2 output."""
import datetime as dt
import re

import polars as pl

FORMS_V2 = r"^(S-1|S-3)(/A|ASR|MEF)?$|^424B[1345678]$"
FILING_DELAY = dt.timedelta(seconds=60)
WINDOW = dt.timedelta(days=30)
_FORMS = re.compile(FORMS_V2)


def dilution_v2(filings, cik_of, symbols, times):
    """Per (symbol, t): dilution_v2 in {'true', 'false', 'unknown'}, pass_dilution_v2 (only 'false' passes), and the
    availability of the latest filing used (None if none)."""
    by_cik = {}
    for cik, at, form in filings.select("cik", "event_at", "form").iter_rows():
        if _FORMS.match(form):
            by_cik.setdefault(cik, []).append((at, at + FILING_DELAY))
    rows = []
    for s in symbols:
        cik = cik_of.get(s)
        for t in times:
            if cik is None:
                rows.append((s, t, "unknown", False, None))
                continue
            hit = [fa for at, fa in by_cik.get(cik, ()) if fa <= t and at > t - WINDOW]
            rows.append((s, t, "true" if hit else "false", not hit, max(hit) if hit else None))
    return pl.DataFrame(rows, schema={"symbol": pl.Utf8, "t": pl.Datetime("us", "UTC"), "dilution_v2": pl.Utf8,
                                      "pass_dilution_v2": pl.Boolean, "dilution_avail": pl.Datetime("us", "UTC")},
                        orient="row")
