#!/usr/bin/env python3
"""Small tmux protocol fake for delivery tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(os.environ["FAKE_TMUX_STATE_DIR"])
root.mkdir(parents=True, exist_ok=True)
arguments = sys.argv[1:]
if arguments[:1] == ["-S"]:
    arguments = arguments[2:]
command = arguments.pop(0)


def read(name: str, default: str = "") -> str:
    path = root / name
    return path.read_text(encoding="utf-8") if path.exists() else default


def write(name: str, value: str) -> None:
    (root / name).write_text(value, encoding="utf-8")


def screen(escaped: bool) -> str:
    mode = read("mode", "idle").strip()
    prompt = read("buffer")
    if escaped:
        if mode == "pasted":
            return f"\x1b[1m›\x1b[0m {prompt}\n"
        if mode == "accepted":
            return f"{prompt}\nworking\n\x1b[1m›\x1b[0m \x1b[2mReady\x1b[0m\n"
        return "\x1b[1m›\x1b[0m \x1b[2mReady\x1b[0m\n"
    if mode == "busy":
        return "Working (1s • esc to interrupt)\n› Ready\n"
    if mode == "pasted":
        return f"› {prompt}\n"
    if mode == "accepted":
        return f"{prompt}\nWorking (0s • esc to interrupt)\n› Ready\n"
    return "› Ready\n"


if command == "display-message":
    format_string = arguments[-1]
    if format_string == "#{pid}":
        print(read("server_pid", "1234").strip())
    elif "#{pane_current_path}" in format_string:
        print(read("pane_meta_full").strip())
    elif "#{pane_tty}" in format_string:
        print(read("pane_meta_short").strip())
elif command == "capture-pane":
    sys.stdout.write(screen("-e" in arguments))
elif command == "load-buffer":
    source = Path(arguments[-1])
    write("buffer", source.read_text(encoding="utf-8"))
elif command == "paste-buffer":
    count = int(read("paste_count", "0")) + 1
    write("paste_count", str(count))
    write("mode", "pasted")
elif command == "send-keys":
    count = int(read("enter_count", "0")) + 1
    write("enter_count", str(count))
    failures = int(read("fail_enter_count", "0"))
    if count <= failures:
        raise SystemExit(1)
    if read("mode").strip() == "pasted":
        write("mode", "accepted")
else:
    print(f"unsupported fake tmux command: {command} {arguments}", file=sys.stderr)
    raise SystemExit(2)
