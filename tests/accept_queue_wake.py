#!/usr/bin/env python3
"""Opt-in acceptance using an isolated real Codex server and a local model stub.

No credentials, existing threads, installed skills, or shared daemons are used.
The caller supplies an empty evidence directory and an explicitly selected CLI.
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import queue_wake as qw


class ModelStub(http.server.BaseHTTPRequestHandler):
    requests: list[dict] = []
    tool_next = False
    gate = threading.Event()
    entered = threading.Event()

    def log_message(self, *args):
        pass

    def do_POST(self):
        data = self.rfile.read(int(self.headers["Content-Length"]))
        request = json.loads(data)
        self.requests.append(request)
        self.entered.set()
        if not self.gate.wait(timeout=60):
            raise TimeoutError("model fixture was not released")
        identity = f"response-{len(self.requests)}"
        message = {
            "id": identity + "-message",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {
                    "type": "output_text",
                    "text": "ISOLATED_ACCEPTANCE_OK",
                    "annotations": [],
                }
            ],
        }
        if type(self).tool_next:
            type(self).tool_next = False
            message = {
                "type": "function_call",
                "id": identity + "-tool",
                "call_id": identity + "-call",
                "name": "acceptance_tool",
                "arguments": "{}",
                "status": "completed",
            }
        response = {
            "id": identity,
            "object": "response",
            "status": "completed",
            "output": [message],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
        events = [
            (
                "response.created",
                {"response": {**response, "status": "in_progress", "output": []}},
            ),
            ("response.output_item.done", {"output_index": 0, "item": message}),
            ("response.completed", {"response": response}),
        ]
        payload = "".join(
            f"event: {kind}\ndata: {json.dumps({'type': kind, **value})}\n\n"
            for kind, value in events
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class Acceptance:
    def __init__(self, binary: str, root: Path, *, server_binary: str | None = None):
        if root.exists():
            raise ValueError("evidence directory must not already exist")
        root.mkdir(parents=True, mode=0o700)
        self.root = root.resolve()
        self.binary = str(Path(binary).resolve())
        self.server_binary = str(Path(server_binary or binary).resolve())
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.work = self.root / "workspace"
        self.work.mkdir(mode=0o700)
        self.endpoint = self.home / "app-server-control/app-server-control.sock"
        self.endpoint.parent.mkdir(mode=0o700)
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not any(
                part in key.upper()
                for part in ("TOKEN", "API_KEY", "CODEX", "OPENAI", "ANTHROPIC")
            )
        }
        self.env.update(
            CODEX_HOME=str(self.home),
            CODEX_LONG_JOBS_STATE_DIR=str(self.root / "jobs-state"),
            CODEX_LONG_JOBS_CODEX_BIN=self.binary,
            NO_PROXY="127.0.0.1,localhost",
        )
        self.server = None
        self.processes = []
        self.handles = []
        ModelStub.gate.set()
        self.model = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ModelStub)
        self.model_thread = threading.Thread(
            target=self.model.serve_forever, daemon=True
        )
        self.model_thread.start()
        (self.home / "config.toml").write_text(f"""model = "gpt-5.2"
