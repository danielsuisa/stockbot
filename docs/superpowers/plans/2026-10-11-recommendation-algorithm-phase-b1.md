# Recommendation algorithm phase B.1 (sizing by equity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Size live `/ticket`s from the account's equity (spec section 10), with the p cap, the 20% position cap, `/equity`, the open-risk number and the new message fields. E, the dollar risk and the position value appear only in Telegram messages.

**Architecture:**
- **`bot/levels.py`** gets the pure sizing function.
- **`bot/equity.py`** (new) is the store: a bot message pinned in the owner's chat.
- **`bot/ticket_command.py`** sizes with E.
- **`bot/listen.py`** gets `/equity` and a traceback printer that never prints an exception's message.
- **`telegram-listen.yml`** gets the `TICKET_RISK_PCT` variable.

**Tech Stack:** Python 3.11+ stdlib; unittest with the network mocked; `tests/msgrules.py`; the Telegram Bot API through `common.tg`.

**Spec:** `docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md`, revision 7, section 10 (with sections 3, 5 and 8). Owner's conditions for the store: 2026-10-11, 1–5 below.

## Global Constraints

- **Scope.** No signal of section 4. No outcome or return of any past ticket. Nothing from phases C–E: the `/equity` re-send of the scan list is phase E. The blind rule is unchanged.
- **The fixed $100 risk stays** for the backtest, the blind run, the verdict run and the shadow journal. Only `/ticket` reads E.
- **The rule for a live ticket:**
  1. risk = p × E.
  2. shares = floor(risk / R).
  3. Position cap: shares = min(shares, floor(0.20 × E / P)).
  4. Then section 3 unchanged: legs; refusals (shares < 2, 1% of dollar volume, usable quote); the cost guard on the actual risk shares × R.
  5. Without E: `TICKET_RISK_USD`, and the message says the size is not tied to the account.
- **p.** Default 0.5%, from the repository variable `TICKET_RISK_PCT` (p is not private). Cap 0.5% while the label is "ללא יתרון מוכח" (no proven edge); 1% after a GO and a passing forward check, behind `EDGE_PROVEN = False`. A higher setting is cut to the cap, and the message says so.
- **Open risk.** floor(3% / p) positions, shown and not enforced.
- **Small account.** The cost guard is not loosened. Its refusal adds the small-account reason when E is set and the risk is under $20.

## Owner's changes on approving `4f5a319` (2026-10-11)

Decisions 1–5 are approved as written. These changes are binding and override the text below where they differ.

1. **Private chat only.** `equity.store` and `equity.load` work only when `getChat(TG_CHAT_ID).type == "private"`, because a pin is visible to every member of a chat. Otherwise `/equity` stores nothing and replies that sizing by equity needs a private chat, and `/ticket` falls back to `TICKET_RISK_USD`.
   - Tests: `test_store_refuses_a_non_private_chat`, `test_ticket_falls_back_in_a_non_private_chat`.
2. **Task 0, a live probe before Task 3.** One run with the Telegram secrets:
   - send a short test message with no number in it;
   - pin it silently;
   - call `getChat`;
   - unpin and delete the message.

   It prints only three booleans: the chat is private; the pin succeeded; `pinned_message` is that message and its `from.id` is the bot's. If any is false: stop, report, and do not build the store.
3. **Traceback inventory.**
   - `bot/listen.py` has five `traceback.print_exc()` calls (lines 89, 176, 208, 280, 360). Modules the listener imports have seven more: `check` 1, `scan` 2, `shadow` 1, `squeeze_live` 3. All twelve are replaced by `common.trace`. This changes only what those modules print on an exception: the type, the frames and an HTTP status, never the message.
   - Test `test_no_raw_traceback_in_the_listener_closure`: scans the source of every `bot` module in the listener's import closure (computed with `ast`) for `print_exc`, `format_exc` and `print_exception`, and finds none.
   - `sys.excepthook` and `threading.excepthook` are set to the same printer at the listener's entry point (the listener's `/check` and `/scan` paths use threads).
     - Tests: `test_excepthook_prints_no_message`, `test_thread_excepthook_prints_no_message`.
   - `common.trace` may print an `urllib.error.HTTPError`'s integer status code, never its URL, headers or body.
     - Test: `test_trace_prints_http_status_only`.
