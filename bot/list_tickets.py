"""Tickets for the squeeze lists (docs/superpowers/specs/2026-10-13-list-tickets-design.md sections 2 and 3).

When a squeeze list run sends a list, the names new to it that day (at most 5, by rank) get the same ticket as
`/ticket SYMBOL` at that moment (`ticket_command.build`: signal day, bars, guard quote, sizing from the pinned equity,
refusals, label), in one message that carries the list's measured fact once.

Privacy: the equity E and every number derived from it appear only in that Telegram message. The paper journal
(`data/ticket_journal.json`) is fed by a second build of the same ticket at the backtest's fixed $100 risk, so which
tickets are journaled, their sizes and the logged counts never depend on E. Outcomes are filled later by the
backtest's own functions (`levels.fill`, `levels.exits`, `levels.gross_r`, `levels.cost_r`) on the completed daily
bars after the signal day. Information only: no rule decides anything from the journal, and the registered forward
test (bot/squeeze_forward.py) is separate."""
import datetime as dt
import json
import os
import statistics

from bot import alpaca, common, equity, levels, ticket_command, ticket_journal
from bot.common import code

MAX = 5  # spec 2: at most 5 tickets a list, by rank
SMALL = 100  # spec 3: "מדגם קטן" below 100 closed tickets
PART_MAX = 3500  # visible characters a Telegram part may hold here (common.send adds its tail; the limit is 4096)
FACT = (f"על {code('1,330')} בחירות היסטוריות של הרשימה ({code(2024)} עד {code(2026)}), קנייה עם סטופ של "
        f"{code('10%')} בלי יעד הניבה בממוצע ירידה של {code('0.1%')} לעסקה לפני עלויות, ו-{code('34%')} מהעסקאות "
        "הרוויחו")  # docs/backtest/squeeze-2026-10-13-reanalysis.md: stop 0.10, no target: -0.0011; 33.7% above zero


def new_names(rows, old_rows, other_rows=None, limit=MAX):
    """The list's names that were in neither the previous list of the same day and kind nor the day's list of the
    other kind (pre-market / in session), as (rank, symbol) in rank order, at most `limit`."""
    old = {r["t"] for r in (old_rows or ())} | {r["t"] for r in (other_rows or ())}
    return [(k, r["t"]) for k, r in enumerate(rows, 1) if r["t"] not in old][:limit]


def load_sizing():
    """The sizing `/ticket` uses (spec section 10): E from the owner's pinned message, or None (fixed risk)."""
    got = equity.load()
    if not got:
        return None
    p_raw, p_note = equity.p_setting()
    return {"E": got[0], "date": got[1], "p_raw": p_raw, "p_note": p_note}


def build_all(names, sizing):
    """For each (rank, symbol): the message's ticket (E-sized when an equity is pinned) and the journal's ticket at
    the fixed $100 -> ([(rank, symbol, ticket or None, error type or None)], [(rank, symbol, $100 ticket)])."""
    items, fixed = [], []
    for rank, sym in names:
        s = sym.replace("-", ".")
        try:
            t = ticket_command.build(s, sizing=sizing) if sizing else ticket_command.build(s)
            fixed.append((rank, s, ticket_command.build(s) if sizing else t))  # the second build reads the cache
            items.append((rank, s, t, None))
        except Exception as e:  # noqa: BLE001 - missing keys, network, bad data: the type only, never a value
            common.trace()
            items.append((rank, s, None, type(e).__name__))
    return items, fixed


def _blocks(items):
    """The message's blocks: each ticket as `/ticket` writes it (without its 35,083-ticket line) or a one-line
    refusal."""
    out = []
    for rank, sym, t, err in items:
        if err:
            out.append(f"מקום {code(rank)}: לא הצלחתי להכין כרטיס ל-{code(sym)}: תקלה בקריאת הנתונים ({code(err)}).")
        elif not t["ok"]:
            out.append(f"מקום {code(rank)}: " + ticket_command.message(t).splitlines()[0])
        else:
            out.append(ticket_command.message(t, measured=False, origin=f"רשימת הסקוויז, מקום {code(rank)}"))
    return out


def parts(items):
    """The one message, as Telegram parts that break between tickets only: the header and the list's measured fact
    once, then the tickets."""
    head = "\n".join(["🎫 <b>כרטיסים לשמות החדשים ברשימת הסקוויז</b>", FACT])
    out, cur = [], head
    for block in _blocks(items):
        if common.visible(cur) + common.visible(block) + 2 > PART_MAX:
            out.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}"
    out.append(cur)
    return out


# ---------- the paper journal ----------
def journal_record(t, rank):
    """The journal record of a $100 ticket (the format of bot/ticket_journal.py), with its source, rank, the cap
    flag, and whether it was issued after its first valid session had opened (its first session's moves were
    partly known: such tickets are kept out of the stats)."""
    rec = ticket_journal.record({**t, "signal": "squeeze-list"})
    rec.update(source="squeeze", rank=rank, stop_capped=bool(t.get("stop_capped")),
               issued_in_first_session=bool(t.get("first_session_started")))
    return rec


