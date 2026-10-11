"""The account's equity E for sizing live tickets (docs/superpowers/specs/2026-10-10-recommendation-algorithm-design.md
section 10, phase B.1): kept only in Telegram, as a bot message pinned in the owner's private chat. E, the risk in
dollars and the position value never reach a log line, an exception message, a workflow summary or a file.

`python -m bot.equity --probe` checks the two Telegram behaviours the store rests on and prints three booleans only."""
import sys

from bot import common

PROBE_TEXT = "בדיקת נעיצה של הבוט: ההודעה תימחק מיד."  # no number in it


def _chat():
    return common.env("TG_CHAT_ID")


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
