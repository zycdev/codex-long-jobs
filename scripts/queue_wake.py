#!/usr/bin/env python3
"""Consume an accepted completion through the owner's existing Codex daemon.

The helper uses only the Python standard library. It never
adds queue items, changes permissions, or starts a separate headless agent.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import socket
import struct
import time
from pathlib import Path
from typing import Any

import codex_long_jobs as jobs

MAX_MESSAGE = 8 * 1024 * 1024
FINISHED = {"started", "consumed-after-resume", "consumed-concurrently"}
# These observations end an automatic attempt, but permit explicit reinspection.
STOPPED = FINISHED | {"absent", "timed-out"}


class RpcError(RuntimeError):
    pass


class UnixWebSocket:
    """Minimal RFC 6455 text client for a local, owner-controlled Unix socket."""

    def __init__(self, path: Path, timeout: float = 30) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.buffer = bytearray()
        deadline = time.monotonic() + timeout
        try:
            self.set_deadline(deadline)
            self.sock.connect(str(path))
            self.set_deadline(deadline)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            self.sock.sendall(
                (
                    "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                    "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                    f"Sec-WebSocket-Key: {key}\r\n\r\n"
                ).encode("ascii")
            )
            while b"\r\n\r\n" not in self.buffer:
                self.set_deadline(deadline)
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("socket closed during WebSocket upgrade")
                self.buffer.extend(chunk)
                if len(self.buffer) > 16384:
                    raise ConnectionError("WebSocket upgrade headers too large")
            head, rest = self.buffer.split(b"\r\n\r\n", 1)
            self.buffer = bytearray(rest)
            lines = head.decode("ascii").split("\r\n")
            headers = {
                key.lower(): value.strip()
                for key, value in (line.split(":", 1) for line in lines[1:])
            }
            expected = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
                ).digest()
            ).decode("ascii")
            if (
                lines[0].split()[1] != "101"
                or headers.get("sec-websocket-accept", "").strip() != expected
            ):
                raise ConnectionError("invalid WebSocket upgrade response")
        except BaseException:
            self.sock.close()
            raise

    def set_deadline(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("daemon operation timed out")
        self.sock.settimeout(remaining)

    def read(self, count: int, deadline: float) -> bytes:
        self.set_deadline(deadline)
        while len(self.buffer) < count:
            self.set_deadline(deadline)
            chunk = self.sock.recv(min(65536, count - len(self.buffer)))
            if not chunk:
                raise ConnectionError("daemon socket closed")
            self.buffer.extend(chunk)
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def send_frame(
        self, opcode: int, data: bytes, deadline: float | None = None
    ) -> None:
        mask = os.urandom(4)
        size = len(data)
        if size > MAX_MESSAGE:
            raise ValueError("WebSocket message too large")
        if size < 126:
            header = bytes([0x80 | opcode, 0x80 | size])
        elif size < 65536:
            header = bytes([0x80 | opcode, 0xFE]) + struct.pack("!H", size)
        else:
            header = bytes([0x80 | opcode, 0xFF]) + struct.pack("!Q", size)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(data))
        self.set_deadline(deadline if deadline is not None else time.monotonic() + 30)
        self.sock.sendall(header + mask + masked)

    def send(self, message: dict[str, Any], deadline: float | None = None) -> None:
        self.send_frame(1, json.dumps(message).encode("utf-8"), deadline)

    def receive(self, deadline: float) -> dict[str, Any]:
        chunks = bytearray()
        started = False
        while True:
            first, second = self.read(2, deadline)
            opcode, final, size = first & 15, bool(first & 128), second & 127
            if first & 0x70 or second & 128:
                raise ConnectionError("unexpected WebSocket extension or server mask")
            if size == 126:
                size = struct.unpack("!H", self.read(2, deadline))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.read(8, deadline))[0]
            if size > MAX_MESSAGE or len(chunks) + size > MAX_MESSAGE:
                raise ConnectionError("daemon response too large")
            payload = self.read(size, deadline)
            if opcode >= 8:
                if not final or size > 125:
                    raise ConnectionError("invalid WebSocket control frame")
                if opcode == 8:
                    raise ConnectionError("daemon closed WebSocket")
                if opcode == 9:
                    self.send_frame(10, payload, deadline)
                elif opcode != 10:
                    raise ConnectionError("unknown WebSocket control frame")
                continue
            if opcode == 1 and not started:
                started = True
            elif opcode != 0 or not started:
                raise ConnectionError("unexpected WebSocket data frame")
            chunks.extend(payload)
            if final:
                result = json.loads(chunks.decode("utf-8"))
                if not isinstance(result, dict):
                    raise ConnectionError("daemon response is not an object")
                return result

    def close(self) -> None:
        self.sock.close()


class Rpc:
    def __init__(self, path: Path) -> None:
        self.ws = UnixWebSocket(path)
        self.sequence = 0
        self.unhandled_requests: set[str] = set()
        try:
            self.call(
                "initialize",
                {
                    "clientInfo": {"name": "codex_long_jobs_wake", "version": "1.0"},
                    "capabilities": {
                        "experimentalApi": True,
                        "requestAttestation": False,
                    },
                },
            )
            self.ws.send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.sequence += 1
        deadline = time.monotonic() + 30
        self.ws.send(
            {"id": self.sequence, "method": method, "params": params}, deadline=deadline
        )
        while True:
            message = self.ws.receive(deadline)
            if message.get("id") == self.sequence and "method" not in message:
                if "error" in message:
                    raise RpcError(f"{method}: {message['error']}")
                return message["result"]
            if "id" in message and "method" in message:
                self.unhandled_requests.add(str(message["method"]))
                self.ws.send(
                    {
                        "id": message["id"],
                        "error": {
                            "code": -32601,
                            "message": "Queue wake client does not handle tools or approvals",
                        },
                    },
                    deadline=deadline,
                )

    def close(self) -> None:
        self.ws.close()


def queue_entries(rpc: Rpc, thread: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    cursor = None
    seen: set[str] = set()
    while True:
        params: dict[str, Any] = {"threadId": thread, "limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        page = rpc.call("thread/queue/list", params)
        entries.extend(page["data"])
        cursor = page.get("nextCursor")
        if not cursor:
            return entries
        if cursor in seen or len(seen) >= 1000:
            raise RpcError("queue pagination did not terminate")
        seen.add(cursor)


def wake(rpc: Rpc, thread: str, token: str) -> dict[str, Any]:
    entries = queue_entries(rpc, thread)
    matching = [
        entry
        for entry in entries
        if any(
            part.get("type") == "text" and part.get("text", "").rstrip().endswith(token)
            for part in entry["input"]
        )
    ]
    if not matching:
        return {"status": "absent"}
    if len(matching) != 1:
        return {"status": "pending", "reason": "ambiguous-completion-token"}
    item = matching[0]["id"]
    info = rpc.call("thread/read", {"threadId": thread, "includeTurns": False})[
        "thread"
    ]
    status = info["status"]["type"]
    resumed = status == "notLoaded"
    if resumed:
        # Resumption dispatches the persisted queue in its existing order. It is
        # independent of whether our completion currently occupies the head.
        rpc.call("thread/resume", {"threadId": thread, "excludeTurns": True})
    # Resume and concurrent consumers may change both the queue and thread state.
    entries = queue_entries(rpc, thread)
    if not any(entry["id"] == item for entry in entries):
        return {
            "status": "consumed-after-resume" if resumed else "consumed-concurrently"
        }
    if entries[0]["id"] != item:
        return {"status": "waiting-for-earlier-input"}
    status = rpc.call("thread/read", {"threadId": thread, "includeTurns": False})[
        "thread"
    ]["status"]["type"]
    if status != "idle":
        return {"status": "waiting-for-idle", "thread_status": status}
    try:
        # Select the head atomically on the server. An explicit ID can bypass
        # earlier input if another client reorders the queue after our snapshot.
        result = rpc.call("thread/queue/start", {"threadId": thread})
    except RpcError as exc:
        entries = queue_entries(rpc, thread)
        if not any(entry["id"] == item for entry in entries):
            return {"status": "consumed-concurrently"}
        if "thread already has an active or pending turn" in str(exc):
            return {"status": "waiting-for-idle", "thread_status": "active-or-pending"}
        raise
    turn = result["turn"]
    for entry in turn.get("items", []):
        if entry.get("type") == "userMessage" and any(
            part.get("type") == "text" and part.get("text", "").rstrip().endswith(token)
            for part in entry.get("content", [])
        ):
            return {"status": "started", "turn_id": turn["id"]}
    # Some versions return a turn before its input is visible. Preserve the
    # observed turn ID without claiming it belongs to our completion.
    remaining = queue_entries(rpc, thread)
    return {
        "status": "consumed-concurrently"
        if not any(entry["id"] == item for entry in remaining)
        else "waiting-for-earlier-input",
        "observed_turn_id": turn["id"],
    }


def run(name: str, *, retry: bool = False, wait_for_lock: bool = False) -> int:
    directory = jobs.job_dir(name)
    # Validate before creating files, and re-read after obtaining the lock.
    jobs.validate_queue_wake(jobs.read_job(name))
    fd = os.open(directory / "queue-wake.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait_for_lock else fcntl.LOCK_NB))
        except BlockingIOError:
            return 0
        record = jobs.read_job(name)
        jobs.validate_queue_wake(record)
        evidence = directory / "queue-wake.json"
        previous = jobs.read_queue_wake(name)
        status = previous.get("status")
        if status in FINISHED:
            return 0
        if status in STOPPED and not retry:
            return 1
        delivery = record["delivery"]
        thread, token = record["owner_thread_id"], delivery["token"]
        root = Path(delivery.get("queue_codex_home") or jobs.codex_env.codex_home())
        endpoint = root / "app-server-control/app-server-control.sock"
        now = time.time()
        # A crashed helper or supervisor continues the same bounded attempt.
        # Only an explicit retry starts a new waiting interval.
        deadline_at = (
            now + max(1, int(delivery.get("wait_seconds", 86400)))
            if retry or not previous.get("deadline_at")
            else float(previous["deadline_at"])
        )
        deadline = time.monotonic() + max(0, deadline_at - now)
        unhandled = set(previous.get("unhandled_requests", []))
        context = {
            "attempts": int(previous.get("attempts", 0)) + 1,
            "deadline_at": deadline_at,
            "helper_identity": jobs.process_identity(os.getpid()),
        }
        if unhandled:
            context["unhandled_requests"] = sorted(unhandled)
        # Persist ownership and the deadline before the first external RPC, so
        # a crash during connection or resume cannot restart the waiting budget.
        jobs.atomic_write_json(
            evidence,
            {
                **context,
                "status": "pending",
                "reason": "wake-running",
                "updated_at": jobs.now_iso(),
            },
        )
        last = None
        while time.monotonic() < deadline:
            rpc = None
            try:
                with contextlib.closing(Rpc(endpoint)) as rpc:
                    result = wake(rpc, thread, token)
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                result = {"status": "pending", "reason": str(exc)[:1000]}
            if rpc is not None:
                unhandled.update(getattr(rpc, "unhandled_requests", set()))
            if unhandled:
                context["unhandled_requests"] = sorted(unhandled)
                result["unhandled_requests"] = sorted(unhandled)
            if result != last:
                jobs.atomic_write_json(
                    evidence, {**context, **result, "updated_at": jobs.now_iso()}
                )
                print(json.dumps(result), flush=True)
                last = result
            if result["status"] in FINISHED:
                return 0
            if result["status"] == "absent":
                return 1
            time.sleep(min(15, max(0, deadline - time.monotonic())))
        jobs.atomic_write_json(
            evidence,
            {
                **context,
                "status": "timed-out",
                "last_result": last if last is not None else previous,
                "updated_at": jobs.now_iso(),
            },
        )
        return 1
    finally:
        os.close(fd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args.name, retry=args.retry))
