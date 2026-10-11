# Insider-cluster swing ticket — STEP 0 probe (2026-10-10)

Measurement only, under the blind rule: no mean, median, win rate or any signed statistic of returns was computed,
printed, logged or saved, for any subset. Script: `bot/probe_insider.py` (stdlib; run on GitHub Actions run
38034479955, where the keys exist; 462 s). No live bot behaviour, workflow schedule or strategy changed.

## a) Source: SEC Insider Transactions Data Sets — usable

- **Coverage:** quarterly zips `https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/
  {y}q{q}_form345.zip`, 45 quarters found, 2015Q1 → **2026Q1** (filings through 2026-03-31). 2026Q2 and 2026Q3 are not
  published yet, so the most recent ~6 months need EDGAR's daily indexes + Form 4 XML (the live scan's path).
- **Fields present** (2015Q4 schema; the same table names in every quarter read):

| Need (bot/form4.py) | Data set column | Present |
|---|---|---|
| Filing date | SUBMISSION.FILING_DATE | yes |
| Form type (4 vs 4/A) | SUBMISSION.DOCUMENT_TYPE | yes |
| Issuer symbol as filed | SUBMISSION.ISSUERTRADINGSYMBOL | yes |
| Transaction code | NONDERIV_TRANS.TRANS_CODE | yes |
| Acquired / disposed | NONDERIV_TRANS.TRANS_ACQUIRED_DISP_CD | yes |
| Shares, price | TRANS_SHARES, TRANS_PRICEPERSHARE | yes |
| Transaction date | TRANS_DATE | yes |
| Owner relationship, title | REPORTINGOWNER.RPTOWNER_RELATIONSHIP, RPTOWNER_TITLE | yes |
| Footnotes (10b5-1 text) and the line's footnote references | FOOTNOTES.FOOTNOTE_TXT, the `*_FN` columns | yes |
| The 10b5-1 checkbox (`aff10b5One`) | SUBMISSION.AFF10B5ONE | **not in 2015Q4**; present in later quarters (1,400 purchase filings carry it) |

