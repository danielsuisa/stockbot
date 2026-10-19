# I0 report: close the intraday line + scope the insider-signal validation

Branch `engine/i0` (from `engine/p3d` 908f25d). Date 2026-10-19. **I0 gate: PASS.**

## Audit (details in [I0-audit.md](I0-audit.md))

**I1. The stockbot insider rule**
- **Rule:** at frozen commit `2ffda0a` (2026-09-24), a cluster is:
  - code `P` + `A`, non-derivative, not 10b5-1;
  - ≥ 3 distinct officers or directors, ≥ $100K total;
  - transaction dates within 30 calendar days of the scan day.
  - A separate "big" trigger fires on any single purchase ≥ $500K.
- **Threshold overrides:** the thresholds can be overridden by GitHub repository variables, which are not visible
  locally.
- **Quality score:** 0–100, from five parts. Its regime part is computed at run time from Yahoo, not as of the event.
- **Clock and amendments:** the clock is the **transaction date**; the acceptance time is never used. Form 4/A
  amendments are fully handled.
- **Tests:** about 92 tests across six stockbot test files.
- **Prior research:**
  - STEP 0 (2016–2026-03, 6,476 events) looked at dispersion only.
  - Ensemble S1, an insider construct, had its signed mean observed (−0.032) over 2017–2026-03.
  - The live rule's acceptance-time returns have never been computed.

**I2. The journal**
- Location: `data/journal.json`.
- 106 alerts from 2026-09-24 to 2026-10-16: 70 `big`, 19 `cluster`, 17 `cluster+big`.
- Follow-ups are at 30, 90 and 180 calendar days vs SPY, on Yahoo closes. **0 filled so far**, so there is no summary.
- The 16:30 and 19:30 New York alerts use a baseline close that precedes the alert, and the journal cannot tell those
  alerts apart.

**I3. SEC Insider Transactions Data Sets**
- Coverage: 2006 Q1 → 2026 Q3, quarterly, with every field the rule needs, including `AFF10B5ONE` and
  `DATE_OF_ORIG_SUB`.
- **No acceptance timestamp** (verified on the 2024 Q1 sample: 13.9 MB, 67,671 submissions).
- Filing lag: 40% of Form 4 purchase lines are filed more than 2 days after the trade, so a transaction-date clock
  would use information not yet public.
- Acceptance-time source: the submissions-JSON time is filer-inconsistent (ensemble finding). The `.txt` header /
  index page is the source of truth.
- Estimated fetch, 2022 Q1–2026 Q3: 19 zips ≈ 260 MB plus cluster-member headers. Minutes to hours at 5 requests per
  second.

**I4. Price and cost data**
- Alpaca free SIP daily bars go back to 2016. OTC is **not** covered.
- In the 2024 Q1 sample, 80% of purchase issuers have bars. Of those: 25% trade under $5, 41% have under $1M daily
  dollar volume, and 13% under $100K.
- At 09:35 the quoted spread has a median of 0.80% and a p90 of 2.72%, and 24% of events have no quote.
- Corporate actions are stored only from 2023-08, so earlier history is an I1 task.

**I5. Engine gaps**
- A reimplementation of the rule, with parity against the journal's recorded accessions.
- A daily-horizon L7 evaluator:
  - entry: market-on-open / marketable, per P3D;
  - multi-day holds, adjustments and delisting returns;
  - a concurrency rule for the $500 cash account.
- A matched control: SIC sector and a dollar-volume size proxy.
- Cluster-robust statistics.
- New contracts.

## Changes (all on `engine/i0`, local until the backup push)

| Commit | Content |
|---|---|
| e3c0604 `docs: ADR 0007 close intraday line + rules banner` | ADR 0007 (Accepted, user 2026-10-19); status banner at the top of `ARCHITECTURE.md`; the rule in `scalp_engine/CLAUDE.md` |
| c42a5c1 `docs: I0 insider validation audit` | `docs/engine/I0-audit.md` (I1–I5) |
| ea2ef02 `docs: ADR 0008 insider validation design (Proposed)` | Hypothesis, event study plus L7 tradable version, trials ledger, power table, gate draft, E1–E5 |
| this commit `docs: I0 report` | This report. It is a fourth commit beyond the three listed, following the P-phase convention of a report commit |

- Network use was official SEC and Alpaca documentation pages and **one** SEC sample file (`2024q1_form345.zip`,
  outside both sealed windows). There were no Alpaca API requests.
