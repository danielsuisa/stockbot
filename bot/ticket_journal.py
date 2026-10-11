"""The ticket journal (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md section 8,
`data/ticket_journal.json`): every issued ticket with its levels, and later its outcomes. Phase C builds the format and
the writer only (ruling C22): the blind run writes no journal, and the live journal (with outcomes) is phase E."""
import json
import os

VERSION = 1
FIELDS = ("day", "signal", "symbol", "mode", "horizon", "entry_stop", "limit", "planned_entry", "stop_loss", "target",
          "trail", "shares", "legs", "R", "guard_spread", "outcome")


def record(t):
    """A ticket -> its journal record (levels and size only; "outcome" stays None until the forward check)."""
    rec = {k: t.get(k) for k in FIELDS}
    rec["legs"] = list(rec["legs"]) if rec["legs"] is not None else None
    rec["outcome"] = None
    return rec


def load(path):
    if not os.path.exists(path):
        return {"version": VERSION, "tickets": []}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def append(path, rec):
    j = load(path)
    j["tickets"].append(rec)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(j, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
