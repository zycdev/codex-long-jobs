from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "check_updates.py"
SPEC = importlib.util.spec_from_file_location("clj_updates", SOURCE)
assert SPEC and SPEC.loader
UPDATES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATES)


class UpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="clj-updates-")
        self.addCleanup(self.temp.cleanup)
        self.environment = mock.patch.dict(os.environ, {"CODEX_HOME": self.temp.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.clock = mock.patch.object(UPDATES.time, "time", return_value=1000000)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.network = mock.patch.object(
            UPDATES, "fetch_release", return_value="v99.0.0"
        )
        self.fetch = self.network.start()
        self.addCleanup(self.network.stop)

    def invoke(self, *args: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(UPDATES.main(list(args)), 0)
        return json.loads(output.getvalue())

    def test_defaults_and_exact_due_boundary(self) -> None:
        initialized = self.invoke("init")
        self.assertTrue(initialized["enabled"])
        self.assertEqual(initialized["interval_seconds"], 604800)
        self.assertIsNone(initialized["last_attempt_at"])
        self.assertIn("--enabled no", initialized["notice"])
        self.now.return_value += 604799
        self.assertEqual(self.invoke("check")["status"], "not-due")
        self.fetch.assert_not_called()
        self.now.return_value += 1
        checked = self.invoke("check")
        self.assertEqual(checked["status"], "update-available")
        self.assertEqual(checked["release_url"], UPDATES.RELEASE_URL + "v99.0.0")
        self.assertEqual(self.invoke("check")["status"], "not-due")
        self.fetch.assert_called_once()
        self.assertEqual(UPDATES.state_path().stat().st_mode & 0o777, 0o600)

    def test_first_use_initializes_without_network(self) -> None:
        self.assertEqual(self.invoke("check")["status"], "not-due")
        self.fetch.assert_not_called()

    def test_disabled_and_reinitialization_preserve_preferences(self) -> None:
        self.invoke("configure", "--enabled", "no", "--interval", "12h")
        self.now.return_value += 900000
        self.assertEqual(self.invoke("check")["status"], "disabled")
        result = self.invoke("init")
        self.assertFalse(result["enabled"])
        self.assertEqual(result["interval_seconds"], 43200)
        self.fetch.assert_not_called()
        self.invoke("configure", "--enabled", "yes")
        self.assertEqual(self.invoke("check")["status"], "update-available")
        self.invoke("configure", "--interval", "2w")
        self.now.return_value += 604800
        self.assertEqual(self.invoke("check")["status"], "not-due")

    def test_failure_and_interruption_are_throttled(self) -> None:
        for error in (
            OSError("offline"),
            ValueError("invalid response"),
            UPDATES.http.client.IncompleteRead(b"partial"),
            KeyboardInterrupt(),
        ):
            with self.subTest(error=type(error).__name__):
                self.invoke("init")
                self.now.return_value += 604800
                self.fetch.side_effect = error
                if isinstance(error, KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        self.invoke("check")
                else:
                    self.assertEqual(self.invoke("check")["status"], "unavailable")
                calls = self.fetch.call_count
                self.assertEqual(self.invoke("check")["status"], "not-due")
                self.assertEqual(self.fetch.call_count, calls)

    def test_concurrent_process_does_not_query(self) -> None:
        self.invoke("init")
        self.now.return_value += 604800

        def during_query() -> str:
            completed = subprocess.run(
                [sys.executable, str(SOURCE), "check"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            self.assertEqual(json.loads(completed.stdout)["status"], "busy")
            return "v99.0.0"

        self.fetch.side_effect = during_query
        self.assertEqual(self.invoke("check")["status"], "update-available")
        self.fetch.assert_called_once()

    def test_numeric_comparison_no_downgrade(self) -> None:
        self.assertGreater(UPDATES.version("v0.10.0"), UPDATES.version("0.9.9"))
        current = (SOURCE.parents[1] / "VERSION").read_text().strip()
        self.invoke("init")
        for tag in ("v0.0.1", current):
            self.now.return_value += 604800
            self.fetch.return_value = tag
            self.assertEqual(self.invoke("check")["status"], "up-to-date")
        for bad in ("1.2", "01.2.3", "v1.2.3-rc1", "$(touch x)"):
            with self.assertRaises(ValueError):
                UPDATES.version(bad)

    def test_corrupt_settings_preserved(self) -> None:
        self.invoke("init")
        path = UPDATES.state_path()
        for raw in (
            "{",
            "[]",
            '{"enabled": false}',
            json.dumps(
                {
                    "enabled": False,
                    "interval_seconds": 3600,
                    "created_at": -1,
                }
            ),
        ):
            path.write_text(raw)
            self.assertEqual(self.invoke("check")["status"], "unavailable")
            self.assertEqual(path.read_text(), raw)
        self.fetch.assert_not_called()

    def test_clock_rollback_does_not_request(self) -> None:
        self.invoke("init")
        self.now.return_value -= 100
        self.assertEqual(self.invoke("check")["status"], "not-due")
        self.fetch.assert_not_called()

    def test_unwritable_state_does_not_request(self) -> None:
        with mock.patch.object(
            UPDATES.Path, "mkdir", side_effect=PermissionError("denied")
        ):
            self.assertEqual(self.invoke("check")["status"], "unavailable")
        self.fetch.assert_not_called()

    def test_invalid_interval_rejected_without_changes(self) -> None:
        for interval in ("0d", "-1h", "1.5d", "weekly", "1s"):
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                UPDATES.main(["configure", "--interval", interval])
        self.assertFalse(UPDATES.state_path().exists())

    def test_http_release_validation_and_timeout(self) -> None:
        self.network.stop()
        for payload, valid in (
            ({"tag_name": "v1.2.3", "draft": False, "prerelease": False}, True),
            ({"tag_name": "v1.2.3", "draft": True}, False),
            ({"tag_name": "v1.2.3", "prerelease": True}, False),
            ({"tag_name": "v1.2.3-rc1"}, False),
            ({}, False),
            ([], False),
        ):
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(
                payload
            ).encode()
            with mock.patch.object(
                UPDATES.urllib.request, "urlopen", return_value=response
            ) as call:
                if valid:
                    self.assertEqual(UPDATES.fetch_release(), "v1.2.3")
                else:
                    with self.assertRaises(ValueError):
                        UPDATES.fetch_release()
                self.assertEqual(call.call_args.kwargs["timeout"], 3)
                self.assertEqual(call.call_args.args[0].full_url, UPDATES.API_URL)
