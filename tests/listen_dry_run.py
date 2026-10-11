"""A dry run of the listener's own update path: one fake update from the owner's chat goes through `listen.answer`
(the ACK, the chat check, `handle`, the per-message error guard) with Telegram replaced by print. Nothing is sent and
no update is consumed. Run it with the listener workflow's environment (SEC_UA, the Alpaca keys; no Telegram token):

    python -m tests.listen_dry_run "/ticket AAPL BRK.B ZZZZZ"

Exit 0 when every message was answered without a failure."""
import os
import sys
from unittest import mock

from bot import common, listen

CHAT = "dry-run"


def main(text):
    os.environ["TG_CHAT_ID"] = CHAT  # the fake update's chat: a test value, never the real chat id
    update = {"update_id": 1, "message": {"chat": {"id": CHAT}, "text": text}}
    answers = []

    def tg(method, **params):
        if method == "getUpdates":  # the ACK of the batch: nothing to consume in a dry run
            return []
        print(f"[telegram {method} skipped]")
        return {}

    def send(msg, **kw):
        answers.append(msg)
        print(msg)
        if kw.get("source"):
            print(kw["source"])
        print()

    with mock.patch.object(common, "tg", side_effect=tg), mock.patch.object(common, "send", side_effect=send):
        failed = listen.answer([update])
    print(f"answers: {len(answers)}, failed: {failed}")
    return 0 if failed == 0 and answers else 1


if __name__ == "__main__":
    raise SystemExit(main(" ".join(sys.argv[1:]) or "/ticket AAPL BRK.B ZZZZZ"))
