from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import queue_wake as qw


class FakeRpc:
    def __init__(self, status="idle", consume_on_resume=False, race=False):
        self.status = status
        self.consume_on_resume = consume_on_resume
        self.race = race
        self.entries = [
            {"id": "job-id", "input": [{"type": "text", "text": "done TOKEN"}]}
        ]
        self.next_cursor = None
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/queue/list":
            return {"data": list(self.entries), "nextCursor": self.next_cursor}
        if method == "thread/read":
            return {"thread": {"status": {"type": self.status}}}
        if method == "thread/resume":
            self.status = "idle"
            if self.consume_on_resume:
                self.entries = []
            return {"thread": {"status": {"type": self.status}}}
        if method == "thread/queue/start":
            assert params == {"threadId": "thread"}
            self.entries = []
            if self.race:
                raise qw.RpcError("already consumed")
            return {
                "turn": {
                    "id": "turn-id",
                    "items": [
                        {
                            "type": "userMessage",
                            "content": [{"type": "text", "text": "done TOKEN"}],
                        }
                    ],
                }
            }
        raise AssertionError(method)

    def close(self):
        pass


class FakeSocket:
    def __init__(self, incoming=b"", bad_handshake=False):
        self.incoming = incoming
        self.sent = []
        self.closed = False
        self.bad_handshake = bad_handshake

    def connect(self, path):
        self.path = path

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        self.sent.append(data)
        if data.startswith(b"GET "):
            key = data.split(b"Sec-WebSocket-Key: ")[1].split(b"\r\n")[0]
            accept = base64.b64encode(
                hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
            )
            if self.bad_handshake:
                accept = b"wrong"
            self.incoming = (
                b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: "
                + accept
                + b"\r\n\r\n"
                + self.incoming
            )

    def recv(self, count):
        # Split network reads to exercise partial headers and payloads.
        count = min(count, 17)
        result, self.incoming = self.incoming[:count], self.incoming[count:]
        return result

    def close(self):
        self.closed = True


def server_frame(payload, opcode=1, final=True):
    size = len(payload)
    first = opcode | (128 if final else 0)
    if size < 126:
        return bytes([first, size]) + payload
    if size < 65536:
        return bytes([first, 126]) + struct.pack("!H", size) + payload
    return bytes([first, 127]) + struct.pack("!Q", size) + payload


