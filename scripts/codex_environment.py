"""Conservative executable discovery and local Codex capability diagnostics."""

from __future__ import annotations

import grp
import json
import os
import platform
import pwd
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any


def codex_home() -> Path:
    return (
        Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        .expanduser()
        .resolve()
    )


def trusted_executable(path: Path) -> bool:
    """Reject executable discovery through replaceable files or directories."""
    if not path.is_absolute():
        return False
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return False
        owners = {0, os.getuid()}
        user = pwd.getpwuid(os.getuid())
        group = grp.getgrgid(user.pw_gid)
        private_group = (
            group.gr_name == user.pw_name
            and set(group.gr_mem) <= {user.pw_name}
            and all(
                p.pw_uid == user.pw_uid
                for p in pwd.getpwall()
                if p.pw_gid == group.gr_gid
            )
        )
        for candidate in {path, resolved}:
            for entry in (candidate, *candidate.parents):
                info = entry.stat()
                if info.st_uid not in owners:
                    return False
                writable = info.st_mode & 0o002 or (
                    info.st_mode & 0o020
                    and not (private_group and info.st_gid == group.gr_gid)
                )
                # A root-owned sticky ancestor cannot replace a private subtree.
                if writable and not (
                    entry not in {candidate, candidate.parent}
                    and info.st_uid == 0
                    and info.st_mode & stat.S_ISVTX
                ):
                    return False
        return True
    except OSError:
        return False


def extension_candidates() -> list[Path]:
    arch = {"x86_64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(
        platform.machine()
    )
    system = {"Linux": "linux", "Darwin": "macos"}.get(platform.system())
    if not arch or not system:
        return []
    roots = [
        Path.home() / directory / "extensions"
        for directory in (
            ".vscode-server",
            ".vscode-server-insiders",
            ".vscode",
            ".vscode-insiders",
        )
    ]
    if os.environ.get("VSCODE_AGENT_FOLDER"):
        roots.insert(
            0, Path(os.environ["VSCODE_AGENT_FOLDER"]).expanduser() / "extensions"
        )
    candidates: list[tuple[tuple[int, ...], Path]] = []
    for root in roots:
        # Metadata supplies the installed version; no release directory is pinned.
        for package in root.glob("openai.chatgpt-*/package.json"):
            try:
                metadata = json.loads(package.read_text(encoding="utf-8"))
                if not isinstance(metadata, dict):
                    continue
                if (
                    metadata.get("publisher") != "openai"
                    or metadata.get("name") != "chatgpt"
                ):
                    continue
                version = tuple(int(v) for v in re.findall(r"\d+", metadata["version"]))
                binary = package.parent / "bin" / f"{system}-{arch}" / "codex"
                if trusted_executable(binary):
                    candidates.append((version, binary))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return [
        path
        for _, path in sorted(
            candidates, key=lambda item: (item[0], str(item[1])), reverse=True
        )
    ]


def discover_codex() -> tuple[str, str]:
    override = os.environ.get("CODEX_LONG_JOBS_CODEX_BIN")
    if override is not None:
        # An invalid explicit override must fail rather than silently select another CLI.
        return str(Path(override).expanduser()) if override else "", "explicit"
    candidate = shutil.which("codex")
    if candidate and trusted_executable(Path(candidate)):
        return candidate, "path"
    for binary in extension_candidates():
        return str(binary), "vscode-extension"
    return "", "unavailable"


def queue_capabilities(binary: str, timeout: float = 5) -> dict[str, Any]:
    result: dict[str, Any] = {"queue": False, "resume": False}
    try:
        probe = subprocess.run(
            [binary, "queue", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return {**result, "reason": "codex-unavailable"}
    except subprocess.TimeoutExpired:
        return {**result, "reason": "queue-help-timeout"}
    except OSError as exc:
        return {**result, "reason": f"queue-help-oserror:{exc.errno}"}
    if probe.returncode:
        return {**result, "reason": f"queue-help-exit-{probe.returncode}"}
    options = set(
        re.findall(r"(?<!\S)--[a-z][a-z-]*\b", probe.stdout + "\n" + probe.stderr)
    )
    if not {"--thread", "--message"} <= options:
        return {**result, "reason": "queue-options-unavailable"}
    return {"queue": True, "resume": "--resume" in options, "reason": "supported"}


def codex_version(binary: str) -> str:
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        match = re.search(r"\bcodex-cli\s+(\d+\.\d+\.\d+[^\s]*)", result.stdout)
        if not result.returncode and match:
            return match[1]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def upgrade_advice(
    source: str, version: str, capabilities: dict[str, Any]
) -> str | None:
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if (
        source in {"path", "explicit"}
        and capabilities["queue"]
        and not capabilities["resume"]
        and match
        and tuple(map(int, match.groups())) < (0, 166, 0)
    ):
        return (
            "Consider Codex CLI 0.166.0 or newer for native queue --resume. "
            "The RPC compatibility path remains available; no upgrade is required."
        )
    return None
