# Recommendation algorithm: phase A (data sources and coverage)

Spec: `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md`, section 7 A.
Plan: `docs/superpowers/plans/2026-10-10-recommendation-algorithm-phase-a.md`.
Code: `bot/probe_ensemble.py`, `bot/probe_ensemble_data.py`, `tests/test_probe_ensemble.py`.
Raw output: `docs/backtest/ensemble-phaseA-2026-10-10.json` (full run) and
`docs/backtest/ensemble-phaseA-2026-10-10-checks.json` (pre-run checks).

**Counts and coverage only.** No return of a ticket or a stock is computed, logged or written. The signals' own
backward inputs (S2's close / previous close, S3's D−147→D−21 rank, S4's 5-session move) are ranked in memory and
dropped. The only files written are raw-data caches (SEC zips, Alpaca bars).

## The run

| | |
|---|---|
| Pre-run checks | GitHub Actions run 38049699735 (code `f075bb3`), passed |
| Full run | GitHub Actions run 38064220744 (code `f377114`), passed, 5,523 s |
| Earlier full runs | 38051218575 stopped at the end on an S1 sort bug (fixed in `8c20aa5`, no output kept); 38057369680 (code `8c20aa5`) passed and was superseded after the independent review: normalising zero-padded issuer CIKs in S1 changed one S1 firing (2021: 552 → 551), nothing else |
| Signal window | 2017-01-03 .. 2026-03-31 (bars from 2016-01-04) |
| Requests | Alpaca 3,171; SEC quarterly zips 48; EDGAR index pages 23,605 (0 missing); submissions.zip once |
| Tests | 473 offline tests, all passing (`tests/test_probe_ensemble.py`: 33) |

## Pre-run checks (sample of 400 symbols + AAPL, GE)

| Check | Result |
|---|---|
| Look-ahead | Universe, S2, S3 and S4 states identical on 2017-03-14, 2019-08-01, 2021-12-16 with every bar after D removed |
| Acceptance time zone | submissions.zip's `acceptanceDateTime` is inconsistent across filers (Apple and JPMorgan: +10 h from the EDGAR index page; Microsoft: +5 h, i.e. UTC; 30 filings 2017–2026). Owner's decision: the time comes from the EDGAR index page (Eastern), fetched only where it can change a result |
| CIK → symbol | AAPL → 320193, FB and META → 1326801; 815 of 14,035 symbols were filed under more than one CIK (mapped point in time) |
| Raw vs split-adjusted | AAPL 2020-08-31 and GE 2021-08-02: the raw close moves by the split factor, the split-adjusted close stays continuous, one segment |
| Item 2.02 coverage (sample) | 677 / 723 stock-years = 93.6% |

## Item 2.02 coverage and the S2 decision

Threshold: 90% overall (spec section 2), fixed before the run.

| Year | Universe stock-years | With an item 2.02 8-K | Share |
|---|---:|---:|---:|
| 2017 | 2,149 | 2,067 | 96.2% |
| 2018 | 2,224 | 2,152 | 96.8% |
| 2019 | 2,176 | 2,094 | 96.2% |
| 2020 | 2,392 | 2,252 | 94.2% |
| 2021 | 2,766 | 2,633 | 95.2% |
| 2022 | 2,526 | 2,426 | 96.0% |
| 2023 | 2,339 | 2,258 | 96.5% |
| 2024 | 2,382 | 2,304 | 96.7% |
| 2025 | 2,531 | 2,408 | 95.1% |
| 2026 (Q1) | 2,455 | 2,156 | 87.8% |
| **Overall** | **23,940** | **22,750** | **95.0%** |

**S2 decision: 95.0% ≥ 90%, so S2 keeps the 8-K item 2.02 condition.** The coverage year is the release's filing
year. 2026 covers January to March only, so issuers whose earnings release falls later in the year are not yet covered.

## Universe per year

| Year | Sessions | Average daily size | Distinct symbols |
|---|---:|---:|---:|
| 2017 | 251 | 1,590.9 | 2,149 |
| 2018 | 251 | 1,703.1 | 2,224 |
| 2019 | 252 | 1,645.8 | 2,176 |
| 2020 | 253 | 1,709.0 | 2,392 |
| 2021 | 252 | 1,933.9 | 2,766 |
| 2022 | 251 | 1,869.4 | 2,526 |
| 2023 | 250 | 1,745.0 | 2,339 |
| 2024 | 252 | 1,793.5 | 2,382 |
| 2025 | 250 | 1,906.9 | 2,531 |
| 2026 (Q1) | 61 | 2,033.3 | 2,455 |

## Firings per signal per year

| Year | S1 (all) | S1 (in universe) | S2 (decided rule, in universe) | S3 | S4 |
|---|---:|---:|---:|---:|---:|
| 2017 | 503 | 137 | 693 | 1,353 | 664 |
| 2018 | 606 | 176 | 847 | 1,314 | 1,179 |
| 2019 | 642 | 163 | 869 | 1,502 | 862 |
| 2020 | 892 | 272 | 622 | 1,345 | 1,848 |
| 2021 | 551 | 122 | 694 | 1,719 | 2,190 |
| 2022 | 661 | 158 | 793 | 1,305 | 2,391 |
| 2023 | 586 | 159 | 999 | 1,460 | 1,340 |
| 2024 | 395 | 91 | 1,103 | 1,526 | 1,462 |
| 2025 | 515 | 183 | 1,056 | 1,471 | 1,893 |
| 2026 (Q1) | 128 | 50 | 303 | 360 | 850 |
| **Total** | **5,479** | **1,511** | **7,979** | **13,355** | **14,679** |

- S1 is counted by issuer from the insider data; 1,511 of 5,479 firings (28%) fall on a symbol in the universe on the firing session (a filing on a non-session day is checked against the next session); 135 have no usable symbol.
- S2 for information: 8-K rule over all symbols with bars 15,698; fallback rule (no 8-K condition) in universe
  26,750, over all symbols 155,203.
- S3 and S4 are counted in the universe (their states are defined on it).

## Warm-up (owner's decision 2026-10-10)

States are observed from 2017-01-03, the first session the universe can hold a symbol (252 sessions of bars from
2016-01-04). Earlier sessions are unknown, not "outside", so an entry counts only after the required observed sessions
outside the state.

| Signal | Sessions outside required | Warm-up | Entries not counted |
|---|---:|---|---:|
| S3 | 21 | 2017-01-03 .. 2017-02-01 (21 sessions) | 364 |
| S4 | 1 | 2017-01-03 (1 session) | 4 |

## Interpretations approved by the owner (2026-10-10)

- S2's volume average is the 20 sessions before D (D excluded).
- S4's "no S2 reaction session" means no item 2.02 reaction session in D−4..D, whether or not S2 fired.
- S3/S4 lags count the symbol's own sessions.
- Coverage years are filing-date years.
- The universe's 20-session dollar volume includes D.

Not settled by the owner: whether S1 and S2 firings must be in the universe. Both counts are reported (S1 all / in
universe; S2 in universe / all).

## Data gaps

| | |
|---|---|
| Insider data sets | 2015Q1 .. 2026Q1 (45 quarters) |
| Symbols as filed on Forms 3/4/5 | 14,035 |
| With Alpaca bars | 10,508 |
| Without bars | 3,527 |
| Series broken by the bad-price rule | 480 |
| Filings with an unusable symbol | 41,596 |
| S1 purchase lines | 206,530 |

## Limitations

1. **Survivorship.** 3,527 filed symbols have no Alpaca bars (delisted, renamed, OTC or not covered). The universe can
   only hold symbols Alpaca serves today under the same ticker, so delisted names are under-represented.
2. **Symbol reuse.** Bars are keyed by today's ticker; a ticker reused by another company joins two histories unless the
   bad-price rule breaks the series. The insider filing condition is point in time, the bars are not.
3. **Acceptance times.** Reaction sessions rely on the EDGAR index page's "Accepted" time; it was read for the 23,605
   releases where it could change a result and was never missing. The bulk field was not used.
4. **2026 is a partial year** (Q1): its coverage (87.8%) and counts are not comparable with full years.
5. **Unusable symbols.** 41,596 insider filings carry a symbol that could not be normalised (blank, "NONE", or not a
   listed-share pattern); their issuers count only through other filings.
6. **Data feed.** Alpaca SIP daily bars, raw and split-adjusted (dividends not adjusted).

7. **Form 4/A amendments are ignored** (S1 reads original Form 4s only), as spec section 2 requires to be stated.
8. **8-K/A filings count as item 2.02 releases** (as in the plan; the spec says "8-K"). A late amendment adds a
   reaction session: S2 can fire on it and S4 can be suppressed by it; coverage rises slightly.
9. **Symbols without bars vs the estimate.** 3,527 of 14,035 filed symbols (25%) have no bars, against roughly 10%
   estimated before phase A; symbols as filed include strings that pass the pattern without being listed shares, and
   share classes not named on Forms 3/4/5 never enter the universe.
10. **S2's previous close** is the previous bar: after a gap of up to 10 sessions (no new segment) the 1.05 jump spans
    more than one session.
11. **S1 can fire again** after its 21-session block while the 30-day condition still holds; the spec's "first filing
    date on which" could also be read as requiring the condition to become true anew. An S1 filing can be after the
    close.

## Open questions for the owner

- 8-K/A as releases (limitation 8), S2's previous close after a gap (10), S1 re-firing (11), and whether S1/S2 must be
  in the universe. None changes the S2 coverage decision.

## Independent review

A reviewer who did not write the code reviewed the code, tests, coverage and this report (2026-10-10): no critical
finding; no look-ahead path; no breach of the counts-only rule; every number matched the JSON; no finding invalidates
the results or the S2 decision. Fixed after the review: S1 issuer and owner CIKs normalised; the 90% decision moved to
`keeps_8k()` with tests at the edge; tests for coverage by filing year; S1 in universe per year; the plan records the
index-page decision; the pre-run checks JSON committed; limitations 7–11 added.

## Next

Phase B is not started. It needs the owner's explicit approval.
