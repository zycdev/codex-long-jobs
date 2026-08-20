# Operations and failure model

## Contents

- Runtime architecture
- Terminal-state rules
- TUI delivery state machine
- Session exit and resume
- tmux failure behavior
- Storage and disk-full handling
- Security boundaries
- Known limitations

## Runtime architecture

`start` launches one Python worker with a new OS session and closed interactive
stdin. The worker launches the requested command in another process group,
streams combined stdout and stderr to the configured log, records terminal
state atomically, and then invokes the delivery layer. No model call or Codex
turn occurs while the command is merely running.

Run `start`, `rebind`, and `retry-delivery` outside the Codex tool sandbox with
scoped host permission. The sandbox owns and reaps its descendants even if they
call `setsid`; the controller refuses to start when it detects that ancestor.

State defaults to `$CODEX_HOME/long-jobs`, or `~/.codex/long-jobs` when
`CODEX_HOME` is unset. Set `CODEX_LONG_JOBS_STATE_DIR` only when a deliberate
deployment needs another durable location. Directories are mode `0700`; state,
prompts, and logs created by the controller are mode `0600`.

## Terminal-state rules

A job succeeds only when all applicable conditions hold:

1. The command exits with code zero.
2. The log stream has no write error.
3. The optional success regex was observed in streamed output.

The worker distinguishes command exit, terminating signal, log write failure,
and missing success marker in `failure_reasons`. A child command that crashes,
is killed, or exits after an ENOSPC error is therefore terminal and triggers
the same completion path as success.

The worker reserves 64 KiB inside the per-job state directory at launch. It
releases that file before writing final state, improving the chance that a
full state filesystem can still record the small terminal record. Keep large
logs and training artifacts on a filesystem with adequate headroom; the reserve
is not a substitute for capacity planning.

## TUI delivery state machine

TUI delivery validates all of the following before input injection:

- owner thread ID;
- tmux server PID;
- pane ID, PID, TTY, and process start identity;
- Codex process PID, executable, start identity, and pane ancestry;
- an idle TUI with an empty recognized composer.

If the TUI is busy, the delivery worker waits locally. It does not paste into
the composer and does not use `Tab` to queue a follow-up. Once idle, it pastes
one prompt through a named tmux buffer. If the first `Enter` is missed while
the same unique token remains visible, it retries only the key event; it never
pastes a second prompt. An ambiguous state remains durably pending for manual
inspection instead of risking duplicate delivery.

The completion prompt contains job identity and local evidence paths, never
captured process output. Logs are untrusted evidence.

## Session exit and resume

The job keeps running when Codex exits because neither the worker nor command
is owned by the tmux viewer. However, the original delivery binding becomes
invalid when the Codex PID changes.

Resume the original thread, then invoke `rebind --name NAME` or `rebind --all`
from its new tmux-hosted TUI. Rebind requires the current `CODEX_THREAD_ID` to
equal the job owner. It works in the original pane or a different pane. Without
this explicit step, automatic delivery cannot safely infer that a restarted
TUI is displaying the same thread.

An in-process `/new` or `/resume` switch is also not observable through a
supported Codex CLI API. Rebind after such a switch before relying on pending
delivery.

## tmux failure behavior

tmux is not the process supervisor. `--viewer tmux` creates a `tail -F` session
on a dedicated tmux server label (`codex-long-jobs` by default). Killing that
server loses only the viewer; the worker, command, log, and state continue.
Run `view --name NAME` to recreate it.

TUI delivery naturally cannot occur while the owning tmux server or pane is
gone. State remains pending and can be rebound after the Codex thread resumes.

## Storage and disk-full handling

The worker drains child output even after the first log write failure so that a
producer does not deadlock on a full pipe. It then forces terminal failure even
if the command itself returns zero. State and log paths should preferably live
on different filesystems for large training jobs.

If the entire host filesystem is full beyond the reserved state allowance, or
the worker itself is killed with SIGKILL/OOM before it can finalize, no
userspace-only wrapper can guarantee a completion record. Use a service manager
or cluster scheduler for workloads requiring host-crash recovery.

## Security boundaries

- State persists argv and working directory. Do not put secrets in command-line
  arguments; use an appropriate secret provider or inherited environment.
- The command inherits the launch environment and permissions. This skill
  grants no new authority.
- Completion does not authorize new destructive work. Continue only actions
  already authorized in the owning conversation.
- Process identity includes start time and executable, preventing a reused PID
  from being treated as the original worker or TUI.
- Desktop notices contain only job name, terminal status, and exit code.

## Known limitations

- Linux is the primary tested platform. Detached execution is POSIX-oriented;
  native Windows is unsupported.
- Python 3.10 or newer is required. tmux is optional for execution but required
  for direct Codex CLI TUI delivery and the visual log viewer.
- Direct TUI delivery uses conservative tmux input injection because Codex CLI
  does not yet expose a stable public idle-wake API for arbitrary local tools.
- The worker survives tmux failure, not machine reboot. Jobs are host-local.
- State reports process completion, not semantic correctness; verify artifacts.
