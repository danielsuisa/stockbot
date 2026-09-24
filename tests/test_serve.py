"""Long-poll listener (listen --serve): stop conditions, offsets across restarts, 409, the already-running guard."""
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import common, listen, scan  # noqa: E402

ENV = {"TG_TOKEN": "1:x", "TG_CHAT_ID": "42", "GITHUB_ACTIONS": "", "GITHUB_OUTPUT": "", "GITHUB_EVENT_NAME": "",
       "GITHUB_STEP_SUMMARY": "", "TG_OFFSET": "", "STOP_FILE": "", "PARENT_RUN_ID": "", "GITHUB_RUN_ID": ""}


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://api.telegram.org/bot1:x/getUpdates", code, "err", {}, io.BytesIO(body))


class FakeTelegram:
    """Bot API getUpdates semantics: a call with `offset` confirms (forgets) every update below it, for good;
    a long poll with nothing waiting uses up its timeout on a fake clock."""

    def __init__(self):
        self.updates, self.confirmed, self.now, self.polls, self.next_id = [], 0, 0.0, [], 100
        self.on_poll = None  # hook(n): runs before the n-th poll answers (messages arrive, stop file, errors)

    def add(self, text, chat=42):
        self.updates.append({"update_id": self.next_id, "message": {"chat": {"id": chat}, "text": text}})
        self.next_id += 1

    def tg(self, method, **p):
        assert method == "getUpdates", method
        if "offset" in p:
            self.confirmed = max(self.confirmed, p["offset"])
        if p.get("allowed_updates"):  # a poll (the ACK has no allowed_updates)
            self.polls.append((p.get("offset"), p["timeout"]))
            if self.on_poll:
                self.on_poll(len(self.polls))
        waiting = [u for u in self.updates if u["update_id"] >= self.confirmed]
        if not waiting and p.get("allowed_updates"):
            self.now += p["timeout"]
        return waiting

    def sleep(self, s):
        self.now += s