4. **Stale pin.** `load()` returns `(E, date set)`, where the date is the pinned message's date in Israel. `/equity` without an amount, and the sizing line of every ticket, show the date E was set.
   - Test `test_older_equity_pin_surfaces_with_its_date`: the newest equity message is unpinned and an older one is still pinned, so the older E and the older date are shown.
5. **`TICKET_RISK_PCT` is in percent** (`0.5` means 0.5%). This is stated in the workflow comment and in the `/equity` reply. An unset, non-numeric or ≤ 0 value gives the default 0.5% and a note in the message.
   - Tests: `test_p_setting_unset`, `test_p_setting_non_numeric`, `test_p_setting_not_positive`, `test_p_setting_percent_unit`.
6. **Only the synthetic E** (`12,345.67` in dry runs, `31,337.17` in tests, both marked as synthetic) appears in the phase B.1 report and in every example in the repository.
7. **Live check without `gh`.** The owner opens the run's log page on GitHub (Actions → Telegram listener → the run → the "Answer Telegram messages" step) and searches it with the browser's find for their own E in both forms, plain (`25000`) and with thousands commas (`25,000`). Expected: no match in either form.

## The store for E: a pinned bot message (accepted in principle)

- `/equity 25000` makes the bot send `💼 הון לחישוב גודל: <code>25,000.00$</code>` ("equity for sizing") to `TG_CHAT_ID` and pin it silently (`pinChatMessage`, `disable_notification`).
- To read E, the bot calls `getChat(TG_CHAT_ID)` and takes `pinned_message`. It is accepted only when all three hold:
  - it was sent by this bot (`from.id` equal to `getMe`'s id);
  - its text matches exactly `^💼 הון לחישוב גודל: ([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2})\$$`;
  - the value is within the bounds.

  Anything else means E is not set.
- Nothing is cached in a file. E is read per `/ticket` and `/equity` call and is held only in local variables.

## The owner's conditions: how each is met and tested

**1. `/equity` only from the owner's chat.**
- *Met by:* `listen.answer` already drops every update whose chat id differs from `TG_CHAT_ID` before `handle` runs. It prints `update N: ignored, not from TG_CHAT_ID`, with no text and no chat id, and sends no reply. `bot/equity.py` reads and writes only `TG_CHAT_ID`; there is no chat argument.
- *Test* `test_equity_from_another_chat_is_ignored`: an update `/equity 31337.17` from chat `999` through `listen.answer` gives:
  - no `sendMessage`, no `pinChatMessage` and no `getChat`;
  - the store is unchanged (the next `load()` still returns the previous value);
  - stdout holds only the "ignored" line.

**2. Nothing reaches a log, an exception message, a summary or a file.**
- **What the listener logs today for an incoming update:**
  - `update N: handling` or `update N: ignored, not from TG_CHAT_ID` (the id only);
  - `serve_end` and the workflow summary with counts and the offset.
- **The gap:** three `traceback.print_exc()` calls print the exception's *message* (`answer`, `/ticket` per symbol, the serve loop). That message can quote the update text or a sized number, e.g. `ValueError: could not convert string to float: '25000x'`.
- **Change:** `common.trace(e)` prints the exception's type and the frames (file, line, function), and never its message or arguments. It replaces all three `print_exc()` calls. `bot/equity.py` never raises with a value in the message: the parser never raises, and Telegram errors become `EquityStoreError("store unavailable")`. The `getChat` response is parsed in memory and never printed. No new file write, no `common.log` and no `common.summary` touches E.
- **Tests:**
  - `test_trace_prints_no_message`: an exception carrying a sentinel prints its type and frames, never the sentinel.
  - `test_privacy_end_to_end`: drives `/equity 31337.17` then `/ticket AAPL` through `listen.answer` with Telegram and Alpaca mocked. It captures stdout and stderr, every `common.log` and `common.summary` call, and every file opened for writing (patched `open`, `Path.write_text`, `Path.write_bytes`, `gzip.open`). The sentinel forms (E, the dollar risk, the position value; raw, comma and `common.price` forms) must appear only in `sendMessage`/`pinChatMessage` arguments.
  - `test_privacy_when_the_store_fails`: the same with `getChat` and `pinChatMessage` raising. The printed traceback holds no sentinel, and the owner gets a Hebrew "could not read or save the equity" line.

**3. Fallback and strict parsing.**
- *Met by:* `load()` returns None for no pin, a pin from someone else, a bot message without the exact layout, a value out of bounds, or any Telegram error. `/ticket` then uses `TICKET_RISK_USD` and adds "הגודל לא קשור לחשבון" ("the size is not tied to the account").
- *`/equity` parsing:* exactly one token after the command, matching `^[0-9]{1,3}(,[0-9]{3})*(\.[0-9]{1,2})?$` or `^[0-9]+(\.[0-9]{1,2})?$`, and within **$100 ≤ E ≤ $100,000,000**. Anything else gets a usage reply that does not echo the input: words, two numbers, negatives, `0`, `1e5`, `25k`, `25,00`, and anything out of bounds. The store is not changed.
- **Tests:**
  - `test_parse_strict`: every accepted and rejected form, with both bounds at the edge ($100 and $99.99; $100,000,000 and $100,000,000.01).
  - `test_load_fallbacks`: no pin; the owner's own pin; another bot's message; the marker with a changed layout; out of bounds; a `getChat` error. Each gives None.
  - `test_ticket_fallback_line`: no E gives the fixed risk and the line.

**4. Dry runs on GitHub use only a synthetic E, marked as synthetic.**
- *Met by:* `tests/listen_dry_run.py` gets `--synthetic-equity`. It patches `equity.load` to return the constant `SYNTHETIC_E = 12_345.67` and never calls `getChat` (the dry-run job has no Telegram secrets).
  - Every message it prints is prefixed `[E סינתטי לבדיקה]` ("synthetic E for testing").
  - The run's first log line states that E is synthetic.
  - The harness refuses to run (exit 2) when `TG_TOKEN` is set, so it can never read the real pin.
- **Tests:**
  - `test_dry_run_synthetic_marked`: every printed message carries the marker and the synthetic value; `getChat` is never called.
  - `test_dry_run_refuses_with_a_token`: with `TG_TOKEN` set, exit 2.

**5. Proof.**
- A test for each condition (above).
- **Live check after the first real `/equity`**, after a merge approved by the owner:
  1. Download the public log of the listener run that handled it (`gh run view <id> --log`) and its summary (the job summary through `gh api`).
  2. Check that every line of the listener step matches the whitelist of known line shapes. These are `update N: handling`, `serve_end` JSON, `getUpdates` retries, `runs API`, `data refresh` and the frame lines of `common.trace`.
  3. Check that no line contains `$`, "הון" (equity) or a number with a thousands comma.
  4. I never know the real E. So the owner runs one local command that searches the log for their own E and prints only the count:

     ```bash
     gh run view <run id> --repo danielsuisa/stockbot --log | grep -c "<E>"
     ```

     Expected: `0`. The value stays on the owner's machine.

## Decisions (for the owner's review)

1. **Bounds** for E: $100 to $100,000,000.
2. **Formats.** Plain digits or thousands commas, at most 2 decimals. No `k`, no exponents, no currency sign.
3. **Not shown.** `/equity` replies show E, p (with the cap note), the risk per trade, the open-risk positions and the small-account warning. They never show a ticket.
4. **Logging change.** `common.trace` replaces the three `print_exc()` calls for every command, not only `/equity` and `/ticket`. This keeps one rule in the listener, at the cost of exception messages in the logs (types and frames stay).
5. **Pin replaced.** If the owner pins something else, E reads as not set, and `/equity` without an amount says "לא נקבע (הנעיצה הוחלפה או הוסרה)" ("not set: the pin was replaced or removed").

## Review Focus

1. A pinned message edited by the owner keeps `from.id` = the bot only if the bot sent it. Owners cannot edit bot messages, so only the bot's exact layout counts.
2. Two `/equity` in a row: the newer pin wins (`getChat` returns the most recent pin), and the older is unpinned.
3. A huge E with a tiny R is cut by the position cap. A tiny E gives the small-account refusal, never a bare "shares below 2".
4. An exception in any listener command prints no message text (condition 2).
5. Phase B's message without E is unchanged, apart from the fallback line.

---

### Task 0: Live probe of the Telegram behaviours (owner's change 2)

**Files:** Create `bot/equity.py` with `probe() -> (private: bool, pinned: bool, pinned_is_ours: bool)` and `main(["--probe"])`. Throwaway branch `probe-ensemble` with a workflow that runs `python -m bot.equity --probe` with `TG_TOKEN` and `TG_CHAT_ID`.

- [ ] **Step 1: Write the failing test** `test_probe_prints_three_booleans_only`, with `common.tg` mocked:
  - `probe()` calls, in order, `getMe`, `getChat`, `sendMessage` (a text with no digit), `pinChatMessage` (`disable_notification`), `getChat`, `unpinChatMessage` and `deleteMessage`;
  - it returns the three booleans;
  - `main` prints exactly three lines of the form `name: True|False` and nothing else;
  - the message is deleted even when a step fails.
- [ ] **Step 2: Run.** Expect FAIL. **Step 3: Implement.** **Step 4: Run the full suite.** Expect OK. **Step 5: Commit.**
- [ ] **Step 6: Live probe.** Run once on GitHub. If any boolean is false: stop and report to the owner, and do not build the store.

### Task 1: Sizing from equity (pure)

**Files:** Modify `bot/levels.py`; Test `tests/test_levels.py`.

**Interfaces (produces):**
- Constants: `P_DEFAULT, P_CAP_UNPROVEN, P_CAP_PROVEN, POSITION_CAP, OPEN_RISK, SMALL_RISK = 0.005, 0.005, 0.01, 0.20, 0.03, 20.0`; `EDGE_PROVEN = False`.
- `p_used(p, edge_proven=EDGE_PROVEN) -> (float, bool)`.
- `equity_size(E, p, R, P) -> {"risk", "shares", "position_capped"}`.
- `open_positions(p) -> int`.
- `plan(..., max_shares=None)`.

- [ ] **Step 1: Write the failing tests** (synthetic E 31,337.17).
  - `test_equity_size`: p 0.005 and R 2 give risk 156.68585 and 78 shares; with P 50.51 the cap of 124 does not bind.
  - `test_position_cap_threshold`: E 10,000, R 0.10 (500 shares by risk). P 4.00 gives a cap of 500, so not capped; P 4.01 gives 498, capped.
  - `test_p_cap_by_label`: `p_used(0.01)` gives (0.005, True); `p_used(0.004)` gives (0.004, False); with `edge_proven` true, `p_used(0.02)` gives (0.01, True).
  - `test_open_positions`: 0.005 gives 6, 0.004 gives 7, 0.01 gives 3.
  - `test_plan_with_max_shares`: 60 shares give legs (30, 30).
  - `test_small_equity_cost_refusal`: E 3,000 and R 2 give 7 shares; spread 0.02 gives (1.40 + 0.14) / 14 > 0.10, so a refusal.
- [ ] **Step 2: Run.** Expect FAIL. **Step 3: Implement.** **Step 4: Run the full suite.** Expect OK. **Step 5: Commit.**

### Task 2: Listener log hardening (condition 2)

**Files:** Modify `bot/common.py` (`trace`, `install_excepthooks`), `bot/listen.py` (five `print_exc` calls, the hooks at `main`), `bot/check.py`, `bot/scan.py`, `bot/shadow.py`, `bot/squeeze_live.py` (seven more; change 3); Test `tests/test_serve.py`, `tests/test_commands.py`.

- [ ] **Step 1: Write the failing tests.**
  - `test_trace_prints_no_message`.
  - `test_answer_failure_prints_no_text`: `handle` raises `ValueError("could not convert '31337.17x'")`. stdout and stderr hold the type and frames, and not `31337.17`. The owner still gets the Hebrew failure line.
  - The same for `/ticket` per symbol and for the serve loop.
- [ ] **Steps 2–5:** run (FAIL), implement, run the full suite (OK), commit.

### Task 3: The store and `/equity` (conditions 1 and 3)

**Files:** Create `bot/equity.py`; Modify `bot/listen.py` (`COMMANDS`, `HELP`, `equity_cmd`); Test `tests/test_equity.py`, `tests/test_commands.py`.

**Interfaces (produces):**
- `MARKER`, `MIN_E, MAX_E = 100.0, 100_000_000.0`.
- `parse_amount(rest) -> float | None`, which never raises.
- `store(E) -> bool`: send, pin, unpin the previous own pin. Errors become `EquityStoreError`.
- `load() -> float | None`.
- `p_setting() -> float`.
- `reply(E) -> str`.

- [ ] **Step 1: Write the failing tests:** `test_parse_strict`, `test_store_and_load`, `test_load_fallbacks`, `test_two_equity_newest_wins`, `test_reply` (risk, positions, cap note, small-account warning, `msgrules`), `test_equity_command` (set, show, usage without echo), `test_equity_from_another_chat_is_ignored`, `test_help_and_menu` (adds `equity`).
- [ ] **Steps 2–5:** run (FAIL), implement, run the full suite (OK), commit.

### Task 4: `/ticket` sized by E

**Files:** Modify `bot/ticket_command.py` and `.github/workflows/telegram-listen.yml` (`TICKET_RISK_PCT: ${{ vars.TICKET_RISK_PCT }}`); Test `tests/test_ticket_command.py`, `tests/test_serve.py`.

- [ ] **Step 1: Write the failing tests.**
  - `test_build_with_equity`.
  - `test_message_with_equity`: the risk in $ and as % of E, the position value in $ and as % of E, the positions; `msgrules` passes.
  - `test_message_caps_noted`.
  - `test_ticket_fallback_line`.
  - `test_small_account_refusal_reason`.
  - `test_workflow_has_the_p_variable`.
- [ ] **Steps 2–5:** run (FAIL), implement, run the full suite (OK), commit. The phase B dry-run `main` keeps the fixed risk and never reads E.

### Task 5: Privacy proof, synthetic dry run, review, report (conditions 4 and 5)

**Files:** Create `tests/test_equity_privacy.py` and `docs/backtest/sizing-phaseB1-<date>.md`; Modify `tests/listen_dry_run.py` (`--synthetic-equity`). Throwaway branch `probe-ensemble` (deleted afterwards).

- [ ] **Step 1: Write the failing tests:** `test_privacy_end_to_end`, `test_privacy_when_the_store_fails`, `test_dry_run_synthetic_marked`, `test_dry_run_refuses_with_a_token`. The end-to-end test is shown to catch a leak first, against a temporary `print(E)` in `equity_cmd` that is removed before the commit.
- [ ] **Step 2:** run the full suite (OK) and commit.
- [ ] **Step 3: Synthetic dry run on GitHub.** Run `python -m tests.listen_dry_run --synthetic-equity "/equity 12345.67" "/ticket AAPL"` with the listener step's environment and no Telegram secrets. Expected: every message marked synthetic, exit 0.
- [ ] **Step 4: Independent review** (a reviewer who did not write the code, most capable model) of the code, the tests and the privacy proof. Fix confirmed findings with tests.
- [ ] **Step 5:** report and commit; open a PR to main; no merge without the owner's approval.
- [ ] **Step 6: After the owner approves the merge and sends the first real `/equity`:** run the live log check of condition 5 (whitelist, patterns, and the owner's local count of their own E). Report the result.
