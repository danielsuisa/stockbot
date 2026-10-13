# Order tickets with a 10% stop for the squeeze lists, with a paper journal (2026-10-13)

The owner's decision (2026-10-13): **the stop loss of every position is at most 10% below the planned entry.**
The bot's purpose, in his words: find the candidates (the squeeze lists) and write the full order ticket for a
position: entry, stop loss, trailing stop, target and size. Nothing here claims an edge: every ticket keeps the
label "ללא יתרון מוכח" and a measured fact. The bot sends no order; real money is the owner's separate decision.

## Spec

**1. The stop cap, in the levels engine (one code path, `bot/levels.py`, `plan`).** R = min(2 x ATR14, 0.10 x P),
where P is the planned entry. Stop loss = P - R, target = P + 1.5 R, trail = R, shares = risk / R, as today.
Every refusal stays as it is. The ticket message shows the stop as a percentage of P, and says "סטופ מוגבל
ל-10%" when the cap applied. This applies to `/ticket` for any symbol and to the tickets of section 2. The closed
tests (ensemble, ZBA, the index program) are not run again; their records stand. The measured line of
`/ticket` (the 35,083 tickets) stays, with the words "בסטופ של 2×ATR" added, because those tickets had that stop.

**2. Tickets for the squeeze lists, automatically.** Whenever a squeeze list run sends a list (new or changed,
pre-market or in session), it also sends one message with the tickets of the names new to that list, at most 5
by the list's rank. Each ticket is computed by the same code path as `/ticket SYMBOL` at that moment: the same
signal day, bars, guard quote, sizing from the pinned equity, refusals and label; a refusal is one line. The
message carries, once, the measured fact of the list: "על 1,330 בחירות היסטוריות של הרשימה (2024 עד 2026), קנייה
עם סטופ של 10% בלי יעד הניבה בממוצע −0.1% לעסקה לפני עלויות, ו-34% מהעסקאות הרוויחו" (from
`docs/backtest/squeeze-2026-10-13-reanalysis.md`). Privacy: the equity E and every number derived from it appear
only in Telegram messages, never in a log, a file or a workflow summary; the workflow prints counts only.

**3. The paper journal.** Every ticket of section 2 is recorded in `data/ticket_journal.json` (the format of
`bot/ticket_journal.py`, plus `source: "squeeze"` and the list's rank). The squeeze run fills outcomes with the
backtest's own functions (`levels.fill`, `levels.exits`, `levels.gross_r`, `levels.cost_r`) on the daily bars
after the signal day: filled or not, the exit of each leg, gross R, cost R from the guard spread, net R. An open
ticket stays open until its legs close or the time exit comes. `/squeeze stats` gains a section "כרטיסים":
tickets, filled share, mean net R, share of winning tickets, mean win and mean loss in R, with "מדגם קטן" below
100 tickets. Information only: no rule decides anything from it, and it does not replace the registered forward
test (`bot/squeeze_forward.py`), which is not touched.

**4. Out of scope.** No change to the screen itself, to the forward test, to the index program's files, to the
ensemble files, to `/equity`, or to any secret. No new data source. No order to any broker.
