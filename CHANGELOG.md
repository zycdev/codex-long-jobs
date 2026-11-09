# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Wake an unloaded owner after accepted queue delivery through its existing
  Codex daemon, without a UI click, extra model polling, or third-party runtime
  dependencies. Preserve queue order and avoid duplicate input after resume.
- Store separate wake evidence and provide a wake-only recovery helper.
- Explain how to handle an active Goal while a background job runs.

### Changed

- Prefer runtime-detected native `queue --resume`; retain RPC recovery for older CLIs and uncertain native results without retransmitting accepted notifications.
- Discover trusted PATH and VS Code extension executables after explicit overrides, and report executable selection, capabilities, and optional CLI upgrade advice through `doctor`.
- Expose independent wake observations through `status`, and provide detached `retry-wake` recovery with a persisted waiting deadline and existing-supervisor takeover.

### Fixed

- Resume unloaded threads with preceding queued input, preserve FIFO under concurrent reordering, paginate queue inspection, and distinguish absence from confirmed turn startup.
- Recover acceptance-to-launch gaps and abnormal helper exits without resetting automatic waiting budgets or creating a permanent service.
- Preserve concurrent queue acceptance during explicit duplicate-risk retries, and retain original Codex-home and unsupported-client-request evidence across recovery.
- Bound complete WebSocket/RPC operations despite partial progress, stop recovery attempts at the waiting deadline, and preserve command-status visibility when separate wake evidence is malformed.
- Wait for valid PID content in the process-group cancellation test instead of only checking file existence.
- Make standalone runtime tests resolve script imports without relying on another test module's import order.

### Testing

- Add deterministic native-resume, compatibility, discovery, recovery, concurrency, writer-lock, and unsupported-client-request regressions, plus an opt-in isolated real-CLI acceptance harness.
- Record real old-CLI VS Code acceptance for online and busy continuation, conversation switching, and verified tool continuation before window reopening or remote reconnection.
- Verify CLI 0.167.0 native resume against isolated servers from both 0.167.0 and extension 0.164.0-alpha.11.1, including failed-resume warnings, wake-only recovery, concurrent consumers, and switching back to a legacy CLI.
- Record supervised CLI 0.167.0 continuation in online, busy, unselected, closed-window, and disconnected cases, including verified tool results before reopening or reconnection.
- Exercise actual helper/supervisor process loss and acceptance-to-launch crashes, and verify three concurrent background jobs with six wake-recovery callers against an isolated old CLI.
- Verify a limited compatibility fallback with five focused regressions and one isolated real tmux delivery and duplicate-suppression check.

## [0.4.2] - 2026-09-30

### Changed

- Include the Codex extension for VS Code in skill discovery and provide concise client-specific operating guidance.
- Present core job operations before client guidance and release checks, and retain detailed acceptance evidence in reference documents.

### Documentation

- Record real VS Code Remote-SSH acceptance for queue delivery, conversation routing, panel and connection recovery, unexpected client-side connection loss, extension-backend and Server main-process termination, failure, and cancellation.
- Extend skill discovery and operating guidance to the tested VS Code environment, including the distinction between queue acceptance and continuation after reconnection.

## [0.4.1] - 2026-09-30

### Added

- Add configurable, notification-only release checks on skill use, enabled weekly by default, with persistent attempt throttling and preserved preferences across installations.

### Changed

- Default to no managed log viewer, including inside tmux, and provide a shell-quoted `log_follow` command alongside the log path in start and status output.
- Make file paths and terminal-independent log access the default Skill guidance; retain explicitly requested tmux viewers.

## [0.4.0] - 2026-09-27

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
