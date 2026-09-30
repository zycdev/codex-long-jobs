# Testing and release acceptance

## Scope

The test program separates deterministic behavior from environmental
acceptance. Passing the automated suite means the implemented state and process
contracts behaved as specified on the tested host. It does not claim survival
of machine reboot, loss of both supervisor and worker, or arbitrary future
Codex TUI changes. Supervisor-only SIGKILL after the worker reaches `running`
has a separate real-host acceptance test.

## Automated matrix

GitHub Actions runs the suite on Linux with every supported Python minor
version from 3.10 through 3.14. The current suite covers:

1. Successful completion, nonzero exit, missing success marker, command signal,
   missing executable, worker SIGKILL, and durable cancellation.
2. Simulated ENOSPC, kernel-enforced file-size failure, partial writes, large
   output, live output visibility, log fsync, and final-state reserve release.
3. Atomic state replacement, private file modes, symlink rejection, unsafe log
   path rejection, invalid regex rejection, and startup rollback.
4. Exact argv boundaries for spaces, shell-like text, and embedded newlines.
5. Concurrent duplicate starts and cross-job TUI delivery serialization.
6. Queue capability detection, legacy fallback, missing TUI context, accepted
   queueing, command failure, timeout uncertainty, interrupted dispatch,
   duplicate suppression, and guarded migration of pending TUI notifications.
7. Busy TUI deferral, empty-composer validation, missed Enter retry, persistent
   Enter failure, completion tokens wrapped across indented composer lines,
   delivery timeout, retry, and thread-isolated rebind.
8. Disposable tmux viewer failure and recreation behavior.
9. Queue, event-only, direct TUI, and explicitly selected headless delivery
   paths.
10. Cancellation before launch, process-group termination, `SIGTERM` to
   `SIGKILL` escalation, supervisor loss, worker loss, natural-completion races,
   idempotent terminal cancellation, and one cancelled completion prompt.
11. Desktop notification minimization, completion path escaping, corrupt-state
    reporting, status JSON, doctor output, log tailing, version consistency, and
    viewer-name uniqueness.
12. Release installation dry runs, fixed-commit worktrees, safe destination
    replacement, upgrades, private receipts, and drift detection.

The coverage harness uses coverage.py subprocess instrumentation because the
runtime deliberately creates detached Python processes. CI requires at least
80 percent combined branch coverage. Platform-specific fallback code and
defensive OS-error branches account for most intentionally uncovered lines.

## Local commands

Run the ordinary host integration suite:

```bash
scripts/smoke_test.sh
```

Run the subprocess-aware branch coverage gate after installing coverage.py:

```bash
scripts/coverage_test.sh
```

Run repeated lifecycle and race regression tests:

```bash
scripts/stress_test.sh 10
```

Lint all Python and shell entrypoints:

```bash
ruff check scripts/*.py tests
ruff format --check scripts/*.py tests
shellcheck scripts/codex-long-jobs scripts/*.sh
```

## Real Codex TUI acceptance

Run this layer with an isolated Codex thread and disposable job names. Preserve
the state and log as evidence, and never send acceptance traffic to an active
project thread.

For queue delivery:

1. Queue a completion to an idle persistent TUI and confirm a new turn starts
   and completes.
2. Keep the TUI busy with a bounded command, queue a second message, and confirm
   it starts only after the active turn completes.
3. Exit the isolated TUI, queue another message by thread UUID, and confirm the
   persistent daemon consumes it without tmux.
4. Confirm the queue command returns promptly and document that exit code zero
   proves acceptance only. A one-shot `codex exec` client can close after its
   first turn and abort an accepted follow-up.

For compatibility TUI delivery:

1. Start a short TUI-delivered job while Codex is actively working. Confirm the
   job reaches terminal state with delivery still pending and reason
   `owning-tui-busy`.
2. End the Codex turn. Confirm the prompt is submitted once, Enter is accepted,
   and the owning TUI refreshes without manual input.
3. Start two jobs that complete during the same active turn. Confirm delivery
   serialization and exactly one prompt per job.
4. Start a job, exit Codex before completion, resume the same thread in another
   tmux pane, and run `rebind --name NAME`. Confirm delivery succeeds.
5. Attempt the same rebind from another thread. Confirm it is refused.
6. Start a job with a tmux viewer, terminate the dedicated viewer server, and
   confirm the command, supervisor, state, and log continue.