- Every SEC request used `SEC_USER_AGENT`, and the value was never printed.
- The sample is kept outside git, at `D:\scalp-data\i0_sample\`.

## Gate

| Criterion | Result |
|---|---|
| ADR 0007 Accepted; banner and rule in place | **PASS.** Status "Accepted. Decided by the user on 2026-10-19". The banner is line 1 of `ARCHITECTURE.md`. The rule is under "Phase-gate protocol" in `scalp_engine/CLAUDE.md` |
| The I0 audit covers I1–I5 with evidence | **PASS.** Each finding is in F/E/I/R form, with `path:line` or URL citations |
| ADR 0008 Proposed with E1–E5 laid out | **PASS.** Status Proposed; E1–E5 each give evidence and options; recommendations are marked |
| stockbot suite unchanged at 902; engine and eval suites pass | **PASS.** stockbot 902/902 OK; `bot/`, `tests/`, `.github/` and `data/` are identical to `main`. Engine 116/116 OK. Eval 74/74 OK |
| `git diff --stat engine/p3d...engine/i0` touches only `docs/engine/` and `scalp_engine/CLAUDE.md` | **PASS.** 5 files, +682 lines before this report: `docs/engine/ARCHITECTURE.md`, `I0-audit.md`, `adr/0007-…`, `adr/0008-…`, and `scalp_engine/CLAUDE.md`. This report adds `docs/engine/I0-report.md` |
| No data files tracked | **PASS.** The branch adds no data files; every changed file is Markdown. The tracked data-format files are stockbot's pre-existing `docs/backtest/*.csv` and `data/index/h15-3m-tbill.csv` (unchanged, identical to `main`), plus the empty `scalp_contracts/holdout_ledger.jsonl`. The SEC sample is outside the repo |
| Backup push checks pass | **PASS.** `push_check.py` on the final head: workflows triggered none; secret and e-mail hits 0; data files none; ALL CHECKS PASS. Pushed to `origin/engine/i0`; not merged |

**Holdout:** no read covered a sealed window.
- The sample file is 2024 Q1; the stored bars read for coverage counts are 2024 H1 plus the 2026-10-17 asset snapshot.
- `holdout.yaml` and `holdout_ledger.jsonl` are unchanged.

## Decisions needed

- **E1, evaluation windows.**
  - Options:
    - (a) development 2022-02-01 → 2024-06-28, confirmation 2024-07-01 → 2026-07-20, with both sealed windows untouched;
    - (b) add `forward_2026q4` as a second, veto-only confirmation (MDE ≈ 13–14 pp at 21 sessions);
    - (c) add `legacy_2016_2021`, run once as a veto under its existing role;
    - (d) unseal `legacy_2016_2021` as development data.
  - **Recommendation: (a) + (c)**, keeping `forward_2026q4` sealed for a later decision.
- **E2, frozen signal.**
  - Options:
    - (a) `2ffda0a`, raw cluster only, with the defaults 3 / $100K / 30 days;
    - (b) also quality ≥ 70 as a secondary;
    - (c) the actual GitHub repository-variable values.
  - **Recommendation: (a)**, after you confirm whether the repository variables `MIN_INSIDERS`, `MIN_CLUSTER_USD` and
    `WINDOW_DAYS` are unset; (c) if they differ.
- **E3, universe and liquidity floor.**
  - Options:
    - (a) no floor;
    - (b) price ≥ $1, dollar volume ≥ $100K, spread ≤ 3%;
    - (c) price ≥ $2, dollar volume ≥ $1M, spread ≤ 1%.
  - **Recommendation: (b)** for the event study, **(c)** for the tradable L7 version.
- **E4, primary horizon and gate.**
  - Options:
    - (a) H = 20, mean ≥ +2.0 pp vs matched control with cluster-robust t ≥ 2.0;
    - (b) H = 20, t ≥ 3.0 with no magnitude floor;
    - (c) H = 60 primary.
  - **Recommendation: (a)**, with H = 5 and H = 60 as secondaries without gate weight.
  - Note: the confirmation MDE is about 4.8 pp, so a true +2 pp effect would probably fail. That trade-off is yours.
- **E5, the live journal.**
  - Options:
    - (a) ignore it;
    - (b) parity oracle only;
    - (c) also a descriptive forward re-score.
  - **Recommendation: (b)**; (c) only together with E1(b).

## Next step: I1 prerequisites only (I1 not started)

1. Accept or amend ADR 0008, with E1–E5 decided, including the GitHub repository-variable values for E2.
2. If E1(c) or E1(b) is chosen, an owner-written ledger/ADR record for that window's use.
3. Before I1 ingests anything, fix in an I1 ADR:
   - the acceptance-time source rule (`.txt` header; a JSON check on a fixed-seed sample);
   - the CIK → point-in-time symbol mapping and the depth of corporate-action history;
   - the bad-price, delisting and adjustment rules;
   - the parity-test design;
   - the new contracts.
4. Still open from P3D: rotate the Alpaca paper key that appeared in chat.
