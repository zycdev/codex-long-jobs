#!/usr/bin/env bash
set -euo pipefail

skill_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export COVERAGE_FILE="$skill_root/.coverage"

coverage erase --rcfile="$skill_root/.coveragerc"
coverage run --rcfile="$skill_root/.coveragerc" \
  -m unittest discover -s "$skill_root/tests" -p 'test_*.py'
coverage combine --rcfile="$skill_root/.coveragerc"
coverage report --rcfile="$skill_root/.coveragerc"
