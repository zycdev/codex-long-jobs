from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import queue_wake as qw
from test_queue_wake import FakeRpc


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.dict(
            os.environ,
            {
                "CODEX_LONG_JOBS_STATE_DIR": str(self.root),
                "CODEX_HOME": str(self.root / "codex"),
                "CODEX_LONG_JOBS_ALLOW_SANDBOX": "1",
            },
        )
        patch.start()
        self.addCleanup(patch.stop)
        self.directory = self.root / "jobs/test"
        self.directory.mkdir(parents=True)
        self.record = {
            "schema_version": 1,
            "name": "test",
            "status": "succeeded",
            "cwd": str(self.root),
            "log": str(self.root / "log"),
            "owner_thread_id": "thread",
            "notify_user": False,
            "delivery": {
                "mode": "queue",
                "status": "queued",
                "token": "TOKEN",
                "wait_seconds": 1,
            },
        }
        qw.jobs.atomic_write_json(self.directory / "state.json", self.record)

    def evidence(self):
        return qw.jobs.read_queue_wake("test")

    def test_unloaded_with_earlier_input_resumes_without_bypassing_it(self):
        rpc = FakeRpc("notLoaded")
        earlier = {"id": "earlier", "input": [{"type": "text", "text": "user input"}]}
        rpc.entries.insert(0, earlier)
        self.assertEqual(
            qw.wake(rpc, "thread", "TOKEN")["status"], "waiting-for-earlier-input"
        )
        self.assertEqual(rpc.entries[0], earlier)
        self.assertIn("thread/resume", [m for m, _ in rpc.calls])
        self.assertNotIn("thread/queue/start", [m for m, _ in rpc.calls])

    def test_concurrent_reorder_starts_server_head_and_does_not_attribute_turn(self):
        rpc = FakeRpc()
        original = rpc.call

        def reorder(method, params):
            if method == "thread/queue/start":
                self.assertNotIn("queuedSubmissionId", params)
                # Another client moved earlier input ahead of our target. The
                # server starts that head, leaving our notification untouched.
                return {"turn": {"id": "earlier-turn", "items": []}}
            return original(method, params)

        rpc.call = reorder
        result = qw.wake(rpc, "thread", "TOKEN")
        self.assertEqual(
            result,
            {"status": "waiting-for-earlier-input", "observed_turn_id": "earlier-turn"},
        )
        self.assertEqual(rpc.entries[0]["id"], "job-id")

    def test_pending_native_consumer_is_safe_waiting_not_failure(self):
        rpc = FakeRpc()
        original = rpc.call

        def busy(method, params):
            if method == "thread/queue/start":
                raise qw.RpcError("thread already has an active or pending turn")
            return original(method, params)

        rpc.call = busy
        result = qw.wake(rpc, "thread", "TOKEN")
        self.assertEqual(result["status"], "waiting-for-idle")
        self.assertEqual(rpc.entries[0]["id"], "job-id")

    def test_minimal_start_response_is_only_disappearance_evidence(self):
        rpc = FakeRpc()
        original = rpc.call

        def minimal(method, params):
            value = original(method, params)
            if method == "thread/queue/start":
                value["turn"]["items"] = []
            return value

        rpc.call = minimal
        self.assertEqual(
            qw.wake(rpc, "thread", "TOKEN"),
            {"status": "consumed-concurrently", "observed_turn_id": "turn-id"},
        )

    def test_two_jobs_share_thread_without_skipping_first_completion(self):
        rpc = FakeRpc("notLoaded")
        rpc.entries.append(
            {"id": "second", "input": [{"type": "text", "text": "SECOND"}]}
        )
        original = rpc.call
        started = []

        def consume_head(method, params):
            if method == "thread/queue/start":
                self.assertEqual(params, {"threadId": "thread"})
                self.assertEqual(rpc.status, "idle")
                entry = rpc.entries.pop(0)
                started.append(entry["id"])
                rpc.status = "active"
                return {
                    "turn": {
                        "id": entry["id"] + "-turn",
                        "items": [{"type": "userMessage", "content": entry["input"]}],
                    }
                }
            return original(method, params)

        rpc.call = consume_head
        self.assertEqual(
            qw.wake(rpc, "thread", "SECOND")["status"], "waiting-for-earlier-input"
        )
        self.assertEqual(qw.wake(rpc, "thread", "TOKEN")["status"], "started")
        self.assertEqual(qw.wake(rpc, "thread", "SECOND")["status"], "waiting-for-idle")
        self.assertEqual(started, ["job-id"])
        rpc.status = "idle"
        self.assertEqual(qw.wake(rpc, "thread", "SECOND")["status"], "started")
        self.assertEqual(qw.wake(rpc, "thread", "TOKEN")["status"], "absent")
        self.assertEqual(qw.wake(rpc, "thread", "SECOND")["status"], "absent")
        self.assertEqual(started, ["job-id", "second"])
        self.assertEqual(rpc.entries, [])

    def test_writer_lock_failure_preserves_queue_and_can_recover(self):
        rpc = FakeRpc("notLoaded")
        original = rpc.call

        def locked(method, params):
            if method == "thread/resume":
                raise qw.RpcError("thread writer lock is held by another app server")
            return original(method, params)

        rpc.call = locked
        with self.assertRaisesRegex(qw.RpcError, "writer lock"):
            qw.wake(rpc, "thread", "TOKEN")
        self.assertEqual(len(rpc.entries), 1)
        rpc.call = original
        self.assertEqual(qw.wake(rpc, "thread", "TOKEN")["status"], "started")

    def test_daemon_unavailable_then_recovers_in_same_bounded_attempt(self):
        with (
            mock.patch.object(
                qw, "Rpc", side_effect=[ConnectionRefusedError("offline"), FakeRpc()]
            ) as constructor,
            mock.patch.object(qw.time, "sleep"),
        ):
            self.assertEqual(qw.run("test"), 0)
        self.assertEqual(constructor.call_count, 2)
        self.assertEqual(self.evidence()["status"], "started")
        self.assertEqual(self.evidence()["attempts"], 1)

    def test_timeout_and_explicit_retry_do_not_resend(self):
        with (
            mock.patch.object(qw, "Rpc", side_effect=ConnectionRefusedError("offline")),
            mock.patch.object(qw.time, "monotonic", side_effect=[0, 2]),
        ):
            self.assertEqual(qw.run("test"), 1)
        previous = self.evidence()
        self.assertEqual(previous["status"], "timed-out")
        with mock.patch.object(qw, "Rpc", return_value=FakeRpc()) as constructor:
            self.assertEqual(qw.run("test"), 1)
            constructor.assert_not_called()
            self.assertEqual(qw.run("test", retry=True), 0)
        self.assertGreater(self.evidence()["deadline_at"], previous["deadline_at"])
        self.assertEqual(qw.jobs.read_job("test"), self.record)

    def test_wait_expiry_does_not_start_one_more_rpc_attempt(self):
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        with (
            mock.patch.object(qw.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(qw.time, "sleep", side_effect=sleep),
            mock.patch.object(
                qw, "Rpc", side_effect=[ConnectionRefusedError("offline"), FakeRpc()]
            ) as constructor,
        ):
            self.assertEqual(qw.run("test"), 1)
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(self.evidence()["status"], "timed-out")

    def test_malformed_wake_evidence_does_not_hide_command_status(self):
        before = (self.directory / "state.json").read_bytes()
        invalid_records = [
            {},
            {"status": []},
            {"status": "pending", "helper_identity": "invalid"},
            {"status": "pending", "deadline_at": "invalid"},
            {"status": "pending", "deadline_at": float("nan")},
            {"status": "pending", "deadline_at": 10**400},
            {"status": "pending", "attempts": -1},
            {"status": "pending", "unhandled_requests": [{}]},
        ]
        for record in invalid_records:
            with self.subTest(record=record):
                path = self.directory / "queue-wake.json"
                qw.jobs.atomic_write_json(path, record)
                wake_bytes = path.read_bytes()
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = qw.jobs.command_status(
                        argparse.Namespace(name="test", json=False)
                    )
                self.assertEqual(result, 0)
                self.assertIn("status=succeeded", output.getvalue())
                self.assertIn(
                    "queue_wake_reason=wake-evidence-unreadable", output.getvalue()
                )
                self.assertEqual(path.read_bytes(), wake_bytes)
                self.assertEqual((self.directory / "state.json").read_bytes(), before)

    def test_absent_is_not_success_and_explicit_retry_reinspects(self):
        empty = FakeRpc()
        empty.entries = []
        with mock.patch.object(qw, "Rpc", return_value=empty):
            self.assertEqual(qw.run("test"), 1)
        self.assertEqual(self.evidence()["status"], "absent")
        with mock.patch.object(qw, "Rpc", return_value=FakeRpc()):
            self.assertEqual(qw.run("test", retry=True), 0)
        self.assertEqual(qw.jobs.read_job("test"), self.record)

    def test_launch_failure_then_supervisor_takeover(self):
        with mock.patch.object(
            qw.jobs.subprocess, "Popen", side_effect=OSError(11, "temporary failure")
        ):
            self.assertIsNone(qw.jobs.launch_queue_wake("test"))
        self.assertEqual(self.evidence()["reason"], "wake-launch-failed:11")
        with mock.patch.object(qw, "Rpc", return_value=FakeRpc()) as constructor:
            qw.jobs.supervise_queue_wake("test")
            qw.jobs.supervise_queue_wake("test")
            constructor.assert_called_once()

    def test_supervisor_recovers_worker_exit_after_acceptance_before_launch(self):
        queued = {**self.record, "status": "queued"}
        qw.jobs.atomic_write_json(self.directory / "state.json", queued)

        def worker_exit():
            qw.jobs.atomic_write_json(self.directory / "state.json", self.record)
            return -9

        child = mock.Mock(pid=os.getpid())
        child.wait.side_effect = worker_exit
        with (
            mock.patch.object(qw.jobs.subprocess, "Popen", return_value=child),
            mock.patch.object(qw, "Rpc", return_value=FakeRpc()) as constructor,
        ):
            qw.jobs.supervisor_main("test")
            constructor.assert_called_once()
        self.assertEqual(self.evidence()["status"], "started")

    def test_supervisor_covers_cancellation_before_worker_launch(self):
        queued = {**self.record, "status": "queued", "cancel_requested_at": "now"}
        qw.jobs.atomic_write_json(self.directory / "state.json", queued)

        def cancel(name):
            qw.jobs.atomic_write_json(self.directory / "state.json", self.record)
            return 0

        with (
            mock.patch.object(
                qw.jobs, "finalize_cancelled_before_launch", side_effect=cancel
            ),
            mock.patch.object(qw, "Rpc", return_value=FakeRpc()),
        ):
            self.assertEqual(qw.jobs.supervisor_main("test"), 0)
        self.assertEqual(self.evidence()["status"], "started")

    def test_deadline_is_durable_before_first_rpc_and_expiry_prevents_rpc(self):
        def connect(_):
            evidence = self.evidence()
            self.assertEqual(evidence["reason"], "wake-running")
            self.assertGreater(evidence["deadline_at"], time.time())
            return FakeRpc()

        with mock.patch.object(qw, "Rpc", side_effect=connect):
            qw.run("test")
        qw.jobs.atomic_write_json(
            self.directory / "queue-wake.json",
            {"status": "pending", "deadline_at": time.time() - 10},
        )
        with mock.patch.object(qw, "Rpc") as constructor:
            self.assertEqual(qw.run("test"), 1)
            constructor.assert_not_called()
        self.assertEqual(self.evidence()["status"], "timed-out")

    def test_crashed_helper_releases_lock_and_preserves_deadline(self):
        deadline = time.time() + 30
        code = """import fcntl,json,os,sys
from pathlib import Path
p=Path(sys.argv[1]);f=open(p/'queue-wake.lock','w');fcntl.flock(f,fcntl.LOCK_EX)
(p/'queue-wake.json').write_text(json.dumps({'status':'pending','deadline_at':float(sys.argv[2]),'attempts':1}))
os._exit(17)
"""
        child = subprocess.run(
            [sys.executable, "-c", code, str(self.directory), str(deadline)],
            check=False,
        )
        self.assertEqual(child.returncode, 17)
        with mock.patch.object(qw, "Rpc", return_value=FakeRpc()):
            qw.jobs.supervise_queue_wake("test")
        self.assertEqual(self.evidence()["deadline_at"], deadline)
        self.assertEqual(self.evidence()["attempts"], 2)

    def test_concurrent_retry_does_not_reset_active_deadline_or_call_rpc(self):
        with (self.directory / "queue-wake.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with mock.patch.object(qw, "Rpc") as constructor:
                self.assertEqual(qw.run("test", retry=True), 0)
                constructor.assert_not_called()

    def test_status_adds_wake_observation_without_changing_state(self):
        before = (self.directory / "state.json").read_bytes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            qw.jobs.command_status(argparse.Namespace(name="test", json=True))
        self.assertEqual(
            json.loads(output.getvalue())["queue_wake"]["reason"], "wake-not-started"
        )
        self.assertEqual((self.directory / "state.json").read_bytes(), before)

    def test_rpc_refuses_client_tools_and_approvals_without_fake_results(self):
        ws = mock.Mock()
        ws.receive.side_effect = [
            {"id": 1, "result": {}},
            {"id": 10, "method": "item/tool/call", "params": {}},
            {"id": 11, "method": "item/commandExecution/requestApproval", "params": {}},
            {"id": 2, "result": {"ok": True}},
        ]
        with mock.patch.object(qw, "UnixWebSocket", return_value=ws):
            rpc = qw.Rpc(Path("unused"))
            self.assertEqual(rpc.call("thread/read", {}), {"ok": True})
        replies = [
            c.args[0] for c in ws.send.call_args_list if c.args[0].get("id") in {10, 11}
        ]
        self.assertEqual(len(replies), 2)
        self.assertTrue(
            all(r["error"]["code"] == -32601 and "result" not in r for r in replies)
        )
        self.assertEqual(len(rpc.unhandled_requests), 2)

    def test_native_resume_warning_recovers_after_acceptance_without_resending(self):
        self.record["delivery"]["status"] = "pending"
        qw.jobs.atomic_write_json(self.directory / "state.json", self.record)
        help_result = subprocess.CompletedProcess(
            [], 0, "--thread --message --resume", ""
        )
        accepted = subprocess.CompletedProcess(
            [],
            0,
            "Queued message item for thread thread.",
            "Warning: message was queued, but could not resume thread thread: active writer",
        )
        with (
            mock.patch.object(
                qw.jobs.codex_env,
                "discover_codex",
                return_value=("/fake/codex", "explicit"),
            ),
            mock.patch.object(
                qw.jobs.subprocess, "run", side_effect=[help_result, accepted]
            ) as command,
            mock.patch.object(qw.jobs, "launch_queue_wake", side_effect=qw.run),
            mock.patch.object(qw, "Rpc", return_value=FakeRpc()),
        ):
            qw.jobs.deliver_queue("test")
            self.assertEqual(command.call_count, 2)
            self.assertIn("--resume", command.call_args.args[0])
            qw.jobs.deliver_queue("test")
            self.assertEqual(command.call_count, 2)
        state = qw.jobs.read_job("test")
        self.assertEqual(state["delivery"]["status"], "queued")
        self.assertEqual(state["delivery"]["queue_attempts"], 1)
        self.assertTrue(state["delivery"]["queue_resume_warning"])
        self.assertEqual(self.evidence()["status"], "started")

    def test_duplicate_risk_acknowledgement_cannot_overwrite_concurrent_acceptance(
        self,
    ):
        stale = copy.deepcopy(self.record)
        stale["delivery"].update(status="pending", queue_acceptance_uncertain=True)
        original = qw.jobs.read_job
        reads = 0

        def read(name):
            nonlocal reads
            reads += 1
            return stale if reads == 1 else original(name)

        def dispatch(name, **kwargs):
            qw.jobs.deliver_queue(name, **kwargs)
            return 123

        with (
            mock.patch.object(qw.jobs, "read_job", side_effect=read),
            mock.patch.object(qw.jobs, "spawn_delivery", side_effect=dispatch),
            mock.patch.object(qw.jobs, "launch_queue_wake"),
            mock.patch.object(qw.jobs.subprocess, "run") as command,
        ):
            qw.jobs.command_retry_delivery(
                argparse.Namespace(
                    name="test", use_queue=False, allow_possible_duplicate=True
                )
            )
            command.assert_not_called()
        self.assertEqual(original("test"), self.record)

    def test_explicit_acknowledgement_reaches_locked_dispatcher(self):
        self.record["delivery"].update(
            status="pending", queue_acceptance_uncertain=True
        )
        qw.jobs.atomic_write_json(self.directory / "state.json", self.record)
        with (
            mock.patch.object(
                qw.jobs.codex_env,
                "queue_capabilities",
                return_value={"queue": True, "resume": False},
            ),
            mock.patch.object(
                qw.jobs.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as command,
            mock.patch.object(qw.jobs, "launch_queue_wake"),
        ):
            qw.jobs.dispatch_delivery("test")
            command.assert_not_called()
            qw.jobs.dispatch_delivery("test", allow_possible_duplicate=True)
            command.assert_called_once()
        self.assertEqual(qw.jobs.read_job("test")["delivery"]["status"], "queued")

    def test_unhandled_tool_observation_survives_later_rpc_attempt(self):
        first = FakeRpc("active")
        first.unhandled_requests = {"item/tool/call"}
        with (
            mock.patch.object(qw, "Rpc", side_effect=[first, FakeRpc()]),
            mock.patch.object(qw.time, "sleep"),
        ):
            qw.run("test")
        self.assertEqual(self.evidence()["unhandled_requests"], ["item/tool/call"])

    def test_retry_uses_accepted_codex_home_after_environment_changes(self):
        original_home = self.root / "original-home"
        self.record["delivery"]["queue_codex_home"] = str(original_home)
        qw.jobs.atomic_write_json(self.directory / "state.json", self.record)
        with mock.patch.object(qw, "Rpc", return_value=FakeRpc()) as constructor:
            qw.run("test")
        constructor.assert_called_once_with(
            original_home / "app-server-control/app-server-control.sock"
        )

    def test_pagination_cycle_and_duplicate_tokens_fail_conservatively(self):
        rpc = FakeRpc()
        rpc.next_cursor = "repeated"
        with self.assertRaisesRegex(qw.RpcError, "pagination"):
            qw.wake(rpc, "thread", "TOKEN")
        rpc.next_cursor = None
        rpc.entries.append({"id": "duplicate", "input": rpc.entries[0]["input"]})
        self.assertEqual(
            qw.wake(rpc, "thread", "TOKEN")["reason"], "ambiguous-completion-token"
        )


if __name__ == "__main__":
    unittest.main()
