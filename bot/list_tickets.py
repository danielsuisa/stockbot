"""Tickets for the squeeze lists (docs/superpowers/specs/2026-10-13-list-tickets-design.md sections 2 and 3).

When a squeeze list run sends a list, the names new to it (at most 5, by rank) get the same ticket as `/ticket SYMBOL`
at that moment (`ticket_command.build`: signal day, bars, guard quote, sizing from the pinned equity, refusals,
label), in one message that carries the list's measured fact once. Every issued ticket goes to the paper journal
(`data/ticket_journal.json`) at the backtest's fixed $100 risk, never at E: the equity and every number derived from
it appear only in the Telegram message. Outcomes are filled later by the backtest's own functions (`levels.fill`,
`levels.exits`, `levels.gross_r`, `levels.cost_r`) on the daily bars after the signal day. Information only: no rule
decides anything from the journal, and the registered forward test (bot/squeeze_forward.py) is separate."""
import datetime as dt
import os
import statistics

from bot import alpaca, common, equity, levels, ticket_command, ticket_journal
from bot.common import code

MAX = 5  # spec 2: at most 5 tickets a list, by rank
RISK = 100.0  # the journal's fixed risk (the backtest's), so no number derived from E is written
SMALL = 100  # spec 3: "מדגם קטן" below 100 closed tickets
FACT = (f"על {code('1,330')} בחירות היסטוריות של הרשימה ({code(2024)} עד {code(2026)}), קנייה עם סטופ של "
        f"{code('10%')} בלי יעד הניבה בממוצע ירידה של {code('0.1%')} לעסקה לפני עלויות, ו-{code('34%')} מהעסקאות "
        "הרוויחו")  # docs/backtest/squeeze-2026-10-13-reanalysis.md: stop 0.10, no target: -0.0011; 33.7% above zero


def new_names(rows, old_rows, limit=MAX):
    """The list's names that were not in the previous list of the same day and kind (all of them for a first list),
    as (rank, symbol) in rank order, at most `limit`."""
    old = {r["t"] for r in old_rows or ()}
    return [(k, r["t"]) for k, r in enumerate(rows, 1) if r["t"] not in old][:limit]


def load_sizing():
    """The sizing `/ticket` uses (spec section 10): E from the owner's pinned message, or None (fixed risk)."""
    got = equity.load()
    if not got:
        return None
    p_raw, p_note = equity.p_setting()
    return {"E": got[0], "date": got[1], "p_raw": p_raw, "p_note": p_note}


def build_all(names, sizing):
    """`ticket_command.build` for each (rank, symbol) -> [(rank, symbol, ticket or None, error type or None)]; one
    symbol's failure never stops the others."""
    out = []
    for rank, sym in names:
        try:
            s = sym.replace("-", ".")
            t = ticket_command.build(s, sizing=sizing) if sizing else ticket_command.build(s)
            out.append((rank, sym, t, None))
        except Exception as e:  # noqa: BLE001 - missing keys, network, bad data: the type only, never a value
            common.trace()
            out.append((rank, sym, None, type(e).__name__))
    return out


def message(items):
    """The one message: a header, the list's measured fact once, then each ticket as `/ticket` writes it (without its
    35,083-ticket line) or a one-line refusal."""
    lines = ["🎫 <b>כרטיסים לשמות החדשים ברשימת הסקוויז</b>", FACT, ""]
    for rank, sym, t, err in items:
        if err:
            lines.append(f"מקום {code(rank)}: לא הצלחתי להכין כרטיס ל-{code(sym)}: תקלה בקריאת הנתונים ({code(err)}).")
        elif not t["ok"]:
            lines.append(f"מקום {code(rank)}: " + ticket_command.message(t).splitlines()[0])
        else:
            lines += [ticket_command.message(t, measured=False, origin=f"רשימת הסקוויז, מקום {code(rank)}"), ""]
    return "\n".join(lines).strip()


# ---------- the paper journal ----------
def journal_record(t, rank):
    """A journal record of an issued ticket at the fixed $100 risk: the ticket's own levels, with shares and legs
    recomputed as floor(100 / R) (at least 2), so nothing derived from E is written."""
    shares = max(int(RISK / t["R"] + levels.EPS), levels.MIN_SHARES)
    rec = ticket_journal.record({**t, "signal": "squeeze-list", "shares": shares,
                                 "legs": (shares // 2, shares - shares // 2)})
    rec.update(source="squeeze", rank=rank, stop_capped=bool(t.get("stop_capped")))
    return rec


def journal(path, items):
    """Append each issued ticket once per symbol and signal day -> the number added."""
    j = ticket_journal.load(path)
    seen = {(r.get("symbol"), r.get("day")) for r in j["tickets"] if r.get("source") == "squeeze"}
    added = 0
    for rank, _sym, t, err in items:
        if err or not t.get("ok") or (t["symbol"], t["day"]) in seen:
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
    (completed sessions only) -> the number filled. Missing data waits for the next run."""
    end = (dt.date.fromisoformat(today) - dt.timedelta(1)).isoformat()
    filled = 0
    for rec in j["tickets"]:
        if rec.get("source") != "squeeze" or rec.get("outcome") is not None:
            continue
        start = (dt.date.fromisoformat(rec["day"]) + dt.timedelta(1)).isoformat()
        if end < start:
            continue
        got = alpaca.daily([rec["symbol"]], start, end).get(rec["symbol"]) or []
        rows = [tuple(b[:6]) for b in got if b[0] > rec["day"]]
        o = outcome(rec, rows)
        if o is not None:
            rec["outcome"] = o
            filled += 1
    return filled


def save(path, j):
    tmp = f"{path}.tmp"
    import json
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
    closed = [r["outcome"] for r in recs if r.get("outcome")]
    small = f" (מדגם קטן, פחות מ־{code(SMALL)})" if len(closed) < SMALL else ""
    lines.append(f"הונפקו {code(len(recs))} כרטיסים: {code(len(closed))} נסגרו, {code(len(recs) - len(closed))} "
                 f"פתוחים{small}.")
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
def run(rows, old_rows, path):
    """The squeeze run's part: the names new to the list sent, their tickets, the journal -> (message or "", counts).
    The counts (names, issued, journaled) are the only numbers the run may log."""
    names = new_names(rows, old_rows)
    if not names:
        return "", {"names": 0, "issued": 0, "journaled": 0}
    items = build_all(names, load_sizing())
    issued = sum(1 for _r, _s, t, err in items if not err and t.get("ok"))
    return message(items), {"names": len(names), "issued": issued, "journaled": journal(path, items)}