class WakeTests(unittest.TestCase):
    def test_unloaded_consumes_during_resume(self):
        rpc = FakeRpc("notLoaded", consume_on_resume=True)
        self.assertEqual(
            qw.wake(rpc, "thread", "TOKEN")["status"], "consumed-after-resume"
        )
        self.assertNotIn("thread/queue/start", [m for m, _ in rpc.calls])

    def test_idle_starts_fifo_head_with_correlated_input(self):
        self.assertEqual(
            qw.wake(FakeRpc(), "thread", "TOKEN"),
            {"status": "started", "turn_id": "turn-id"},
        )

    def test_unloaded_resume_without_consumption_starts_existing_id(self):
        self.assertEqual(
            qw.wake(FakeRpc("notLoaded"), "thread", "TOKEN")["status"], "started"
        )

    def test_active_never_interrupts(self):
        rpc = FakeRpc("active")
        self.assertEqual(qw.wake(rpc, "thread", "TOKEN")["status"], "waiting-for-idle")
        self.assertNotIn("thread/queue/start", [m for m, _ in rpc.calls])

    def test_earlier_user_input_not_bypassed(self):
        rpc = FakeRpc()
        rpc.entries.insert(
            0, {"id": "user", "input": [{"type": "text", "text": "other"}]}
        )
        self.assertEqual(
            qw.wake(rpc, "thread", "TOKEN")["status"], "waiting-for-earlier-input"
        )
        self.assertNotIn("thread/queue/start", [m for m, _ in rpc.calls])

    def test_missing_token_never_adds_message(self):
        rpc = FakeRpc()
        self.assertEqual(qw.wake(rpc, "thread", "MISSING")["status"], "absent")
        self.assertEqual(len(rpc.calls), 1)

    def test_later_page_not_misreported_absent(self):
        rpc = FakeRpc("notLoaded")
        original = rpc.call

        def paged(method, params):
            if method == "thread/queue/list":
                if params.get("cursor") == "more":
                    return {"data": rpc.entries, "nextCursor": None}
                return {"data": [{"id": "earlier", "input": []}], "nextCursor": "more"}
            return original(method, params)

        rpc.call = paged
        self.assertEqual(
            qw.wake(rpc, "thread", "TOKEN")["status"], "waiting-for-earlier-input"
        )
        self.assertIn("thread/resume", [m for m, _ in rpc.calls])
        self.assertNotIn("thread/queue/start", [m for m, _ in rpc.calls])

    def test_ui_race_no_duplicate(self):
        rpc = FakeRpc(race=True)
        self.assertEqual(
            qw.wake(rpc, "thread", "TOKEN")["status"], "consumed-concurrently"
        )
        self.assertEqual(qw.wake(rpc, "thread", "TOKEN")["status"], "absent")
        self.assertEqual(sum(m == "thread/queue/start" for m, _ in rpc.calls), 1)

    def test_unconsumed_start_error_remains_retriable(self):
        rpc = FakeRpc()
        original = rpc.call

        def call(method, params):
            if method == "thread/queue/start":
                raise qw.RpcError("busy")
            return original(method, params)

        rpc.call = call
        with self.assertRaisesRegex(qw.RpcError, "busy"):
            qw.wake(rpc, "thread", "TOKEN")

    def test_record_is_separate_and_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.dict(os.environ, {"CODEX_LONG_JOBS_STATE_DIR": str(root)}):
                directory = root / "jobs/test"
                directory.mkdir(parents=True)
                record = {
                    "schema_version": 1,
                    "name": "test",
                    "status": "succeeded",
                    "owner_thread_id": "thread",
                    "delivery": {
                        "mode": "queue",
                        "status": "queued",
                        "token": "TOKEN",
                        "wait_seconds": 1,
                    },
                }
                qw.jobs.atomic_write_json(directory / "state.json", record)
                before = (directory / "state.json").read_bytes()
                with mock.patch.object(qw, "Rpc", return_value=FakeRpc()) as rpc:
                    self.assertEqual(qw.run("test"), 0)
                    self.assertEqual(qw.run("test"), 0)
                    self.assertEqual(rpc.call_count, 1)
                self.assertEqual((directory / "state.json").read_bytes(), before)
                evidence = json.loads((directory / "queue-wake.json").read_text())
                self.assertEqual(evidence["status"], "started")
                self.assertEqual(
                    (directory / "queue-wake.json").stat().st_mode & 0o777, 0o600
                )

    def test_launch_is_detached_and_does_not_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.dict(os.environ, {"CODEX_LONG_JOBS_STATE_DIR": str(root)}):
                (root / "jobs/test").mkdir(parents=True)
                qw.jobs.atomic_write_json(
                    root / "jobs/test/state.json",
                    {
                        "schema_version": 1,
                        "name": "test",
                        "status": "succeeded",
                        "owner_thread_id": "thread",
                        "delivery": {
                            "mode": "queue",
                            "status": "queued",
                            "token": "TOKEN",
                        },
                    },
                )
                with mock.patch.object(qw.jobs.subprocess, "Popen") as popen:
                    qw.jobs.launch_queue_wake("test")
                    args, kwargs = popen.call_args
                    self.assertTrue(args[0][1].endswith("queue_wake.py"))
                    self.assertEqual(args[0][-2:], ["--name", "test"])
                    self.assertTrue(kwargs["start_new_session"])
                    self.assertEqual(kwargs["stdin"], qw.jobs.subprocess.DEVNULL)


