# Short-squeeze screener — design

Date: 2026-09-27 · Status: approved to build (owner: "do all till finish") · Branch: `squeeze-screener`

## 1. Purpose

A self-use screener inside stockbot that surfaces stocks with short-squeeze potential **before** the move, plus a
gamma-squeeze (options) read on the finalists. Research alerts only — it never trades.

Owner's answers (2026-09-27): module inside stockbot (1A) · pre-market ranked list + `/squeeze TICKER` (2A) ·
options = gamma-fuel score, no contract picking (3A) · free data only (4A) · **"exploded" = the highest price in the
10 sessions from entry reaches ≥ 1.5× entry (5B)**. Approach B: backtest first, build live only on a GO.

Out of scope: order placement, contract/size picking, intraday alerts, paid data, IBKR API.

## 2. Measured data sources (probed 2026-09-27)

| Layer | Source | Status |
|---|---|---|
| Short interest | FINRA API `otcMarket/consolidatedShortInterest` (POST, no key, 5,000 rows/page, `domainFilters` on `marketClassCode`) | ✅ 210 settlement dates since 2017-12-29; 8,729 rows for NNM+SC+NYSE+AMEX on 2026-09-15 |
| Shares outstanding | SEC frames `dei/EntityCommonStockSharesOutstanding/shares/CY{Y}Q{q}I` | ✅ 4,417 filers for CY2026Q2I |
| Float (finalists only) | Yahoo `v10 quoteSummary defaultKeyStatistics` with cookie+crumb | ✅ unofficial → display only, SEC fallback |
| Daily prices | Yahoo `v8 chart` (already `bot/market.py`) | ✅ 60 tickers / 3 s with 8 threads, ~23 KB gz each since 2020 |
| Options chain | CBOE `cdn.cboe.com/api/global/delayed_quotes/options/{T}.json` | ✅ OI, volume, IV, delta per contract; `iv30` |
| Borrow fee / availability | IBKR `ftp3.interactivebrokers.com` user `shortstock`, `usa.txt` | ⚠ unverified (FTP blocked from the dev sandbox) → probe on Actions in phase 3 |

Rejected: Nasdaq short-interest endpoint (Nasdaq-listed only), iBorrowDesk (no response), FINRA daily short-sale
volume (not short interest; misleading).

## 3. Phases

1. **Backtest** — data layer + scoring (fuel + trigger) + `bot/backtest.py` → report
   `docs/backtest/squeeze-<date>.md` with a GO / NO-GO verdict. **NO-GO ends the project** (report only).
2. **Live** (GO only) — daily pre-market list to Telegram, `/squeeze`, `/squeeze TICKER`, `/squeeze stats`,
   squeeze journal with the 10-session hit rule, `.github/workflows/squeeze.yml`.
3. **Borrow + options** (GO only) — one Actions probe of the IBKR file; CBOE gamma-fuel and borrow for the shown
   names. Displayed and journaled as **"unvalidated"** — they never change the rank until the journal validates them.

## 4. Architecture

Stdlib only, one purpose per file, existing patterns (`common.fetch`, `market.chart`, `journal` save/load style).

| File | Purpose | Depends on |
|---|---|---|
| `bot/shorts.py` | FINRA short interest (dates, one date, latest), SEC shares-outstanding frames, point-in-time lookup, Yahoo float (finalists), IBKR borrow (phase 3) | `common` |
| `bot/squeeze.py` | Pure feature + scoring + ranking functions (shared by backtest and live); live run, Hebrew message, `/squeeze` report, squeeze journal | `shorts`, `market`, `common` |
| `bot/options.py` | CBOE chain → gamma-fuel metrics (phase 3) | `common` |
| `bot/backtest.py` | Downloads + disk cache (gitignored `.cache/squeeze/`), day-by-day replay with `squeeze` functions, grid selection in-sample, verdict out-of-sample, Markdown report | `shorts`, `squeeze` |
| `.github/workflows/squeeze.yml` | Mon–Fri pre-market run, own concurrency group, commit-with-retry, failure alarm | — |