model_provider = "isolated"
approval_policy = "never"
sandbox_mode = "read-only"
[model_providers.isolated]
name = "Isolated acceptance model"
base_url = "http://127.0.0.1:{self.model.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
request_max_retries = 0
stream_max_retries = 0
[analytics]
enabled = false
""")
        self.results = []
        self.queue_count = 0

    def start_server(self, socket_path=None):
        endpoint = socket_path or self.endpoint
        handle = (self.root / f"server-{len(self.handles)}.log").open("wb")
        self.handles.append(handle)
        self.server = subprocess.Popen(
            [self.server_binary, "app-server", "--listen", "unix://" + str(endpoint)],
            env=self.env,
            cwd=self.work,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=handle,
            start_new_session=True,
        )
        self.processes.append(self.server)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                raise RuntimeError("isolated app server exited; inspect its log")
            if endpoint.is_socket():
                try:
                    rpc = qw.Rpc(endpoint)
                    return rpc
                except (OSError, qw.RpcError):
                    pass
            time.sleep(0.05)
        raise TimeoutError("isolated app server did not become ready")

    def stop_server(self):
        if self.server is not None:
            # Only this harness's newly created process group is signalled.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.server.pid, signal.SIGTERM)
            try:
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.server.pid, signal.SIGKILL)
                self.server.wait(timeout=5)
            self.server = None

    def materialized_thread(self, rpc, thread):
        try:
            return rpc.call("thread/read", {"threadId": thread, "includeTurns": True})[
                "thread"
            ]
        except qw.RpcError as exc:
            # A fresh thread can briefly lack a persisted turn listing. The
            # caller's existing deadline bounds this readiness condition.
            if "list_turns is not supported yet" in str(exc):
                return None
            raise

    def completed(self, rpc, thread, count):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            info = self.materialized_thread(rpc, thread)
            turns = info.get("turns", []) if info else []
            if len(turns) >= count and all(
                turn["status"] == "completed" for turn in turns
            ):
                return turns
            time.sleep(0.05)
        raise TimeoutError("isolated turns did not complete")

    def queue(self, thread, message, *, resume=False):
        self.queue_count += 1
        result = subprocess.run(
            [
                self.binary,
                "queue",
                *(["--resume"] if resume else []),
                "--thread",
                thread,
                "--message",
                message,
            ],
            env=self.env,
            cwd=self.work,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        (self.root / f"queue-{self.queue_count}.json").write_text(
            json.dumps(
                {
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
        )
        if result.returncode:
            raise RuntimeError("isolated real CLI queue failed; inspect its receipt")
        return result

    def check(self, name, **evidence):
        self.results.append({"case": name, **evidence})
        (self.root / "results.json").write_text(json.dumps(self.results, indent=2))
        print(json.dumps(self.results[-1]), flush=True)

    def run(self):
        version = subprocess.run(
            [self.binary, "--version"],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        capabilities = qw.jobs.codex_env.queue_capabilities(self.binary)
        if capabilities["resume"]:
            raise ValueError(
                "this phase-one harness requires a CLI without native --resume"
            )
        self.check("environment", version=version, native_resume=False)
        rpc = self.start_server()
        try:
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
                    "input": [
                        {
                            "type": "text",
                            "text": "Materialize this isolated test thread.",
                        }
                    ],
                },
            )
            self.completed(rpc, thread, 1)
            self.check("materialized", completed_turns=1)
        finally:
            rpc.close()
        self.stop_server()
        rpc = self.start_server()
        try:
            status = rpc.call(
                "thread/read", {"threadId": thread, "includeTurns": False}
            )["thread"]["status"]["type"]
            assert status == "notLoaded", status
            self.queue(thread, "Earlier isolated user input.")
            self.queue(thread, "Inspect isolated results. ACCEPTANCE_TOKEN")
            entries = qw.queue_entries(rpc, thread)
            assert len(entries) == 2, entries
            self.check(
                "old_cli_accepted_fifo", queued_items=len(entries), thread_status=status
            )
            result = qw.wake(rpc, thread, "ACCEPTANCE_TOKEN")
            assert result["status"] in {
                "consumed-after-resume",
                "started",
                "waiting-for-earlier-input",
                "waiting-for-idle",
            }, result
            turns = self.completed(rpc, thread, 3)
            messages = [
                part["text"]
                for turn in turns
                for item in turn["items"]
                if item.get("type") == "userMessage"
                for part in item.get("content", [])
                if part.get("type") == "text"
            ]
            assert messages[-2:] == [
                "Earlier isolated user input.",
                "Inspect isolated results. ACCEPTANCE_TOKEN",
            ], messages
            self.check(
                "cold_resume_preserved_fifo", wake=result, completed_turns=len(turns)
            )
            repeated = qw.wake(rpc, thread, "ACCEPTANCE_TOKEN")
            assert repeated["status"] == "absent", repeated
            assert len(qw.queue_entries(rpc, thread)) == 0
            assert len(self.completed(rpc, thread, 3)) == 3
            self.check(
                "repeated_wake_did_not_duplicate", wake=repeated, completed_turns=3
            )
            # Only a synthetic model response is held. No production process
            # or user turn participates in the writer-lock test.
            ModelStub.entered.clear()
            ModelStub.gate.clear()
            rpc.call(
                "turn/start",
                {
                    "threadId": thread,
                    "input": [
                        {"type": "text", "text": "Hold the isolated active turn."}
                    ],
                },
            )
            assert ModelStub.entered.wait(timeout=10)
            self.queue(thread, "Wait for the owning server. LOCK_TOKEN")
            assert len(qw.queue_entries(rpc, thread)) == 1
            owner = self.server
            contender = self.start_server(self.home / "contender.sock")
            try:
                try:
                    qw.wake(contender, thread, "LOCK_TOKEN")
                except qw.RpcError as exc:
                    assert any(
                        word in str(exc).lower()
                        for word in ("lock", "another", "active writer")
                    ), str(exc)
                    self.check(
                        "writer_lock_refused_safely",
                        preserved_items=len(qw.queue_entries(rpc, thread)),
                    )
                else:
                    raise AssertionError("expected isolated writer lock conflict")
            finally:
                contender.close()
                self.stop_server()
                self.server = owner
                ModelStub.gate.set()
            self.completed(rpc, thread, 5)
        finally:
            rpc.close()
        self.stop_server()
        self.recovery_case(thread)
        self.check("acceptance_complete", model_requests=len(ModelStub.requests))

    def run_concurrent_jobs(self, *, require_native=False):
        capabilities = qw.jobs.codex_env.queue_capabilities(self.binary)
        if capabilities["resume"] != require_native:
            raise ValueError("CLI resume capability differs from the requested case")
        runtime = Path(qw.jobs.__file__).resolve()
        rpc = self.start_server()
        try:
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
                    "input": [
                        {
                            "type": "text",
                            "text": "Seed the isolated concurrent-job thread.",
                        }
                    ],
                },
            )
            self.completed(rpc, thread, 1)
            ModelStub.entered.clear()
            ModelStub.gate.clear()
            rpc.call(
                "turn/start",
                {
                    "threadId": thread,
                    "input": [
                        {
                            "type": "text",
                            "text": "Hold the isolated turn during concurrent completion.",
                        }
                    ],
                },
            )
            assert ModelStub.entered.wait(timeout=10)
            environment = {**self.env, "CODEX_THREAD_ID": thread}
            names = [f"concurrent-{index}" for index in range(3)]
            state_root = Path(environment["CODEX_LONG_JOBS_STATE_DIR"])
            for name in names:
                code = f"from pathlib import Path; Path({name!r}).write_text({name!r}); print('BATCH_DONE')"
                started = subprocess.run(
                    [
                        sys.executable,
                        str(runtime),
                        "start",
                        "--name",
                        name,
                        "--log",
                        str(self.root / f"{name}.log"),
                        "--delivery",
                        "queue",
                        "--delivery-wait-seconds",
                        "30",
                        "--success-pattern",
                        "^BATCH_DONE$",
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
                    timeout=10,
                )
                (self.root / f"{name}-start.txt").write_text(started.stdout)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                records = [
                    json.loads((state_root / "jobs" / name / "state.json").read_text())
                    for name in names
                ]
                if all(record["delivery"]["status"] == "queued" for record in records):
                    break
                time.sleep(0.05)
            else:
                raise TimeoutError(
                    "concurrent test jobs did not reach accepted delivery"
                )
            assert all(record["status"] == "succeeded" for record in records)
            assert all(
                record["delivery"]["queue_resume"] == require_native
                for record in records
            )
            tokens = [record["delivery"]["token"] for record in records]
            entries = qw.queue_entries(rpc, thread)
            assert len(entries) == 3, entries
            order = [
                next(
                    token
                    for token in tokens
                    if any(
                        part.get("text", "").endswith(token) for part in entry["input"]
                    )
                )
                for entry in entries
            ]
            assert len(set(order)) == 3
            callers = []
            for name in names:
                for action in ["retry-wake", "retry-delivery"]:
                    caller = subprocess.Popen(
                        [
                            sys.executable,
                            str(runtime),
                            action,
                            "--name",
                            name,
                        ],
                        env=environment,
                        cwd=self.work,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        start_new_session=True,
                    )
                    self.processes.append(caller)
                    callers.append(caller)
            for caller in callers:
                _, stderr = caller.communicate(timeout=10)
                assert caller.returncode == 0, stderr
            assert qw.queue_entries(rpc, thread) == entries
            self.check(
                "concurrent_recovery_preserved_queue",
                jobs=3,
                recovery_callers=6,
                queued_items=3,
            )
            ModelStub.gate.set()
            turns = self.completed(rpc, thread, 5)
            consumed = [
                part["text"]
                for turn in turns
                for item in turn["items"]
                if item.get("type") == "userMessage"
                for part in item.get("content", [])
                if part.get("type") == "text"
            ]
            assert len(consumed) == 5
            assert all(
                text.endswith(token) for text, token in zip(consumed[-3:], order)
            )
            for name in names:
                record = json.loads(
                    (state_root / "jobs" / name / "state.json").read_text()
                )
                assert record["delivery"]["queue_attempts"] == 1, record
                assert (self.work / name).read_text() == name
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                identities = []
                for name in names:
                    directory = state_root / "jobs" / name
                    record = json.loads((directory / "state.json").read_text())
                    identities.append(record.get("supervisor_identity"))
                    if (directory / "queue-wake.json").exists():
                        identities.append(
                            json.loads((directory / "queue-wake.json").read_text()).get(
                                "helper_identity"
                            )
                        )
                if not any(qw.jobs.identity_alive(identity) for identity in identities):
                    break
                time.sleep(0.05)
            else:
                raise TimeoutError("isolated wake processes did not exit")
            self.check(
                "concurrent_jobs_consumed_once_in_fifo_order",
                jobs=3,
                queue_attempts_per_job=1,
                completed_turns=5,
                artifacts=3,
                wake_processes_exited=True,
            )
        finally:
            ModelStub.gate.set()
            rpc.close()
        self.stop_server()

    def run_tools_case(self):
        rpc = self.start_server()
        try:
            thread = rpc.call(
                "thread/start",
                {
                    "cwd": str(self.work),
                    "model": "gpt-5.2",
                    "modelProvider": "isolated",
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "dynamicTools": [
                        {
                            "type": "function",
                            "name": "acceptance_tool",
                            "description": "Isolated unavailable client tool",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ],
                },
            )["thread"]["id"]
            ModelStub.tool_next = True
            rpc.call(
                "turn/start",
                {
                    "threadId": thread,
                    "input": [
                        {
                            "type": "text",
                            "text": "Exercise the unavailable isolated client tool.",
                        }
                    ],
                },
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                info = self.materialized_thread(rpc, thread)
                if info is None:
                    time.sleep(0.05)
                    continue
                turns = info.get("turns", [])
                if turns and turns[-1]["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.05)
            else:
                raise TimeoutError("isolated tool turn did not reach terminal state")
            (self.root / "tool-turn.json").write_text(json.dumps(info, indent=2))
            assert rpc.unhandled_requests, info
            failures = [
                item
                for turn in turns
                for item in turn["items"]
                if item.get("type") == "dynamicToolCall"
                and (item.get("success") is False or item.get("status") == "failed")
            ]
            assert failures, info
            self.check(
                "unavailable_client_tool_refused",
                requests=sorted(rpc.unhandled_requests),
                failed_tool_calls=len(failures),
                turn_status=turns[-1]["status"],
            )
        finally:
            rpc.close()
        self.stop_server()

    def recovery_case(self, thread):
        rpc = self.start_server()
        self.queue(thread, "Recover after isolated server loss. RECOVERY_TOKEN")
        assert len(qw.queue_entries(rpc, thread)) == 1
        rpc.close()
        self.stop_server()
        directory = Path(self.env["CODEX_LONG_JOBS_STATE_DIR"]) / "jobs/recovery"
        directory.mkdir(parents=True, mode=0o700)
        record = {
            "schema_version": 1,
            "name": "recovery",
            "status": "succeeded",
            "cwd": str(self.work),
            "log": str(self.root / "synthetic-job.log"),
            "owner_thread_id": thread,
            "delivery": {
                "mode": "queue",
                "status": "queued",
                "token": "RECOVERY_TOKEN",
                "wait_seconds": 45,
                "queue_attempts": 1,
            },
        }
        qw.jobs.atomic_write_json(directory / "state.json", record)
        helper_log = (self.root / "helper.log").open("wb")
        self.handles.append(helper_log)
        helper = subprocess.Popen(
            [sys.executable, str(Path(qw.__file__)), "--name", "recovery"],
            env=self.env,
            stdout=helper_log,
            stderr=helper_log,
            start_new_session=True,
        )
        self.processes.append(helper)
        deadline = time.monotonic() + 10
        pending = None
        while time.monotonic() < deadline:
            if (directory / "queue-wake.json").exists():
                pending = json.loads((directory / "queue-wake.json").read_text())
                if pending.get("reason") != "wake-running":
                    break
            time.sleep(0.05)
        assert pending and pending.get("reason") != "wake-running", pending
        assert pending["status"] == "pending", pending
        self.check(
            "daemon_unavailable_preserves_acceptance",
            wake=pending["status"],
            queue_attempts=1,
        )
        rpc = self.start_server()
        try:
            helper.wait(timeout=35)
            wake = json.loads((directory / "queue-wake.json").read_text())
            self.completed(rpc, thread, 6)
            assert json.loads((directory / "state.json").read_text()) == record
            assert wake["status"] in qw.STOPPED - {"timed-out"}, wake
            repeated = subprocess.run(
                [
                    sys.executable,
                    str(Path(qw.__file__)),
                    "--retry",
                    "--name",
                    "recovery",
                ],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            assert repeated.returncode in {0, 1}
            assert len(self.completed(rpc, thread, 6)) == 6
            self.check(
                "durable_helper_recovered_without_resend",
                wake=wake["status"],
                completed_turns=6,
                queue_attempts=1,
            )
        finally:
            rpc.close()
        self.stop_server()

    def close(self):
        ModelStub.gate.set()
        self.stop_server()
        for process in self.processes:
            if process.poll() is None:
                self.server = process
                self.stop_server()
        self.model.shutdown()
        self.model.server_close()
        self.model_thread.join(timeout=2)
        for handle in self.handles:
            handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--server-codex")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tools-only", action="store_true")
    mode.add_argument("--concurrent-jobs", action="store_true")
    args = parser.parse_args()
    acceptance = Acceptance(
        args.codex, args.evidence_dir, server_binary=args.server_codex
    )
    os.environ.clear()
    os.environ.update(acceptance.env)
    try:
        if args.concurrent_jobs:
            acceptance.run_concurrent_jobs()
        elif args.tools_only:
            acceptance.run_tools_case()
        else:
            acceptance.run()
    finally:
        acceptance.close()


if __name__ == "__main__":
    main()
