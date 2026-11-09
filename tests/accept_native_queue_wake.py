#!/usr/bin/env python3
"""Opt-in native queue acceptance with private servers and a local model stub.

The CLI must support queue --resume. An optional independently selected server
and legacy CLI exercise version differences without changing any installation.
No production threads, credentials, shared daemons, or external models are used.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from accept_queue_wake import Acceptance, ModelStub, qw


def messages(turns):
    return [
        part["text"]
        for turn in turns
        for item in turn["items"]
        if item.get("type") == "userMessage"
        for part in item.get("content", [])
        if part.get("type") == "text"
    ]


class NativeAcceptance(Acceptance):
    def read_job(self, name):
        return json.loads((self.job_directory(name) / "state.json").read_text())

    def job_directory(self, name):
        return Path(self.env["CODEX_LONG_JOBS_STATE_DIR"]) / "jobs" / name

    def start_job(self, name, thread, binary=None):
        environment = {
            **self.env,
            "CODEX_THREAD_ID": thread,
            "CODEX_LONG_JOBS_CODEX_BIN": binary or self.binary,
        }
        code = (
            f"from pathlib import Path; Path({name!r}).write_text({name!r}); "
            "print('NATIVE_JOB_DONE')"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(Path(qw.jobs.__file__).resolve()),
                "start",
                "--name",
                name,
                "--log",
                str(self.root / f"{name}.log"),
                "--delivery",
                "queue",
                "--delivery-wait-seconds",
                "90",
                "--success-pattern",
                "^NATIVE_JOB_DONE$",
                "--viewer",
                "none",
                "--no-desktop-notify",
                "--",
                sys.executable,
                "-c",
                code,
            ],
            env=environment,
            cwd=self.work,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        (self.root / f"{name}-launch.txt").write_text(result.stdout)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            record = self.read_job(name)
            if record["delivery"]["status"] == "queued":
                assert record["status"] == "succeeded", record
                assert record["delivery"]["queue_attempts"] == 1, record
                assert (self.work / name).read_text() == name
                return record
            time.sleep(0.05)
        raise TimeoutError("native job notification was not accepted")

    def retry_job(self, name):
        for action in ("retry-wake", "retry-delivery"):
            subprocess.run(
                [sys.executable, str(Path(qw.jobs.__file__)), action, "--name", name],
                env=self.env,
                cwd=self.work,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
        assert self.read_job(name)["delivery"]["queue_attempts"] == 1

    def settle_job(self, name):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            record = self.read_job(name)
            path = self.job_directory(name) / "queue-wake.json"
            wake = json.loads(path.read_text()) if path.exists() else {}
            identities = [
                record.get("supervisor_identity"),
                wake.get("helper_identity"),
            ]
            if wake.get("status") in qw.STOPPED and not any(
                qw.jobs.identity_alive(identity) for identity in identities
            ):
                assert wake["status"] != "timed-out", wake
                return wake
            time.sleep(0.05)
        raise TimeoutError("native acceptance wake processes did not finish")

    def new_thread(self, rpc):
        thread = rpc.call(
            "thread/start",
            {
                "cwd": str(self.work),
                "model": "gpt-5.2",
                "modelProvider": "isolated",
                "approvalPolicy": "never",
                "sandbox": "read-only",
            },
        )["thread"]["id"]
        rpc.call(
            "turn/start",
            {
                "threadId": thread,
                "input": [{"type": "text", "text": "Seed isolated native thread."}],
            },
        )
        self.completed(rpc, thread, 1)
        return thread

    def cold_case(self, legacy_binary):
        with contextlib.closing(self.start_server()) as rpc:
            thread = self.new_thread(rpc)
        self.stop_server()
        with contextlib.closing(self.start_server()) as rpc:
            status = rpc.call(
                "thread/read", {"threadId": thread, "includeTurns": False}
            )["thread"]["status"]["type"]
            assert status == "notLoaded", status
            self.queue(thread, "Earlier native acceptance input.")
            assert len(qw.queue_entries(rpc, thread)) == 1
            record = self.start_job("native-cold", thread)
            assert record["delivery"]["queue_resume"] is True, record
            assert record["delivery"]["queue_resume_warning"] is False, record
            turns = self.completed(rpc, thread, 3)
            texts = messages(turns)
            assert len(texts) == 3, texts
            assert texts[-2] == "Earlier native acceptance input.", texts
            assert texts[-1].endswith(record["delivery"]["token"]), texts
            assert not qw.queue_entries(rpc, thread)
            wake = self.settle_job("native-cold")
            self.retry_job("native-cold")
            self.settle_job("native-cold")
            assert len(self.completed(rpc, thread, 3)) == 3
            self.check(
                "native_cold_resume_fifo_and_duplicate_suppression",
                queue_attempts=1,
                native_resume=True,
                resume_warning=False,
                completed_turns=3,
                wake_observation=wake["status"],
            )
        self.stop_server()
        if legacy_binary:
            with contextlib.closing(self.start_server()) as rpc:
                record = self.start_job("legacy-after-native", thread, legacy_binary)
                assert record["delivery"]["queue_resume"] is False, record
                turns = self.completed(rpc, thread, 4)
                assert messages(turns)[-1].endswith(record["delivery"]["token"])
                self.settle_job("legacy-after-native")
                self.check(
                    "legacy_cli_after_native_same_server",
                    queue_attempts=1,
                    native_resume=False,
                    completed_turns=4,
                )
            self.stop_server()

    def native_cli_case(self):
        with contextlib.closing(self.start_server()) as rpc:
            thread = self.new_thread(rpc)
        self.stop_server()
        with contextlib.closing(self.start_server()) as rpc:
            status = rpc.call(
                "thread/read", {"threadId": thread, "includeTurns": False}
            )["thread"]["status"]["type"]
            assert status == "notLoaded", status
            self.queue(thread, "Earlier direct native input.")
            result = self.queue(thread, "Direct native completion.", resume=True)
            assert "could not resume thread" not in result.stderr.lower()
            turns = self.completed(rpc, thread, 3)
            assert messages(turns)[-2:] == [
                "Earlier direct native input.",
                "Direct native completion.",
            ]
            assert len(turns) == 3 and not qw.queue_entries(rpc, thread)
            self.check(
                "native_cli_resumes_without_rpc_helper",
                completed_turns=3,
                preserved_fifo=True,
                cli_exit_code=result.returncode,
            )
        self.stop_server()

    def writer_case(self):
        # The owner and contender are both private processes created here.
        owner_rpc = self.start_server(self.home / "owner.sock")
        owner = self.server
        thread = self.new_thread(owner_rpc)
        ModelStub.entered.clear()
        ModelStub.gate.clear()
        owner_rpc.call(
            "turn/start",
            {
                "threadId": thread,
                "input": [{"type": "text", "text": "Hold the isolated writer."}],
            },
        )
        assert ModelStub.entered.wait(timeout=10)
        try:
            with contextlib.closing(self.start_server()) as rpc:
                contender = self.server
                record = self.start_job("native-writer", thread)
                delivery = record["delivery"]
                assert delivery["queue_resume"] is True, record
                assert delivery["queue_resume_warning"] is True, record
                assert delivery["queue_exit_code"] == 0, record
                entries = qw.queue_entries(rpc, thread)
                assert len(entries) == 1, entries
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    path = self.job_directory("native-writer") / "queue-wake.json"
                    wake = json.loads(path.read_text()) if path.exists() else {}
                    if "active writer" in wake.get("reason", "").lower():
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError(f"writer refusal not observed: {wake}")
                self.retry_job("native-writer")
                assert qw.queue_entries(rpc, thread) == entries
                self.check(
                    "native_accepted_despite_resume_failure",
                    queue_attempts=1,
                    cli_exit_code=0,
                    resume_warning=True,
                    wake_status=wake["status"],
                    preserved_items=1,
                )
                self.server = owner
                self.stop_server()
                self.server = contender
                owner_rpc.close()
                ModelStub.gate.set()
                # The interrupted owner turn is not expected to be completed.
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    info = self.materialized_thread(rpc, thread)
                    turns = info.get("turns", []) if info else []
                    matching = [
                        turn
                        for turn in turns
                        if any(
                            text.endswith(delivery["token"])
                            for text in messages([turn])
                        )
                    ]
                    if len(matching) == 1 and matching[0]["status"] == "completed":
                        break
                    time.sleep(0.05)
                else:
                    raise TimeoutError("wake-only recovery did not consume once")
                assert not qw.queue_entries(rpc, thread)
                wake = self.settle_job("native-writer")
                self.retry_job("native-writer")
                self.settle_job("native-writer")
                final = self.materialized_thread(rpc, thread)
                assert (
                    sum(
                        text.endswith(delivery["token"])
                        for text in messages(final["turns"])
                    )
                    == 1
                )
                self.check(
                    "native_warning_recovered_without_resend",
                    queue_attempts=1,
                    matching_completed_turns=1,
                    wake_observation=wake["status"],
                )
        finally:
            ModelStub.gate.set()
            owner_rpc.close()
        self.stop_server()

    def run_native(self, legacy_binary):
        assert qw.jobs.codex_env.queue_capabilities(self.binary)["resume"]
        if legacy_binary:
            assert not qw.jobs.codex_env.queue_capabilities(legacy_binary)["resume"]
        versions = {}
        for label, binary in (
            ("cli", self.binary),
            ("server", self.server_binary),
            ("legacy_cli", legacy_binary),
        ):
            if binary:
                versions[label] = subprocess.run(
                    [binary, "--version"],
                    env=self.env,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                ).stdout.strip()
        self.check("native_environment", **versions)
        self.native_cli_case()
        self.cold_case(legacy_binary)
        self.writer_case()
        self.run_concurrent_jobs(require_native=True)
        self.check("native_acceptance_complete", model_requests=len(ModelStub.requests))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--server-codex")
    parser.add_argument("--legacy-codex")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    acceptance = NativeAcceptance(
        args.codex, args.evidence_dir, server_binary=args.server_codex
    )
    os.environ.clear()
    os.environ.update(acceptance.env)
    try:
        acceptance.run_native(args.legacy_codex)
    finally:
        acceptance.close()


if __name__ == "__main__":
    main()
