# Recommendation algorithm — phase A.1 (survivorship and 8-K coverage) Implementation Plan

Status: not executed. Phase A.1 runs from 2026-10-10-recommendation-algorithm-phase-a1-claude.md (owner's decision, 2026-10-10).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run only spec section 7 A.1: recompute item 2.02 coverage using 8-K (not 8-K/A), measure universe-like stock-months without Alpaca bars using FINRA average daily share volume and SEC Form 4/5 transaction prices, count recoveries by the registered rename rule, and report (f) and (b=f\times1R) against (0.03R).

**Architecture:** Extend the existing phase-A probe and readers rather than creating a second data pipeline. Use `bot/probe_ensemble.py` for the A.1 counts/report, `bot/probe_ensemble_data.py` for EDGAR/SEC readers and 8-K-only coverage, and `bot/shorts.py` for FINRA report access and its `averageDailyVolumeQuantity` field. Add a separate A.1 report; do not overwrite the phase-A report.

**Tech Stack:** Python 3.11/3.12, standard library only, existing project clients/caches, `unittest` with network mocked.

**Spec:** `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md`, revision 6; owner approval is for phase A.1 only, sections 1.1 and 7.

## Global Constraints

- Scope is only spec section 7 A.1 items 1–4. Do not implement phases B–E.
- Counts and coverage only. No return of any ticket or stock may be computed, printed, logged, cached, or saved.
- Do not substitute a different volume source or definition if FINRA short-interest reports lack a volume field; stop and report the blocker.
- FINRA volume is the report's average daily share volume (V), field `averageDailyVolumeQuantity`; price (P) is the latest eligible non-derivative Form 4/5 transaction price in the preceding 365 days. Apply the spec's (V\times P\geq\$10M) threshold.
- Measurement period for universe-like stock-months: 2018-01 through 2026-03 inclusive. A symbol is eligible only using information allowed by the spec as of each month-end.
- Use the existing rename rule only: an as-filed symbol with no bars can be mapped to another symbol for the same CIK only when exactly one other filed symbol has bars. Apply the one-third recovery threshold as specified; do not invent aliases.
- Report (f=\text{unrecovered missing universe-like stock-months}/\text{all universe-like stock-months}) and (b=f\times1R); compare (b) to (0.03R).
- No merge, no PR, no implementation beyond this plan. Stop after presenting this plan for owner review.

## Review Focus

1. **FINRA field availability and historical coverage:** verify the actual response schema and historical dates before counting. If the reports do not carry the required average daily volume field, stop without substituting another source.
2. **Point-in-time leakage:** for each month-end, only use Form 3/4/5 filings and transaction prices from the preceding 365 days, and a FINRA report available by that month-end. Pin the exact cutoff in tests.
3. **Form-type contamination:** coverage must count only form type `8-K` with item 2.02; `8-K/A` must not count as a release.
4. **Price provenance:** use non-derivative transaction prices from Form 4/5, not closing prices, prices from derivative rows, or insider purchase-only rows.
5. **Rename ambiguity and denominator integrity:** only a single alternate filed symbol for the same CIK can recover a missing symbol; count every denominator and numerator explicitly so duplicate CIK/symbol rows cannot inflate coverage.

---

### Task 1: Verify FINRA's required field and date availability — fail closed

**Files:**
- Modify only if necessary: `bot/shorts.py`
- Test: `tests/test_probe_ensemble_a1.py`

**Interfaces:**
- Reuse `bot.shorts.report(date)`, `bot.shorts.slim(row)`, and `bot.shorts.usable_from(settle, cal)`.
- Required field: `averageDailyVolumeQuantity`, normalized by `slim()` as `adv`.

- [ ] **Step 1: Write tests** for a FINRA row containing `averageDailyVolumeQuantity`, a missing field, and a null/zero volume. Assert that missing schema is distinguishable from a valid report row with a zero value.
- [ ] **Step 2: Run the focused tests and verify failure** before adding the minimal schema validation.
- [ ] **Step 3: Verify the production metadata/response schema and date coverage** using the existing FINRA client path. Record whether the needed historical reports are available for the entire A.1 period.
- [ ] **Step 4: Implement fail-closed validation.** If the field is absent from the report schema, stop A.1 with a clear error/report and do not use another volume source. Do not silently turn an absent field into zero.
- [ ] **Step 5: Run focused tests.** If FINRA lacks the required field, stop the phase here and report the evidence; do not proceed to Tasks 2–4.

### Task 2: Recompute item 2.02 coverage with 8-K only

**Files:**
- Modify: `bot/probe_ensemble_data.py`
- Modify: `bot/probe_ensemble.py`
- Test: `tests/test_probe_ensemble_a1.py`

**Interfaces:**
- Reuse the existing SEC submissions reader, `release_times`, `release_symbols`, `item_202_coverage`, and `keeps_8k` where their current contracts fit.
- Add or refine a form-type-aware reader so only `8-K` filings with item 2.02 count; `8-K/A` is excluded.
- Produce per-filing-year and overall counts: universe stock-years, covered stock-years, and coverage share; apply the spec's 90% overall rule.

- [ ] **Step 1: Write failing tests** with separate 8-K and 8-K/A item 2.02 records, including an amendment-only stock-year. Assert that the 8-K/A-only stock-year is not covered and that filing-date years are used.
- [ ] **Step 2: Run the focused tests and verify failure.**
- [ ] **Step 3: Implement the 8-K-only filter** in the existing reader/counting path; keep the established EDGAR index acceptance-time handling.
- [ ] **Step 4: Run focused tests** for per-year counts, overall coverage, and the below/at/above-90% decision. Do not output signal firings or any return statistic.

### Task 3: Count universe-like stock-months with and without Alpaca bars

**Files:**
- Modify: `bot/probe_ensemble_data.py`
- Modify: `bot/probe_ensemble.py`
- Reuse: `bot/shorts.py`
- Test: `tests/test_probe_ensemble_a1.py`

**Interfaces:**
- Add an A.1 reader for non-derivative transaction prices from Form 4/5 records, retaining at minimum filing date, issuer CIK, as-filed symbol, transaction date, and price.
- Add a deterministic counter that consumes symbol filings, transaction prices, month-end-available FINRA rows and bar-presence indexes, and returns counts only.
- For each month-end from 2018-01 through 2026-03, include an issuer symbol if it appears on a Form 3/4/5 filed in the prior 365 days, has a latest eligible non-derivative Form 4/5 transaction price (P\geq\$5) in that window, and the latest eligible FINRA report has average daily share volume (V) with (V\times P\geq\$10M). Count whether an Alpaca bar exists for that symbol during the month.
- Do not calculate returns, price changes, performance or trade outcomes.

- [ ] **Step 1: Write failing tests** for the exact 365-day boundaries; Form 3/4/5 versus other forms; derivative versus non-derivative transactions; latest transaction price selection; FINRA report availability cutoff; (P=\$5); (V\times P=\$10M); month bar present/absent; and duplicate rows.
- [ ] **Step 2: Run the focused tests and verify failure.**
- [ ] **Step 3: Implement the minimal readers and counter** using existing SEC data readers/caches and `shorts.py`; avoid new dependencies and avoid materializing more data than needed.
- [ ] **Step 4: Run focused tests** and verify the denominator equals unique eligible CIK/symbol/month units as defined by the spec, with separate per-year and overall counts.

### Task 4: Measure rename recovery, calculate f and b, and write the report

**Files:**
- Modify: `bot/probe_ensemble.py`
- Test: `tests/test_probe_ensemble_a1.py`
- Create (report only after a successful run): `docs/backtest/ensemble-phaseA1-<date>.md`

**Interfaces:**
- Consume Task 3's count-only stock-month records.
- For each missing-bar stock-month, find other filed symbols for the same CIK that have bars. The rename rule recovers it only when exactly one other filed symbol has bars.
- Produce counts of all universe-like stock-months, with bars, without bars, recoverable by rename, unrecovered, per year and overall; then compute (f) and (b=f\times1R).

- [ ] **Step 1: Write failing tests** for zero, one, and two alternate symbols with bars; the exactly-one rule; the one-third recovery threshold; (f) with zero and nonzero denominators; and (b>0.03R) versus (b\leq0.03R).
- [ ] **Step 2: Run the focused tests and verify failure.**
- [ ] **Step 3: Implement rename counting and the (f,b) calculation** without emitting individual symbols or price/return data in the report.
- [ ] **Step 4: Add a report whitelist test.** The report may contain only counts, coverage shares, the rename decision, (f), (b), the (0.03R) comparison, source/date coverage and request/runtime diagnostics. Assert that no per-stock/per-ticket data or return-related values are present.
- [ ] **Step 5: Run the focused tests and full offline suite** (`python -m unittest discover -s tests`); network calls are mocked in tests. Check that all written outputs comply with the count-only constraint.
- [ ] **Step 6: Run A.1 only** using the project's existing secret-backed execution pattern if required. If FINRA's required field is absent, stop and report instead of generating a partial substitute result. Save the count-only report.
- [ ] **Step 7: Report to the owner in Hebrew** with 8-K-only coverage, annual/overall stock-month counts, rename recovery, (f), (b), whether the (0.03R) limit is exceeded, tests, and any blocker. Stop for owner approval; do not start phase B.

## Final self-review

- Spec section 7 A.1 items 1–4 are covered by Tasks 2–4; Task 1 is the mandatory FINRA-field fail-closed precondition.
- No phase B–E code, ticket/stock return, performance statistic, merge, or PR is in scope.
- The plan deliberately does not authorize implementation. Wait for the owner's review before executing it.
