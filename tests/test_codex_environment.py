from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import codex_environment as env
import codex_long_jobs as jobs


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = mock.patch.dict(os.environ, {"PATH": ""}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        home = mock.patch.object(env.Path, "home", return_value=self.root)
        home.start()
        self.addCleanup(home.stop)

    def binary(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o700)
        return path

    def extension(self, version="1.2.3"):
        package = self.root / ".vscode-server/extensions" / f"openai.chatgpt-{version}"
        binary = self.binary(package / "bin/linux-x86_64/codex")
        (package / "package.json").write_text(
            json.dumps(
                {
                    "publisher": "openai",
                    "name": "chatgpt",
                    "version": version,
                }
            )
        )
        return binary

    def test_default_codex_home_does_not_require_environment_variable(self):
        self.assertEqual(env.codex_home(), self.root / ".codex")
        with mock.patch.dict(os.environ, {"CODEX_HOME": ""}):
            self.assertEqual(jobs.state_root(), self.root / ".codex/long-jobs")
        with mock.patch.dict(os.environ, {"CODEX_HOME": "~/isolated"}):
            # expanduser follows the operating-system home, not Path.home().
            self.assertTrue(str(env.codex_home()).endswith("/isolated"))

    def test_explicit_override_has_priority_even_if_unavailable(self):
        self.extension()
        with mock.patch.dict(
            os.environ, {"CODEX_LONG_JOBS_CODEX_BIN": "/missing/codex"}
        ):
            self.assertEqual(env.discover_codex(), ("/missing/codex", "explicit"))
            self.assertEqual(
                env.queue_capabilities("/missing/codex")["reason"], "codex-unavailable"
            )

    def test_path_precedes_extension_and_extension_works_without_cli(self):
        extension = self.extension()
        binary = self.binary(self.root / "bin/codex")
        with mock.patch.dict(os.environ, {"PATH": str(binary.parent)}):
            self.assertEqual(env.discover_codex(), (str(binary), "path"))
        with (
            mock.patch.object(env.platform, "system", return_value="Linux"),
            mock.patch.object(env.platform, "machine", return_value="x86_64"),
        ):
            self.assertEqual(env.discover_codex(), (str(extension), "vscode-extension"))

    def test_discovery_rejects_replaceable_and_relative_path_candidates(self):
        binary = self.binary(self.root / "unsafe/codex")
        binary.parent.chmod(0o777)
        with mock.patch.dict(os.environ, {"PATH": str(binary.parent)}):
            self.assertEqual(env.discover_codex(), ("", "unavailable"))
        binary.parent.chmod(0o700)
        binary.chmod(0o777)
        self.assertFalse(env.trusted_executable(binary))
        self.assertFalse(env.trusted_executable(Path("codex")))
        binary.chmod(0o600)
        self.assertFalse(env.trusted_executable(binary))

    def test_extension_metadata_versions_and_invalid_packages(self):
        self.extension("1.9.0")
        latest = self.extension("1.10.0")
        invalid = self.extension("9.0.0")
        (invalid.parents[2] / "package.json").write_text('{"publisher":"other"}')
        with (
            mock.patch.object(env.platform, "system", return_value="Linux"),
            mock.patch.object(env.platform, "machine", return_value="x86_64"),
        ):
            self.assertEqual(env.discover_codex(), (str(latest), "vscode-extension"))
        with mock.patch.object(env.platform, "machine", return_value="unsupported"):
            self.assertEqual(env.extension_candidates(), [])

    def test_capabilities_use_flags_not_version_strings(self):
        for help_text, expected in [
            ("--thread X --message X --resume", True),
            ("--thread X --message X --resume-other", False),
        ]:
            result = subprocess.CompletedProcess([], 0, help_text, "")
            with mock.patch.object(env.subprocess, "run", return_value=result):
                self.assertEqual(env.queue_capabilities("chosen")["resume"], expected)
        for error, reason in [
            (subprocess.TimeoutExpired([], 1), "queue-help-timeout"),
            (PermissionError(13, "denied"), "queue-help-oserror:13"),
        ]:
            with mock.patch.object(env.subprocess, "run", side_effect=error):
                self.assertEqual(env.queue_capabilities("chosen")["reason"], reason)

    def test_version_diagnostics_and_nonmandatory_advice(self):
        old = {"queue": True, "resume": False}
        self.assertIn(
            "no upgrade is required", env.upgrade_advice("path", "0.164.0", old)
        )
        self.assertIsNone(
            env.upgrade_advice("vscode-extension", "0.164.0-alpha.1", old)
        )
        self.assertIsNone(env.upgrade_advice("path", "0.166.0", old))
        self.assertIsNone(env.upgrade_advice("path", "unknown", old))
        self.assertIsNone(
            env.upgrade_advice("path", "0.164.0", {"queue": True, "resume": True})
        )
        with mock.patch.object(
            env.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                [], 0, "codex-cli 0.164.0-alpha.1\n", ""
            ),
        ):
            self.assertEqual(env.codex_version("chosen"), "0.164.0-alpha.1")
        with mock.patch.object(env.subprocess, "run", side_effect=OSError):
            self.assertEqual(env.codex_version("chosen"), "unknown")


if __name__ == "__main__":
    unittest.main()
