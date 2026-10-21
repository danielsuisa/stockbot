# S2 report — swing tickets: development run, re-simulation check, one confirmation run

- **Branch:** `engine/s2`, from `engine/s1` (49063a0). Pushed as a backup, not merged.
- **Decisions:** [ADR 0020](adr/0020-s2-decisions.md).
- **"H1"** is the swing H1: non-earnings gap-and-hold.

## Verdict: NO-GO (a), no net edge

**Gate (i) fails.** Confirmation H1 loses **−0.142R** per filled ticket (t **−1.60**, 933 fills). Gate (ii)'s
issued-basis difference is +0.007R (t 0.16): above 0 but meaningless, since the tickets themselves lose. Every year,
every liquidity tier and both secondaries are negative in confirmation. The development window's positive point
estimate (+0.062R, t 0.62) did not carry over.

## Stage 0

**Unmapped issuers (decision 2).** These are would-be H1 signals with no CIK, so A2 and A3 cannot be checked. They
stay excluded.

| Window | Signals | Symbols | Of which survivorship names |
|---|---:|---:|---:|
| Development | 578 | 233 | 20 |
| Confirmation | 533 | 261 | 8 |

**Order (UTC, 2026-10-21):**

| Time | Event |
|---|---|
| 17:19:22 | Driver (91b711c) |
| 17:19:47 | **ADR 0020** (6ff89ec) |
| 17:19:58 | **`authorize`** (d70334e) |
| **17:20:06** | **First outcome**: development run 1 start, on d70334e |
| 17:21:33 | Re-simulation check committed (a57c17c) |
| 17:25:28 | Re-simulation **pass** |
| 17:25:39 | **Confirmation start** (on 1ec3c29) |

- **Exactly one confirmation.** A second confirmation attempt is refused (exit 2): "the confirmation run has already
  started once".
- **No vendor request was made in S2.**

## Gate (i), confirmation H1

| Measure | Value |
|---|---|
| Issued / filled / fill rate | 1,155 / 933 / 80.8% (no touch 188, gapped above the limit and never back 34) |
| Mean net R per filled ticket | **−0.142** |
| SE (CR1, ISO week of the fill, 89 weeks) / t | 0.088 / **−1.60** |
| Week-block bootstrap 95% CI | −0.313 to +0.024 |
| Realized MDE (2.8416 · SE) | **0.251R**, against S1's 0.154R estimate. Week clustering and the fat-tailed 2R bracket widen it |
| Mean gross R / mean cost R | **−0.109** / 0.032 |
| Hit rate | 34.9% |

**Gate (ii)** (issued basis, 1,155 tickets, 0 without controls): **+0.007R**, t 0.16, CI −0.084 to +0.092. The
filled-basis version (reported only) is +0.039R, t 0.64, with 53 tickets dropped for having no filled control.

## Reported only

Mean net R per filled ticket:

| Set | Development: filled · mean · t | Confirmation: filled · mean · t |
|---|---|---|
| **H1** | 1,149 · +0.062 · 0.62 | 933 · **−0.142** · −1.60 |
| H1 without late-8-K tickets (115 / 74 dropped) | 1,057 · +0.021 · 0.20 | 875 · −0.142 · −1.67 |
| H1, T1 (≥ $50M) | 509 · +0.039 · 0.45 | 583 · −0.139 · −1.38 |
| H1, T2 ($10–50M) | 640 · +0.080 · 0.62 | 350 · −0.146 · −1.55 |
| H1 without the top 1% | +0.023 · 0.25 | −0.184 · −2.09 |
| S-b (H1 with earnings days) | 1,764 · +0.088 · 1.23 | 1,479 · −0.105 · −1.44 |
| S-a (20-day breakout; daily bars, stop first) | 9,006 · −0.027 · −0.60 | 7,340 · −0.035 · −0.97 |
| Controls (random entries) | 28,575 · −0.026 · −0.64 | 22,894 · −0.041 · −1.25 |

- **By year:**
  - H1 development: 2022 −0.119, 2023 +0.284, 2024 +0.002. Confirmation: 2025 −0.142, 2026 −0.141.
  - S-b development: 2022 −0.091, 2023 +0.235, 2024 +0.049. Confirmation: 2025 −0.051, 2026 −0.164.
  - S-a is negative in every year.
- **Exit cases (H1):**

  | Window | Stop | Target | Time | Gap below stop | Gap above target | Stop in fill session | Merger (cash) |
  |---|---:|---:|---:|---:|---:|---:|---:|
  | Development | 465 | 327 | 174 | 98 | 47 | 37 | 1 |
  | Confirmation | 445 | 234 | 76 | 85 | 39 | 53 | 1 |

