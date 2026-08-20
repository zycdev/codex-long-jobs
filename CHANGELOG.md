# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/zycdev/codex-long-jobs/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/zycdev/codex-long-jobs/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/zycdev/codex-long-jobs/releases/tag/v0.1.0