class Serve(unittest.TestCase):
    def setUp(self):
        self.tg, self.handled = FakeTelegram(), []
        patches = [mock.patch.dict(os.environ, ENV), mock.patch.object(common, "tg", side_effect=self.tg.tg),
                   mock.patch.object(listen.time, "monotonic", side_effect=lambda: self.tg.now),
                   mock.patch.object(listen.time, "sleep", side_effect=self.tg.sleep),
                   mock.patch.object(listen, "handle", side_effect=lambda t: self.handled.append(t) or 0),
                   mock.patch.object(listen, "refresh"), mock.patch.object(common, "send"),
                   mock.patch("builtins.print")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_deadline_stops_and_no_poll_outlives_it(self):
        self.assertEqual(listen.serve(seconds=120), ("deadline", None))
        self.assertEqual(self.tg.polls, [(None, 50), (None, 50), (None, 20)])  # the last poll is cut to what is left

    def test_default_deadline_is_5h20m_within_the_job_timeout(self):
        self.assertEqual(listen.serve()[0], "deadline")
        self.assertEqual((self.tg.now, listen.SERVE_SECONDS), (19200, 19200))
        wf = (Path(__file__).resolve().parent.parent / ".github/workflows/telegram-listen.yml").read_text("utf-8")
        self.assertIn("timeout-minutes: 340", wf)
        self.assertLess(listen.SERVE_SECONDS + listen.POLL + 540, 330 * 60)  # step timeout: ~9 min for a last reply

    def test_stop_file_ends_the_loop_before_the_next_poll(self):
        with tempfile.TemporaryDirectory() as d:
            stop = Path(d, "listen.stop")
            self.tg.on_poll = lambda n: n == 2 and stop.write_text("")
            self.assertEqual(listen.serve(seconds=3600, stop_file=str(stop))[0], "stop")
        self.assertEqual(len(self.tg.polls), 2)

    def test_each_message_handled_once_acked_before_handling_other_chats_ignored(self):
        self.tg.add("AAPL")
        self.tg.add("spam", chat=9)
        self.tg.on_poll = lambda n: n == 3 and self.tg.add("/status")
        order = []
        listen.handle.side_effect = lambda t: order.append((t, self.tg.confirmed)) or 0
        self.assertEqual(listen.serve(seconds=300), ("deadline", 103))
        self.assertEqual(order, [("AAPL", 102), ("/status", 103)])  # confirmed (ACKed) before each handle
        self.assertEqual(self.tg.polls[:4], [(None, 50), (102, 50), (102, 50), (103, 50)])
        self.assertEqual(listen.refresh.call_count, 2)  # fresh data/ and caches before every batch

    def test_offset_persists_across_restarts(self):
        self.tg.add("AAPL")
        self.tg.add("MSFT")
        reason, offset = listen.serve(seconds=100)
        self.assertEqual((reason, offset, self.handled), ("deadline", 102, ["AAPL", "MSFT"]))
        self.tg.add("/status")  # arrives between jobs
        listen.serve(seconds=100)  # next job, fresh process, no offset handed on: Telegram's confirmation is enough
        self.assertEqual(self.handled, ["AAPL", "MSFT", "/status"])
        self.tg.add("/help")
        self.tg.confirmed, first = 0, len(self.tg.polls)  # Telegram "forgot": the handed-on offset still holds
        self.assertEqual(listen.serve(seconds=100, offset=103), ("deadline", 104))
        self.assertEqual(self.handled, ["AAPL", "MSFT", "/status", "/help"])
        self.assertEqual(self.tg.polls[first][0], 103)

    def test_a_job_killed_mid_reply_does_not_replay_the_message(self):
        class Killed(BaseException):
            pass
        self.tg.add("AAPL")
        listen.handle.side_effect = Killed
        with self.assertRaises(Killed):
            listen.serve(seconds=100)
        listen.handle.side_effect = lambda t: self.handled.append(t) or 0
        self.tg.add("MSFT")
        listen.serve(seconds=100)
        self.assertEqual(self.handled, ["MSFT"])

    def test_main_serve_reads_offset_and_writes_step_outputs(self):
        self.tg.confirmed = 0
        self.tg.updates = [{"update_id": 7, "message": {"chat": {"id": 42}, "text": "old"}}]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(listen, "SERVE_SECONDS", 60):
            out = Path(d, "out")
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out), "TG_OFFSET": "8"}):
                listen.main(["--serve"])
            self.assertEqual(out.read_text("utf-8"), "offset=8\nchain=true\n")
        self.assertEqual((self.tg.polls[0][0], self.handled), (8, []))  # update 7 was handled by the previous job

    def test_crash_still_hands_on_offset_and_asks_for_a_restart(self):
        self.tg.add("AAPL")
        self.tg.on_poll = lambda n: n == 2 and (_ for _ in ()).throw(http_error(400))
        with tempfile.TemporaryDirectory() as d:
            out = Path(d, "out")
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}), self.assertRaises(urllib.error.HTTPError):
                listen.main(["--serve"])
            self.assertEqual(out.read_text("utf-8"), "offset=\nchain=true\n")  # offset: the ACK already confirmed 100

    def test_network_blips_are_waited_out(self):
        errors = iter([OSError("reset"), http_error(502), http_error(502)])
        self.tg.on_poll = lambda n: n in (2, 3, 4) and (_ for _ in ()).throw(next(errors))
        self.tg.add("AAPL")
        self.assertEqual(listen.serve(seconds=600)[0], "deadline")
        self.assertEqual(self.handled, ["AAPL"])
        self.assertGreater(len(self.tg.polls), 5)

    def test_bad_token_is_not_retried(self):
        self.tg.on_poll = lambda n: (_ for _ in ()).throw(RuntimeError("Telegram rejected TG_TOKEN (401)"))
        with self.assertRaises(RuntimeError):
            listen.serve(seconds=600)
        self.assertEqual(len(self.tg.polls), 1)


class Conflict409(unittest.TestCase):
    BODY = json.dumps({"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates "
                       "request; make sure that only one bot instance is running"}).encode()

    def setUp(self):
        for p in (mock.patch.dict(os.environ, ENV), mock.patch("builtins.print"), mock.patch.object(common, "send")):
            p.start()
            self.addCleanup(p.stop)

    def test_409_exits_cleanly_without_retrying(self):
        with mock.patch.object(common, "fetch", side_effect=http_error(409, self.BODY)) as f, \
                mock.patch.object(listen.time, "sleep") as sleep, tempfile.TemporaryDirectory() as d:
            out = Path(d, "out")
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}):
                listen.main(["--serve"])  # no exception: a clean exit
            self.assertEqual(out.read_text("utf-8"), "offset=\nchain=false\n")  # the other consumer lives on
        self.assertEqual((f.call_count, sleep.called), (1, False))  # one request, no backoff, no retry

    def test_409_on_the_ack_handles_nothing(self):
        ups = [{"update_id": 5, "message": {"chat": {"id": 42}, "text": "AAPL"}}]

        def tg(method, **p):
            if "offset" in p:
                raise http_error(409, self.BODY)
            return ups
        with mock.patch.object(common, "tg", side_effect=tg), mock.patch.object(listen, "handle") as h, \
                mock.patch.object(listen, "refresh"):
            self.assertEqual(listen.serve(seconds=100), ("conflict", None))
        h.assert_not_called()  # unconfirmed: the other consumer gets (and answers) them

    def test_one_shot_run_exits_cleanly_on_409(self):
        with mock.patch.object(common, "fetch", side_effect=http_error(409, self.BODY)) as f:
            listen.main([])  # returns: no SystemExit, no retry
        self.assertEqual(f.call_count, 1)