- **0-share skips** (S1 build):

  | Window | H1 | S-b | S-a |
  |---|---:|---:|---:|
  | Development | 36 | 66 | 486 |
  | Confirmation | 51 | 100 | 662 |

- **Control shortfall:** 0.
- **Gate (ii) on development, reported only:** +0.115R, t 2.10. The confirmation's +0.007 did not hold it.

## Book (reported only)

$2,500 cash, T+1, at most 4 positions, 40% cap, idle cash at 0%. The comparison is SPY plus cash dividends over
the same span.

| Window | Trades | Skipped: 4 positions / unsettled cash | Book | SPY |
|---|---:|---|---:|---:|
| Development (2022-10-04 → 2025-01-24) | 201 | 937 / 11 | **−5.9%** ($2,352) | +64.9% |
| Confirmation (2025-01-06 → 2026-09-28) | 202 | 697 / 34 | **+0.7%** ($2,517) | +30.4% |

## Re-simulation check (Stage 2)

**PASS.**
- **Sample:** 200 development H1 tickets and 200 of their controls (seed tag `s2-resim-v1`), 310 of them filled.
- **Method:** re-simulated by fresh code that does not import the evaluator's simulation functions or `costs.py`.
- **Result: 0 disagreements** on fill state, fill and exit sessions and prices (within $0.01 per share) and net R.
- Its own synthetic tests check it against the evaluator and the fee schedule.

## Process gate: PASS

- **Stage 0.** Passed.
- **Order.** ADR 0020 and `authorize` came before the first outcome.
- **Runs.** 1 development run, logged. The re-simulation check passed. Exactly 1 confirmation run.
- **No defect fixes were needed.**
- **Suites:**

  | Suite | Result | Note |
  |---|---|---|
  | Engine | **204/204** | |
  | Eval | **155/155** | golden set, R4 static tests. One S1 guard test assumed an empty ledger; the legacy `swing` CLI now always refuses real sets and points to `swing_s2`, and the test asserts that |
  | Stockbot | **901/902** | only `test_review…test_corrections_are_saved_before_alerts` fails, the known date-expired test |

- **Repository.** Only allowed paths changed (`scalp_eval/`, `scalp_contracts/s_trials.jsonl`, `docs/engine/`). No
  data files are tracked. The push checks are in the final message.

## Records

**Trials ledger** (`scalp_contracts/s_trials.jsonl`), in order:
1. authorize;
2. development start and result (run 1, on d70334e);
3. resim_check (pass, 0 disagreements);
4. confirmation_start, start and result (on 1ec3c29, verdict NO-GO (a)).

**Results files:**
- `derived/s2/development_run1.json`, `confirmation_run1.json` and the per-ticket files;
- `derived/s2/resim_run1.json`.

**Run facts:**
- 0 vendor requests.
- Development took 4 minutes, the re-simulation 1 minute and the confirmation 3 minutes.

**Commits:**

| Commit | Subject |
|---|---|
| 91b711c | Driver (extra commit, before any outcome) |
| 6ff89ec | ADR 0020 |
| d70334e | `authorize` |
| a57c17c | Re-simulation check |
| 1ec3c29 | Development and check records |
| (next) | Legacy CLI guard test |
| (this) | Confirmation records and the report |

## Decisions needed

The recommendations are marked **(Rec.)**.

1. **Close the swing H1 line (non-earnings gap-and-hold) on this NO-GO (a)** **(Rec.: close it, with an ADR like
   0018)**.
   - The confirmation is negative before costs (gross −0.109R), and costs are small (0.032R), so cost is not the
     problem.
   - Development's +0.062R was noise around 0. The 2023 spike did not recur.
2. **Do not promote S-b or S-a** (decision 4). Both are negative in confirmation. A future swing hypothesis needs a new
   family and untouched data.
   - The only untouched single-stock window is `forward_2026q4`, which opens after 2027-01-29 and is too short to
     decide anything alone (S0 S7: MDE about 0.47R).
3. **The engine.**
   - Every M1 line is now closed or failed: intraday models (0007), insider (0011), intraday H1 tickets (0018), and
     swing H1 if item 1 is accepted.
   - **(Rec.: pause the engine, as ADR 0011 did, until a new user decision names a new hypothesis family.)**
   - M2 (the forward check) has nothing to check without a provisional GO.

## Next step (prerequisites only)

- A user decision on items 1–3. If the line is closed, a closing ADR and the engine status update.
- Separately: merge [danielsuisa/stockbot#25](https://github.com/danielsuisa/stockbot/pull/25) and
  [danielsuisa/stockbot#26](https://github.com/danielsuisa/stockbot/pull/26).