- **Gaps against bot/form4.py / bot/scan.py** (the probe's approximation of the live rule):
  1. **Joint filings are not merged** (`form4.joint`): a fund and the director who sits on the board reporting the same
     trade can count as two distinct people. This can over-count clusters.
  2. **Form 4/A amendments are ignored** (only original Form 4s), where the live scan applies them.
  3. **The 10b5-1 box** exists only in later quarters; before it, plan detection relies on footnote text, exactly as
     form4.py does when the box is absent. 46,745 code-P lines were excluded as 10b5-1 plan lines.
  4. **Window anchor:** the live scan counts purchases whose trade date is within 30 days of the scan day; the probe
     uses the filing date as that day.
  5. **Universe:** the live scan keeps issuers in SEC's current ticker map; the probe uses the symbol as filed, which is
     broader (it includes later-delisted names) and is matched to Alpaca by that symbol (c).
  6. **The single ≥ $500k purchase rule** of the live alert is not part of this event definition (clusters only, as
     specified).

## b) Events, 2016-01-04 → 2026-03-31

Open-market purchases (code P, acquired, not a 10b5-1 plan line) by officers/directors on original Form 4s; event =
the first filing date on which ≥ 3 distinct officers/directors (first officer/director owner of each filing, by CIK)
total ≥ $100,000 with trade dates in the 30 days before; no second event for the issuer within 21 sessions.
171,933 purchase filings read (2015Q4 onward).

| Year | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (Q1) | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Events | 611 | 540 | 660 | 691 | 939 | 609 | 697 | 614 | 432 | 550 | 133 | 6,476 |

## c) Price coverage (Alpaca daily bar on the entry session, symbol as filed)

| Year | Events | Covered | Share | Missing |
|---|---:|---:|---:|---:|
| 2016 | 611 | 527 | 86.3% | 84 |
| 2017 | 540 | 476 | 88.1% | 64 |
| 2018 | 660 | 582 | 88.2% | 78 |
| 2019 | 691 | 628 | 90.9% | 63 |
| 2020 | 939 | 870 | 92.7% | 69 |
| 2021 | 609 | 558 | 91.6% | 51 |
| 2022 | 697 | 646 | 92.7% | 51 |
| 2023 | 614 | 571 | 93.0% | 43 |
| 2024 | 432 | 384 | 88.9% | 48 |
| 2025 | 550 | 498 | 90.5% | 52 |
| 2026 | 133 | 124 | 93.2% | 9 |

Entry session = the first session after the filing date. 2,913 symbols queried. The missing events (612 in all) were
not split further into delisted versus renamed: the probe only knows the symbol as filed has no bar that day. Coverage
is lowest in 2016–2018, consistent with delisted and renamed names (the survivorship pattern of spec 14.0 c).

## d) Dispersion of r (blind)

r = close of the h-th session after entry / entry-session open − the same ratio for SPY; split-adjusted bars.

| Horizon | n | Entry months | SD | SD clustered by entry month | Design effect | 99th pct of \|r\| | Lost (no bar at the horizon) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 21 sessions | 5,852 | 124 | 0.206 | 0.594 | 8.28 | 0.654 | 12 |
| 63 sessions | 5,824 | 124 | 1.205 | 1.336 | 1.23 | 1.202 | 40 |

- **21 sessions:** strong clustering. Events in the same month move together beyond SPY (design effect 8.3), so the
  effective sample is about 5,852 / 8.3 ≈ 700 independent observations, not 5,852.
- **63 sessions:** the SD (1.205) exceeds the 99th percentile of |r| (1.202), so a handful of extreme values dominate
  it. These are likely data errors (a reused symbol or a missed split) rather than returns. They must be found and
  handled by a rule registered before any return is looked at; until then, the 63-session SD is not usable for
  planning.

## e) Costs (fixed-seed sample of 200 covered events, seed 7)

- **Quoted spread at 09:35 on the entry session, % of the mid:** median 0.80%, 75th percentile 1.74%, 90th
  percentile 2.72%. 47 of the 200 (24%) had no usable SIP quote in the 60 seconds before 09:35.
- **20-day average dollar volume before entry:** median $3.57M, 10th percentile $0.26M (n 181 with 20 prior bars).
- A round trip pays about the full quoted spread: for the median event about 0.8%, and 2.7% at the 90th percentile.

## f) Requests and runtime

48 SEC requests (the quarterly zips), 743 Alpaca requests (calendar, split-adjusted daily bars in chunks of 100
symbols, 200 quotes), 462 seconds on one GitHub runner.

## What this means for section 15 / 15.1 (derived from dispersion and counts only)

With SD clustered by entry month as 15.1 requires, the smallest planning edge per trade that can be confirmed within
E evidence years is μ_min = 2.49 × SD_c / √(N × E), with N ≈ 571 covered events a year (5,852 over 10.25 years):

| Horizon | SD_c | E = 7.5 years (a 6-year unseen holdout + 1.5 forward) | E = 1.5 years (forward only) |
|---|---:|---:|---:|
| 21 sessions | 0.594 | 2.3% net of SPY and costs per trade | 5.0% |
| 63 sessions | 1.336 (outlier-inflated) | 5.1% | 11.4% |

Against these, the median round-trip spread is 0.8%. Note: the 2016–2021 period would be an unseen holdout for this
rule set only if no one looks at its returns before the rule is frozen; this probe kept it blind.

## Open items before any design

1. Merge joint filings and apply 4/A amendments (as form4.py does), then re-count events.
2. A pre-registered rule for bad prices (reused symbols, missed splits) before r is used for anything.
3. Map symbols as filed to Alpaca symbols for renamed issuers (the 612 missing events), or report the bias.
4. 2026Q2 onward from EDGAR daily indexes (the data set lags about two quarters).
