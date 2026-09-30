#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "install_skill.py"
SPEC = importlib.util.spec_from_file_location("clj_install_skill", SOURCE)
assert SPEC and SPEC.loader
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class InstallSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="clj-install-test-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "SKILL.md").write_text(
            "---\nname: codex-long-jobs\ndescription: test\n---\n\n# Test\n",
            encoding="utf-8",
        )
        (self.repo / "scripts").mkdir()
        shutil.copy2(SOURCE.parent / "check_updates.py", self.repo / "scripts")
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", "SKILL.md", "scripts")
        self.git("commit", "-m", "initial")
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.root / "home"),
                "CODEX_HOME": str(self.root / "home" / ".codex"),
                "CODEX_LONG_JOBS_INSTALL_STATE_DIR": str(self.root / "state"),
            },
            clear=False,
        )
        self.environment.start()
        self.repo_patch = mock.patch.object(INSTALLER, "REPO_ROOT", self.repo)
        self.repo_patch.start()

    def tearDown(self) -> None:
        self.repo_patch.stop()
        self.environment.stop()
        self.temp.cleanup()

    def git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    @property
    def destination(self) -> Path:
        return self.root / "home" / ".codex" / "skills" / "codex-long-jobs"

    def test_dry_run_writes_nothing(self) -> None:
        result = INSTALLER.install("main", None, False)
        self.assertFalse(result["applied"])
        self.assertFalse((self.root / "state").exists())
        self.assertFalse(os.path.lexists(self.destination))

    def test_install_creates_fixed_release_link_and_private_receipt(self) -> None:
        result = INSTALLER.install("main", None, True)
        commit = self.git("rev-parse", "main")
        self.assertTrue(result["applied"])
        self.assertEqual(result["commit"], commit)
        self.assertTrue(self.destination.is_symlink())
        self.assertEqual(
            self.destination.resolve(), INSTALLER.release_skill().resolve()
        )
        self.assertEqual(INSTALLER.worktree_head(), commit)
        self.assertEqual(INSTALLER.receipt_path().stat().st_mode & 0o777, 0o600)
        receipt = json.loads(INSTALLER.receipt_path().read_text(encoding="utf-8"))
        self.assertEqual(receipt["commit"], commit)
        self.assertTrue(INSTALLER.verify(None)["ok"])

    def test_install_initializes_checks_and_preserves_disabled_setting(self) -> None:
        result = INSTALLER.install("main", None, True)
        config = json.loads(result["update_checks"])
        self.assertTrue(config["enabled"])
        self.assertEqual(config["interval_seconds"], 604800)
        path = Path(config["config"])
        saved = json.loads(path.read_text())
        saved["enabled"] = False
        saved["interval_seconds"] = 3600
        path.write_text(json.dumps(saved))
        result = INSTALLER.install("main", None, True)
        self.assertFalse(json.loads(result["update_checks"])["enabled"])
        self.assertEqual(json.loads(path.read_text()), saved)

    def test_reinstall_moves_release_to_new_commit(self) -> None:
        INSTALLER.install("main", None, True)
        skill = self.repo / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8") + "updated\n", encoding="utf-8"
        )
        self.git("add", "SKILL.md", "scripts")
        self.git("commit", "-m", "update")
        updated = self.git("rev-parse", "main")
        INSTALLER.install("main", None, True)
        self.assertEqual(INSTALLER.worktree_head(), updated)
        self.assertIn(
            "updated", (self.destination / "SKILL.md").read_text(encoding="utf-8")
        )

    def test_foreign_destination_is_refused_before_state_is_created(self) -> None:
        self.destination.mkdir(parents=True)
        with self.assertRaisesRegex(INSTALLER.InstallError, "non-symlink"):
            INSTALLER.install("main", None, True)
        self.assertFalse((self.root / "state").exists())

    def test_verify_detects_release_worktree_changes(self) -> None:
        INSTALLER.install("main", None, True)
        skill = INSTALLER.release_skill() / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8") + "dirty\n", encoding="utf-8"
        )
        result = INSTALLER.verify(None)
        self.assertFalse(result["ok"])
        self.assertIn("release worktree has local changes", result["problems"])


if __name__ == "__main__":
    unittest.main()
