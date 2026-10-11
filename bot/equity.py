"""The account's equity E for sizing live tickets (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md
section 10, phase B.1): kept only in Telegram, as a bot message pinned in the owner's private chat. E, the risk in
dollars and the position value never reach a log line, an exception message, a workflow summary or a file: nothing
here prints, and every error is raised without a value.

`python -m bot.equity --probe` checks the two Telegram behaviours the store rests on and prints three booleans only."""
import datetime as dt
import re
import sys

from bot import common, levels
from bot.common import code, il_date, price

PROBE_TEXT = "בדיקת נעיצה של הבוט: ההודעה תימחק מיד."  # no number in it
MARKER = "💼 הון לחישוב גודל"
MIN_E, MAX_E = 100.0, 100_000_000.0
_AMOUNT = re.compile(r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?")
_PINNED = re.compile(rf"{re.escape(MARKER)}: ([0-9]{{1,3}}(?:,[0-9]{{3}})*\.[0-9]{{2}})\$")
P_NOTES = {"unset": "לא הוגדר", "non-numeric": "אינו מספר", "not positive": "אינו חיובי"}


class EquityStoreError(Exception):
    """Telegram could not save or read the equity (never carries a value)."""


def _chat():
    return common.env("TG_CHAT_ID")


def parse_amount(rest):
    """`/equity`'s argument -> E, or None unless it is exactly one positive number (plain or with thousands commas,
    at most 2 decimals) between $100 and $100,000,000. Never raises."""
    toks = (rest or "").split()
    if len(toks) != 1 or not _AMOUNT.fullmatch(toks[0]):
        return None
    value = float(toks[0].replace(",", ""))
    return value if MIN_E <= value <= MAX_E else None


def date_of(unix):
    """A Telegram message date (Unix seconds) -> its ISO date in Israel."""
    return dt.datetime.fromtimestamp(unix, common.IL).date().isoformat()


def _pinned_equity(chat, me):
    """The chat's pinned message when it is this bot's equity message in the exact layout -> (message, E) or None."""
    pm = chat.get("pinned_message") or {}
    m = _PINNED.fullmatch(pm.get("text") or "")
    if not m or (pm.get("from") or {}).get("id") != me or me is None:
        return None
    value = float(m.group(1).replace(",", ""))
    return (pm, value) if MIN_E <= value <= MAX_E else None


def store(E):
    """Pin E in the owner's chat (silently), unpinning the previous equity message -> True; False when the chat is not
    private (a pin is visible to every member of a chat), in which case nothing is sent. EquityStoreError on a
    Telegram failure."""
    try:
        me = (common.tg("getMe") or {}).get("id")
        chat = common.tg("getChat", chat_id=_chat()) or {}
        if chat.get("type") != "private":
            return False
        old = _pinned_equity(chat, me)
        msg = common.tg("sendMessage", chat_id=_chat(), text=f"{MARKER}: {code(f'{E:,.2f}$')}", parse_mode="HTML",
                        disable_notification=True) or {}
        common.tg("pinChatMessage", chat_id=_chat(), message_id=msg.get("message_id"), disable_notification=True)
        if old:
            common.tg("unpinChatMessage", chat_id=_chat(), message_id=old[0].get("message_id"))
    except Exception:  # noqa: BLE001 - the original message could quote the value
        raise EquityStoreError("store unavailable") from None
    return True


def load():
    """(E, the ISO date it was set) from the pinned equity message, or None: no pin, a pin that is not this bot's
    equity message in the exact layout, a value out of bounds, a chat that is not private, or a Telegram error."""
    try:
        me = (common.tg("getMe") or {}).get("id")
        chat = common.tg("getChat", chat_id=_chat()) or {}
    except Exception:  # noqa: BLE001
        return None
    if chat.get("type") != "private":
        return None
    got = _pinned_equity(chat, me)
    if not got or not got[0].get("date"):
        return None
    return got[1], date_of(got[0]["date"])


def p_setting():
    """The risk share p from TICKET_RISK_PCT, in percent (0.5 means 0.5%) -> (p, None), or (0.005, the reason the
    default was used)."""
    raw = common.env("TICKET_RISK_PCT").strip()
    if not raw:
        return levels.P_DEFAULT, "unset"
    try:
        value = float(raw)
    except ValueError:
        return levels.P_DEFAULT, "non-numeric"
    if not value > 0 or value != value:
        return levels.P_DEFAULT, "not positive"
    return value / 100, None


def pct(p):
    return f"{p * 100:g}%"


def p_notes(p_raw, p_note):
    """The Hebrew notes on p: the default used, the cap of the current label -> (p used, [lines])."""
    p, cut = levels.p_used(p_raw)
    lines = []
    if p_note:
        lines.append(f"שים לב: הערך {code('TICKET_RISK_PCT')} {P_NOTES[p_note]}, ולכן נלקחה ברירת המחדל "
                     f"{code(pct(levels.P_DEFAULT))}.")
    if cut:
        lines.append(f"שים לב: הסיכון לעסקה הוגבל ל-{code(pct(p))} כל עוד התווית היא ללא יתרון מוכח.")
    return p, lines


def reply(E, day, p_raw, p_note):
    """The Hebrew answer to /equity: E and the date it was set, the risk per trade, the open-risk positions, notes."""
    p, notes = p_notes(p_raw, p_note)
    risk = p * E
    lines = [f"{MARKER}: {code(price(E))} · נקבע ב-{il_date(day)}",
             f"סיכון לעסקה: {code(pct(p))} מההון, כלומר {code(price(risk))} (הערך {code('TICKET_RISK_PCT')} הוא באחוזים: "
             f"{code('0.5')} פירושו {code('0.5%')})",
             f"מגבלת הסיכון הפתוח: עד {code(levels.open_positions(p))} פוזיציות פתוחות בבת אחת "
             f"({code(pct(levels.OPEN_RISK))} מההון; הבוט לא אוכף זאת)", *notes]
    if risk < levels.SMALL_RISK:
        lines.append(f"שים לב: חשבון קטן. סיכון של פחות מ-{code(price(levels.SMALL_RISK))} לעסקה נדחה כמעט תמיד בשומר "
                     f"העלות (עמלות של לפחות {code(price(1.40))} על ארבע פקודות).")
    return "\n".join(lines)


def probe():
    """Send a test message, pin it silently, read it back through getChat, then unpin and delete it -> (the chat is
    private, the pin succeeded, pinned_message is that message and its from.id is the bot's). No value is printed."""
    private = pinned = ours = False
    mid = None
    try:
        me = (common.tg("getMe") or {}).get("id")
        private = (common.tg("getChat", chat_id=_chat()) or {}).get("type") == "private"
        mid = (common.tg("sendMessage", chat_id=_chat(), text=PROBE_TEXT, disable_notification=True)
               or {}).get("message_id")
        pinned = common.tg("pinChatMessage", chat_id=_chat(), message_id=mid, disable_notification=True) is True
        pm = (common.tg("getChat", chat_id=_chat()) or {}).get("pinned_message") or {}
        ours = pm.get("message_id") == mid and (pm.get("from") or {}).get("id") == me and me is not None
    except Exception:  # noqa: BLE001 - a failed step is a False, never a printed message
        pass
    finally:
        for method in ("unpinChatMessage", "deleteMessage"):
            if mid is not None:
                try:
                    common.tg(method, chat_id=_chat(), message_id=mid)
                except Exception:  # noqa: BLE001
                    pass
    return private, pinned, ours


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--probe"]:
        flags = probe()
        for name, value in zip(("private_chat", "pin_succeeded", "pinned_message_is_ours"), flags):
            print(f"{name}: {value}")
        return 0 if all(flags) else 1
    print("usage: python -m bot.equity --probe")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