7. Start a gated job, wait for `running`, kill only its supervisor with
   SIGKILL, and confirm the worker and command continue through terminal state
   and TUI delivery.
8. Start a TUI-delivered job with a child process in the same process group,
   request `cancel`, and confirm both processes exit, the durable state is
   `cancelled`, and exactly one cancellation prompt wakes the owning TUI.

Real TUI acceptance is version-sensitive because Codex currently provides no
stable cross-version wake contract. Repeat queue acceptance after meaningful
Codex queue or daemon changes, and repeat input-injection acceptance after TUI
rendering changes.

## Recorded v0.4.0 queue acceptance

The queue transport passed on Linux with Codex CLI 0.157.1 on 2026-09-26:

1. An idle persistent TUI consumed the queued message and completed its turn.
2. A message queued during a 15-second active turn returned in 0.20 seconds and
   was consumed after the active turn completed.
3. After `/exit` terminated the isolated TUI and its disposable tmux server,
   the app-server daemon consumed and completed another queued message.
4. A one-shot `codex exec` session accepted a busy follow-up in 0.22 seconds and
   created its turn after the first turn, but client shutdown immediately
   aborted that follow-up. This confirms that command success is an acceptance
   acknowledgement, not a consumption acknowledgement.

The shared production daemon was not stopped for failure testing. Deterministic
fake-CLI tests cover daemon-style nonzero failure, retry after recovery, timeout
uncertainty, restart recovery, and duplicate suppression without affecting live
sessions.

## Recorded v0.2.0 host acceptance

The following scenarios passed on Linux with Codex CLI 0.148.0 and Python
3.14.4 on 2026-08-20:

1. A running job survived exit of the owning Codex process. The same thread was
   resumed in a different tmux pane, explicitly rebound while still running,
   and automatically delivered exactly one completion prompt to the new pane.
2. A running job's supervisor was killed with SIGKILL. The worker was reparented
   to PID 1, the command continued, the success marker was recorded once, and
   the worker independently persisted `succeeded` state and delivered exactly
   one completion prompt.

Both scenarios produced exit code zero, no failure reasons, private mode 0600
state and log files, and first-attempt Enter submission.

## Recorded v0.3.0 release acceptance

The v0.3.0 release candidate passed on Linux with Codex CLI 0.149.0 and Python
3.14.4 on 2026-08-24:

1. The 64-test suite passed, including the real-host tmux viewer test, with
   83.29 percent combined branch coverage. Ruff, ShellCheck, the skill
   validator, and ten consecutive stress runs also passed.
2. A tmux-hosted Codex TUI launched a process-group job with a child process,
   requested cancellation, and observed both processes disappear. Durable
   state recorded `cancelled`, `SIGTERM`, private 0600 state and log files, and
   no log-write error.
3. Delivery remained pending with reason `owning-tui-busy` during the active
   Codex turn. After that turn ended, the completion prompt was submitted once
   to the owning pane, the TUI refreshed automatically, and durable delivery
   state became `delivered` with reason `submitted-after-1-enter-attempts`.

The preserved acceptance evidence is job
`clj-cancel-live-20260824-v030`, state file
`~/.codex/long-jobs/jobs/clj-cancel-live-20260824-v030/state.json`, and log
`/tmp/clj-cancel-live-20260824-v030.log`.

## Boundaries that cannot be proven locally

1. A running worker can finish after supervisor-only loss, but no watchdog
   remains for a later worker failure. Host reboot or loss of both supervisor
   and worker requires an external service manager.
2. The 64 KiB reserve improves final-state reliability during storage pressure,
   but cannot overcome a filesystem with no reclaimable space or metadata.
3. Process completion does not prove semantic artifact correctness. Completion
   messages require Codex to inspect the state, log, and requested artifacts.
4. Native Windows is outside the supported runtime contract.

## Recorded v0.4.1 acceptance

On 2026-09-30, the final implementation passed 93 tests with 83.78% subprocess-aware branch coverage on the Linux host.
Ruff, formatting, and the OpenAI Skill validator passed.
Tests cover independent log access inside and outside tmux, shell-safe log commands, default weekly release checks, exact interval boundaries, disabled checks, preserved installation preferences, concurrent callers, failed and interrupted requests, and numeric release comparison.
An isolated real GitHub request returned the published release, and an immediate second invocation returned `not-due` without a second request.
The installed skill passed installation verification and all 11 update-check tests.
No production job or notification state was changed.
