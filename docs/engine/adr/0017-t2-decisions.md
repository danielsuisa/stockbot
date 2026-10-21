# ADR 0017: T2 decisions and pre-outcome amendments

- **Status:** Accepted. Decided by the user on 2026-10-21 (T2 prompt, "User decisions", items 1–7). Every
  amendment below was made before any outcome existed.
- **Context:**
  - [ADR 0015](0015-intraday-ticket-validation-m1.md) and [ADR 0016](0016-t1-pre-registered-design.md);
  - the [T1 report](../T1-report.md), "Decisions needed" 1–6.

## 1. Decisions

1. **The universe is the bot's own.**
   - The T1 coverage remedy stands, leveraged ETFs and other ETPs included.
   - A reported-only breakdown splits **ETPs vs stocks**.
   - An ETP is an NYSE Arca listing, or a name carrying an exchange-traded-product keyword (P1's classifier words).
     The bot's fund-word filter lets names such as "United States Oil Fund, LP" and "ProShares …" through.
   - A ticket's class is a column of every signed set.
2. **The ADR 0016 "T1 details" stay, with two exceptions.**
   - **Gate (ii) moves to the issued-ticket basis.**
     - For each issued H1 ticket *i* with at least one control:
       - Xᵢ = its net R if filled, else 0;
       - Cᵢ = the mean over its controls of (net R if filled, else 0).
     - **Gate (ii) is mean(Xᵢ − Cᵢ) > 0.** Its t is clustered by trading day (CR1) and reported.
     - Tickets with 0 controls are excluded and counted. That is a structural fact, not an outcome.
     - The T1 filled basis is reported only.
     - Why: dropping tickets whose controls never filled conditions on the day's price path, which is an outcome.
   - **ORB 5 check.**
     - It is reported gross and net.
     - The [−0.36R, +0.39R] band applies to the **net** development mean.
     - Outside the band, the cause is investigated. A defect counts only when a failing test pins it; it is fixed,
       then development is rerun.
     - If no defect is found, the run **stops before confirmation**. The run CLI refuses a confirmation unless the
       last logged development run is in band.
     - No rule or parameter changes because of this check.
3. **Liquidity rejections are counted at the fill minute** (an order larger than 1% of that minute's SIP volume).
4. **The real-set loader** (`scalp_eval/intraday_t2.py`) runs only after Stage 0 passes and this ADR and the
   `authorize` line are committed. Before simulating, it verifies:
   - every signature;
   - every re-derived digest;
   - the hashes in the `authorize` line;
   - the rules-file hash;
   - that the code under `scalp_eval/` and `scalp_engine/` is committed.

   It refuses a second confirmation run.
5. **Stockbot's expired-date test.**
   - `test_review.Signals.test_corrections_are_saved_before_alerts` is a known failure that existed before T2.
   - The stockbot item of the gate is "901/902 with exactly this test failing".
   - It is fixed on `main` under a separate maintenance prompt
     ([danielsuisa/stockbot#25](https://github.com/danielsuisa/stockbot/pull/25), open, not merged).
6. **Parity covers every live-journal day committed in git** from 2026-10-20 through the start of this run
   (≤ 2026-10-30).
   - At the start, `data/intraday_state.json` had one commit: a83748e, the 2026-10-20 morning window.
7. **Survivorship.**
   - The universe adds every ticker Alpaca's asset list omits that was acquired, worthless-removed or renamed away
     between 2022-01-03 and 2026-10-16. The source is the stored corporate actions, read with P1's `top500_v3`
     method.
   - **Each added name is valid only before its delisting or rename.** A rename date wins over a delisting, as in
     P1.
   - **Filters:**
     - the bot's symbol filter always applies;
     - the exchange and fund-word filter applies where the name is known (a renamed ticker inherits its new
       listing);
     - a merger into a listed security outside the universe is a fund reorganization and is skipped.
   - **Data:** only the daily and minute data the T1 rules need for these names was fetched, as new parts (`t2f*`,
     `t1s1c`, `t1denc`, `t1topc`, `sip-t1gridc`). Stages 1 and 2 were rebuilt and every set re-signed.

## 2. Windows

- Development 2022-02-01 → 2024-12-31.
- Confirmation 2025-01-02 → 2026-10-16. It runs once.

## 3. Final hashes (the signed sets the `authorize` line names)

- **Rules** (`scalp_engine/intraday_rules.py`, the copy of `bot/intraday.py` at 09bfa55): `199a3d60227e50ad5e2c9026fd3821ebe1445913406fcbabefc60c79d001e2cc`
- **Signing key:** b4388290d66a62da.

| Window | Set | Rows | sha256 |
|---|---|---:|---|
| development | H1 | 4,751 | `24170b0bc8a237f41d830c9730823693998e3c6d8ccd940166760f70f10e7159` |
| development | CONTROLS | 14,253 | `eb3cf4a39f354cb85264adcb9fb3cd8a5b288d5b6c87cc092a674b66b9fd77d8` |
| development | ORB5 | 1,688 | `0ddb279b8a1b38d5822c58cedebe609672848cc105a41df9bd9aa456253a43e9` |
| development | H1_SIPSIG | 6,613 | `079ba24dd7e4fb6c16add65df9527a85f072f429017b33c798d6cd345a6d25aa` |
| confirmation | H1 | 3,137 | `ebe4a2520c607fe2d03d96f35093f50e5aeb8c50cb80ae07f3042ee2b1382c20` |
| confirmation | CONTROLS | 9,411 | `3e5545ec5caf1a97e63e24e4b91ffb043a41a2016364303cfff1189c32d88ba2` |
| confirmation | ORB5 | 1,185 | `a8c8af1fce000a82c15e3c749f27133768dc7bb05b40c2aae26330027e3d4ac2` |
| confirmation | H1_SIPSIG | 4,210 | `b8533dd2dac616c73870e45825331744fddb2076cf4d84b6894120543de7e34d` |

## 4. Stage 0 record

- **Survivorship:**
  - 3,147 names added: 2,588 delisted or acquired, 623 renamed old tickers, 64 in both.
  - 744 of 1,184 watchlists changed (1,347 new symbol-days).
  - H1 tickets: development 4,587 → 4,751 (+453 / −289); confirmation 3,143 → 3,137 (+90 / −96).
- **Duplicates:** 0 after the AHPAW remedy (b1abbb4).
- **Leakage:** 30 sessions, 690 cut-offs, 4,172 ticket checks, 0 violations.
- **Parity:** 2026-10-20 (a83748e). 4/4 tickets and 11/11 watchlist rows identical. The only difference is VICR's
  ATR, at 1e-15. 0 unexplained.
