#!/usr/bin/env python3
"""Check published releases on use, without installing or executing remote code."""

from __future__ import annotations

import argparse
import fcntl
import http.client
import json
import math
import os
import re
import tempfile
import time
import urllib.request
from pathlib import Path

API_URL = "https://api.github.com/repos/zycdev/codex-long-jobs/releases/latest"
RELEASE_URL = "https://github.com/zycdev/codex-long-jobs/releases/tag/"
DEFAULT_INTERVAL = 7 * 86400
NOTICE = (
    "Release checks are enabled weekly by default. Use check_updates.py configure "
    "--enabled no to disable them, or configure --interval 1d (also h or w) "
    "to change the interval. Updates require an explicit user request."
)


def state_path() -> Path:
    base = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return base / "long-jobs" / "update-check.json"


def duration(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)([hdw])", value)
    if not match:
        raise argparse.ArgumentTypeError(
            "use a positive duration such as 12h, 7d, or 2w"
        )
    return int(match[1]) * {"h": 3600, "d": 86400, "w": 604800}[match[2]]


def version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value)
    if not match:
        raise ValueError("expected a stable major.minor.patch version")
    return tuple(int(part) for part in match.groups())


def read_state(path: Path, now: float) -> dict:
    if not path.exists():
        return {
            "enabled": True,
            "interval_seconds": DEFAULT_INTERVAL,
            "created_at": now,
            "last_attempt_at": None,
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or type(value.get("enabled")) is not bool
        or type(value.get("interval_seconds")) is not int
        or value["interval_seconds"] <= 0
    ):
        raise ValueError("invalid update-check configuration; existing file preserved")
    for key in ("created_at", "last_attempt_at"):
        timestamp = value.get(key)
        if timestamp is None and key == "last_attempt_at":
            continue
        if (
            type(timestamp) not in (int, float)
            or not math.isfinite(timestamp)
            or timestamp < 0
        ):
            raise ValueError("invalid update-check timestamp; existing file preserved")
    return value


def save_state(path: Path, value: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".update-check-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def fetch_release() -> str:
    request = urllib.request.Request(
        API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "codex-long-jobs-update-check",
        },
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        payload = json.loads(response.read(1024 * 1024))
    if (
        not isinstance(payload, dict)
        or payload.get("draft")
        or payload.get("prerelease")
    ):
        raise ValueError("expected a published stable release")
    tag = payload.get("tag_name")
    if not isinstance(tag, str):
        raise ValueError("release tag is missing")
    version(tag)
    return tag


def run(args: argparse.Namespace) -> dict:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Separate lock inode remains stable across atomic configuration replacement.
    with (path.parent / "update-check.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        now = time.time()
        existed = path.exists()
        state = read_state(path, now)
        if args.command == "configure":
            if args.enabled is not None:
                state["enabled"] = args.enabled == "yes"
            if args.interval is not None:
                state["interval_seconds"] = args.interval
        if not existed or args.command == "configure":
            save_state(path, state)
        result = {"config": str(path), **state}
        if args.command != "check":
            return {**result, "notice": NOTICE}
        if not state["enabled"]:
            return {"status": "disabled"}
        previous = state["last_attempt_at"]
        baseline = state["created_at"] if previous is None else previous
        if now - baseline < state["interval_seconds"]:
            return {
                "status": "not-due",
                "next_check_at": baseline + state["interval_seconds"],
            }
        # Persist before network I/O so failures or interrupted checks are throttled.
        state["last_attempt_at"] = now
        save_state(path, state)
        try:
            current = (
                (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
            )
            current_version = version(current)
            tag = fetch_release()
            available = version(tag) > current_version
        except (OSError, ValueError, http.client.HTTPException) as error:
            return {"status": "unavailable", "error": type(error).__name__}
        return {
            "status": "update-available" if available else "up-to-date",
            "installed_version": current,
            "latest_version": tag,
            "release_url": RELEASE_URL + tag,
            "notice": "Installation requires an explicit user request.",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "init", help="initialize defaults without resetting preferences"
    )
    commands.add_parser("check", help="check only when enabled and due")
    commands.add_parser("status", help="show local settings without network access")
    config = commands.add_parser("configure")
    config.add_argument("--enabled", choices=("yes", "no"))
    config.add_argument("--interval", type=duration)
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError) as error:
        result = {"status": "unavailable", "error": str(error)}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
