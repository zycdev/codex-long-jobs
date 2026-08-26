#!/usr/bin/env python3
"""Install codex-long-jobs from a fixed local Git commit.

The installer is offline and conservative.  It keeps a detached release
worktree under the user's state directory and points Codex at that worktree.
Running without ``--yes`` only prints the proposed change.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "release-install/v1"
SKILL_NAME = "codex-long-jobs"
REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_RELATIVE = Path(".")
DEFAULT_REF = "main"
STATE_ENV = "CODEX_LONG_JOBS_INSTALL_STATE_DIR"
TARGET_NAMES = ("codex",)


class InstallError(RuntimeError):
    """A precondition failed, so installation must stop."""


def _home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home())))


def state_dir() -> Path:
    override = os.environ.get(STATE_ENV)
    if override:
        return Path(override)
    base = Path(os.environ.get("XDG_STATE_HOME", _home() / ".local" / "state"))
    return base / SKILL_NAME


def release_worktree() -> Path:
    return state_dir() / "release"


def release_skill() -> Path:
    return release_worktree() / SKILL_RELATIVE


def receipt_path() -> Path:
    return state_dir() / "install-receipt.json"


def known_targets() -> dict[str, Path]:
    codex_home = Path(os.environ.get("CODEX_HOME", _home() / ".codex"))
    return {"codex": codex_home / "skills" / SKILL_NAME}


def resolve_targets(names: Sequence[str] | None) -> dict[str, Path]:
    catalogue = known_targets()
    selected = tuple(names or TARGET_NAMES)
    unknown = [name for name in selected if name not in catalogue]
    if unknown:
        raise InstallError(f"unknown target(s): {', '.join(unknown)}")
    return {name: catalogue[name] for name in selected}


def git(*arguments: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd or REPO_ROOT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise InstallError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout.strip()


def resolve_ref(ref: str) -> str:
    return git("rev-parse", "--verify", f"{ref}^{{commit}}")


def git_common_dir(worktree: Path) -> Path:
    value = Path(git("rev-parse", "--git-common-dir", cwd=worktree))
    if not value.is_absolute():
        value = worktree / value
    return value.resolve()


def worktree_head() -> str | None:
    worktree = release_worktree()
    if not (worktree / ".git").is_file():
        return None
    try:
        return git("rev-parse", "--verify", "HEAD^{commit}", cwd=worktree)
    except InstallError:
        return None


def ensure_state_dir() -> None:
    root = state_dir()
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise InstallError(f"state path is not a real directory: {root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)


def validate_skill(path: Path) -> None:
    skill_file = path / "SKILL.md"
    if not skill_file.is_file():
        raise InstallError(f"release has no SKILL.md: {skill_file}")
    text = skill_file.read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)
    if len(frontmatter) < 3 or f"name: {SKILL_NAME}" not in frontmatter[1]:
        raise InstallError(f"SKILL.md does not declare {SKILL_NAME}")


def ensure_release(ref: str) -> tuple[Path, str]:
    commit = resolve_ref(ref)
    ensure_state_dir()
    worktree = release_worktree()
    git_file = worktree / ".git"
    if git_file.is_file() and not git_file.is_symlink():
        if git_common_dir(worktree) != git_common_dir(REPO_ROOT):
            raise InstallError(
                f"release worktree belongs to another repository: {worktree}"
            )
        if git("--no-optional-locks", "status", "--porcelain", cwd=worktree):
            raise InstallError(f"release worktree has local changes: {worktree}")
        git("checkout", "--detach", commit, cwd=worktree)
    else:
        if os.path.lexists(worktree):
            raise InstallError(f"release path is not a managed worktree: {worktree}")
        git("worktree", "add", "--detach", str(worktree), commit)
    source = release_skill()
    validate_skill(source)
    return source, commit


def allowed_link_targets() -> set[Path]:
    return {
        (REPO_ROOT / SKILL_RELATIVE).resolve(),
        release_skill().resolve(),
    }


def inspect_destination(destination: Path) -> dict[str, str]:
    if destination.is_symlink():
        return {
            "kind": "symlink",
            "target": os.readlink(destination),
            "resolved": str(destination.resolve()),
        }
    if os.path.lexists(destination):
        return {"kind": "other", "target": "", "resolved": str(destination)}
    return {"kind": "absent", "target": "", "resolved": ""}


def require_replaceable(destination: Path) -> None:
    state = inspect_destination(destination)
    if state["kind"] == "absent":
        return
    if state["kind"] != "symlink":
        raise InstallError(
            f"refusing to replace non-symlink destination: {destination}"
        )
    if Path(state["resolved"]) not in allowed_link_targets():
        raise InstallError(
            f"refusing to replace unmanaged symlink {destination} -> {state['target']}"
        )


def install_link(destination: Path, source: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() and destination.resolve() == source.resolve():
        return
    temporary = destination.with_name(f".{destination.name}.install-{os.getpid()}")
    if os.path.lexists(temporary):
        raise InstallError(f"temporary install path already exists: {temporary}")
    os.symlink(str(source), temporary)
    try:
        os.replace(temporary, destination)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def save_receipt(ref: str, commit: str, targets: dict[str, Path]) -> None:
    ensure_state_dir()
    payload = {
        "schema": SCHEMA,
        "skill": SKILL_NAME,
        "mode": "link-release",
        "ref": ref,
        "commit": commit,
        "release": str(release_worktree()),
        "installed_at": now(),
        "targets": {name: str(path) for name, path in targets.items()},
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".install-receipt-", suffix=".json", dir=state_dir()
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, receipt_path())
    finally:
        if temporary.exists():
            temporary.unlink()


def load_receipt() -> dict[str, object] | None:
    path = receipt_path()
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if (
        value.get("schema") != SCHEMA
        or value.get("skill") != SKILL_NAME
        or value.get("mode") != "link-release"
    ):
        return None
    return value


def status(names: Sequence[str] | None) -> dict[str, object]:
    targets = resolve_targets(names)
    return {
        "schema": SCHEMA,
        "skill": SKILL_NAME,
        "release": str(release_worktree()),
        "release_head": worktree_head(),
        "receipt": load_receipt(),
        "targets": {
            name: {"path": str(path), **inspect_destination(path)}
            for name, path in targets.items()
        },
    }


def install(ref: str, names: Sequence[str] | None, apply: bool) -> dict[str, object]:
    targets = resolve_targets(names)
    commit = resolve_ref(ref)
    for destination in targets.values():
        require_replaceable(destination)
    result: dict[str, object] = {
        "schema": SCHEMA,
        "skill": SKILL_NAME,
        "mode": "link-release",
        "ref": ref,
        "commit": commit,
        "source": str(release_skill()),
        "targets": {name: str(path) for name, path in targets.items()},
        "applied": False,
    }
    if not apply:
        return result
    source, commit = ensure_release(ref)
    for destination in targets.values():
        install_link(destination, source)
    save_receipt(ref, commit, targets)
    result["commit"] = commit
    result["applied"] = True
    return result


def verify(names: Sequence[str] | None) -> dict[str, object]:
    problems: list[str] = []
    receipt = load_receipt()
    if receipt is None:
        problems.append("missing or invalid install receipt")
    expected = receipt.get("commit") if receipt else None
    actual = worktree_head()
    if not isinstance(expected, str) or actual != expected:
        problems.append(f"release HEAD {actual!r} differs from receipt {expected!r}")
    worktree = release_worktree()
    if actual is not None and git(
        "--no-optional-locks", "status", "--porcelain", cwd=worktree
    ):
        problems.append("release worktree has local changes")
    try:
        validate_skill(release_skill())
    except InstallError as error:
        problems.append(str(error))
    for name, destination in resolve_targets(names).items():
        if not destination.is_symlink():
            problems.append(f"{name} destination is not a symlink: {destination}")
        elif destination.resolve() != release_skill().resolve():
            problems.append(f"{name} destination does not point at the release")
    return {
        "schema": SCHEMA,
        "skill": SKILL_NAME,
        "ok": not problems,
        "problems": problems,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    for name in ("status", "verify"):
        command = subparsers.add_parser(name)
        command.add_argument("--target", action="append", choices=TARGET_NAMES)
    command = subparsers.add_parser("install")
    command.add_argument("--ref", default=DEFAULT_REF)
    command.add_argument("--target", action="append", choices=TARGET_NAMES)
    command.add_argument("--yes", action="store_true", help="apply the reported plan")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "status":
            result = status(arguments.target)
        elif arguments.command == "install":
            result = install(arguments.ref, arguments.target, arguments.yes)
        else:
            result = verify(arguments.target)
    except InstallError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if arguments.command != "verify" or result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
