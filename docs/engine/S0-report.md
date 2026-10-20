# S0 report: resume for swing bracket tickets ($2,500) and scope the line

Branch `engine/s0` (from `engine/i2c` e26ceab). Date 2026-10-20. **S0 gate: PASS.**

**No outcome was computed.** No return, P&L or R exists for any ticket, signal or control.

## Findings (details in [S0-audit.md](S0-audit.md))

- **S1, prior evidence.** Most candidate families were already tested, all negative:
  - **Ensemble S1–S4** swing tickets (2017 → 2026-03; 35,083 tickets): gross ≈ +0.0004R before a 0.061R cost; NO-GO.
    - **S3 (52-week high): −0.084R.**
    - **S2 (earnings with a buy-stop trigger): −0.033R.**
  - **[branch]** swing pullback: −1.12R, t −3.84 out-of-sample. PEAD at the next open: −0.267R. 20-day breakout:
    intraday only.
  - ZBA's +0.275R gross vs 0.214R of costs is pooled development + holdout (S:…ticket-backtest-design.md:398@70a9a58);
    the holdout was NO-GO.
  - **Untouched:** non-earnings multi-day gap-and-hold, and a standalone N-day breakout (adjacent to the negative
    1-day-high pools).
  - **Untouched periods:** none in single-stock history from 2016 → 2026-10 for the tested families; only forward
    (and `forward_2026q4`).
- **S2, ticket code.**
  - `bot/levels.py` already builds daily bracket tickets: buy-stop at high + $0.01, limit + 0.25×ATR, 3-session
    expiry, R = min(2×ATR, 10%), 1.5R / trailing legs, H = 21, tick rounding.
  - Its fill model is reusable as a reference (gap fills, stop first), but its costs are not IBKR Tiered and it has
    known defects (raw bars after splits, no delisting close).
  - The journal has 8 tickets (2026-10-13 → 15): 3 closed, 5 open.
  - Copy, never import: `px`, `atr14`, `fill`, `exits` (stop-first), the outcome semantics.
- **S3, candidates.** C1 52-week breakout (PR, touched −), C2 N-day breakout in an uptrend (PR/PRAC, adjacent),
  C3 pullback (PR, touched −), C4 PEAD trigger (PR with decay, touched −), **C5 gap-and-hold, non-earnings (PRAC,
  untouched)**. Signals per year in the liquid universe: about 1,250 (C5) to 11,800 (C3).
- **S4, data.**
  - Held: daily bars 2022-01 → 2026-10 (about 10.4k symbols); minute bars for 1,088 names, 2023-08 → 2026-10;
    close − 5 min quotes for every session; corporate actions; 8-K Item 2.02 for 909 CIKs from 2023-06.
  - Gaps:
    - 8-K dates for the full universe: ≈ 8k submissions requests, ≈ 20 min;
    - optional minute bars for conflict days: ≈ 32k requests, ≈ 3–4 h;
    - 09:35 samples on fill days: ≈ 2k requests.
- **S5, evaluator.** It needs:
  - a stop-limit entry with expiry and gap skip;
  - multi-day bracket exits with the conflict rule (minute bars, else stop first);
  - split, delisting and merger handling;
  - a multi-position cash book with T+1.
  - **IBKR pays 0% on the first $10,000 of cash** (official page, rates as of 2026-09-18), so idle cash in a $2,500
    book earns 0%.
- **S6, costs (measured spreads).**
  - Median close − 5 min spread: T1 0.050%, T2 0.095%, T3 0.152%, T4 0.327%.
  - Cost per R at a 3% / 5% / 8% stop:
    - T1: 0.047 / 0.040 / 0.034;
    - T2: 0.066 / 0.051 / 0.042;
    - T3: 0.091 / 0.066 / 0.052;
    - T4: 0.153 / 0.102 / 0.075.
- **S7, power** (assumptions fixed first: σ 1.3R, fill rate 50%, ρ 0.02, 2.8416·SE).
  - Per-ticket confirmation MDE: 0.10–0.12R (C1–C3), 0.18R (C5), 0.22R (C4).
  - `forward_2026q4`: 0.25–0.56R.
  - **Book vs SPY over 2 years: MDE ≈ 60% a year. Too weak to decide anything; report only.**

