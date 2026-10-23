"""Phase B.1 condition 2: no exception message, URL, header or body reaches the public logs. `common.trace` prints an
exception's type, its frames and, for an HTTP error, the integer status only. Values here are synthetic."""
import ast
import contextlib
import io
import pathlib
import sys
import threading
import unittest
import urllib.error
from unittest import mock

from bot import common, listen

SENTINEL = "31337.17"  # a synthetic equity


def captured(fn, *a):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        fn(*a)
    return out.getvalue() + err.getvalue()


def boom():
    raise ValueError(f"could not convert '{SENTINEL}x'")


class Trace(unittest.TestCase):
    def test_trace_prints_no_message(self):
        def run():
            try:
                boom()
            except ValueError as e:
                common.trace(e)
        text = captured(run)
        self.assertIn("ValueError", text)
        self.assertIn("in boom", text)  # the frames stay, for debugging
        self.assertNotIn(SENTINEL, text)

    def test_trace_without_argument_uses_the_current_exception(self):
        def run():
            try:
                boom()
            except ValueError:
                common.trace()
        text = captured(run)
        self.assertIn("ValueError", text)
        self.assertNotIn(SENTINEL, text)

    def test_trace_prints_http_status_only(self):
        err = urllib.error.HTTPError(f"https://api.telegram.org/botTOKEN/sendMessage?x={SENTINEL}", 429,
                                     f"Too Many Requests {SENTINEL}", {"X-Secret": SENTINEL}, io.BytesIO(SENTINEL.encode()))
        text = captured(common.trace, err)
        self.assertIn("429", text)
        for leak in (SENTINEL, "api.telegram.org", "TOKEN", "Too Many", "X-Secret"):
            self.assertNotIn(leak, text)

    def test_chained_causes_by_type_only(self):
        def run():
            try:
                try:
                    boom()
                except ValueError as e:
                    raise RuntimeError(f"wrapped {SENTINEL}") from e
            except RuntimeError as e:
                common.trace(e)
        text = captured(run)
        self.assertIn("RuntimeError", text)
        self.assertIn("ValueError", text)
        self.assertNotIn(SENTINEL, text)

    def test_excepthook_prints_no_message(self):
        old = sys.excepthook
        try:
            common.install_excepthooks()
            try:
                boom()
            except ValueError as e:
                exc = e
            text = captured(sys.excepthook, ValueError, exc, exc.__traceback__)
        finally:
            sys.excepthook = old
        self.assertIn("ValueError", text)
        self.assertNotIn(SENTINEL, text)

    def test_thread_excepthook_prints_no_message(self):
        old = threading.excepthook
        try:
            common.install_excepthooks()
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                t = threading.Thread(target=boom)
                t.start()
                t.join()
        finally:
            threading.excepthook = old
        self.assertIn("ValueError", out.getvalue())
        self.assertNotIn(SENTINEL, out.getvalue())

    def test_listener_main_installs_the_hooks(self):
        with mock.patch.object(common, "install_excepthooks") as hooks, \
                mock.patch.dict("os.environ", {"TG_TOKEN": "", "TG_CHAT_ID": ""}):
            with self.assertRaises(SystemExit):
                listen.main([])
        hooks.assert_called_once()

    def test_answer_failure_prints_no_text(self):
        sent = []
        with mock.patch.object(listen, "updates", return_value=[]), \
                mock.patch.object(listen, "handle", side_effect=ValueError(f"could not convert '{SENTINEL}x'")), \
                mock.patch.object(common, "send", side_effect=lambda t, **k: sent.append(t)), \
                mock.patch.dict("os.environ", {"TG_CHAT_ID": "7"}):
            text = captured(listen.answer, [{"update_id": 1, "message": {"chat": {"id": 7}, "text": "/x"}}])
        self.assertNotIn(SENTINEL, text)
        self.assertIn("ValueError", text)
        self.assertEqual(len(sent), 1)  # the owner still hears about it


def closure(entry):
    """The bot modules `entry` imports, recursively (ast)."""
    seen, todo = set(), [entry]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        path = pathlib.Path(__file__).resolve().parent.parent / "bot" / f"{mod}.py"
        if not path.exists():
            continue
        for n in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(n, ast.ImportFrom) and n.module == "bot":
                todo += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("bot."):
                todo.append(n.module[4:])
    return {m for m in seen if (pathlib.Path(__file__).resolve().parent.parent / "bot" / f"{m}.py").exists()}


class Inventory(unittest.TestCase):
    def test_no_raw_traceback_in_the_listener_closure(self):
        mods = closure("listen")
        self.assertTrue({"listen", "check", "scan", "ticket_command"} <= mods)
        root = pathlib.Path(__file__).resolve().parent.parent / "bot"
        for m in sorted(mods):
            src = (root / f"{m}.py").read_text("utf-8")
            for name in ("print_exc", "format_exc", "print_exception"):
                self.assertNotIn(name, src, f"bot/{m}.py uses {name}")


if __name__ == "__main__":
    unittest.main()
