# Repository Guidelines

## Project Structure & Module Organization

The runtime is `scripts/codex_long_jobs.py`; `scripts/codex-long-jobs` is the
shell entrypoint. Skill metadata is in `SKILL.md` and `agents/openai.yaml`;
contracts and release evidence are in `references/`. `unittest` tests live in
`tests/`; `fake_tmux.py` simulates TUI behavior. Releases update `CHANGELOG.md`
and `VERSION`.

## Build, Test, and Development Commands

There is no build step. Use:

- `scripts/smoke_test.sh`: integration suite.
- `scripts/coverage_test.sh`: subprocess-aware branch coverage.
- `scripts/stress_test.sh 10`: repeated lifecycle and race tests.
- `ruff check scripts/*.py tests` and `ruff format --check scripts/*.py tests`:
  lint and format checks.
- `shellcheck scripts/codex-long-jobs scripts/*.sh`: shell lint.
- `scripts/codex-long-jobs doctor`: runtime and TUI diagnostics.

Run the OpenAI Skill validator against the repository root before release.

## Architecture & Agent-Specific Invariants

Preserve the established contract: supervisor, worker, and command run outside
tmux; tmux is only a disposable viewer and validated live-TUI transport.
Completion is event-driven for the model. Local readiness checks are allowed,
but never add periodic LLM/model polling.

Durable state is truth; TUI delivery is best effort because Codex has no stable
public wake API. Never inject job output, paste into a busy turn, or submit over
a nonempty composer. Cancellation is explicit and process-group scoped. If no
signal takes effect before natural completion, the actual result wins. Keep
`status --json` as the result interface; do not add redundant `result` or reuse
immutable job names.

## Coding Style & Naming Conventions

Target Python 3.10+, four-space indentation, type annotations, and 88-character
lines. Follow `ruff.toml`; use `snake_case`, `test_<behavior>`, and unique job
names such as `train-model-run-01`. Keep runtime dependencies at zero, preserve
argv boundaries, and avoid dash-based sentence breaks in prose.

## Testing Guidelines

Every behavior change needs a deterministic regression test. CI covers Python
3.10 through 3.14 and requires 80% branch coverage. Test failures, races,
cleanup, durable state, and artifacts, not only CLI text. Repeat the real tmux
acceptance in `references/testing.md` after delivery changes. Releases require
green CI, an annotated tag, release notes, and a backed-up installed-skill sync.

## Commit & Pull Request Guidelines

Follow the repository's Conventional Commit style, for example
`feat: add durable job cancellation`, `fix: preserve unknown job names`, or
`docs: clarify session recovery`. Agent-assisted commits should retain the
current `Co-Authored-By` trailer convention. Pull requests explain the contract,
list verified commands and results, link issues, and update tests and changelog.
Open an issue before changing durable schema or delivery. Never commit secrets,
transcripts, logs, or host state.