Separate workflow so a squeeze failure can never delay or break the insider scan. `market.py`, the insider
journal and all existing commands keep their behavior.

## 5. Point-in-time rules (no look-ahead)

- **Trading calendar**: SPY's Yahoo sessions.
- **Short interest usable on session D** only if D ≥ settlement date + 8 business days (FINRA disseminates after the
  close of business day 7).
- **Shares outstanding usable on D** only if D ≥ the fact's `end` date + 15 calendar days (dei facts are dated at
  the filing's cover date; 15 days covers the filing delay). Latest usable fact wins.
- **Price/volume features** use bars through session D−1 only.
- **Entry** = D's open. **Outcome** window = sessions D … D+9 of the global calendar.

## 6. Universe, features, scores

**Eligible on D**: FINRA class ∈ {NNM, SC, NYSE, AMEX}; symbol maps to an SEC CIK and is not a warrant / unit /
right / preferred (`common.noncommon`); usable SI and shares outstanding > 0; Yahoo bars exist with a bar on D−1;
close[D−1] ≥ $1; mean dollar volume over the last 21 sessions ≥ $2M (`market.LOW_LIQUIDITY`). Plausibility (added 2026-09-27 after the first run exposed SEC scale errors, e.g. LAES reported with 100 shares → short interest 88,000 %): an SEC share figure below 100,000 is unusable, and a stock whose short interest exceeds 150 % of shares outstanding is dropped (a stale or mis-scaled denominator; GME's January-2021 peak was ~100–140 %).

**Features**
- `si_pct` = short shares / shares outstanding · `dtc` = FINRA days-to-cover · `si_chg` = FINRA change % since the
  prior report.
- `rvol` = volume[D−1] / mean(volume[D−21 … D−2]) · `ret5` = close[D−1] / close[D−6] − 1 ·
  `brk` = close[D−1] / max(close[D−21 … D−2]).

**Scores** (percentile ranks, 0–1, within the candidate set of the day)
- Candidate set C = eligible ∧ `si_pct ≥ G_si`.
- `fuel` = mean(rank(si_pct), rank(dtc)) · `trigger` = mean(rank(rvol), rank(ret5), rank(brk)).
- `score` = w·fuel + (1−w)·trigger, among C members with `rvol ≥ G_rv`. **List = top 10 by score** (fewer if C is
  small).
- Pre-declared grid (27 variants): w ∈ {0.3, 0.5, 0.7} × G_si ∈ {0.10, 0.15, 0.20} × G_rv ∈ {1.0, 1.5, 2.0}.

Live needs Yahoo only for C (the SI gate is applied from FINRA + SEC first), so the daily run is a few hundred
Yahoo calls, not thousands — and the backtest applies the identical funnel.

## 7. Backtest

- **Periods**: in-sample 2018-02-01 … 2023-12-31 (FINRA history starts 2017-12-29; this extends the originally
  quoted June 2020 start — more tuning data, the verdict period is unchanged); out-of-sample (verdict)
  2024-01-02 … the last session with a full 10-session outcome window.
- **Hit** = max(high[D … D+9]) ≥ 1.5 × open[D]. Also recorded: `dd` = min(low[D … D+9]) / open[D] − 1 and
  `r10` = close[D+9] / open[D] − 1 (last close inside the window if D+9 has no bar). A stock-day is skipped when it
  has no open on D or when the calendar does not yet reach D+9; a halt inside the window just uses the bars that exist.
- **Repeat picks**: per variant, a ticker picked on D is not a new pick on D+1 … D+9.
- **Baseline** = hit rate over all eligible stock-days. **Diagnostics**: hit rate of all C stock-days (SI gate
  alone), fuel-only top 10 (rank by fuel in C), trigger-only top 10 (rank by trigger among all eligible, no SI gate).
- **Selection**: in-sample only — the variant with the highest lift (hit rate ÷ baseline) among those with ≥ 30
  in-sample hits; tie → more hits. Frozen before the out-of-sample run.
- **Verdict (fixed 2026-09-27, out-of-sample only). GO iff all three:**
  1. top-10 hit rate ≥ 3× baseline;
  2. ≥ 30 distinct hits;
  3. hit rate ≥ 3× that year's baseline in ≥ 2 of 2024, 2025, 2026-YTD.
  Otherwise NO-GO. Median `dd` and `r10` are reported, not gated.
- **Known biases, reported not hidden**: Yahoo lacks delisted and renamed tickers (survivorship) — report per-year
  coverage = FINRA eligible-class symbols with Yahoo + SEC data ÷ all; baseline comes from the same covered set, so
  lift is like-for-like, absolute rates are not. Multi-class issuers and splits between the SEC fact and the FINRA
  date add noise to `si_pct`.
- Runs in the dev workspace (FINRA, SEC, Yahoo reachable), downloads cached to gitignored `.cache/squeeze/`.

## 8. Live (phase 2, GO only)

- **Schedule**: Mon–Fri 12:00 UTC (before the US pre-market open), for that day's session. Corrects Section 1's
  "Tuesday to Saturday", which mirrored the insider scan's morning-after timing.
- **Run**: latest usable FINRA report (2 calls) + 3 latest SEC frames + Yahoo 3-month bars for C (8 threads) →
  score with the frozen variant → top 10 → Telegram (Hebrew, RTL rules, disclaimer) → journal entries.
- **Message per name**: rank, ticker, score, `si_pct`, `dtc`, `rvol`, `ret5`, breakout mark, SI report date, float
  (Yahoo, if available), phase-3 fields marked "לא מאומת".
- **Commands**: `/squeeze` = the last list; `/squeeze TICKER` = full breakdown for any ticker (rank inside today's C
  if present); `/squeeze stats` = journal hit rate vs the backtest's out-of-sample hit rate and baseline.
- **Journal** `data/squeeze_journal.json` (separate from the insider journal): entry per listed name
  {date, ticker, rank, score, features, variant}; each run fills, for entries with 10 finished sessions, entry open,
  max high, min low, close D+9, `hit`. Missing data → left empty, retried, never estimated.

## 9. Phase 3 (GO only)

- **Borrow**: `usa.txt` → fee rate and available shares for shown names. First a one-off `workflow_dispatch` probe;
  if Actions can't reach it, the field shows "לא זמין" and nothing else changes.
- **Gamma fuel** (CBOE, shown names only): Σ call OI × 100 × delta for expiries ≤ 21 days ÷ float (or shares out);
  near-dated call volume ÷ call OI; call/put OI; `iv30`.

## 10. Errors

Every value states its source or "missing" — never a default (existing bot rule). FINRA down → last cached report
with its date and a ⚠ line; SEC frame down → previous frame; Yahoo fails for a ticker → ticker skipped and counted
in the message footer; nothing to show → the message says so. Workflow failure → the existing alarm pattern
(Telegram message with the run link). Backtest: HTTP errors retried with backoff (`common.fetch`); a ticker that
keeps failing is excluded and counted in the coverage table.

## 11. Tests (stdlib `unittest`, offline, network mocked — matches `tests.yml`)

- Point-in-time: SI not usable before settlement + 8 business days; shares not usable before end + 15 days;
  features never read bar D or later; outcome window exactly D … D+9.
- Scoring: percentile ranks with ties, gates, top-10 cut, determinism.
- Backtest: a synthetic market where the right answer is known (one planted squeeze, one look-ahead trap), repeat-
  pick de-duplication, verdict thresholds at their edges.
- Live (phase 2): message RTL check via `common.rtl_bad_lines`, journal follow-up, command parsing.
