#!/usr/bin/env python3
"""Small Codex CLI fake for queue-delivery tests."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    arguments = sys.argv[1:]
    root = Path(os.environ["FAKE_CODEX_STATE_DIR"])
    root.mkdir(parents=True, exist_ok=True)
    if arguments == ["queue", "--help"]:
        help_mode = os.environ.get("FAKE_CODEX_QUEUE_HELP_MODE", "supported")
        if help_mode == "error":
            return 2
        if help_mode == "missing":
            print("Usage: codex [OPTIONS]")
            return 0
        print("Usage: codex queue --thread <THREAD> --message <TEXT>")
        return 0
    if arguments and arguments[0] == "queue":
        with (root / "queue-attempts.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(arguments))
            handle.write("\n")
        mode = os.environ.get("FAKE_CODEX_QUEUE_MODE", "success")
        if mode == "failure":
            return 9
        if mode == "timeout":
            time.sleep(5)
        return 0
    print(f"unsupported fake codex command: {arguments}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
