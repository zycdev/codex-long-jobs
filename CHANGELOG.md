# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/zycdev/codex-long-jobs/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/zycdev/codex-long-jobs/releases/tag/v0.1.0
