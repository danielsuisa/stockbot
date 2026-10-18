# ADR 0004: P2 gate failure and the plan change for P3

- **Status:** Accepted (user decision, 2026-10-18, via the orchestrator).
- **Date:** 2026-10-18.
- **Branch:** `engine/p3`.
- **Context documents:**
  - [P2 report](../P2-report.md)
  - [ADR 0003](0003-features-regime-screener.md)

## 1. What happened

- The P2 gate **failed**. The primary ratio was **1.4304**, against the 1.5 required.
  - The measurement was run once on commit c37f9e3, at 2026-10-18T13:46Z.
  - The spec it implements was committed in 36902f5.
- **P2 stays recorded as FAILED.**
  - The gate text in ARCHITECTURE.md §4 and ADR 0003 §9 is not edited.
  - The P2 measurement is not re-run.

## 2. Plan change (user decision)

**L2 rule table demoted.** Only these tradability filters remain hard filters:

| Filter | Rule |
|---|---|
| Price | $5–$200 |
| Volume | 20-day average volume > 2M |
| Spread | < 0.10%, measured on the **live-eligible delayed NBBO** (`spread_nbbo_delayed`). No estimator fallback; no sample → fail |
| Dilution | exclusion, v2 (§3) |

RVOL, ATR %, the catalyst flag and the regime become **model features**; they are no longer filters.

**Account model:**
- Starting equity is **$500**.
- **Cash account**: no margin, no short selling, and buys only with **settled** cash.
- Settlement is T+1. Good-faith violations are not allowed, which follows from buying only with settled cash.
- PDT does not apply to cash accounts, so `broker_pdt_active` does not bind this account model.

**Direction:** long only. Short selling is out of scope.

## 3. ADR 0003 Proposed items: outcome

| Item | Outcome |
|---|---|
| §0.2: parse-time range drop for whole-document sources | **Accepted** |
| §1: the `public_realtime` and `public_eod` availability profiles | **Accepted** |
| §6: delisted-ticker CIK mapping via full-text search, with the Form 25/15 check | **Accepted** |
| §6: dilution `unknown` (no CIK) passes | **Changed**: a missing CIK now **fails** the dilution filter (fail closed) |
| §2: dilution forms include 424B2 | **Changed**: 424B2 (structured-note supplements) is removed. The forms are now `^(S-1|S-3)(/A|ASR|MEF)?$` and `^424B[1345678]$` |

**The dilution change is a new feature version.**
- It is `dilution_filing@2.0.0`, implemented in `scalp_engine/dilution.py` and registered in
  `scalp_contracts/features/registry_p3.json`.
- `dilution_filing@1.0.0` and the P2 `screener_snapshots` stay unchanged.

## 4. BLS identity

- `SEC_USER_AGENT` is no longer sent to bls.gov.
- Any BLS request uses `BLS_USER_AGENT`. If that is unset, the BLS step is skipped and the skip is logged.
- The CPI and NFP dates already ingested stay.

## 5. Consequences

- P3 decision points use the four tradability filters above. Everything else is a feature.
- The evaluator models a $500 cash account with T+1 settled cash. ADR 0005 holds the details.
