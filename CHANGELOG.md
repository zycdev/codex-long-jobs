# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Prefer `codex queue --thread` for automatic completion delivery when the
  installed CLI supports it, without depending on tmux pane metadata.
- Record queue acceptance separately from session consumption, suppress normal
  duplicate dispatch, and preserve ambiguous timeout or restart outcomes for
  explicit review.
- Add guarded migration of terminal pending TUI notifications to queue delivery.

### Changed

- Retain conservative TUI delivery as the automatic compatibility path for
  older Codex CLI versions.
- Report queue capability in `doctor` and document real Codex CLI 0.157.1 idle,
  busy, exited-TUI, and one-shot-client acceptance boundaries.

## [0.3.1] - 2026-09-21

### Added

- Add an offline release-worktree installer so contributors can run a fixed
  local commit while continuing development in the primary checkout.

### Fixed

- Recognize unique completion tokens that the Codex TUI renders across
  multiple composer lines, both before the first Enter and during missed-Enter
  retries, without pasting the prompt again.

## [0.3.0] - 2026-08-24

### Added

- Add durable `cancel` requests with synchronous terminal-state confirmation.
- Stop the validated child process group with `SIGTERM` and bounded `SIGKILL`
  escalation, including worker-loss and supervisor-loss handling.
- Persist `cancelled` terminal state, request and effectiveness timestamps,
  applied signal, command result, and normal completion delivery.
- Cover prelaunch cancellation, descendant cleanup, escalation, duplicate
  cancellation, delivery, and natural-completion races in the automated suite.

### Changed

- Report unreadable job records during unfiltered `status` listings instead of
  silently omitting them.
- Preserve unknown job names after a failed cancellation request and restore
  process cleanup when group-identity capture fails.
- Reduce Linux process-group validation from full process metadata reads to
  lightweight `/proc/PID/stat` checks.
- Keep `status --json` as the stable machine-readable lifecycle and result
  interface rather than adding a redundant `result` command.

### Documentation

- Clarify repository and skill metadata around Codex background jobs, detached
  processes, no-model-polling completion, and original-session wake-up.
- Add focused explanations of the problem, tmux wake path, use cases, lifecycle
  boundaries, polling distinction, and frequently asked questions.
- Record real Codex TUI acceptance for same-thread resume in a different tmux
  pane and supervisor-only SIGKILL survival.
- Clarify that a running worker can finish after supervisor loss, but no
  watchdog remains for a later worker failure.

## [0.2.0] - 2026-08-20

### Added

- A detached supervisor that records and reports unexpected worker exits.
- Cross-job TUI delivery locking to prevent concurrent completion prompts from sharing one composer.
- Kernel-enforced file-size failure, worker SIGKILL, concurrency, headless, notification, and CLI recovery tests.
- Branch coverage measurement across Python subprocesses with an 80 percent minimum.
- Complete CI coverage for Python 3.10 through 3.14 and ShellCheck validation.
- A repeatable stability runner and a documented release test matrix.

### Changed

- Job startup is transactional and reports the supervisor PID.
- Log output is flushed incrementally for live viewing, and worker-time opening
  rejects replaced symlinks and unsafe file types.
- Log writes detect partial writes, fsync failures, unsafe control-path
  collisions, and non-regular files.
- State replacement fsyncs the parent directory for stronger crash durability.
- Completion prompts JSON-escape evidence paths before TUI injection.
- Viewer session names include a stable hash to avoid truncation collisions.
- Command signals are classified separately from numeric exit codes.
- Success marker expressions match per log line, including anchored markers
  that follow earlier command output.

## [0.1.0] - 2026-08-20

### Added

- Detached worker and command lifecycle independent of tmux.
- Optional disposable tmux log viewer.
- Atomic private state, process-start identities, and final-state disk reserve.
- Exit-code, signal, success-marker, and log-write failure classification.
- Safe idle-boundary TUI delivery with missed-Enter retry and no busy-turn paste.
- Explicit same-thread rebind after Codex process or pane changes.
- Event-only and explicitly opted-in headless completion modes.
- Integration tests for failure, delivery, rebind, and viewer survival.
- Reproducible CI across Python 3.10, 3.13, and 3.14.
- Standard user installation under `~/.agents/skills`.

[Unreleased]: https://github.com/zycdev/codex-long-jobs/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/zycdev/codex-long-jobs/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/zycdev/codex-long-jobs/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/zycdev/codex-long-jobs/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/zycdev/codex-long-jobs/releases/tag/v0.1.0
