"""Screener presets: the version-controlled defaults (presets.yaml) and the owner's own (data/screener_presets.json,
written only by the screener workflow), plus the scheduled runs' state (data/screener_state.json: last result per
preset for new-or-changed alerts, slots already served). Files are written atomically, like the squeeze's."""
import datetime as dt
import json
import os
import re
from pathlib import Path

from bot import common
from bot.screener import parser

DEFAULTS = Path(__file__).with_name("presets.yaml")
USER, STATE = "screener_presets.json", "screener_state.json"
NAME = re.compile(r"^[a-z0-9_-]{1,30}$")
SESSIONS = ("pre", "rth", "post")
MAX_USER = 30


def path(name):
    return Path(common.env("SCREENER_DATA", str(common.DATA))) / name


def _read(name, default):
    p = path(name)
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def _write(name, obj):
    p = path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)


def defaults():
    text = DEFAULTS.read_text("utf-8")
    rows = json.loads("\n".join(x for x in text.splitlines() if not x.lstrip().startswith("#")))["presets"]
    return [{**r, "builtin": True} for r in rows]


def user():
    return _read(USER, {"v": 1, "presets": []})["presets"]


def all_presets():
    """Defaults first, then the owner's (a user preset never shadows a default)."""
    d = defaults()
    names = {p["name"] for p in d}
    return d + [p for p in user() if p["name"] not in names]


def get(name):
    return next((p for p in all_presets() if p["name"] == name.lower()), None)


def every_minutes(text):
    """'60m' / '2h' / '1d' / '90' -> minutes, or None."""
    m = re.fullmatch(r"(\d{1,4})([mhd]?)", (text or "").strip().lower())
    if not m:
        return None
    n = int(m.group(1)) * {"": 1, "m": 1, "h": 60, "d": 1440}[m.group(2)]
    return n if 30 <= n <= 10080 else None


class PresetError(ValueError):
    """Bad /screen save or /screen del input (the message is shown to the owner)."""


def parse_save(text):
    """'NAME CODES [o=...] [sessions=pre,rth] [every=60m]' -> preset dict (validated). PresetError on bad input."""
    parts = text.split()
    if len(parts) < 2:
        raise PresetError("usage")
    name = parts[0].lower()
    if not NAME.match(name):
        raise PresetError("name")
    sessions, every, rest = [], 60, []
    for tok in parts[1:]:
        low = tok.lower()
        if low.startswith("sessions="):
            sessions = [s for s in low.split("=", 1)[1].split(",") if s]
            if not sessions or any(s not in SESSIONS for s in sessions):
                raise PresetError("sessions")
        elif low.startswith("every="):
            every = every_minutes(low.split("=", 1)[1])
            if every is None:
                raise PresetError("every")
        else:
            rest.append(tok)
    try:
        q = parser.parse(" ".join(rest), strict=True)
    except parser.InputError as e:
        raise PresetError("codes") from e
    if not q.filters and not q.signal:
        raise PresetError("empty")
    return {"name": name, "title": name, "filters": ",".join(q.codes), "sort": None if not q.sort else
            ("-" if q.sort[1] else "") + q.sort[2], "sessions": sessions, "every": every, "max_results": 15,
            "alert": "new_or_changed", "enabled": bool(sessions)}


def save(preset):
    """Add or replace a user preset -> "added" | "replaced". PresetError("builtin") for a default's name."""
    if any(p["name"] == preset["name"] for p in defaults()):
        raise PresetError("builtin")
    data = _read(USER, {"v": 1, "presets": []})
    old = [p for p in data["presets"] if p["name"] != preset["name"]]
    if len(old) >= MAX_USER:
        raise PresetError("full")
    verb = "replaced" if len(old) < len(data["presets"]) else "added"
    data["presets"] = old + [preset]
    _write(USER, data)
    return verb


def delete(name):
    """Remove a user preset -> True if it existed. PresetError("builtin") for a default."""
    name = name.lower()
    if any(p["name"] == name for p in defaults()):
        raise PresetError("builtin")
    data = _read(USER, {"v": 1, "presets": []})
    keep = [p for p in data["presets"] if p["name"] != name]
    if len(keep) == len(data["presets"]):
        return False
    data["presets"] = keep
    _write(USER, data)
    return True


def query_text(preset):
    return " ".join(x for x in (preset["filters"], f"o={preset['sort']}" if preset.get("sort") else "") if x)


# ---------- scheduled state ----------
def load_state():
    return _read(STATE, {"v": 1, "presets": {}, "slots": {}})


def save_state(state):
    _write(STATE, state)


def claim(state, slot, day):
    """Record slot `slot` as served on New York date `day` -> False if it already ran (a retry or twin start)."""
    rec = state.setdefault("slots", {})
    if slot in rec.get(day, []):
        return False
    rec[day] = rec.get(day, []) + [slot]
    for d in sorted(rec)[:-7]:
        del rec[d]
    return True


def due(preset, session, now, state):
    """Is a scheduled preset due in `session` ("pre"|"rth"|"post") at `now` (aware)? Its last run must be at least
    `every` minutes ago (5 minutes of slack: slots are hourly)."""
    if not preset.get("enabled") or session not in (preset.get("sessions") or []):
        return False
    last = ((state.get("presets") or {}).get(preset["name"]) or {}).get("at")
    if not last:
        return True
    age = (now - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() / 60
    return age >= (preset.get("every") or 60) - 5
