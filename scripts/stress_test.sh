#!/usr/bin/env bash
set -euo pipefail

skill_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
runs=${1:-10}

if [[ ! $runs =~ ^[1-9][0-9]*$ ]]; then
  printf 'usage: %s [positive-run-count]\n' "$0" >&2
  exit 2
fi

for ((run = 1; run <= runs; run++)); do
  printf 'stress_run=%d/%d\n' "$run" "$runs"
  python3 -m unittest discover \
    -s "$skill_root/tests" \
    -p 'test_*.py' \
    --quiet
done

printf 'CODEX_LONG_JOBS_STRESS_COMPLETE runs=%d\n' "$runs"
