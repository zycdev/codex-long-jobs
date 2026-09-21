#!/usr/bin/env python3
"""Durable, event-driven background jobs for Codex CLI.

The worker and the user command run outside tmux.  tmux is used only as an
optional, disposable log viewer and as a conservative transport to an owning
Codex TUI.
"""

from __future__ import annotations

import argparse
import codecs
import contextlib
import datetime as dt
import errno
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

VERSION = "0.3.1"
SCHEMA_VERSION = 1
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
THREAD_RE = re.compile(r"^[A-Za-z0-9_-]{8,160}$")
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
DELIVERY_DONE = {"delivered", "disabled", "headless-dispatched"}
BUSY_MARKERS = ("esc to interrupt", "ctrl+c to interrupt")
MAX_MARKER_BUFFER_BYTES = 128 * 1024
CANCEL_CHECK_INTERVAL_SECONDS = 0.5
CANCEL_FINALIZE_WAIT_SECONDS = 5.0
CANCEL_SUPPORTED_SINCE = (0, 3, 0)


class JobError(RuntimeError):
    pass


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def state_root() -> Path:
    override = os.environ.get("CODEX_LONG_JOBS_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return codex_home.expanduser().resolve() / "long-jobs"


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise JobError(f"state path is not a real directory: {path}")
    if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
        raise JobError(f"state directory is not owned by the current user: {path}")
    path.chmod(0o700)


def ensure_state_root() -> Path:
    root = state_root()
    ensure_private_dir(root)
    ensure_private_dir(root / "jobs")
    ensure_private_dir(root / "logs")
    return root


def validate_name(name: str) -> str:
    if not NAME_RE.fullmatch(name):
        raise JobError("job name must match [A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
    return name


def job_dir(name: str) -> Path:
    return state_root() / "jobs" / validate_name(name)


def state_file(name: str) -> Path:
    return job_dir(name) / "state.json"


def lock_file(name: str) -> Path:
    return job_dir(name) / "state.lock"


def reserve_file(name: str) -> Path:
    return job_dir(name) / ".final-state-reserve"


def prompt_file(name: str) -> Path:
    return job_dir(name) / "completion.prompt"


def delivery_lock_file(name: str) -> Path:
    return job_dir(name) / "delivery.lock"


def tui_delivery_lock_file() -> Path:
    return state_root() / "tui-delivery.lock"


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    body = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


@contextlib.contextmanager
def job_lock(name: str, *, nonblocking: bool = False) -> Iterator[None]:
    directory = job_dir(name)
    ensure_private_dir(directory)
    fd = os.open(lock_file(name), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        fcntl.flock(fd, flags)
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def tui_delivery_lock() -> Iterator[bool]:
    """Serialize delivery across jobs so two prompts cannot share a composer."""
    ensure_private_dir(state_root())
    fd = os.open(tui_delivery_lock_file(), os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_job(name: str) -> dict[str, Any]:
    path = state_file(name)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise JobError(f"unknown job: {name}") from exc
    if len(raw.encode("utf-8")) > 256 * 1024:
        raise JobError(f"job state is unexpectedly large: {path}")
    record = json.loads(raw)
    if record.get("schema_version") != SCHEMA_VERSION or record.get("name") != name:
        raise JobError(f"invalid job state: {path}")
    return record


def update_job(
    name: str, change: Callable[[dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    with job_lock(name):
        current = read_job(name)
        updated = change(current)
        updated["updated_at"] = now_iso()
        atomic_write_json(state_file(name), updated)
        return updated


def create_reserve(name: str, size: int = 64 * 1024) -> None:
    path = reserve_file(name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        block = b"\0" * 4096
        remaining = size
        while remaining:
            written = os.write(fd, block[: min(len(block), remaining)])
            remaining -= written
        os.fsync(fd)
    finally:
        os.close(fd)


def release_reserve(name: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        reserve_file(name).unlink()


def compile_success_pattern(pattern: str | None) -> re.Pattern[str] | None:
    if pattern is None:
        return None
    try:
        # Treat markers as log-line patterns. This keeps the documented
        # ``^MARKER$`` form valid even when a command emitted earlier output.
        return re.compile(pattern, re.MULTILINE)
    except re.error as exc:
        raise JobError(f"invalid success pattern: {exc}") from exc


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be a finite number zero or greater")
    return parsed


def version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def supports_cancellation(record: dict[str, Any]) -> bool:
    parsed = version_tuple(str(record.get("runtime_version", "")))
    return parsed is not None and parsed >= CANCEL_SUPPORTED_SINCE


def return_code_signal(return_code: int | None) -> str | None:
    if return_code is None or return_code >= 0:
        return None
    signum = -return_code
    try:
        return signal.Signals(signum).name
    except ValueError:
        return f"SIG{signum}"


def write_log_chunk(handle: Any, chunk: bytes, written_total: int) -> int:
    """Write a complete output chunk and surface partial-write failures."""
    fail_after = int(os.environ.get("CODEX_LONG_JOBS_TEST_LOG_FAIL_AFTER_BYTES", "-1"))
    max_write = int(os.environ.get("CODEX_LONG_JOBS_TEST_LOG_MAX_WRITE", "-1"))
    view = memoryview(chunk)
    offset = 0
    while offset < len(view):
        if fail_after >= 0 and written_total + offset >= fail_after:
            raise OSError(errno.ENOSPC, "simulated log filesystem full")
        limit = len(view) - offset
        if fail_after >= 0:
            limit = min(limit, fail_after - written_total - offset)
        if max_write > 0:
            limit = min(limit, max_write)
        if limit <= 0:
            raise OSError(errno.ENOSPC, "simulated log filesystem full")
        written = handle.write(view[offset : offset + limit])
        if written is None or written <= 0:
            raise OSError(errno.EIO, "log write made no progress")
        offset += written
    return written_total + offset


def open_log_for_append(path: Path, *, create: bool = False) -> Any:
    """Open a private regular log without following a replaced symlink."""
    flags = os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC
    if create:
        flags |= os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise JobError(f"log path is not a regular file: {path}")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise JobError(f"log file is not owned by the current user: {path}")
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "ab", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def linux_proc_stat_fields(pid: int) -> list[str]:
    stat_line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    close = stat_line.rfind(")")
    if close < 0:
        raise JobError(f"invalid /proc stat for pid {pid}")
    fields = stat_line[close + 2 :].split()
    if len(fields) <= 19:
        raise JobError(f"incomplete /proc stat for pid {pid}")
    return fields


def linux_proc_info(pid: int) -> dict[str, Any]:
    fields = linux_proc_stat_fields(pid)
    try:
        cmdline = (
            Path(f"/proc/{pid}/cmdline")
            .read_bytes()
            .replace(b"\0", b" ")
            .decode("utf-8", "replace")
        )
    except OSError:
        cmdline = ""
    return {
        "pid": pid,
        "state": fields[0],
        "ppid": int(fields[1]),
        "pgrp": int(fields[2]),
        "session": int(fields[3]),
        "start": fields[19],
        "exe": os.path.realpath(f"/proc/{pid}/exe"),
        "cmdline": cmdline,
    }


def portable_proc_info(pid: int) -> dict[str, Any]:
    result = subprocess.run(
        ["ps", "-o", "ppid=", "-o", "lstart=", "-o", "comm=", "-p", str(pid)],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    line = result.stdout.strip()
    match = re.match(r"^(\d+)\s+(.{24})\s+(.+)$", line)
    if not match:
        raise JobError(f"unable to parse process identity for pid {pid}")
    return {
        "pid": pid,
        "ppid": int(match.group(1)),
        "start": match.group(2),
        "exe": match.group(3),
    }


def process_info(pid: int) -> dict[str, Any]:
    if sys.platform.startswith("linux"):
        return linux_proc_info(pid)
    return portable_proc_info(pid)


def process_identity(pid: int) -> dict[str, Any]:
    info = process_info(pid)
    return {"pid": pid, "start": info["start"], "exe": info["exe"]}


def identity_alive(identity: dict[str, Any] | None) -> bool:
    if not identity:
        return False
    try:
        actual = process_identity(int(identity["pid"]))
    except (OSError, ValueError, KeyError, JobError, subprocess.SubprocessError):
        return False
    return actual == identity


def process_group_identity(pid: int) -> dict[str, Any]:
    return {
        "pgid": os.getpgid(pid),
        "session": os.getsid(pid),
        "leader": process_identity(pid),
    }


def process_group_alive(group: dict[str, Any] | None) -> bool:
    if not group:
        return False
    try:
        pgid = int(group["pgid"])
        session_id = int(group["session"])
    except (KeyError, TypeError, ValueError):
        return False
    if sys.platform.startswith("linux"):
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = linux_proc_stat_fields(int(entry.name))
            except (OSError, JobError, ValueError):
                continue
            if (
                int(fields[2]) == pgid
                and int(fields[3]) == session_id
                and fields[0] != "Z"
            ):
                return True
        return False
    return identity_alive(group.get("leader"))


def signal_process_group(group: dict[str, Any] | None, signum: int) -> bool:
    if not process_group_alive(group):
        return False
    try:
        os.killpg(int(group["pgid"]), signum)
    except ProcessLookupError:
        return False
    return True


def find_codex_ancestor(start_pid: int) -> dict[str, Any] | None:
    override = os.environ.get("CODEX_LONG_JOBS_TEST_CODEX_PID")
    if override:
        return process_identity(int(override))
    current = start_pid
    visited: set[int] = set()
    while current >= 1 and current not in visited:
        visited.add(current)
        try:
            info = process_info(current)
        except (OSError, JobError, subprocess.SubprocessError):
            return None
        executable = Path(str(info["exe"])).name.removesuffix(" (deleted)")
        if executable == "codex":
            return {"pid": current, "start": info["start"], "exe": info["exe"]}
        current = int(info["ppid"])
        if current <= 0:
            break
    return None


def running_under_codex_sandbox(start_pid: int | None = None) -> bool:
    """Detect the local Codex process sandbox that reaps detached descendants."""
    if os.environ.get("CODEX_LONG_JOBS_ALLOW_SANDBOX") == "1":
        return False
    current = start_pid or os.getppid()
    visited: set[int] = set()
    while current >= 1 and current not in visited:
        visited.add(current)
        try:
            info = process_info(current)
        except (OSError, JobError, subprocess.SubprocessError):
            return False
        command_name = Path(str(info["exe"])).name
        cmdline = str(info.get("cmdline", ""))
        if command_name == "codex-linux-sandbox" or "--sandbox-policy-cwd" in cmdline:
            return True
        current = int(info["ppid"])
        if current <= 0:
            break
    return False


def is_descendant(child: int, ancestor: int) -> bool:
    current = child
    visited: set[int] = set()
    while current > 1 and current not in visited:
        if current == ancestor:
            return True
        visited.add(current)
        try:
            current = int(process_info(current)["ppid"])
        except (OSError, JobError, subprocess.SubprocessError):
            return False
    return False


def find_codex_in_pane(pane_pid: int, pane_tty: str) -> dict[str, Any] | None:
    """Find the unique Codex process attached to a tmux pane.

    Scoped host execution may no longer be a descendant of the TUI process, so
    ancestry from the launcher is insufficient.  TTY plus pane ancestry keeps
    this fallback narrow.
    """
    if not sys.platform.startswith("linux"):
        return None
    matches: list[dict[str, Any]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            identity = process_identity(pid)
            executable = Path(str(identity["exe"])).name.removesuffix(" (deleted)")
            if executable != "codex":
                continue
            if os.path.realpath(f"/proc/{pid}/fd/0") != pane_tty:
                continue
            if is_descendant(pid, pane_pid):
                matches.append(identity)
        except (OSError, JobError, subprocess.SubprocessError):
            continue
    return matches[0] if len(matches) == 1 else None


def tmux_binary() -> str:
    return os.environ.get("CODEX_LONG_JOBS_TMUX_BIN", "tmux")


def tmux_call(
    socket: str, arguments: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [tmux_binary(), "-S", socket, *arguments],
        check=check,
        capture_output=True,
        text=True,
        timeout=5,
    )


def capture_endpoint() -> tuple[dict[str, Any] | None, str]:
    thread_id = os.environ.get("CODEX_THREAD_ID", "")
    tmux_env = os.environ.get("TMUX", "")
    pane_id = os.environ.get("TMUX_PANE", "")
    if not thread_id or not THREAD_RE.fullmatch(thread_id):
        return None, "thread-id-unavailable"
    if not tmux_env or not pane_id:
        return None, "not-running-in-tmux"
    socket = tmux_env.split(",", 1)[0]
    try:
        server_pid = int(
            tmux_call(socket, ["display-message", "-p", "#{pid}"]).stdout.strip()
        )
        pane_meta = tmux_call(
            socket,
            [
                "display-message",
                "-p",
                "-t",
                pane_id,
                "#{pane_id}|#{pane_pid}|#{pane_tty}|#{pane_current_command}|#{pane_current_path}",
            ],
        ).stdout.rstrip("\n")
        checked_id, pane_pid_raw, pane_tty, pane_command, pane_cwd = pane_meta.split(
            "|", 4
        )
        if checked_id != pane_id:
            return None, "pane-id-mismatch"
        pane_pid = int(pane_pid_raw)
        pane_identity = process_identity(pane_pid)
        codex_identity = find_codex_ancestor(os.getppid()) or find_codex_in_pane(
            pane_pid, pane_tty
        )
        if not codex_identity:
            return None, "codex-ancestor-unavailable"
        if not is_descendant(int(codex_identity["pid"]), pane_pid):
            return None, "codex-pane-ancestry-mismatch"
    except (OSError, ValueError, JobError, subprocess.SubprocessError) as exc:
        return None, f"tmux-capture-failed:{type(exc).__name__}"
    return {
        "thread_id": thread_id,
        "tmux_socket": socket,
        "tmux_server_pid": server_pid,
        "pane_id": pane_id,
        "pane_pid": pane_pid,
        "pane_identity": pane_identity,
        "pane_tty": pane_tty,
        "pane_command": pane_command,
        "pane_cwd": pane_cwd,
        "codex_identity": codex_identity,
        "bound_at": now_iso(),
    }, "validated-origin-tui"


def validate_endpoint(endpoint: dict[str, Any], owner_thread: str) -> tuple[bool, str]:
    if endpoint.get("thread_id") != owner_thread:
        return False, "thread-id-mismatch"
    socket = str(endpoint.get("tmux_socket", ""))
    pane_id = str(endpoint.get("pane_id", ""))
    try:
        server_pid = int(
            tmux_call(socket, ["display-message", "-p", "#{pid}"]).stdout.strip()
        )
        if server_pid != int(endpoint["tmux_server_pid"]):
            return False, "tmux-server-restarted"
        meta = tmux_call(
            socket,
            [
                "display-message",
                "-p",
                "-t",
                pane_id,
                "#{pane_id}|#{pane_pid}|#{pane_tty}",
            ],
        ).stdout.strip()
        expected = f"{pane_id}|{endpoint['pane_pid']}|{endpoint['pane_tty']}"
        if meta != expected:
            return False, "pane-identity-mismatch"
        if not identity_alive(endpoint.get("pane_identity")):
            return False, "pane-process-changed"
        codex_identity = endpoint.get("codex_identity")
        if not identity_alive(codex_identity):
            return False, "codex-process-changed"
        if not is_descendant(int(codex_identity["pid"]), int(endpoint["pane_pid"])):
            return False, "codex-pane-ancestry-mismatch"
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False, "endpoint-unavailable"
    return True, "validated"


def tmux_capture(endpoint: dict[str, Any], *, escaped: bool) -> str:
    args = ["capture-pane", "-p", "-S", "-80"]
    if escaped:
        args.append("-e")
    args.extend(["-t", str(endpoint["pane_id"])])
    return tmux_call(str(endpoint["tmux_socket"]), args).stdout


def composer_is_empty(escaped_screen: str) -> bool:
    composer = ""
    for line in escaped_screen.splitlines():
        if "›" in line:
            composer = line
    return bool(composer and "\x1b[2m" in composer)


def screen_is_busy(plain_screen: str) -> bool:
    lowered = plain_screen.lower()
    return any(marker in lowered for marker in BUSY_MARKERS)


def delivery_token_is_visible(plain_screen: str, token: str) -> bool:
    if not token:
        return False
    if token in plain_screen:
        return True
    visual_line_break = r"(?:[ \t]*\r?\n[ \t]*)?"
    wrapped_token = visual_line_break.join(re.escape(character) for character in token)
    return re.search(wrapped_token, plain_screen) is not None


def set_delivery(name: str, status: str, reason: str, **extra: Any) -> dict[str, Any]:
    def change(record: dict[str, Any]) -> dict[str, Any]:
        delivery = dict(record.get("delivery", {}))
        delivery.update({"status": status, "reason": reason, **extra})
        record["delivery"] = delivery
        return record

    return update_job(name, change)


def completion_prompt(record: dict[str, Any]) -> str:
    name = record["name"]
    state = json.dumps(str(state_file(name)), ensure_ascii=True)
    log = json.dumps(str(record["log"]), ensure_ascii=True)
    token = record["delivery"]["token"]
    if record["status"] == "succeeded":
        lead = f"Background job '{name}' completed successfully."
    elif record["status"] == "cancelled":
        lead = f"Background job '{name}' was cancelled."
    else:
        lead = f"Background job '{name}' failed."
    return (
        f"{lead} Inspect state_path={state} and log_path={log}, verify the actual artifacts, then continue only work "
        f"already authorized by the conversation or report the concrete blocker. {token}"
    )


def write_private_text(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def try_tui_submission(
    name: str, record: dict[str, Any], endpoint: dict[str, Any]
) -> tuple[bool, str]:
    prompt = completion_prompt(record)
    token = record["delivery"]["token"]
    write_private_text(prompt_file(name), prompt)
    buffer_name = f"codex-long-jobs-{name}"
    socket = str(endpoint["tmux_socket"])
    pane = str(endpoint["pane_id"])
    try:
        tmux_call(socket, ["load-buffer", "-b", buffer_name, str(prompt_file(name))])
        tmux_call(socket, ["paste-buffer", "-p", "-d", "-b", buffer_name, "-t", pane])
    except subprocess.SubprocessError:
        return False, "paste-failed"

    visible = False
    for _ in range(20):
        try:
            if delivery_token_is_visible(tmux_capture(endpoint, escaped=False), token):
                visible = True
                break
        except subprocess.SubprocessError:
            return False, "pane-lost-after-paste"
        time.sleep(0.05)
    if not visible:
        return False, "prompt-not-visible-after-paste"

    # Retry Enter only while the freshly pasted token is still visible and the
    # TUI has not become busy. The token may span rendered composer lines.
    # This addresses a missed key event without creating a second prompt or
    # relying on Codex's queue shortcut.
    for attempt in range(1, 5):
        sent = tmux_call(socket, ["send-keys", "-t", pane, "Enter"], check=False)
        time.sleep(0.25 * attempt)
        try:
            plain = tmux_capture(endpoint, escaped=False)
            escaped = tmux_capture(endpoint, escaped=True)
        except subprocess.SubprocessError:
            return False, "pane-lost-after-submit"
        if composer_is_empty(escaped) or screen_is_busy(plain):
            return True, f"submitted-after-{attempt}-enter-attempts"
        if not delivery_token_is_visible(plain, token):
            return False, "submission-uncertain-token-disappeared"
        if sent.returncode != 0 and attempt == 4:
            return False, "enter-key-failed"
    return False, "composer-did-not-acknowledge-submit"


def deliver_tui(name: str) -> None:
    directory = job_dir(name)
    ensure_private_dir(directory)
    fd = os.open(delivery_lock_file(name), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        record = read_job(name)
        if record["status"] not in TERMINAL_STATUSES:
            return
        if record.get("delivery", {}).get("status") in DELIVERY_DONE:
            return
        wait_seconds = int(record.get("delivery", {}).get("wait_seconds", 86400))
        poll_seconds = float(
            os.environ.get("CODEX_LONG_JOBS_DELIVERY_POLL_SECONDS", "2")
        )
        deadline = time.monotonic() + max(1, wait_seconds)
        last_reason = ""
        while time.monotonic() < deadline:
            record = read_job(name)
            endpoint = record.get("endpoint")
            owner_thread = str(record.get("owner_thread_id", ""))
            if not endpoint:
                reason = "waiting-for-session-rebind"
            else:
                valid, reason = validate_endpoint(endpoint, owner_thread)
                if valid:
                    with tui_delivery_lock() as acquired:
                        if not acquired:
                            reason = "another-job-is-delivering"
                        else:
                            # Revalidate after obtaining the cross-job lock because
                            # another delivery may have changed the TUI meanwhile.
                            valid, reason = validate_endpoint(endpoint, owner_thread)
                            if valid:
                                try:
                                    plain = tmux_capture(endpoint, escaped=False)
                                    escaped = tmux_capture(endpoint, escaped=True)
                                except subprocess.SubprocessError:
                                    valid, reason = False, "pane-capture-failed"
                            if valid and screen_is_busy(plain):
                                reason = "owning-tui-busy"
                            elif valid and not composer_is_empty(escaped):
                                reason = "composer-not-empty"
                            elif valid:
                                set_delivery(name, "delivering", "safe-idle-boundary")
                                delivered, submit_reason = try_tui_submission(
                                    name, record, endpoint
                                )
                                if delivered:
                                    set_delivery(
                                        name,
                                        "delivered",
                                        submit_reason,
                                        delivered_at=now_iso(),
                                        pane_id=endpoint["pane_id"],
                                    )
                                    return
                                set_delivery(name, "pending", submit_reason)
                                # A prompt may now be present.  Do not paste a duplicate.
                                if submit_reason not in {
                                    "enter-key-failed",
                                    "composer-did-not-acknowledge-submit",
                                }:
                                    return
                                time.sleep(min(2.0, poll_seconds))
                                continue
            if reason != last_reason:
                set_delivery(name, "pending", reason)
                last_reason = reason
            time.sleep(max(0.1, poll_seconds))
        set_delivery(name, "pending", "delivery-wait-expired-rebind-or-retry-required")
    finally:
        os.close(fd)


def launch_desktop_notification(record: dict[str, Any]) -> None:
    if not record.get("notify_user", True):
        return
    message = f"{record['name']} finished {record['status']}; exit code {record.get('exit_code', 'unknown')}."
    if sys.platform == "darwin" and shutil.which("osascript"):
        command = [
            "osascript",
            "-e",
            "on run argv",
            "-e",
            'display notification (item 1 of argv) with title "Codex Long Jobs"',
            "-e",
            "end run",
            "--",
            message,
        ]
    elif sys.platform.startswith("linux") and shutil.which("notify-send"):
        command = [
            "notify-send",
            "--app-name",
            "Codex Long Jobs",
            "--",
            "Background job finished",
            message,
        ]
    else:
        return
    with contextlib.suppress(OSError):
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def launch_headless(record: dict[str, Any]) -> None:
    name = record["name"]
    thread = record.get("owner_thread_id")
    if not thread:
        set_delivery(name, "disabled", "headless-thread-id-unavailable")
        return
    codex_bin = os.environ.get("CODEX_LONG_JOBS_CODEX_BIN", "codex")
    output = job_dir(name) / "headless-continuation.log"
    try:
        with open_log_for_append(output, create=True) as handle:
            child = subprocess.Popen(
                [codex_bin, "exec", "resume", thread, completion_prompt(record)],
                cwd=record["cwd"],
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        set_delivery(name, "pending", f"headless-dispatch-failed:{exc.errno}")
        return
    set_delivery(
        name,
        "headless-dispatched",
        "explicit-headless-mode",
        continuation_pid=child.pid,
        continuation_log=str(output),
    )


def finalize_delivery(name: str) -> None:
    record = read_job(name)
    launch_desktop_notification(record)
    mode = record.get("delivery", {}).get("mode")
    if mode == "event-only":
        set_delivery(
            name, "disabled", record["delivery"].get("selection_reason", "event-only")
        )
    elif mode == "headless":
        launch_headless(record)
    else:
        deliver_tui(name)


def terminate_process_group(
    identity: dict[str, Any] | None,
    group: dict[str, Any] | None = None,
    *,
    grace_seconds: float = 5.0,
) -> str | None:
    """Best-effort cleanup for a command orphaned by worker failure."""
    if group is None and identity_alive(identity):
        pid = int(identity["pid"])
        group = {"pgid": pid, "session": pid, "leader": identity}
    if not process_group_alive(group):
        return None
    strongest_signal: str | None = None
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if signal_process_group(group, signal.SIGTERM):
            strongest_signal = "SIGTERM"
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and process_group_alive(group):
        time.sleep(0.05)
    if process_group_alive(group):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if signal_process_group(group, signal.SIGKILL):
                strongest_signal = "SIGKILL"
    return strongest_signal


def record_cancel_effective(name: str, signal_name: str | None) -> dict[str, Any]:
    def effective(record: dict[str, Any]) -> dict[str, Any]:
        if not record.get("cancel_requested_at"):
            return record
        record.setdefault("cancel_effective_at", now_iso())
        if signal_name is not None:
            record["cancel_signal"] = signal_name
        return record

    return update_job(name, effective)


def cancelled_terminal(
    record: dict[str, Any],
    *,
    exit_code: int | None,
    signal_name: str | None,
    marker_seen: bool,
    log_error: str | None,
    failure_reasons: list[str],
) -> dict[str, Any]:
    cancelled_at = now_iso()
    record.update(
        status="cancelled",
        completed_at=cancelled_at,
        cancelled_at=cancelled_at,
        exit_code=exit_code,
        signal=signal_name,
        marker_seen=marker_seen,
        log_error=log_error,
        failure_reasons=failure_reasons,
        child_pid=None,
        child_identity=None,
        child_process_group=None,
    )
    return record


def finalize_cancelled_before_launch(name: str) -> int:
    release_reserve(name)

    def terminal(record: dict[str, Any]) -> dict[str, Any]:
        if not record.get("cancel_requested_at"):
            raise JobError(f"job cancellation was not requested: {name}")
        record.setdefault("cancel_effective_at", now_iso())
        return cancelled_terminal(
            record,
            exit_code=None,
            signal_name=None,
            marker_seen=record.get("success_pattern") is None,
            log_error=None,
            failure_reasons=[],
        )

    update_job(name, terminal)
    finalize_delivery(name)
    return 0


def supervisor_main(name: str) -> int:
    """Watch the worker and finalize state if the worker exits unexpectedly."""
    name = validate_name(name)
    supervisor_id = process_identity(os.getpid())

    def supervising(record: dict[str, Any]) -> dict[str, Any]:
        if record["status"] != "queued":
            raise JobError(f"job is not queued: {name}")
        record["supervisor_identity"] = supervisor_id
        return record

    record = update_job(name, supervising)
    if record.get("cancel_requested_at"):
        return finalize_cancelled_before_launch(name)
    runtime_log = job_dir(name) / "worker-runtime.log"
    try:
        with open_log_for_append(runtime_log, create=True) as handle:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "_worker",
                    "--name",
                    name,
                ],
                cwd=read_job(name)["cwd"],
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        worker = None
        return_code: int | None = None
        launch_error = f"worker-launch-failed={exc.__class__.__name__}:{exc.errno}"
    else:

        def worker_launched(record: dict[str, Any]) -> dict[str, Any]:
            record["worker_spawn_pid"] = worker.pid
            return record

        update_job(name, worker_launched)
        return_code = worker.wait()
        launch_error = None

    record = read_job(name)
    if record["status"] in TERMINAL_STATUSES:
        if (
            return_code not in {0, 1}
            and record.get("delivery", {}).get("status") not in DELIVERY_DONE
        ):
            finalize_delivery(name)
        return return_code or 0

    cancel_requested = bool(record.get("cancel_requested_at"))
    cleanup_signal = terminate_process_group(
        record.get("child_identity"),
        record.get("child_process_group"),
        grace_seconds=float(record.get("cancel_grace_seconds", 5.0)),
    )
    if cancel_requested and cleanup_signal is not None:
        record_cancel_effective(name, cleanup_signal)
        record = read_job(name)
    release_reserve(name)
    worker_signal = None
    worker_exit_code = return_code
    worker_signal = return_code_signal(return_code)
    if worker_signal:
        worker_exit_code = None
    if launch_error:
        failure_reason = launch_error
    elif worker_signal:
        failure_reason = f"worker-signal={worker_signal}"
    else:
        failure_reason = f"worker-exit={return_code}-before-terminal-state"

    def worker_failed(current: dict[str, Any]) -> dict[str, Any]:
        reasons = list(current.get("failure_reasons", []))
        reasons.append(failure_reason)
        if current.get("cancel_effective_at"):
            current["worker_exit_code"] = worker_exit_code
            current["worker_signal"] = worker_signal
            return cancelled_terminal(
                current,
                exit_code=None,
                signal_name=None,
                marker_seen=False,
                log_error=None,
                failure_reasons=reasons,
            )
        current.update(
            status="failed",
            completed_at=now_iso(),
            exit_code=None,
            signal=None,
            worker_exit_code=worker_exit_code,
            worker_signal=worker_signal,
            failure_reasons=reasons,
            child_pid=None,
            child_identity=None,
            child_process_group=None,
        )
        return current

    update_job(name, worker_failed)
    finalize_delivery(name)
    return 1


def worker_main(name: str) -> int:
    name = validate_name(name)
    worker_id = process_identity(os.getpid())

    def starting(record: dict[str, Any]) -> dict[str, Any]:
        if record["status"] != "queued":
            raise JobError(f"job is not queued: {name}")
        record.update(started_at=now_iso(), worker_identity=worker_id)
        if not record.get("cancel_requested_at"):
            record["status"] = "starting"
        return record

    record = update_job(name, starting)
    if record.get("cancel_requested_at"):
        return finalize_cancelled_before_launch(name)
    command = list(record["command"])
    log_path = Path(record["log"])
    marker = compile_success_pattern(record.get("success_pattern"))
    marker_seen = marker is None
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    marker_buffer = ""
    log_error: str | None = None
    logged_bytes = 0
    child: subprocess.Popen[bytes] | None = None
    child_id: dict[str, Any] | None = None
    child_group: dict[str, Any] | None = None
    return_code: int | None = None
    cancel_effective = False
    cancel_signal: str | None = None
    cancel_deadline: float | None = None

    try:
        test_start_delay = float(
            os.environ.get("CODEX_LONG_JOBS_TEST_BEFORE_CHILD_START_SECONDS", "0")
        )
        if test_start_delay > 0:
            time.sleep(test_start_delay)
        if read_job(name).get("cancel_requested_at"):
            return finalize_cancelled_before_launch(name)
        child = subprocess.Popen(
            command,
            cwd=record["cwd"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        child_id = process_identity(child.pid)
        child_group = process_group_identity(child.pid)

        def running(current: dict[str, Any]) -> dict[str, Any]:
            current.update(
                status="running",
                child_identity=child_id,
                child_process_group=child_group,
                child_pid=child.pid,
            )
            return current

        update_job(name, running)
        assert child.stdout is not None
        read_chunk = getattr(child.stdout, "read1", child.stdout.read)
        next_cancel_check = time.monotonic()
        with (
            open_log_for_append(log_path) as log_handle,
            selectors.DefaultSelector() as selector,
        ):
            selector.register(child.stdout, selectors.EVENT_READ)
            eof = False
            while True:
                now = time.monotonic()
                timeout = max(0.0, next_cancel_check - now)
                for _, _ in selector.select(timeout):
                    chunk = read_chunk(64 * 1024)
                    if not chunk:
                        eof = True
                        selector.unregister(child.stdout)
                        child.poll()
                        break
                    text = decoder.decode(chunk)
                    if marker and not marker_seen:
                        marker_buffer = (marker_buffer + text)[
                            -MAX_MARKER_BUFFER_BYTES:
                        ]
                        marker_seen = marker.search(marker_buffer) is not None
                    if log_error is None:
                        try:
                            logged_bytes = write_log_chunk(
                                log_handle, chunk, logged_bytes
                            )
                        except OSError as exc:
                            log_error = f"{exc.__class__.__name__}: {exc}"

                now = time.monotonic()
                if now >= next_cancel_check:
                    current = read_job(name)
                    if (
                        current.get("cancel_requested_at")
                        and not cancel_effective
                        and signal_process_group(child_group, signal.SIGTERM)
                    ):
                        cancel_effective = True
                        cancel_signal = "SIGTERM"
                        cancel_deadline = now + float(
                            current.get("cancel_grace_seconds", 10.0)
                        )
                        record_cancel_effective(name, cancel_signal)
                    if (
                        cancel_effective
                        and cancel_signal == "SIGTERM"
                        and cancel_deadline is not None
                        and now >= cancel_deadline
                        and signal_process_group(child_group, signal.SIGKILL)
                    ):
                        cancel_signal = "SIGKILL"
                        record_cancel_effective(name, cancel_signal)
                    next_cancel_check = now + CANCEL_CHECK_INTERVAL_SECONDS
                if (
                    eof
                    and child.poll() is not None
                    and (not cancel_effective or not process_group_alive(child_group))
                ):
                    break
            tail = decoder.decode(b"", final=True)
            if marker and not marker_seen and tail:
                marker_seen = (
                    marker.search((marker_buffer + tail)[-MAX_MARKER_BUFFER_BYTES:])
                    is not None
                )
            if log_error is None:
                try:
                    if os.environ.get("CODEX_LONG_JOBS_TEST_LOG_FSYNC_FAIL") == "1":
                        raise OSError(errno.EIO, "simulated log fsync failure")
                    os.fsync(log_handle.fileno())
                except OSError as exc:
                    log_error = f"{exc.__class__.__name__}: {exc}"
        return_code = child.wait()
        test_terminal_delay = float(
            os.environ.get("CODEX_LONG_JOBS_TEST_BEFORE_TERMINAL_SECONDS", "0")
        )
        if test_terminal_delay > 0:
            time.sleep(test_terminal_delay)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        log_error = log_error or f"worker error: {exc.__class__.__name__}: {exc}"
        if child is not None:
            terminate_process_group(child_id, child_group)
            with contextlib.suppress(BaseException):
                child.wait(timeout=5)
            return_code = child.returncode

    release_reserve(name)
    signal_name = None
    exit_code = return_code
    signal_name = return_code_signal(return_code)
    if signal_name:
        exit_code = None
    succeeded = return_code == 0 and log_error is None and marker_seen
    reasons: list[str] = []
    if signal_name:
        reasons.append(f"command-signal={signal_name}")
    elif return_code != 0:
        reasons.append(f"command-exit={return_code}")
    if log_error:
        reasons.append(f"log-write-failed={log_error}")
    if not marker_seen and not cancel_effective:
        reasons.append("success-marker-missing")

    def terminal(current: dict[str, Any]) -> dict[str, Any]:
        if cancel_effective or current.get("cancel_effective_at"):
            if cancel_signal is not None:
                current["cancel_signal"] = cancel_signal
            return cancelled_terminal(
                current,
                exit_code=exit_code,
                signal_name=signal_name,
                marker_seen=marker_seen,
                log_error=log_error,
                failure_reasons=reasons,
            )
        current.update(
            status="succeeded" if succeeded else "failed",
            completed_at=now_iso(),
            exit_code=exit_code,
            signal=signal_name,
            marker_seen=marker_seen,
            log_error=log_error,
            failure_reasons=reasons,
            child_pid=None,
            child_identity=None,
            child_process_group=None,
        )
        return current

    terminal_record = update_job(name, terminal)
    finalize_delivery(name)
    return 0 if terminal_record["status"] in {"succeeded", "cancelled"} else 1


def choose_log_path(name: str, requested: str | None) -> Path:
    if requested:
        path = Path(requested).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return Path(os.path.abspath(path))
    return Path(os.path.abspath(state_root() / "logs" / f"{name}.log"))


def prepare_log_file(path: Path, *, overwrite: bool, control_directory: Path) -> bool:
    """Create a private regular log file and return whether it was new."""
    if path == control_directory or control_directory in path.parents:
        raise JobError("the log path cannot be inside the job control directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists() or path.is_symlink()
    if existed:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise JobError(f"log path is not a regular file: {path}")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise JobError(f"log file is not owned by the current user: {path}")
        if not overwrite:
            raise JobError(f"log already exists: {path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC
    flags |= os.O_TRUNC if overwrite else os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise JobError(f"log path is not a regular file: {path}")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise JobError(f"log file is not owned by the current user: {path}")
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return not existed


def select_delivery(
    requested: str, thread_id: str, endpoint: dict[str, Any] | None, reason: str
) -> tuple[str, str]:
    if requested == "auto":
        if thread_id:
            return "tui", "auto-tui" if endpoint else f"auto-pending-rebind:{reason}"
        return "event-only", "auto-fallback-thread-id-unavailable"
    if requested in {"tui", "headless"} and not thread_id:
        return "event-only", f"{requested}-fallback-thread-id-unavailable"
    return requested, f"requested-{requested}"


def viewer_label() -> str:
    return os.environ.get("CODEX_LONG_JOBS_TMUX_LABEL", "codex-long-jobs")


def viewer_session(name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"clj-{name[:65]}-{digest}"


def start_viewer(name: str, log_path: Path) -> tuple[bool, str]:
    if not shutil.which(tmux_binary()) or not shutil.which("tail"):
        return False, "tmux-or-tail-unavailable"
    label = viewer_label()
    session = viewer_session(name)
    check = subprocess.run(
        [tmux_binary(), "-L", label, "has-session", "-t", session],
        check=False,
        capture_output=True,
    )
    if check.returncode == 0:
        return True, "viewer-already-running"
    result = subprocess.run(
        [
            tmux_binary(),
            "-L",
            label,
            "new-session",
            "-d",
            "-s",
            session,
            "--",
            shutil.which("tail") or "tail",
            "-n",
            "200",
            "-F",
            str(log_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, f"viewer-start-failed:{result.stderr.strip()}"
    return True, "viewer-started"


def spawn_detached(arguments: list[str], *, cwd: str, log_path: Path) -> int:
    with open_log_for_append(log_path, create=True) as handle:
        child = subprocess.Popen(
            arguments,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return child.pid


def command_start(args: argparse.Namespace) -> int:
    if running_under_codex_sandbox():
        raise JobError(
            "the active Codex tool sandbox will reap detached descendants when this call ends; "
            "re-run this exact start command with scoped host execution"
        )
    ensure_state_root()
    name = validate_name(args.name)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise JobError("a command is required after --")
    compile_success_pattern(args.success_pattern)
    directory = job_dir(name)
    log_path = choose_log_path(name, args.log)
    endpoint, endpoint_reason = capture_endpoint()
    thread_id = os.environ.get("CODEX_THREAD_ID", "")
    if not THREAD_RE.fullmatch(thread_id):
        thread_id = ""
    mode, selection_reason = select_delivery(
        args.delivery, thread_id, endpoint, endpoint_reason
    )
    token = f"[codex-long-jobs:{name}:{secrets.token_hex(6)}]"
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "runtime_version": VERSION,
        "name": name,
        "status": "queued",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "cwd": str(Path.cwd()),
        "command": command,
        "log": str(log_path),
        "success_pattern": args.success_pattern,
        "owner_thread_id": thread_id or None,
        "endpoint": endpoint,
        "endpoint_capture_reason": endpoint_reason,
        "notify_user": not args.no_desktop_notify,
        "delivery": {
            "mode": mode,
            "status": "pending" if mode != "event-only" else "disabled",
            "selection_reason": selection_reason,
            "reason": selection_reason,
            "token": token,
            "wait_seconds": args.delivery_wait_seconds,
        },
    }
    directory_was_created = False
    log_was_created = False
    supervisor_pid: int | None = None
    try:
        directory.mkdir(mode=0o700)
        directory_was_created = True
        ensure_private_dir(directory)
        log_was_created = prepare_log_file(
            log_path, overwrite=args.overwrite_log, control_directory=directory
        )
        create_reserve(name)
        atomic_write_json(state_file(name), record)
        supervisor_pid = spawn_detached(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "_supervisor",
                "--name",
                name,
            ],
            cwd=record["cwd"],
            log_path=job_dir(name) / "supervisor-runtime.log",
        )
    except FileExistsError as exc:
        if supervisor_pid is None and directory_was_created:
            shutil.rmtree(directory)
        raise JobError(f"job already exists or log path is occupied: {name}") from exc
    except BaseException:
        if supervisor_pid is None:
            if directory_was_created:
                with contextlib.suppress(OSError):
                    shutil.rmtree(directory)
            if log_was_created:
                with contextlib.suppress(OSError):
                    log_path.unlink()
        raise

    def launched(current: dict[str, Any]) -> dict[str, Any]:
        current["supervisor_spawn_pid"] = supervisor_pid
        return current

    update_job(name, launched)
    viewer_mode = args.viewer
    if viewer_mode == "auto":
        viewer_mode = "tmux" if os.environ.get("TMUX") else "none"
    viewer_ok, viewer_reason = (False, "not-requested")
    if viewer_mode == "tmux":
        viewer_ok, viewer_reason = start_viewer(name, log_path)
    print(f"job={name}")
    print(f"supervisor_pid={supervisor_pid}")
    print(f"log={log_path}")
    print(f"state={state_file(name)}")
    print(f"delivery_mode={mode}")
    print(f"delivery_reason={selection_reason}")
    print(f"viewer={viewer_reason}")
    if viewer_ok:
        print(
            f"viewer_attach=TMUX= tmux -L {viewer_label()} attach -t {viewer_session(name)}"
        )
    return 0


def render_status(record: dict[str, Any]) -> str:
    delivery = record.get("delivery", {})
    rows = [
        f"job={record['name']}",
        f"status={record['status']}",
        f"exit_code={record.get('exit_code', 'pending')}",
        f"log={record['log']}",
        f"state={state_file(record['name'])}",
        f"delivery_mode={delivery.get('mode', 'unknown')}",
        f"delivery_status={delivery.get('status', 'unknown')}",
        f"delivery_reason={delivery.get('reason', 'unknown')}",
    ]
    if record.get("failure_reasons"):
        rows.append("failure_reasons=" + ";".join(record["failure_reasons"]))
    if record.get("cancel_requested_at"):
        rows.append(f"cancel_requested_at={record['cancel_requested_at']}")
        rows.append(
            f"cancel_effective_at={record.get('cancel_effective_at', 'not-effective')}"
        )
        rows.append(f"cancel_signal={record.get('cancel_signal', 'none')}")
    if record.get("cancelled_at"):
        rows.append(f"cancelled_at={record['cancelled_at']}")
    return "\n".join(rows)


def command_status(args: argparse.Namespace) -> int:
    errors: list[tuple[str, str]] = []
    if args.name:
        records = [read_job(validate_name(args.name))]
    else:
        records = []
        jobs_directory = state_root() / "jobs"
        if not jobs_directory.exists():
            print("[]" if args.json else "")
            return 0
        for path in sorted(jobs_directory.glob("*/state.json")):
            try:
                records.append(read_job(path.parent.name))
            except (JobError, json.JSONDecodeError, OSError) as exc:
                errors.append((path.parent.name, f"{exc.__class__.__name__}: {exc}"))
    if args.json:
        print(
            json.dumps(
                records[0] if args.name and records else records,
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print("\n\n".join(render_status(record) for record in records))
    for name, error in errors:
        print(f"codex-long-jobs: job={name} unreadable-state={error}", file=sys.stderr)
    return 1 if errors else 0


def command_cancel(args: argparse.Namespace) -> int:
    name = validate_name(args.name)
    grace_seconds = float(args.grace_seconds)
    already_terminal = False
    read_job(name)
    with job_lock(name):
        record = read_job(name)
        if record["status"] in TERMINAL_STATUSES:
            already_terminal = True
        else:
            if not supports_cancellation(record):
                raise JobError(
                    "cannot safely cancel a nonterminal job launched by a runtime older than 0.3.0"
                )
            if not record.get("cancel_requested_at"):
                record["cancel_requested_at"] = now_iso()
                record["cancel_grace_seconds"] = grace_seconds
                record["updated_at"] = now_iso()
                atomic_write_json(state_file(name), record)

    if already_terminal:
        print(f"job={name}")
        print(f"status={record['status']}")
        print("cancel=not-needed-already-terminal")
        print(f"state={state_file(name)}")
        return 0

    deadline = (
        time.monotonic()
        + float(record.get("cancel_grace_seconds", grace_seconds))
        + CANCEL_FINALIZE_WAIT_SECONDS
        + CANCEL_CHECK_INTERVAL_SECONDS
    )
    while time.monotonic() < deadline:
        record = read_job(name)
        if record["status"] in TERMINAL_STATUSES:
            print(f"job={name}")
            print(f"status={record['status']}")
            print(f"cancel_requested_at={record.get('cancel_requested_at')}")
            print(
                f"cancel_effective_at={record.get('cancel_effective_at', 'not-effective')}"
            )
            print(f"cancel_signal={record.get('cancel_signal', 'none')}")
            print(f"state={state_file(name)}")
            return 0
        time.sleep(0.05)
    raise JobError(
        f"cancellation requested but job did not reach terminal state within "
        f"{float(record.get('cancel_grace_seconds', grace_seconds)) + CANCEL_FINALIZE_WAIT_SECONDS:.1f} seconds; "
        f"inspect {state_file(name)} and {record['log']}"
    )


def spawn_delivery(name: str) -> int:
    log_path = job_dir(name) / "delivery-worker.log"
    return spawn_detached(
        [sys.executable, str(Path(__file__).resolve()), "_deliver", "--name", name],
        cwd=read_job(name)["cwd"],
        log_path=log_path,
    )


def command_rebind(args: argparse.Namespace) -> int:
    if running_under_codex_sandbox():
        raise JobError(
            "rebind must use scoped host execution so its delivery worker can survive this call"
        )
    ensure_state_root()
    endpoint, reason = capture_endpoint()
    if not endpoint:
        raise JobError(f"cannot bind the current Codex TUI: {reason}")
    thread_id = endpoint["thread_id"]
    names: list[str]
    if args.all:
        names = [
            path.parent.name
            for path in sorted((state_root() / "jobs").glob("*/state.json"))
        ]
    elif args.name:
        names = [validate_name(args.name)]
    else:
        raise JobError("use --name NAME or --all")
    rebound = 0
    for name in names:
        record = read_job(name)
        if record.get("owner_thread_id") != thread_id:
            if args.name:
                raise JobError(
                    "refusing to bind a job owned by a different Codex thread"
                )
            continue

        def bind(current: dict[str, Any]) -> dict[str, Any]:
            current["endpoint"] = endpoint
            current["endpoint_capture_reason"] = "explicit-rebind"
            if (
                current.get("delivery", {}).get("mode") == "tui"
                and current["delivery"].get("status") != "delivered"
            ):
                current["delivery"]["status"] = "pending"
                current["delivery"]["reason"] = "explicit-rebind"
            return current

        updated = update_job(name, bind)
        rebound += 1
        if (
            updated["status"] in TERMINAL_STATUSES
            and updated.get("delivery", {}).get("mode") == "tui"
            and updated.get("delivery", {}).get("status") not in DELIVERY_DONE
        ):
            spawn_delivery(name)
        print(f"rebound={name}")
    print(f"rebound_count={rebound}")
    return 0


def command_retry_delivery(args: argparse.Namespace) -> int:
    if running_under_codex_sandbox():
        raise JobError(
            "delivery retry must use scoped host execution so it can survive this call"
        )
    name = validate_name(args.name)
    record = read_job(name)
    if record["status"] not in TERMINAL_STATUSES:
        raise JobError("job is not terminal")
    if record.get("delivery", {}).get("mode") != "tui":
        raise JobError("job does not use TUI delivery")
    if record.get("delivery", {}).get("status") == "delivered":
        print("delivery already completed")
        return 0
    if record.get("delivery", {}).get("status") in DELIVERY_DONE:
        raise JobError("job delivery is already finalized")
    pid = spawn_delivery(name)
    print(f"delivery_worker_pid={pid}")
    return 0


def command_view(args: argparse.Namespace) -> int:
    record = read_job(validate_name(args.name))
    ok, reason = start_viewer(record["name"], Path(record["log"]))
    if not ok:
        raise JobError(reason)
    print(f"viewer={reason}")
    print(
        f"viewer_attach=TMUX= tmux -L {viewer_label()} attach -t {viewer_session(record['name'])}"
    )
    return 0


def command_tail(args: argparse.Namespace) -> int:
    record = read_job(validate_name(args.name))
    tail = shutil.which("tail")
    if not tail:
        raise JobError("tail is unavailable")
    os.execv(tail, [tail, "-n", str(args.lines), "-F", record["log"]])
    return 0


def command_doctor(_: argparse.Namespace) -> int:
    print(f"version={VERSION}")
    print(f"python={sys.version.split()[0]}")
    print(f"platform={sys.platform}")
    print(f"state_root={state_root()}")
    print(f"tmux={shutil.which(tmux_binary()) or 'unavailable'}")
    print(f"tail={shutil.which('tail') or 'unavailable'}")
    endpoint, reason = capture_endpoint()
    print(f"tui_endpoint={reason}")
    if endpoint:
        print(f"thread_id={endpoint['thread_id']}")
        print(f"pane_id={endpoint['pane_id']}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="codex-long-jobs", description=__doc__)
    root.add_argument("--version", action="version", version=VERSION)
    sub = root.add_subparsers(dest="subcommand", required=True)

    start = sub.add_parser("start", help="start a durable detached job")
    start.add_argument("--name", required=True)
    start.add_argument("--log")
    start.add_argument("--success-pattern")
    start.add_argument("--overwrite-log", action="store_true")
    start.add_argument(
        "--delivery", choices=["auto", "tui", "headless", "event-only"], default="auto"
    )
    start.add_argument("--viewer", choices=["auto", "tmux", "none"], default="auto")
    start.add_argument("--delivery-wait-seconds", type=positive_int, default=86400)
    start.add_argument("--no-desktop-notify", action="store_true")
    start.add_argument("command", nargs=argparse.REMAINDER)
    start.set_defaults(func=command_start)

    status = sub.add_parser("status", help="show durable job state")
    status.add_argument("--name")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    cancel = sub.add_parser(
        "cancel", help="stop a nonterminal job and wait for durable final state"
    )
    cancel.add_argument("--name", required=True)
    cancel.add_argument("--grace-seconds", type=nonnegative_float, default=10.0)
    cancel.set_defaults(func=command_cancel)

    rebind = sub.add_parser(
        "rebind", help="bind pending jobs to the current resumed Codex TUI"
    )
    group = rebind.add_mutually_exclusive_group(required=True)
    group.add_argument("--name")
    group.add_argument("--all", action="store_true")
    rebind.set_defaults(func=command_rebind)

    retry = sub.add_parser(
        "retry-delivery", help="retry a terminal job's pending TUI delivery"
    )
    retry.add_argument("--name", required=True)
    retry.set_defaults(func=command_retry_delivery)

    view = sub.add_parser(
        "view", help="create or restore the disposable tmux log viewer"
    )
    view.add_argument("--name", required=True)
    view.set_defaults(func=command_view)

    tail = sub.add_parser("tail", help="follow a job log in the current terminal")
    tail.add_argument("--name", required=True)
    tail.add_argument("--lines", type=positive_int, default=200)
    tail.set_defaults(func=command_tail)

    doctor = sub.add_parser(
        "doctor", help="inspect runtime and TUI binding prerequisites"
    )
    doctor.set_defaults(func=command_doctor)

    worker = sub.add_parser("_worker")
    worker.add_argument("--name", required=True)
    worker.set_defaults(func=lambda args: worker_main(args.name))

    supervisor = sub.add_parser("_supervisor")
    supervisor.add_argument("--name", required=True)
    supervisor.set_defaults(func=lambda args: supervisor_main(args.name))

    deliver = sub.add_parser("_deliver")
    deliver.add_argument("--name", required=True)
    deliver.set_defaults(func=lambda args: (deliver_tui(args.name), 0)[1])
    return root


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        return int(args.func(args))
    except JobError as exc:
        print(f"codex-long-jobs: {exc}", file=sys.stderr)
        return 1
    except PermissionError as exc:
        print(
            f"codex-long-jobs: permission denied for {exc.filename or state_root()}; "
            "re-run the identical command with scoped permission for the configured state directory",
            file=sys.stderr,
        )
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"codex-long-jobs: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
