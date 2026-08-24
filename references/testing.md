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
6. Busy TUI deferral, empty-composer validation, missed Enter retry, persistent
   Enter failure, delivery timeout, retry, and thread-isolated rebind.
7. Disposable tmux viewer failure and recreation behavior.
8. Event-only, direct TUI, and explicitly selected headless delivery paths.
9. Cancellation before launch, process-group termination, `SIGTERM` to
   `SIGKILL` escalation, supervisor loss, worker loss, natural-completion races,
   idempotent terminal cancellation, and one cancelled completion prompt.
10. Desktop notification minimization, completion path escaping, corrupt-state
    reporting, status JSON, doctor output, log tailing, version consistency, and
    viewer-name uniqueness.

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
ruff check scripts/codex_long_jobs.py tests
ruff format --check scripts/codex_long_jobs.py tests
shellcheck scripts/codex-long-jobs scripts/*.sh
```

## Real Codex TUI acceptance

Run this layer from a disposable job name in a real Codex CLI TUI hosted by
tmux. Preserve the state and log as evidence.

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
stable public idle-wake API. Repeat it after meaningful Codex CLI rendering or
input changes.

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
