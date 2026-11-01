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
FINISHED = {"started", "absent", "consumed-after-resume", "consumed-concurrently"}


class RpcError(RuntimeError):
    pass


class UnixWebSocket:
    """Minimal RFC 6455 text client for a local, owner-controlled Unix socket."""

    def __init__(self, path: Path, timeout: float = 30) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.buffer = bytearray()
        try:
            self.sock.connect(str(path))
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            self.sock.sendall(
                (
                    "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                    "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                    f"Sec-WebSocket-Key: {key}\r\n\r\n"
                ).encode("ascii")
            )
            while b"\r\n\r\n" not in self.buffer:
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

    def read(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk = self.sock.recv(min(65536, count - len(self.buffer)))
            if not chunk:
                raise ConnectionError("daemon socket closed")
            self.buffer.extend(chunk)
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def send_frame(self, opcode: int, data: bytes) -> None:
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
        self.sock.sendall(header + mask + masked)

    def send(self, message: dict[str, Any]) -> None:
        self.send_frame(1, json.dumps(message).encode("utf-8"))

    def receive(self, deadline: float) -> dict[str, Any]:
        chunks = bytearray()
        started = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("daemon response timed out")
            self.sock.settimeout(remaining)
            first, second = self.read(2)
            opcode, final, size = first & 15, bool(first & 128), second & 127
            if first & 0x70 or second & 128:
                raise ConnectionError("unexpected WebSocket extension or server mask")
            if size == 126:
                size = struct.unpack("!H", self.read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.read(8))[0]
            if size > MAX_MESSAGE or len(chunks) + size > MAX_MESSAGE:
                raise ConnectionError("daemon response too large")
            payload = self.read(size)
            if opcode >= 8:
                if not final or size > 125:
                    raise ConnectionError("invalid WebSocket control frame")
                if opcode == 8:
                    raise ConnectionError("daemon closed WebSocket")
                if opcode == 9:
                    self.send_frame(10, payload)
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
        self.ws.send({"id": self.sequence, "method": method, "params": params})
        deadline = time.monotonic() + 30
        while True:
            message = self.ws.receive(deadline)
            if message.get("id") == self.sequence and "method" not in message:
                if "error" in message:
                    raise RpcError(f"{method}: {message['error']}")
                return message["result"]
            if "id" in message and "method" in message:
                self.ws.send(
                    {
                        "id": message["id"],
                        "error": {
                            "code": -32601,
                            "message": "Queue wake client does not handle tools or approvals",
                        },
                    }
                )

    def close(self) -> None:
        self.ws.close()


def wake(rpc: Rpc, thread: str, token: str) -> dict[str, Any]:
    page = rpc.call("thread/queue/list", {"threadId": thread, "limit": 100})
    entries = page["data"]
    matching = [
        entry
        for entry in entries
        if any(
            part.get("type") == "text" and part.get("text", "").rstrip().endswith(token)
            for part in entry["input"]
        )
    ]
    if not matching:
        return {
            "status": "waiting-for-earlier-input"
            if page.get("nextCursor")
            else "absent"
        }
    item = matching[0]["id"]
    if item != entries[0]["id"]:
        return {"status": "waiting-for-earlier-input"}
    info = rpc.call("thread/read", {"threadId": thread, "includeTurns": False})[
        "thread"
    ]
    status = info["status"]["type"]
    if status == "notLoaded":
        info = rpc.call("thread/resume", {"threadId": thread, "excludeTurns": True})[
            "thread"
        ]
        status = info["status"]["type"]
        entries = rpc.call("thread/queue/list", {"threadId": thread, "limit": 100})[
            "data"
        ]
        if not any(entry["id"] == item for entry in entries):
            return {"status": "consumed-after-resume"}
        if entries[0]["id"] != item:
            return {"status": "waiting-for-earlier-input"}
    if status != "idle":
        return {"status": "waiting-for-idle", "thread_status": status}
    try:
        result = rpc.call(
            "thread/queue/start", {"threadId": thread, "queuedSubmissionId": item}
        )
    except RpcError:
        entries = rpc.call("thread/queue/list", {"threadId": thread, "limit": 100})[
            "data"
        ]
        if not any(entry["id"] == item for entry in entries):
            return {"status": "consumed-concurrently"}
        raise
    return {"status": "started", "turn_id": result["turn"]["id"]}


def run(name: str) -> int:
    record = jobs.read_job(name)
    directory = jobs.job_dir(name)
    jobs.ensure_private_dir(directory)
    fd = os.open(directory / "queue-wake.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        evidence = directory / "queue-wake.json"
        if (
            evidence.exists()
            and json.loads(evidence.read_text()).get("status") in FINISHED
        ):
            return 0
        delivery = record.get("delivery", {})
        if delivery.get("mode") != "queue" or delivery.get("status") != "queued":
            raise jobs.JobError("wake requires a completion already accepted by queue")
        thread, token = record.get("owner_thread_id"), delivery.get("token")
        if (
            not thread
            or not token
            or record.get("status") not in jobs.TERMINAL_STATUSES
        ):
            raise jobs.JobError(
                "wake requires a terminal job with owner and completion token"
            )
        root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        endpoint = root / "app-server-control/app-server-control.sock"
        deadline = time.monotonic() + max(1, int(delivery.get("wait_seconds", 86400)))
        last = None
        while True:
            try:
                with contextlib.closing(Rpc(endpoint)) as rpc:
                    result = wake(rpc, thread, token)
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                result = {"status": "pending", "reason": str(exc)[:1000]}
            if result != last:
                jobs.atomic_write_json(
                    evidence, {**result, "updated_at": jobs.now_iso()}
                )
                print(json.dumps(result), flush=True)
                last = result
            if result["status"] in FINISHED:
                return 0
            if time.monotonic() >= deadline:
                jobs.atomic_write_json(
                    evidence,
                    {
                        "status": "timed-out",
                        "last_result": result,
                        "updated_at": jobs.now_iso(),
                    },
                )
                return 1
            time.sleep(min(15, max(0, deadline - time.monotonic())))
    finally:
        os.close(fd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.name))
