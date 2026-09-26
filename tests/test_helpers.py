#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import errno
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "codex_long_jobs.py"
SPEC = importlib.util.spec_from_file_location("clj_helpers", CLI)
assert SPEC and SPEC.loader
RUNTIME = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME)


class ZeroWriter:
    def write(self, _: memoryview) -> int:
        return 0


class HelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="codex-long-jobs-helper-")
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "CODEX_LONG_JOBS_STATE_DIR": str(self.state),
                "CODEX_LONG_JOBS_ALLOW_SANDBOX": "1",
            },
            clear=False,
        )
        self.environment.start()
        RUNTIME.ensure_state_root()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()

    def write_record(
        self,
        name: str,
        *,
        status: str = "succeeded",
        delivery_mode: str = "event-only",
        delivery_status: str = "disabled",
        owner: str | None = None,
    ) -> dict:
        directory = self.state / "jobs" / name
        directory.mkdir(mode=0o700)
        record = {
            "schema_version": RUNTIME.SCHEMA_VERSION,
            "runtime_version": RUNTIME.VERSION,
            "name": name,
            "status": status,
            "created_at": "test",
            "updated_at": "test",
            "cwd": str(self.root),
            "command": ["true"],
            "log": str(self.root / f"{name}.log"),
            "success_pattern": None,
            "owner_thread_id": owner,
            "endpoint": None,
            "delivery": {
                "mode": delivery_mode,
                "status": delivery_status,
                "reason": "test",
                "selection_reason": "test",
                "token": f"[codex-long-jobs:{name}:test]",
                "wait_seconds": 1,
            },
        }
        RUNTIME.atomic_write_json(directory / "state.json", record)
        return record

    def test_read_job_rejects_missing_large_and_invalid_state(self) -> None:
        with self.assertRaisesRegex(RUNTIME.JobError, "unknown job"):
            RUNTIME.read_job("missing")

        large = self.state / "jobs" / "large"
        large.mkdir(mode=0o700)
        (large / "state.json").write_text("x" * (256 * 1024 + 1), encoding="utf-8")
        with self.assertRaisesRegex(RUNTIME.JobError, "unexpectedly large"):
            RUNTIME.read_job("large")

        invalid = self.write_record("invalid")
        invalid["schema_version"] = 999
        (self.state / "jobs" / "invalid" / "state.json").write_text(
            json.dumps(invalid), encoding="utf-8"
        )
        with self.assertRaisesRegex(RUNTIME.JobError, "invalid job state"):
            RUNTIME.read_job("invalid")

    def test_atomic_write_removes_temporary_file_after_replace_failure(self) -> None:
        destination = self.root / "atomic.json"
        with (
            mock.patch.object(os, "replace", side_effect=OSError(errno.EIO, "fail")),
            self.assertRaises(OSError),
        ):
            RUNTIME.atomic_write_json(destination, {"value": 1})
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".atomic.json.*.tmp")), [])

    def test_write_log_chunk_detects_no_progress(self) -> None:
        with self.assertRaisesRegex(OSError, "made no progress"):
            RUNTIME.write_log_chunk(ZeroWriter(), b"data", 0)

    def test_identity_and_process_helpers_handle_dead_processes(self) -> None:
        identity = RUNTIME.process_identity(os.getpid())
        self.assertTrue(RUNTIME.identity_alive(identity))
        self.assertFalse(RUNTIME.identity_alive(None))
        self.assertFalse(
            RUNTIME.identity_alive({"pid": 999_999_999, "start": "0", "exe": "/gone"})
        )
        self.assertTrue(RUNTIME.is_descendant(os.getpid(), os.getpid()))
        self.assertFalse(RUNTIME.is_descendant(os.getpid(), 999_999_999))

    def test_stale_process_group_identity_is_never_signalled(self) -> None:
        stale_group = {
            "pgid": os.getpgrp(),
            "session": os.getsid(0) + 999_999,
            "leader": RUNTIME.process_identity(os.getpid()),
        }
        with mock.patch.object(RUNTIME.os, "killpg") as killpg:
            self.assertFalse(
                RUNTIME.signal_process_group(stale_group, RUNTIME.signal.SIGTERM)
            )
        killpg.assert_not_called()

    def test_zombie_only_process_group_is_not_alive(self) -> None:
        group = {"pgid": 123, "session": 123, "leader": None}
        with (
            mock.patch.object(
                RUNTIME.Path, "iterdir", return_value=[Path("/proc/123")]
            ),
            mock.patch.object(
                RUNTIME,
                "linux_proc_stat_fields",
                return_value=["Z", "1", "123", "123", *(["0"] * 16)],
            ),
        ):
            self.assertFalse(RUNTIME.process_group_alive(group))

    def test_codex_ancestor_and_sandbox_walks_are_deterministic(self) -> None:
        process_tree = {
            30: {"ppid": 20, "start": "30", "exe": "/usr/bin/python"},
            20: {"ppid": 10, "start": "20", "exe": "/opt/bin/codex (deleted)"},
        }
        with (
            mock.patch.dict(
                os.environ, {"CODEX_LONG_JOBS_TEST_CODEX_PID": ""}, clear=False
            ),
            mock.patch.object(
                RUNTIME, "process_info", side_effect=lambda pid: process_tree[pid]
            ),
        ):
            self.assertEqual(
                RUNTIME.find_codex_ancestor(30),
                {
                    "pid": 20,
                    "start": "20",
                    "exe": "/opt/bin/codex (deleted)",
                },
            )

        with mock.patch.object(
            RUNTIME,
            "process_info",
            return_value={"ppid": 0, "start": "1", "exe": "/usr/bin/python"},
        ):
            self.assertIsNone(RUNTIME.find_codex_ancestor(1))
        with mock.patch.object(RUNTIME, "process_info", side_effect=OSError("gone")):
            self.assertIsNone(RUNTIME.find_codex_ancestor(30))

        sandbox_tree = {
            30: {
                "ppid": 20,
                "start": "30",
                "exe": "/usr/bin/python",
                "cmdline": "python",
            },
            20: {
                "ppid": 1,
                "start": "20",
                "exe": "/usr/bin/codex-linux-sandbox",
                "cmdline": "codex-linux-sandbox",
            },
        }
        with (
            mock.patch.dict(
                os.environ, {"CODEX_LONG_JOBS_ALLOW_SANDBOX": "0"}, clear=False
            ),
            mock.patch.object(
                RUNTIME, "process_info", side_effect=lambda pid: sandbox_tree[pid]
            ),
        ):
            self.assertTrue(RUNTIME.running_under_codex_sandbox(30))

        with (
            mock.patch.dict(
                os.environ, {"CODEX_LONG_JOBS_ALLOW_SANDBOX": "0"}, clear=False
            ),
            mock.patch.object(
                RUNTIME,
                "process_info",
                return_value={
                    "ppid": 0,
                    "start": "1",
                    "exe": "/usr/bin/python",
                    "cmdline": "python",
                },
            ),
        ):
            self.assertFalse(RUNTIME.running_under_codex_sandbox(1))

    def test_signal_names_have_a_numeric_fallback(self) -> None:
        self.assertEqual(RUNTIME.return_code_signal(-15), "SIGTERM")
        self.assertEqual(RUNTIME.return_code_signal(-999), "SIG999")
        self.assertIsNone(RUNTIME.return_code_signal(0))
        self.assertIsNone(RUNTIME.return_code_signal(None))

    def test_delivery_selection_matrix(self) -> None:
        endpoint = {"pane_id": "%1"}
        self.assertEqual(
            RUNTIME.select_delivery(
                "auto",
                "thread-id",
                endpoint,
                "ok",
                queue_supported=True,
                queue_reason="supported",
            ),
            ("queue", "auto-queue"),
        )
        self.assertEqual(
            RUNTIME.select_delivery("auto", "thread-id", endpoint, "ok"),
            ("tui", "auto-tui"),
        )
        self.assertEqual(
            RUNTIME.select_delivery("auto", "thread-id", None, "no-pane"),
            ("tui", "auto-pending-rebind:no-pane"),
        )
        self.assertEqual(
            RUNTIME.select_delivery("auto", "", None, "no-thread"),
            ("event-only", "auto-fallback-thread-id-unavailable"),
        )
        self.assertEqual(
            RUNTIME.select_delivery("headless", "", None, "no-thread"),
            ("event-only", "headless-fallback-thread-id-unavailable"),
        )
        self.assertEqual(
            RUNTIME.select_delivery("event-only", "thread-id", None, "ignored"),
            ("event-only", "requested-event-only"),
        )
        self.assertEqual(
            RUNTIME.select_delivery(
                "queue",
                "thread-id",
                None,
                "no-pane",
                queue_supported=False,
                queue_reason="queue-options-unavailable",
            ),
            (
                "tui",
                "queue-fallback-pending-rebind:no-pane:queue-options-unavailable",
            ),
        )

    def test_queue_capability_requires_both_queue_options(self) -> None:
        supported = subprocess.CompletedProcess(
            [], 0, stdout="--thread <THREAD> --message <TEXT>", stderr=""
        )
        with mock.patch.object(RUNTIME.subprocess, "run", return_value=supported):
            self.assertEqual(RUNTIME.detect_queue_capability(), (True, "supported"))
        incomplete = subprocess.CompletedProcess(
            [], 0, stdout="--thread <THREAD>", stderr=""
        )
        with mock.patch.object(RUNTIME.subprocess, "run", return_value=incomplete):
            self.assertEqual(
                RUNTIME.detect_queue_capability(),
                (False, "queue-options-unavailable"),
            )
        with mock.patch.object(
            RUNTIME.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["codex", "queue"], 5),
        ):
            self.assertEqual(
                RUNTIME.detect_queue_capability(), (False, "queue-help-timeout")
            )

    def test_delivery_worker_lock_detection_is_conservative(self) -> None:
        self.write_record("delivery-lock")
        self.assertFalse(RUNTIME.delivery_worker_is_active("delivery-lock"))
        with mock.patch.object(RUNTIME.fcntl, "flock", side_effect=BlockingIOError):
            self.assertTrue(RUNTIME.delivery_worker_is_active("delivery-lock"))

    def test_screen_classification(self) -> None:
        self.assertTrue(RUNTIME.screen_is_busy("Working, ESC TO INTERRUPT"))
        self.assertFalse(RUNTIME.screen_is_busy("Ready"))
        self.assertTrue(RUNTIME.composer_is_empty("› \x1b[2mReady\x1b[0m"))
        self.assertFalse(RUNTIME.composer_is_empty("› typed text"))
        self.assertFalse(RUNTIME.composer_is_empty("no composer"))

    def test_capture_endpoint_reports_missing_context(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CODEX_THREAD_ID": "", "TMUX": "", "TMUX_PANE": ""},
            clear=False,
        ):
            self.assertEqual(
                RUNTIME.capture_endpoint(), (None, "thread-id-unavailable")
            )
        with mock.patch.dict(
            os.environ,
            {"CODEX_THREAD_ID": "valid-thread-id", "TMUX": "", "TMUX_PANE": ""},
            clear=False,
        ):
            self.assertEqual(RUNTIME.capture_endpoint(), (None, "not-running-in-tmux"))

    def test_validate_endpoint_classifies_identity_failures(self) -> None:
        endpoint = {
            "thread_id": "thread-id",
            "tmux_socket": "/tmp/fake",
            "tmux_server_pid": 1234,
            "pane_id": "%1",
            "pane_pid": 10,
            "pane_tty": "/dev/pts/1",
            "pane_identity": {"pid": 10},
            "codex_identity": {"pid": 11},
        }
        self.assertEqual(
            RUNTIME.validate_endpoint(endpoint, "other-thread"),
            (False, "thread-id-mismatch"),
        )

        server = subprocess.CompletedProcess([], 0, stdout="999\n", stderr="")
        with mock.patch.object(RUNTIME, "tmux_call", return_value=server):
            self.assertEqual(
                RUNTIME.validate_endpoint(endpoint, "thread-id"),
                (False, "tmux-server-restarted"),
            )

        server = subprocess.CompletedProcess([], 0, stdout="1234\n", stderr="")
        wrong_pane = subprocess.CompletedProcess(
            [], 0, stdout="%2|10|/dev/pts/1\n", stderr=""
        )
        with mock.patch.object(RUNTIME, "tmux_call", side_effect=[server, wrong_pane]):
            self.assertEqual(
                RUNTIME.validate_endpoint(endpoint, "thread-id"),
                (False, "pane-identity-mismatch"),
            )

        pane = subprocess.CompletedProcess(
            [], 0, stdout="%1|10|/dev/pts/1\n", stderr=""
        )
        with (
            mock.patch.object(RUNTIME, "tmux_call", side_effect=[server, pane]),
            mock.patch.object(RUNTIME, "identity_alive", return_value=False),
        ):
            self.assertEqual(
                RUNTIME.validate_endpoint(endpoint, "thread-id"),
                (False, "pane-process-changed"),
            )

        with (
            mock.patch.object(RUNTIME, "tmux_call", side_effect=[server, pane]),
            mock.patch.object(RUNTIME, "identity_alive", side_effect=[True, False]),
        ):
            self.assertEqual(
                RUNTIME.validate_endpoint(endpoint, "thread-id"),
                (False, "codex-process-changed"),
            )

        with (
            mock.patch.object(RUNTIME, "tmux_call", side_effect=[server, pane]),
            mock.patch.object(RUNTIME, "identity_alive", return_value=True),
            mock.patch.object(RUNTIME, "is_descendant", return_value=False),
        ):
            self.assertEqual(
                RUNTIME.validate_endpoint(endpoint, "thread-id"),
                (False, "codex-pane-ancestry-mismatch"),
            )

        with (
            mock.patch.object(RUNTIME, "tmux_call", side_effect=[server, pane]),
            mock.patch.object(RUNTIME, "identity_alive", return_value=True),
            mock.patch.object(RUNTIME, "is_descendant", return_value=True),
        ):
            self.assertEqual(
                RUNTIME.validate_endpoint(endpoint, "thread-id"),
                (True, "validated"),
            )

    def test_prepare_log_file_overwrites_only_regular_owned_file(self) -> None:
        control = self.state / "jobs" / "log-test"
        control.mkdir(mode=0o700)
        log = self.root / "existing.log"
        log.write_text("old", encoding="utf-8")
        self.assertFalse(
            RUNTIME.prepare_log_file(log, overwrite=True, control_directory=control)
        )
        self.assertEqual(log.read_bytes(), b"")
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(RUNTIME.JobError, "log already exists"):
            RUNTIME.prepare_log_file(log, overwrite=False, control_directory=control)

    def test_worker_revalidates_log_when_opening_for_append(self) -> None:
        log = self.root / "worker.log"
        log.write_bytes(b"before\n")
        with RUNTIME.open_log_for_append(log) as handle:
            handle.write(b"after\n")
        self.assertEqual(log.read_bytes(), b"before\nafter\n")
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)

        target = self.root / "target.log"
        target.write_bytes(b"untouched\n")
        log.unlink()
        log.symlink_to(target)
        with self.assertRaises(OSError):
            RUNTIME.open_log_for_append(log)
        self.assertEqual(target.read_bytes(), b"untouched\n")

    def test_start_viewer_handles_unavailable_existing_and_failed_tmux(self) -> None:
        log = self.root / "viewer.log"
        log.touch()
        with mock.patch.object(shutil := RUNTIME.shutil, "which", return_value=None):
            self.assertEqual(
                RUNTIME.start_viewer("viewer", log),
                (False, "tmux-or-tail-unavailable"),
            )

        def executable(name: str) -> str:
            return f"/usr/bin/{name}"

        existing = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with (
            mock.patch.object(shutil, "which", side_effect=executable),
            mock.patch.object(RUNTIME.subprocess, "run", return_value=existing),
        ):
            self.assertEqual(
                RUNTIME.start_viewer("viewer", log),
                (True, "viewer-already-running"),
            )

        absent = subprocess.CompletedProcess([], 1, stdout="", stderr="")
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="cannot start")
        with (
            mock.patch.object(shutil, "which", side_effect=executable),
            mock.patch.object(RUNTIME.subprocess, "run", side_effect=[absent, failed]),
        ):
            self.assertEqual(
                RUNTIME.start_viewer("viewer", log),
                (False, "viewer-start-failed:cannot start"),
            )

    def test_headless_without_thread_and_launch_failure_are_persisted(self) -> None:
        self.write_record(
            "no-thread",
            delivery_mode="headless",
            delivery_status="pending",
        )
        RUNTIME.launch_headless(RUNTIME.read_job("no-thread"))
        self.assertEqual(
            RUNTIME.read_job("no-thread")["delivery"]["reason"],
            "headless-thread-id-unavailable",
        )

        self.write_record(
            "headless-failure",
            delivery_mode="headless",
            delivery_status="pending",
            owner="valid-thread-id",
        )
        with mock.patch.object(
            RUNTIME.subprocess, "Popen", side_effect=OSError(errno.ENOENT, "missing")
        ):
            RUNTIME.launch_headless(RUNTIME.read_job("headless-failure"))
        self.assertEqual(
            RUNTIME.read_job("headless-failure")["delivery"]["reason"],
            f"headless-dispatch-failed:{errno.ENOENT}",
        )

    def test_status_empty_and_corrupt_state_handling(self) -> None:
        empty = io.StringIO()
        with contextlib.redirect_stdout(empty):
            self.assertEqual(RUNTIME.main(["status", "--json"]), 0)
        self.assertEqual(json.loads(empty.getvalue()), [])

        corrupt = self.state / "jobs" / "corrupt"
        corrupt.mkdir(mode=0o700)
        (corrupt / "state.json").write_text("not-json", encoding="utf-8")
        self.write_record("healthy", status="succeeded")
        listing = io.StringIO()
        error = io.StringIO()
        with contextlib.redirect_stdout(listing), contextlib.redirect_stderr(error):
            self.assertEqual(RUNTIME.main(["status", "--json"]), 1)
        self.assertEqual(
            [record["name"] for record in json.loads(listing.getvalue())], ["healthy"]
        )
        self.assertIn("job=corrupt unreadable-state=JSONDecodeError", error.getvalue())

    def test_cancel_parser_and_runtime_version_boundaries(self) -> None:
        self.assertEqual(RUNTIME.nonnegative_float("0"), 0.0)
        for invalid in ("-1", "nan", "inf", "-inf"):
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(argparse.ArgumentTypeError),
            ):
                RUNTIME.nonnegative_float(invalid)
        self.assertEqual(RUNTIME.version_tuple("0.3.0"), (0, 3, 0))
        self.assertIsNone(RUNTIME.version_tuple("development"))

        current = self.write_record("current-runtime", status="running")
        current["runtime_version"] = "0.3.0"
        self.assertTrue(RUNTIME.supports_cancellation(current))
        current["runtime_version"] = "0.2.0"
        self.assertFalse(RUNTIME.supports_cancellation(current))

        RUNTIME.atomic_write_json(RUNTIME.state_file("current-runtime"), current)
        before = RUNTIME.state_file("current-runtime").read_bytes()
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(RUNTIME.main(["cancel", "--name", "current-runtime"]), 1)
        self.assertIn("runtime older than 0.3.0", error.getvalue())
        self.assertEqual(RUNTIME.state_file("current-runtime").read_bytes(), before)

    def test_cancelled_completion_prompt_names_the_terminal_state(self) -> None:
        record = self.write_record("cancelled-prompt", status="cancelled")
        prompt = RUNTIME.completion_prompt(record)
        self.assertIn("was cancelled", prompt)
        self.assertNotIn("failed", prompt)

    def test_delivery_token_visibility_allows_only_tui_line_wrapping(self) -> None:
        token = "[codex-long-jobs:legacy-long-job-name:0123456789ab]"
        self.assertTrue(RUNTIME.delivery_token_is_visible(f"> {token}\n", token))
        self.assertTrue(
            RUNTIME.delivery_token_is_visible(
                "› [codex-long-jobs:legacy-long-\n  job-name:0123456789ab]\n",
                token,
            )
        )
        self.assertTrue(
            RUNTIME.delivery_token_is_visible(
                "› [codex-long-jobs:legacy- \n    long-job-name:012345\n  6789ab]\n",
                token,
            )
        )
        self.assertFalse(
            RUNTIME.delivery_token_is_visible(
                "› [codex-long-jobs:another-job:0123456789ab]\n",
                token,
            )
        )
        self.assertFalse(
            RUNTIME.delivery_token_is_visible(
                "› [codex-long-jobs:legacy-long-\n  unrelated job-name:0123456789ab]\n",
                token,
            )
        )

    def test_retry_delivery_terminal_and_nonterminal_boundaries(self) -> None:
        self.write_record(
            "already-delivered",
            delivery_mode="tui",
            delivery_status="delivered",
            owner="valid-thread-id",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                RUNTIME.main(["retry-delivery", "--name", "already-delivered"]),
                0,
            )
        self.assertIn("already completed", output.getvalue())

        self.write_record(
            "still-running",
            status="running",
            delivery_mode="tui",
            delivery_status="pending",
            owner="valid-thread-id",
        )
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(
                RUNTIME.main(["retry-delivery", "--name", "still-running"]), 1
            )
        self.assertIn("job is not terminal", error.getvalue())

    def test_supervisor_retries_delivery_only_after_abnormal_worker_exit(self) -> None:
        class FakeWorker:
            pid = 12345

            def __init__(self, name: str, return_code: int) -> None:
                self.name = name
                self.return_code = return_code

            def wait(self) -> int:
                def terminal(record: dict) -> dict:
                    record["status"] = "succeeded"
                    return record

                RUNTIME.update_job(self.name, terminal)
                return self.return_code

        for name, return_code, expected_calls in (
            ("normal-worker", 0, 0),
            ("signaled-worker", -9, 1),
        ):
            with self.subTest(name=name):
                self.write_record(
                    name,
                    status="queued",
                    delivery_mode="tui",
                    delivery_status="pending",
                )
                worker = FakeWorker(name, return_code)
                with (
                    mock.patch.object(RUNTIME.subprocess, "Popen", return_value=worker),
                    mock.patch.object(RUNTIME, "finalize_delivery") as finalize,
                ):
                    RUNTIME.supervisor_main(name)
                self.assertEqual(finalize.call_count, expected_calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