## Changes (`engine/s0`)

| Commit | Content |
|---|---|
| 9f07cc7 `docs: ADR 0012 resume engine for swing tickets + rules` | ADR 0012 (Accepted); the CLAUDE.md rule replaced; the ARCHITECTURE banner |
| a38b68b `docs: S0 swing-ticket audit` | `docs/engine/S0-audit.md` (S1–S7) |
| e3d5ffe `docs: ADR 0013 swing-ticket validation design (Proposed)` | Primary H1 frozen as text; measurement; gate draft; G1–G6 |
| this commit `docs: S0 report` | This report |

## Gate

| Criterion | Result |
|---|---|
| ADR 0012 Accepted; rule and banner in place | **PASS** |
| Audit covers S1–S7 with evidence; S6 spreads measured | **PASS.** Spreads come from 3.74M stock-days of stored close − 5 min samples (universe-(b) table, tiers T1–T4) |
| ADR 0013 Proposed with G1–G6 | **PASS** |
| No outcome computed; every data read listed | **PASS.** See the reads below |
| Suites unchanged | **PASS.** Engine 159/159, eval 94/94, stockbot 902/902 |
| Diff only `docs/engine/` + `scalp_engine/CLAUDE.md`; no data files tracked | **PASS.** 5 files before this report, all in `docs/engine/` + `scalp_engine/CLAUDE.md`; the only tracked data-format file is the empty `holdout_ledger.jsonl` |
| Backup push checks | **PASS.** `push_check.py` on the head with this report (credentials via `envload`); `engine/s0` pushed, not merged |

**Data reads (all descriptive, no forward prices, nothing in a sealed window):**
- manifests of `raw_bars_1d`, `raw_bars_1m`, `raw_quote_samples`, `edgar_filings`, `corporate_actions` and `cik_map`
  (metadata);
- `universe_b_daily` (price, `dv20`, spread at the prior close);
- `edgar_filings` (form and items);
- `raw_bars_1d` OHLCV up to each signal date (signal counts).

Network: IBKR's official interest page in the browser (a 403 to the plain fetch). There were no Alpaca or SEC
requests.

## Decisions needed (ADR 0013 §4)

- **G1, primary hypothesis.**
  - Options: (a) C5 gap-and-hold, non-earnings; (b) C2 N-day breakout; (c) do not pursue the line.
  - **Recommendation: (a)**, knowing its evidence is practitioner-only and the internal prior for swing breakout
    tickets is negative. (c) is defensible.
- **G2, universe.**
  - Options: (a) ≥ $5 and ≥ $10M dollar volume; (b) ≥ $2M; (c) ≥ $50M.
  - **Recommendation: (a)** (0.04–0.07R cost).
- **G3, windows.**
  - Options:
    - (a) development 2022-10 → 2024-12 and confirmation 2025-01 → 2026-09-17, untouched for C5, with
      `forward_2026q4` as a reported-only forward check (≈ 150 tickets, MDE ≈ 0.47R);
    - (b) forward-only confirmation (about 12 months);
    - (c) `forward_2026q4` gated.
  - **Recommendation: (a).**
- **G4, gate.**
  - Options:
    - (a) (i) t ≥ 2.0 and (ii) beats random entry, with the book vs SPY reported;
    - (b) also t ≥ 2.0 on the control difference;
    - (c) add a 0.10R floor.
  - **Recommendation: (a).**
- **G5, account.**
  - Options: (a) cash with T+1; (b) margin (FINRA minimum $2,000; PDT is irrelevant for swing; IBKR's phase-in status
    is not verified).
  - **Recommendation: (a) cash.**
- **G6, delivery.** A `/swing` Telegram command in a later phase; recorded only.

## Next step: S1 prerequisites only (S1 not started)

1. The user accepts or amends ADR 0013 (G1–G6).
2. An S1 data plan, budget-checked first:
   - 8-K Item 2.02 dates for the full universe (≈ 8k EDGAR submissions requests);
   - 09:35 samples on fill days;
   - optional minute bars for conflict days;
   - credentials via `envload` only.
3. S1 pre-registration: an ADR with the evaluator design (bracket entry and exits, conflict rule, book, control) and a
   golden set, committed before any outcome.