class WebSocketTests(unittest.TestCase):
    def connect(self, incoming=b"", bad_handshake=False):
        sock = FakeSocket(incoming, bad_handshake)
        with mock.patch.object(qw.socket, "socket", return_value=sock):
            ws = qw.UnixWebSocket(Path("/tmp/test.sock"))
        return ws, sock

    def test_handshake_and_buffered_frame(self):
        ws, sock = self.connect(server_frame(b'{"ok":true}'))
        self.assertEqual(ws.receive(time.monotonic() + 1), {"ok": True})
        self.assertEqual(sock.path, "/tmp/test.sock")
        ws.close()
        self.assertTrue(sock.closed)

    def test_invalid_handshake_closes_socket(self):
        sock = FakeSocket(bad_handshake=True)
        with (
            mock.patch.object(qw.socket, "socket", return_value=sock),
            self.assertRaises(ConnectionError),
        ):
            qw.UnixWebSocket(Path("/tmp/test.sock"))
        self.assertTrue(sock.closed)

    def test_fragmentation_and_ping(self):
        incoming = (
            server_frame(b'{"x":', final=False)
            + server_frame(b"hi", opcode=9)
            + server_frame(b"1}", opcode=0)
        )
        ws, sock = self.connect(incoming)
        self.assertEqual(ws.receive(time.monotonic() + 1), {"x": 1})
        self.assertEqual(sock.sent[-1][0], 0x8A)

    def test_client_mask_and_length_encodings(self):
        for size in [5, 126, 65536]:
            with self.subTest(size=size):
                ws, sock = self.connect()
                data = b"x" * size
                ws.send_frame(1, data)
                frame = sock.sent[-1]
                self.assertTrue(frame[1] & 128)
                offset = 2 if size < 126 else 4 if size < 65536 else 10
                mask, body = frame[offset : offset + 4], frame[offset + 4 :]
                self.assertEqual(
                    bytes(v ^ mask[i % 4] for i, v in enumerate(body)), data
                )

    def test_server_length_encodings(self):
        for size in [126, 65536]:
            data = {"x": "a" * size}
            ws, _ = self.connect(server_frame(json.dumps(data).encode()))
            self.assertEqual(ws.receive(time.monotonic() + 2), data)

    def test_oversize_frame_rejected_before_reading_body(self):
        ws, _ = self.connect(b"\x81\x7f" + struct.pack("!Q", qw.MAX_MESSAGE + 1))
        with self.assertRaisesRegex(ConnectionError, "too large"):
            ws.receive(time.monotonic() + 1)

    def test_slow_upgrade_headers_cannot_extend_connection_deadline(self):
        sock = FakeSocket()
        original = sock.recv
        clock = [0.0]

        def slow_recv(count):
            clock[0] += 0.4
            return original(count)

        sock.recv = slow_recv
        with (
            mock.patch.object(qw.socket, "socket", return_value=sock),
            mock.patch.object(qw.time, "monotonic", side_effect=lambda: clock[0]),
            self.assertRaises(TimeoutError),
        ):
            qw.UnixWebSocket(Path("/tmp/test.sock"), timeout=1)
        self.assertTrue(sock.closed)

    def test_slow_frame_payload_cannot_extend_rpc_deadline(self):
        ws, sock = self.connect()
        sock.incoming = server_frame(json.dumps({"value": "x" * 200}).encode())
        original = sock.recv
        clock = [0.0]

        def slow_recv(count):
            clock[0] += 0.3
            return original(count)

        sock.recv = slow_recv
        with (
            mock.patch.object(qw.time, "monotonic", side_effect=lambda: clock[0]),
            self.assertRaises(TimeoutError),
        ):
            ws.receive(1.0)
        ws.close()

    def test_close_eof_and_deadline(self):
        for frame in [b"", server_frame(b"", opcode=8)]:
            ws, _ = self.connect(frame)
            with self.assertRaises(ConnectionError):
                ws.receive(time.monotonic() + 1)
        ws, _ = self.connect()
        with self.assertRaises(TimeoutError):
            ws.receive(time.monotonic() - 1)


if __name__ == "__main__":
    unittest.main()
