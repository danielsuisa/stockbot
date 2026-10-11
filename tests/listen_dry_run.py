"""A dry run of the listener's own update path: fake updates from the owner's chat go through `listen.answer` (the ACK,
the chat check, `handle`, the per-message error guard) with Telegram replaced by print. Nothing is sent and no update
is consumed. Run it with the listener workflow's environment (SEC_UA, the Alpaca keys) and never with a Telegram
token: it refuses to run when TG_TOKEN is set, so it can never read the owner's pinned equity.

    python -m tests.listen_dry_run "/ticket AAPL BRK.B ZZZZZ"
    python -m tests.listen_dry_run --synthetic-equity "/equity 12345.67" "/ticket AAPL"

With --synthetic-equity the equity store is replaced by the synthetic E below (spec section 10, owner's condition 4):
every message is prefixed with SYNTHETIC_MARK and the run's first line says so. Exit 0 when every message was answered
without a failure; 2 when refused."""
import contextlib
import datetime as dt
import os
import sys
from unittest import mock

from bot import common, equity, listen

CHAT = "dry-run"
SYNTHETIC_E = 12_345.67  # synthetic, never an account's equity
SYNTHETIC_MARK = "[E סינתטי לבדיקה]"


def main(texts, synthetic=False):
    texts = [texts] if isinstance(texts, str) else list(texts)
    if common.env("TG_TOKEN"):
        print("refused: a dry run never runs with a Telegram token (it could read the real pinned equity)")
        return 2
    if synthetic and any(t.split()[:1] == ["/equity"] and t.split()[1:] and
                         equity.parse_amount(" ".join(t.split()[1:])) != SYNTHETIC_E for t in texts):
        print(f"refused: a synthetic dry run takes only the synthetic E ({SYNTHETIC_E:,.2f})")
        return 2
    os.environ["TG_CHAT_ID"] = CHAT  # the fake updates' chat: a test value, never the real chat id
    answers = []

    def tg(method, **params):
        if method == "getUpdates":  # the ACK of the batch: nothing to consume in a dry run
            return []
        print(f"[telegram {method} skipped]")
        return {}

    def send(msg, **kw):
        answers.append(msg)
        print(f"{SYNTHETIC_MARK} {msg}" if synthetic else msg)
        if kw.get("source"):
            print(kw["source"])
        print()

    patches = [mock.patch.object(common, "tg", side_effect=tg), mock.patch.object(common, "send", side_effect=send)]
    if synthetic:
        today = dt.datetime.now(common.IL).date().isoformat()
        print(f"SYNTHETIC EQUITY: every equity figure below comes from the synthetic E {SYNTHETIC_E:,.2f}, "
              "not from an account")
        patches += [mock.patch.object(equity, "load", return_value=(SYNTHETIC_E, today)),
                    mock.patch.object(equity, "store", return_value=True)]
    failed = 0
    with contextlib.ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        for i, text in enumerate(texts):
            failed += listen.answer([{"update_id": i + 1, "message": {"chat": {"id": CHAT}, "text": text}}])
    print(f"answers: {len(answers)}, failed: {failed}")
    return 0 if failed == 0 and answers else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    synth = "--synthetic-equity" in args
    args = [a for a in args if a != "--synthetic-equity"]
    raise SystemExit(main(args if len(args) > 1 else (" ".join(args) or "/ticket AAPL BRK.B ZZZZZ"), synth))
