#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "scripts" / "codex_long_jobs.py"
FAKE_TMUX = REPO / "tests" / "fake_tmux.py"


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="codex-long-jobs-test-")
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.viewer_label = f"clj-test-{os.getpid()}-{time.time_ns()}"
        self.env = os.environ.copy()
        self.env.update(
            CODEX_LONG_JOBS_STATE_DIR=str(self.state),
            CODEX_LONG_JOBS_ALLOW_SANDBOX="1",
            CODEX_LONG_JOBS_TMUX_LABEL=self.viewer_label,
            CODEX_LONG_JOBS_DELIVERY_POLL_SECONDS="0.1",
            TMUX_TMPDIR=str(self.root),
        )

    def tearDown(self) -> None:
        subprocess.run(
            ["tmux", "-L", self.viewer_label, "kill-server"],
            env=self.env,
            check=False,
            capture_output=True,
        )
        self.temp.cleanup()

    def cli(
        self, *args: str, env: dict[str, str] | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            env=env or self.env,
            cwd=self.root,
            check=check,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def wait_terminal(self, name: str, timeout: float = 8) -> dict:
        deadline = time.monotonic() + timeout
        path = self.state / "jobs" / name / "state.json"
        while time.monotonic() < deadline:
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["status"] in {"succeeded", "failed"}:
                    return record
            time.sleep(0.05)
        self.fail(f"job did not become terminal: {name}")

    def start(
        self, name: str, code: str, *extra: str, env: dict[str, str] | None = None
    ) -> dict:
        log = self.root / f"{name}.log"
        self.cli(
            "start",
            "--name",
            name,
            "--log",
            str(log),
            "--success-pattern",
            "^DONE$",
            "--delivery",
            "event-only",
            "--viewer",
            "none",
            *extra,
            "--",
            sys.executable,
            "-c",
            code,
            env=env,
        )
        return self.wait_terminal(name)

    def test_success_and_nonzero_failure_are_persisted(self) -> None:
        success = self.start("success", "print('DONE')")
        self.assertEqual(success["status"], "succeeded")
        self.assertEqual(success["exit_code"], 0)
        self.assertTrue(success["marker_seen"])

        failure = self.start("failure", "print('not done'); raise SystemExit(7)")
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(failure["exit_code"], 7)
        self.assertIn("command-exit=7", failure["failure_reasons"])

    def test_cli_and_version_file_agree(self) -> None:
        expected = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        result = self.cli("--version")
        self.assertEqual(result.stdout.strip(), expected)

    def test_log_write_failure_forces_failure_even_when_command_exits_zero(
        self,
    ) -> None:
        env = self.env.copy()
        env["CODEX_LONG_JOBS_TEST_LOG_FAIL_AFTER_BYTES"] = "1"
        record = self.start("disk-full", "print('DONE')", env=env)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["exit_code"], 0)
        self.assertTrue(
            any(
                reason.startswith("log-write-failed=")
                for reason in record["failure_reasons"]
            )
        )
        self.assertFalse(
            (self.state / "jobs" / "disk-full" / ".final-state-reserve").exists()
        )

    def test_job_survives_disposable_tmux_viewer_server_exit(self) -> None:
        log = self.root / "viewer.log"
        launched = self.cli(
            "start",
            "--name",
            "viewer",
            "--log",
            str(log),
            "--success-pattern",
            "^DONE$",
            "--delivery",
            "event-only",
            "--viewer",
            "tmux",
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(0.6); print('DONE')",
        )
        if "viewer=viewer-started" not in launched.stdout:
            self.skipTest(
                "the active test sandbox blocks tmux Unix sockets; run this test on the host"
            )
        subprocess.run(
            ["tmux", "-L", self.viewer_label, "kill-server"],
            env=self.env,
            check=True,
            capture_output=True,
        )
        record = self.wait_terminal("viewer")
        self.assertEqual(record["status"], "succeeded")

    def fake_endpoint(self, thread: str) -> tuple[dict, dict[str, str], Path]:
        fake_root = self.root / "fake-tmux"
        fake_root.mkdir()
        pid = os.getpid()
        tty = "/dev/pts/fake"
        (fake_root / "server_pid").write_text("1234", encoding="utf-8")
        (fake_root / "pane_meta_full").write_text(
            f"%9|{pid}|{tty}|python|{self.root}", encoding="utf-8"
        )
        (fake_root / "pane_meta_short").write_text(f"%9|{pid}|{tty}", encoding="utf-8")
        (fake_root / "mode").write_text("busy", encoding="utf-8")
        (fake_root / "fail_enter_count").write_text("1", encoding="utf-8")
        import importlib.util

        spec = importlib.util.spec_from_file_location("clj_runtime", CLI)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        identity = module.process_identity(pid)
        endpoint = {
            "thread_id": thread,
            "tmux_socket": str(self.root / "fake.sock"),
            "tmux_server_pid": 1234,
            "pane_id": "%9",
            "pane_pid": pid,
            "pane_identity": identity,
            "pane_tty": tty,
            "pane_command": "python",
            "pane_cwd": str(self.root),
            "codex_identity": identity,
            "bound_at": "test",
        }
        env = self.env.copy()
        env.update(
            CODEX_LONG_JOBS_TMUX_BIN=str(FAKE_TMUX),
            FAKE_TMUX_STATE_DIR=str(fake_root),
            TMUX=f"{endpoint['tmux_socket']},1234,0",
            TMUX_PANE="%9",
            CODEX_THREAD_ID=thread,
            CODEX_LONG_JOBS_TEST_CODEX_PID=str(pid),
        )
        return endpoint, env, fake_root

    def write_terminal_job(self, name: str, endpoint: dict, thread: str) -> Path:
        directory = self.state / "jobs" / name
        directory.mkdir(parents=True, mode=0o700)
        log = self.root / f"{name}.log"
        log.write_text("DONE\n", encoding="utf-8")
        state_path = directory / "state.json"
        record = {
            "schema_version": 1,
            "runtime_version": "0.1.0",
            "name": name,
            "status": "succeeded",
            "created_at": "test",
            "updated_at": "test",
            "cwd": str(self.root),
            "command": ["true"],
            "log": str(log),
            "success_pattern": "^DONE$",
            "owner_thread_id": thread,
            "endpoint": endpoint,
            "delivery": {
                "mode": "tui",
                "status": "pending",
                "selection_reason": "test",
                "reason": "test",
                "token": f"[codex-long-jobs:{name}:token]",
                "wait_seconds": 3,
            },
        }
        state_path.write_text(json.dumps(record), encoding="utf-8")
        state_path.chmod(0o600)
        return state_path

    def test_busy_tui_waits_then_retries_missed_enter_without_duplicate_paste(
        self,
    ) -> None:
        thread = "01a012c8-b9fd-7f23-98f5-5d6ed5b64df5"
        endpoint, env, fake_root = self.fake_endpoint(thread)
        state_path = self.write_terminal_job("delivery", endpoint, thread)
        process = subprocess.Popen(
            [sys.executable, str(CLI), "_deliver", "--name", "delivery"],
            env=env,
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.3)
        self.assertFalse((fake_root / "paste_count").exists())
        (fake_root / "mode").write_text("idle", encoding="utf-8")
        stdout, stderr = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 0, stdout + stderr)
        record = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(record["delivery"]["status"], "delivered")
        self.assertEqual((fake_root / "paste_count").read_text(encoding="utf-8"), "1")
        self.assertEqual((fake_root / "enter_count").read_text(encoding="utf-8"), "2")

    def test_explicit_rebind_recovers_after_original_process_is_gone(self) -> None:
        thread = "01a012c8-b9fd-7f23-98f5-5d6ed5b64df5"
        endpoint, env, fake_root = self.fake_endpoint(thread)
        (fake_root / "mode").write_text("idle", encoding="utf-8")
        (fake_root / "fail_enter_count").write_text("0", encoding="utf-8")
        stale = dict(endpoint)
        stale["codex_identity"] = {"pid": 999999999, "start": "0", "exe": "/gone"}
        state_path = self.write_terminal_job("rebind", stale, thread)
        self.cli("rebind", "--name", "rebind", env=env)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            record = json.loads(state_path.read_text(encoding="utf-8"))
            if record["delivery"]["status"] == "delivered":
                break
            time.sleep(0.05)
        self.assertEqual(record["endpoint_capture_reason"], "explicit-rebind")
        self.assertEqual(record["delivery"]["status"], "delivered")
        self.assertEqual((fake_root / "paste_count").read_text(encoding="utf-8"), "1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
