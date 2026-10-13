# List tickets with a 10% stop: implementation plan

**Spec:** `docs/superpowers/specs/2026-10-13-list-tickets-design.md`.

**Goal:**
- the stop cap in `levels.plan`;
- the stop shown as a percentage in `/ticket`;
- one ticket message after each squeeze list that is sent;
- a paper journal of those tickets, with outcomes filled by the backtest's own functions;
- a "כרטיסים" section in `/squeeze stats`.

**Constraints:**
- Standard library only.
- E and every number derived from it appear in Telegram messages only, never in a file, a log or a summary.
- Nothing changes in the screen (`bot/screen.py`), the forward test (`bot/squeeze_forward.py`), the index program,
  the ensemble files, `/equity` or the secrets.
- No order to any broker.

## Task 1: the stop cap (`bot/levels.py`)

- **Code:** `STOP_CAP = 0.10`. In `plan`, P comes first. Then R = min(2 × ATR, 0.10 × P), and
  `t["stop_capped"]` is set when 0.10 × P < 2 × ATR. Shares, stop, target and trail follow from R as before.
  Every refusal stays.
- **Tests (`tests/test_levels_stop_cap.py`):**
  - a volatile synthetic stock (2 × ATR = 20% of P): R = 0.10 × P, the stop is 10% below P, the target is
    P + 1.5 R, the trail is R, shares = risk / R, and the cap flag is on;
  - a calm stock: R = 2 × ATR and the flag is off;
  - the boundary: 2 × ATR exactly 10% of P;
  - the refusals still come at the same places.
- **The existing levels tests:** they keep passing where 2 × ATR ≤ 10% of P. A fixture above that changes only
  where the test describes `/ticket`'s live behaviour.

## Task 2: the ticket message (`bot/ticket_command.py`)

- **The stop line** shows the stop as a percentage of P ("<code>10.0%</code> מתחת לכניסה"), and adds
  "סטופ מוגבל ל-10%" when the cap applied.
- **The measured line** gains "בסטופ של 2×ATR".
- **`message(t, measured=True)`:** the list message passes `measured=False` and carries the list's measured fact
  once instead.
- **Tests:** the percentage, the cap note only when capped, the measured line's new words, msgrules, and the
  privacy tests.

## Task 3: the list tickets (`bot/list_tickets.py`, new)

- **`new_names(scr_rows, old_rows, limit=5)`:** the names of the list not in the previous list of the same day
  and kind (all of them for a first list), in rank order, at most 5.
- **`build_all(names, sizing)`:** `ticket_command.build(sym, sizing=sizing)` for each name. A failure is one line,
  as in `/ticket`.
- **`message(tickets, failures)`:**
  - a header and the list's measured fact, once;
  - each ticket's text from `ticket_command.message(t, measured=False)`;
  - a refusal as one line.
- **`journal_record(t, rank)`:** the journal record at the backtest's fixed risk of $100. The levels are the
  ticket's own, and shares and legs are recomputed as floor(100 / R), at least 2, so nothing derived from E is
  written. The record adds `source: "squeeze"`, `rank` and `signal: "squeeze-list"`.
- **Squeeze run wiring (`bot/squeeze_live.main`):**
  - after the list is sent, the tickets message is sent;
  - each issued ticket is recorded in `data/ticket_journal.json`;
  - the log line carries counts only.
- **Workflow (`.github/workflows/squeeze.yml`):** the existing Alpaca secrets and the `TICKET_RISK_PCT` variable
  are added to the run step, as in `telegram-listen.yml`, and `data/ticket_journal.json` is added to the commit
  step.

## Task 4: the outcomes (`bot/list_tickets.followup`)

- For each journaled squeeze ticket with no outcome, read its daily bars after the signal day through yesterday
  (New York), with `alpaca.daily`.
- `levels.fill`:
  - none within the valid sessions: not filled, net 0;
  - fewer sessions than that: wait.
- `levels.exits`:
  - None: still open, wait;
  - otherwise: `gross_r` and `cost_r` (the guard spread), net = gross − cost, the exit of each leg, and the closing
    day.
- **Tests:** synthetic bars for an unfilled ticket, a stop, a target and trail, a time exit, and still open.

## Task 5: `/squeeze stats`, "כרטיסים" (`bot/squeeze_live.stats_text`)

- **The section shows:**
  - tickets, closed and open;
  - the filled share;
  - mean net, the winning share, mean win and mean loss, each as a percentage of the risk (the message rules
    allow no "R" unit);
  - "מדגם קטן" below 100 tickets.
- **Tests:** a hand case and msgrules.

## Review

An independent reviewer checks the build against the spec. Every finding is fixed or answered in the pull
request.