class AlreadyRunning(unittest.TestCase):
    NOW = dt.datetime.now(dt.timezone.utc)

    def run_(self, rid, age, status="in_progress"):
        return {"id": rid, "status": status, "created_at": "x",
                "run_started_at": (self.NOW - dt.timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def setUp(self):
        env = {**ENV, "GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", "GITHUB_RUN_ID": "300", "PARENT_RUN_ID": "200"}
        for p in (mock.patch.dict(os.environ, env), mock.patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)

    def guard(self, runs):
        with mock.patch.object(common, "fetch", return_value=json.dumps({"workflow_runs": runs}).encode()) as f:
            rid = listen.live_poller()
        url, headers = f.call_args.args[0], f.call_args.kwargs["headers"]
        self.assertEqual(url, "https://api.github.com/repos/o/r/actions/workflows/telegram-listen.yml/runs"
                              "?status=in_progress&per_page=20")
        self.assertEqual(headers["Authorization"], "Bearer t")
        return rid

    def test_live_poller_found(self):
        self.assertEqual(self.guard([self.run_(300, 30), self.run_(150, 3 * 3600)]), 150)

    def test_self_parent_and_starting_runs_are_not_live(self):
        # 200 dispatched us and is finishing; 290 is still in its own start-up (409 settles a tie)
        self.assertIsNone(self.guard([self.run_(300, 30), self.run_(200, 5 * 3600), self.run_(290, 40)]))
        self.assertIsNone(self.guard([self.run_(150, 3600, status="queued")]))

    def test_runs_api_down_means_start(self):
        with mock.patch.object(common, "fetch", side_effect=OSError("down")):
            self.assertIsNone(listen.live_poller())

    def test_cron_run_exits_at_once_while_a_poller_is_live(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(listen, "live_poller", return_value=150), \
                mock.patch.object(listen, "serve") as serve, mock.patch.object(common, "tg") as tg:
            out = Path(d, "out")
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out), "GITHUB_EVENT_NAME": "schedule"}):
                listen.main(["--serve"])
            self.assertEqual(out.read_text("utf-8"), "chain=false\n")  # and starts no successor
        self.assertEqual((serve.called, tg.called), (False, False))

    def test_push_and_manual_runs_take_over_without_asking(self):
        for event in ("push", "workflow_dispatch"):
            with mock.patch.object(listen, "live_poller", return_value=150) as guard, \
                    mock.patch.object(listen, "serve", return_value=("conflict", None)) as serve, \
                    mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": event}):
                listen.main(["--serve"])
            self.assertEqual((guard.called, serve.called), (False, True), event)


class Refresh(unittest.TestCase):
    def test_caches_and_data_are_refreshed(self):
        common.submissions.cache_clear()
        common.STAMPS["sec"] = "old"
        scan._pay_budget[0] = 0
        with mock.patch.object(common, "get_json", return_value={"filings": {}}) as g:
            common.submissions(1)
            with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_REF_NAME": "main"}), \
                    mock.patch.object(listen.subprocess, "run",
                                      return_value=listen.subprocess.CompletedProcess([], 0, "", "")) as run:
                listen.refresh()
            common.submissions(1)
        self.assertEqual((g.call_count, common.STAMPS, scan._pay_budget[0]), (2, {}, scan.MAX_PAY))
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [["git", "fetch", "-q", "--depth=1", "origin", "main"],
                          ["git", "checkout", "-q", "FETCH_HEAD", "--", "data"]])
        common.submissions.cache_clear()

    def test_workflow_restarts_itself_and_keeps_the_backup_cron(self):
        wf = (Path(__file__).resolve().parent.parent / ".github/workflows/telegram-listen.yml").read_text("utf-8")
        for needle in ("python -m bot.listen --serve", "actions: write", '- cron: "*/10 * * * *"', "workflow_dispatch:",
                       "steps.serve.outputs.chain != 'false'", "(success() || failure())",
                       "actions/workflows/telegram-listen.yml/dispatches", "PARENT_RUN_ID: ${{ inputs.parent }}"):
            self.assertIn(needle, wf)
        self.assertNotIn("\nconcurrency:", wf)  # a group would queue/cancel runs behind the 5-hour poller


if __name__ == "__main__":
    unittest.main()