def journal(path, fixed):
    """Append each issued $100 ticket once per symbol and signal day -> the number added."""
    j = ticket_journal.load(path)
    seen = {(r.get("symbol"), r.get("day")) for r in j["tickets"] if r.get("source") == "squeeze"}
    added = 0
    for rank, _sym, t in fixed:
        if not t.get("ok") or (t["symbol"], t["day"]) in seen:
            continue
        seen.add((t["symbol"], t["day"]))
        ticket_journal.append(path, journal_record(t, rank))
        added += 1
    return added


def as_ticket(rec):
    """The journal record as the levels engine's ticket."""
    keys = ("mode", "horizon", "entry_stop", "limit", "planned_entry", "R", "shares")
    return {**{k: rec[k] for k in keys}, "legs": tuple(rec["legs"])}


def outcome(rec, rows):
    """The ticket's outcome on the daily bars after its signal day (the backtest's functions), or None while it can
    still fill or a leg is still open."""
    t = as_ticket(rec)
    f = levels.fill(t, rows)
    need = 1 if t["mode"] == "open" else levels.BREAKOUT_DAYS
    if f is None:
        if len(rows) < need:
            return None
        return {"filled": False, "net_r": 0.0, "gross_r": 0.0, "cost_r": 0.0, "closed": rows[need - 1][0]}
    k, F = f
    ex = levels.exits(t, F, k, rows)
    if ex is None:
        return None
    gross, cost = levels.gross_r(t, F, ex), levels.cost_r(t, rec["guard_spread"])
    return {"filled": True, "fill": F, "entry_day": rows[k][0], "exits": [e[0] for e in ex],
            "exit_days": [rows[e[2]][0] for e in ex], "gross_r": gross, "cost_r": cost, "net_r": gross - cost,
            "closed": max(rows[e[2]][0] for e in ex)}


def followup(j, today):
    """Fill the outcome of every open squeeze ticket from its daily bars after the signal day through yesterday
    (completed sessions only) -> the number filled. A record that fails waits for the next run; the others go on."""
    end = (dt.date.fromisoformat(today) - dt.timedelta(1)).isoformat()
    filled = 0
    for rec in j["tickets"]:
        if rec.get("source") != "squeeze" or rec.get("outcome") is not None:
            continue
        start = (dt.date.fromisoformat(rec["day"]) + dt.timedelta(1)).isoformat()
        if end < start:
            continue
        try:
            got = alpaca.daily([rec["symbol"]], start, end).get(rec["symbol"]) or []
            o = outcome(rec, [tuple(b[:6]) for b in got if b[0] > rec["day"]])
        except Exception:  # noqa: BLE001 - one symbol's data problem never blocks the others; the type only
            common.trace()
            continue
        if o is not None:
            rec["outcome"] = o
            filled += 1
    return filled


def save(path, j):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(j, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------- /squeeze stats ----------
def _risk(x):
    if round(abs(x) * 100) == 0:
        return f"{code('0%')} מהסיכון"
    return f"{'רווח' if x > 0 else 'הפסד'} של {code(f'{abs(x) * 100:.0f}%')} מהסיכון"


def stats_lines(j):
    """Spec 3: the "כרטיסים" section of /squeeze stats (information only)."""
    recs = [r for r in j.get("tickets", []) if r.get("source") == "squeeze"]
    lines = ["🎫 <b>כרטיסים</b> (מעקב נייר על הרשימה, למידע בלבד):"]
    if not recs:
        return lines + ["עוד אין כרטיסים מהרשימה."]
    late = [r for r in recs if r.get("issued_in_first_session")]
    counted = [r for r in recs if not r.get("issued_in_first_session")]
    closed = [r["outcome"] for r in counted if r.get("outcome")]
    small = f" (מדגם קטן, פחות מ־{code(SMALL)})" if len(closed) < SMALL else ""
    lines.append(f"הונפקו {code(len(recs))} כרטיסים: {code(len(closed))} נסגרו, "
                 f"{code(sum(1 for r in counted if not r.get('outcome')))} פתוחים{small}.")
    if late:
        lines.append(f"הונפקו כשיום הכניסה הראשון כבר נסחר, ולכן אינם נכללים במספרים: {code(len(late))} כרטיסים.")
    if not closed:
        return lines
    pct = lambda x: code(f"{x * 100:.0f}%")  # noqa: E731
    net = [o["net_r"] for o in closed]
    wins, losses = [v for v in net if v > 0], [v for v in net if v < 0]
    lines.append(f"מולאו {pct(sum(1 for o in closed if o['filled']) / len(closed))} מהכרטיסים שנסגרו · ממוצע נטו "
                 f"לכרטיס: {_risk(statistics.mean(net))} · {pct(len(wins) / len(closed))} הרוויחו.")
    lines.append(f"רווח ממוצע בכרטיס מרוויח: {pct(statistics.mean(wins)) if wins else '—'} מהסיכון · הפסד ממוצע "
                 f"בכרטיס מפסיד: {pct(-statistics.mean(losses)) if losses else '—'} מהסיכון.")
    return lines


# ---------- one squeeze run ----------
def run(rows, old_rows, other_rows, path):
    """The squeeze run's part: the names new to the list that day, their tickets, the journal -> (message parts,
    counts). The counts (names, journaled) never depend on E, so the run may log them."""
    names = new_names(rows, old_rows, other_rows)
    if not names:
        return [], {"names": 0, "journaled": 0}
    items, fixed = build_all(names, load_sizing())
    return parts(items), {"names": len(names), "journaled": journal(path, fixed)}
