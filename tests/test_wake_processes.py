"""Process-level wake ownership and crash recovery in private test directories."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import codex_long_jobs as jobs

ROOT = Path(__file__).resolve().parents[1]

HELD_HELPER = """
import os, sys
import queue_wake as qw
from test_queue_wake import FakeRpc
class HeldRpc(FakeRpc):
    def __init__(self, path):
        super().__init__()
        print('HELPER_READY', flush=True)
        os.read(int(sys.argv[1]), 1)
qw.Rpc = HeldRpc
raise SystemExit(qw.run('shared'))
"""

SUPERVISOR = """
import queue_wake as qw
from test_queue_wake import FakeRpc
class ObservedRpc(FakeRpc):
    def __init__(self, path):
        super().__init__()
        print('TAKEOVER_RPC', flush=True)
qw.Rpc = ObservedRpc
print('SUPERVISOR_READY', flush=True)
qw.jobs.supervise_queue_wake('shared')
"""


class WakeProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="clj-wake-process-")
        self.root = Path(self.temp.name)
        self.directory = self.root / "state/jobs/shared"
        self.directory.mkdir(parents=True)
        self.env = os.environ.copy()
        self.env.update(
            CODEX_HOME=str(self.root / "codex"),
            CODEX_LONG_JOBS_STATE_DIR=str(self.root / "state"),
            CODEX_LONG_JOBS_CODEX_BIN=str(ROOT / "tests/fake_codex.py"),
            FAKE_CODEX_STATE_DIR=str(self.root / "fake-codex"),
            CODEX_LONG_JOBS_ALLOW_SANDBOX="1",
            PYTHONPATH=os.pathsep.join([str(ROOT / "scripts"), str(ROOT / "tests")]),
        )
        self.record = {
            "schema_version": 1,
            "name": "shared",
            "status": "succeeded",
            "cwd": str(self.root),
            "log": str(self.root / "job.log"),
            "owner_thread_id": "thread",
            "notify_user": False,
            "delivery": {
                "mode": "queue",
                "status": "queued",
                "token": "TOKEN",
                "wait_seconds": 30,
            },
        }
        jobs.atomic_write_json(self.directory / "state.json", self.record)
        self.children = []
        self.pipe_read, self.pipe_write = os.pipe()

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        os.close(self.pipe_read)
        os.close(self.pipe_write)
        self.temp.cleanup()

    def child(self, code, *args, pass_fds=()):
        child = subprocess.Popen(
            [sys.executable, "-c", code, *map(str, args)],
            env=self.env,
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            pass_fds=pass_fds,
        )
        self.children.append(child)
        return child

    def ready(self, child, expected):
        readable, _, _ = select.select([child.stdout], [], [], 5)
        self.assertTrue(readable, "test child did not publish its readiness event")
        self.assertEqual(child.stdout.readline().strip(), expected)

    def evidence(self):
        return json.loads((self.directory / "queue-wake.json").read_text())

    def held_helper(self):
        child = self.child(HELD_HELPER, self.pipe_read, pass_fds=(self.pipe_read,))
        self.ready(child, "HELPER_READY")
        self.assertEqual(self.evidence()["helper_identity"]["pid"], child.pid)
        return child

    def test_supervisor_takes_over_after_real_helper_sigkill(self):
        before = (self.directory / "state.json").read_bytes()
        helper = self.held_helper()
        first = self.evidence()
        supervisor = self.child(SUPERVISOR)
        self.ready(supervisor, "SUPERVISOR_READY")
        self.assertIsNone(supervisor.poll())
        self.assertEqual(self.evidence(), first)

        helper.kill()
        helper.communicate(timeout=5)
        self.assertEqual(helper.returncode, -signal.SIGKILL)
        stdout, stderr = supervisor.communicate(timeout=5)
        self.assertEqual(supervisor.returncode, 0, stderr)
        self.assertEqual(stdout.count("TAKEOVER_RPC"), 1)
        final = self.evidence()
        self.assertEqual(final["status"], "started")
        self.assertEqual(final["attempts"], 2)
        self.assertEqual(final["deadline_at"], first["deadline_at"])
        self.assertEqual(final["helper_identity"]["pid"], supervisor.pid)
        self.assertEqual((self.directory / "state.json").read_bytes(), before)
        self.assertFalse((self.root / "fake-codex/queue-attempts.jsonl").exists())

    def test_helper_finishes_after_supervisor_only_loss(self):
        helper = self.held_helper()
        self.assertEqual(os.getsid(helper.pid), helper.pid)
        first = self.evidence()
        supervisor = self.child(SUPERVISOR)
        self.ready(supervisor, "SUPERVISOR_READY")
        supervisor.kill()
        supervisor.communicate(timeout=5)
        self.assertEqual(supervisor.returncode, -signal.SIGKILL)
        self.assertIsNone(helper.poll())
        os.write(self.pipe_write, b"1")
        _, stderr = helper.communicate(timeout=5)
        self.assertEqual(helper.returncode, 0, stderr)
        self.assertEqual(self.evidence()["status"], "started")
        self.assertEqual(self.evidence()["attempts"], 1)
        self.assertEqual(self.evidence()["deadline_at"], first["deadline_at"])

    def test_parallel_helpers_do_not_reset_or_duplicate_active_attempt(self):
        helper = self.held_helper()
        first = self.evidence()
        duplicate = self.child(
            "import queue_wake as qw; raise SystemExit(qw.run('shared', retry=True))"
        )
        stdout, stderr = duplicate.communicate(timeout=5)
        self.assertEqual(duplicate.returncode, 0, stderr)
        self.assertEqual(stdout, "")
        self.assertEqual(self.evidence(), first)

        os.write(self.pipe_write, b"1")
        stdout, stderr = helper.communicate(timeout=5)
        self.assertEqual(helper.returncode, 0, stderr)
        self.assertEqual(stdout.count('"status": "started"'), 1)
        self.assertEqual(self.evidence()["attempts"], 1)
        self.assertEqual(self.evidence()["deadline_at"], first["deadline_at"])

    def test_worker_crash_after_acceptance_is_recovered_without_resend(self):
        self.record["status"] = "queued"
        self.record["delivery"]["status"] = "pending"
        jobs.atomic_write_json(self.directory / "state.json", self.record)
        worker = self.root / "accepted_then_crashed.py"
        worker.write_text("""
import os
import codex_long_jobs as jobs
jobs.update_job('shared', lambda record: {**record, 'status': 'succeeded', 'exit_code': 0})
jobs.launch_queue_wake = lambda name: os._exit(17)
jobs.deliver_queue('shared')
""")
        supervisor = self.child(
            """
import sys
import queue_wake as qw
from test_queue_wake import FakeRpc
qw.Rpc = lambda path: FakeRpc()
qw.jobs.__file__ = sys.argv[1]
raise SystemExit(qw.jobs.supervisor_main('shared'))
""",
            worker,
        )
        _, stderr = supervisor.communicate(timeout=10)
        self.assertEqual(supervisor.returncode, 17, stderr)
        record = json.loads((self.directory / "state.json").read_text())
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["delivery"]["status"], "queued")
        self.assertEqual(record["delivery"]["queue_attempts"], 1)
        attempts = (
            (self.root / "fake-codex/queue-attempts.jsonl").read_text().splitlines()
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.evidence()["status"], "started")


if __name__ == "__main__":
    unittest.main()
