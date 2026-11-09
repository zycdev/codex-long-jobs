# Operations and failure model

## Contents

- Runtime architecture
- Terminal-state rules
- Cancellation state machine
- Queue delivery state machine
- Unattended queue wake
- TUI delivery state machine
- Session exit and resume
- tmux failure behavior
- Storage and disk-full handling
- Security boundaries
- Known limitations

## Runtime architecture

`start` launches a detached Python supervisor with a new OS session and closed
interactive stdin. The supervisor launches and waits for the worker. The worker
launches the requested command in another process group, streams combined
stdout and stderr to the configured log, checks durable cancellation state,
records terminal state atomically, and then invokes the delivery layer. No
model call or Codex turn occurs while the command is merely running.

Log inspection uses the persistent file path and the `tail` command independently of tmux.
The default `--viewer none` creates no viewing window, even when the launcher runs inside tmux.
Human-readable start and status output include a shell-quoted `log_follow` command for any terminal on the job host; JSON status retains the `log` path.
Explicit `--viewer tmux`, `--viewer auto`, and `view` remain available for users who request a managed tmux viewer.

If the worker exits before terminal state, the supervisor terminates the
validated child process group, records `worker-exit` or `worker-signal`, and
invokes delivery. The supervisor is deliberately separate from the tmux viewer.

The worker is also detached and owns normal terminal-state and delivery work.
If the supervisor alone is killed after the worker is running, the worker and
command continue and can still complete normally. The runtime does not start a
replacement supervisor, so any later worker failure would have no watchdog.

Run `start`, `rebind`, `retry-delivery`, and `retry-wake` outside the Codex tool sandbox with
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

Command output writes handle partial writes explicitly and fsync the completed
log. State updates use atomic replacement, fsync the state file, and fsync the
parent directory. A failure of any applicable durability step prevents a
successful terminal classification.

The worker reserves 64 KiB inside the per-job state directory at launch. It
releases that file before writing final state, improving the chance that a
full state filesystem can still record the small terminal record. Keep large
logs and training artifacts on a filesystem with adequate headroom; the reserve
is not a substitute for capacity planning.

## Cancellation state machine

`cancel --name NAME` writes a durable `cancel_requested_at` value under the job
lock, releases that lock, and waits synchronously for any durable terminal
state. It never holds the lock while waiting because the worker or supervisor
must acquire the same lock to finalize the record.

For a running command, the worker validates the recorded child process group,
sends `SIGTERM`, records `cancel_effective_at` and `cancel_signal`, and waits for
the configured grace period. If any member remains, it sends `SIGKILL`. The
terminal record uses status `cancelled`, retains the command exit code or signal
when available, records `cancelled_at`, clears live child identities, and uses
the same completion delivery path as success and failure.

The controller is idempotent for terminal jobs and leaves their state bytes
unchanged. A duplicate request while the job is active reuses the first request
and grace period. If the process has already completed and no cancellation
signal becomes effective, the actual `succeeded` or `failed` result wins. A
request made before child launch suppresses launch and finalizes as
`cancelled`.

If the main command exits zero while a same-group descendant remains alive, a
later request can still signal that descendant and produce `cancelled` with
`exit_code: 0`. Consumers must use `status` and `cancel_effective_at`, not the
numeric exit code alone, to interpret cancellation. Likewise, controller exit
status zero means a durable terminal state was observed; it does not guarantee
that cancellation beat natural completion.

Cancellation remains worker-owned when the supervisor alone has exited. If the
worker dies after a request, the supervisor terminates the same validated
process group and finalizes `cancelled` when a signal became effective. The CLI
returns an error if no terminal state appears within the grace period plus a
bounded finalization allowance.

Only jobs created by runtime version 0.3.0 or newer have the process-group and
cancellation contract required by this command. The controller refuses to
cancel an older nonterminal record. Use its original runtime or inspect and
stop that workload manually.

The boundary is one OS process group inside the recorded session. A descendant
that deliberately calls `setsid`, submits work to a scheduler, or launches a
container can escape that boundary and requires its native cancellation
mechanism.

## Queue delivery state machine

For `auto` delivery, the launcher checks the selected executable locally with
`codex queue --help`. When `--thread` and `--message` are supported and an owner
thread ID is available, the job records `mode: queue` and does not capture a
tmux endpoint. Older CLIs retain the TUI transport.

At terminal state, the delivery worker serializes per-job dispatch, records a
`queue-command-running` attempt, and invokes `codex queue --thread OWNER
--message PROMPT`, adding `--resume` when the current executable advertises it.
The helper starts only after that invocation returns, avoiding simultaneous native and RPC resume attempts.
Exit code zero changes the delivery status to `queued` with
reason `queue-command-accepted`. This proves only that the CLI accepted the
message. Session-side execution and consumption are outside the transport
acknowledgement.

A nonzero command result remains pending and may be retried explicitly. A
timeout or a delivery process that restarts after recording
`queue-command-running` has uncertain acceptance. The worker records that
uncertainty and does not retransmit automatically, because the first invocation
may already have enqueued the message.
Explicit duplicate-risk acknowledgement is passed into the locked dispatcher; an earlier pending snapshot cannot overwrite a concurrent `queued` result.
Normal repeated delivery workers also
observe `queued` as final and do not send a second message.

### Executable discovery and diagnostics

Selection uses `CODEX_LONG_JOBS_CODEX_BIN` first, then a trusted executable on PATH, then installed `openai.chatgpt` extension packages under the user's standard VS Code extension directories or `VSCODE_AGENT_FOLDER`.
The extension fallback checks publisher/name metadata, installed version ordering, the host platform, executable permission, and ownership of the executable and its ancestor directories.
Automatically discovered paths must not be replaceable by other users; relative PATH entries are rejected.
An explicit override is authoritative, including when it fails, and no fallback silently substitutes another executable.
No PATH changes, installation, version switching, or daemon-package selection occur.
Unrecognized extension layouts require an explicit trusted executable override or the existing TUI/event-only fallback.
Run discovery outside a tool sandbox if its user namespace obscures filesystem ownership.

`doctor` displays the selected path, source, version, queue and native resume capabilities, resolved `CODEX_HOME`, and control-socket presence.
Unset or empty `CODEX_HOME` resolves to `~/.codex`; it does not mean the data directory is absent.
Socket presence does not prove daemon readiness, RPC compatibility, authentication, or writer-lock availability.
Diagnostics do not resume threads or restart services.
Only `doctor` prints the optional recommendation for CLI versions below 0.166.0 that support queueing but lack `--resume`.
Normal jobs do not repeat the recommendation; extension-only discovery does not recommend a mandatory standalone CLI installation or upgrade.
Feature detection, rather than version comparison, selects the execution path.

### Unattended queue wake

Native `queue --resume` performs queue addition followed by an unloaded-thread check and optional resume.
It can return success with a resume warning, so `delivery.status: queued` remains an acceptance acknowledgement only.
`delivery.queue_resume` records whether the option was used, `queue_resume_warning` recognizes the native resume warning, and `queue_diagnostics_present` records other diagnostic output without persisting arbitrary CLI stderr.
None of these fields establishes consumption or completed follow-up work.
See the [upstream implementation](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/tui/src/session_queue_commands.rs).
Isolated CLI 0.167.0 acceptance confirmed this warning behavior against both 0.167.0 and 0.164.0-alpha.11.1 servers; see the [test evidence](testing.md#native-cli-and-mixed-version-isolated-acceptance-2026-11-08) for its scope.

After acceptance, `scripts/queue_wake.py` provides wake-only recovery for both older CLIs and uncertain native outcomes.
It connects to the existing daemon's Unix control socket using WebSocket JSON-RPC.
New delivery records retain the accepted `queue_codex_home`, so explicit recovery uses the original home even if the caller's environment changes; older records fall back to the caller's resolved `CODEX_HOME`.
It never adds a message, launches a daemon, changes thread permissions or model settings, or starts a separate headless agent.
The existing supervisor participates through the same per-job lock and takes over if the worker exits between acceptance and helper launch, launch fails, or the helper exits abnormally.
Either process can acquire the lock first; the recorded wake process identity can therefore belong to the supervisor.
The same persisted deadline bounds takeover; there is no permanent additional service.
If both supervisor and helper are lost, an explicit recovery command is required.

The helper searches paginated queue results for the existing completion token.
A `notLoaded` thread is resumed even when earlier user input precedes the completion; the server consumes the persisted queue in its existing order.
If the completion remains behind earlier input, the helper waits.
For an idle thread with the completion at the head, `thread/queue/start` selects the server's current head atomically, without a submission ID that could bypass concurrent user reordering.
Active or pending turns are never interrupted. Queue races are resolved by rereading; no recovery path re-enqueues input.

Per-job `queue-wake.lock` serializes helper and supervisor recovery.
`queue-wake.json` stores the independent observation, attempt count, deadline, and process identity; helper diagnostics go to `queue-wake.log`, with supervisor takeover diagnostics in `supervisor-runtime.log`.
`status` and `status --json` expose `queue_wake` without modifying `state.json` or its schema version.
The additional JSON field is a presentation field; existing delivery and command-result fields retain their meanings.
Malformed wake evidence is reported as `wake-evidence-unreadable`; status inspection still displays the durable command result and does not rewrite either file.

| Wake status | Evidence and limits |
| --- | --- |
| `started` | A returned turn includes the target input; `turn_id` is correlated with that input. It does not establish completion. |
| `consumed-after-resume` | The observed item disappeared after resumption. Deletion or another consumer cannot be distinguished from successful processing. |
| `consumed-concurrently` | The observed item disappeared during a competing operation or a start whose response did not identify its input. |
| `absent` | The target was not found. This is not a successful continuation acknowledgement. Explicit recovery may reinspect. |
| `waiting-for-idle`, `waiting-for-earlier-input` | Safe local waiting, without interrupting a turn or bypassing input. |
| `pending`, `timed-out` | Recovery failed or exhausted its interval. Queue acceptance remains intact. |

When a start response lacks input correlation, `observed_turn_id` identifies only the observed queue-head turn, not necessarily the completion's turn.
No wake status proves that required tools were available or that the subsequent task succeeded.
Verify the actual turn, command artifacts, and logs separately.

Recovery checks local readiness at intervals of up to 15 seconds for the job's `delivery.wait_seconds` (default 24 hours), with bounded RPC calls.
Handshake and RPC transport operations use absolute deadlines, so partial reads or repeated control frames cannot extend an operation indefinitely.
The helper checks the waiting deadline before starting each new recovery attempt; an already-running bounded RPC may finish afterward.
These checks invoke no model while merely waiting; consuming the authorized notification can start model execution.
A crash resumes the original deadline. An explicit retry starts a fresh interval only after obtaining the lock; concurrent retries do not reset an active attempt.
`started` and the two disappearance observations suppress subsequent wake attempts, without claiming task success.
`absent` and `timed-out` stop automatic attempts and remain explicitly retryable.

Recover an accepted notification, including an older job without wake evidence:

```bash
scripts/codex-long-jobs status --name NAME --json
scripts/codex-long-jobs retry-wake --name NAME
```

`retry-wake` requires terminal command state and an accepted queue notification.
Its successful exit acknowledges helper launch, not completed recovery; inspect `status` for the outcome.
For compatibility, `retry-delivery` on an already-queued job also performs only wake recovery; it never resends the completion, even with duplicate-risk acknowledgement.
The low-level `python3 scripts/queue_wake.py --retry --name NAME` command provides the same bounded attempt synchronously.
Installing files does not replace a healthy worker's loaded code. Do not restart healthy jobs or change Goal state to repair notification delivery.

`retry-delivery --use-queue` can migrate a terminal legacy TUI notification
only when its durable state proves that no prompt was pasted, such as
`waiting-for-session-rebind`, a pane identity failure, a busy TUI, or a nonempty
composer. Records with a paste timestamp or an ambiguous historical reason are
refused unless the caller explicitly supplies `--allow-possible-duplicate`.
Migration is also refused while a legacy delivery worker holds the per-job
lock. Never migrate a running old worker because it retains its loaded runtime.

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
the same unique token remains visible, including when the TUI renders that
token across indented visual lines, it retries only the key event; it never
pastes a second prompt. The visibility check tolerates only rendered line
breaks and their horizontal indentation inside the exact token. An ambiguous
state remains durably pending for manual inspection instead of risking
duplicate delivery.

A state-root lock serializes the validation and submission boundary across all
jobs. Two jobs that complete together cannot both observe and paste into the
same empty composer.

The completion prompt contains job identity and local evidence paths, never
captured process output. Logs are untrusted evidence.

## Session exit and resume

The job keeps running when Codex exits because the supervisor, worker, and
command are not owned by the tmux viewer. However, the original delivery
binding becomes invalid when the Codex PID changes.

Queue delivery continues to address the recorded owner thread after the TUI
exits and does not use pane identity. Real acceptance with Codex CLI 0.157.1
confirmed consumption through the persistent app-server daemon after the
isolated TUI exited.

For TUI-mode records, resume the original thread, then invoke
`rebind --name NAME` or `rebind --all` from its new tmux-hosted TUI. Rebind
requires the current `CODEX_THREAD_ID` to equal the job owner. It works in the
original pane or a different pane. Without this explicit step, automatic TUI
delivery cannot safely infer that a restarted TUI is displaying the same
thread.

An in-process `/new` or `/resume` switch is also not observable through a
supported Codex CLI API. Rebind after such a switch before relying on pending
delivery.

## tmux failure behavior

tmux is not the process supervisor. `--viewer tmux` creates a `tail -F` session
on a dedicated tmux server label (`codex-long-jobs` by default). Killing that
server loses only the viewer; the supervisor, worker, command, log, and state
continue. Run `view --name NAME` to recreate it.

TUI delivery naturally cannot occur while the owning tmux server or pane is
gone. State remains pending and can be rebound after the Codex thread resumes.

## Storage and disk-full handling

The worker drains child output even after the first log write failure so that a
producer does not deadlock on a full pipe. It then forces terminal failure even
if the command itself returns zero. State and log paths should preferably live
on different filesystems for large training jobs.

If the entire host filesystem is full beyond the reserved state allowance, the
host reboots, or both supervisor and worker are lost, no userspace-only wrapper
can guarantee a completion record. Use a service manager or cluster scheduler
for workloads requiring host-crash recovery.

## Security boundaries

- State persists argv and working directory. Do not put secrets in command-line
  arguments; use an appropriate secret provider or inherited environment.
- The command inherits the launch environment and permissions. This skill
  grants no new authority.
- Completion does not authorize new destructive work. Continue only actions
  already authorized in the owning conversation.
- Process identity includes start time and executable, preventing a reused PID
  from being treated as the original worker or TUI.
- Cancellation validates both process-group and OS-session identity before
  signaling, reducing stale process-group reuse risk.
- Desktop notices contain only job name, terminal status, and exit code.

## VS Code client recovery

The tested Linux Remote-SSH extension uses queue delivery without tmux.
The selected executable must support `codex queue`; it can come from PATH or the trusted extension fallback.
The extension App Server and shared daemon are distinct processes, even when they share `CODEX_HOME`.
Their versions need not match, and successful CLI feature detection does not prove compatible daemon RPCs.
Inspect `doctor` and the recorded delivery mode when diagnosing a new installation.
Use `event-only` if automatic queue delivery is unavailable and no validated TUI endpoint exists.

In the September 2026 acceptance, jobs completed and their messages were accepted before the project window or remote connection was reopened.
On reconnecting, the user observed a brief `steer` state followed by history insertion and continuation in the original conversation.
A `queued` record must not be retried merely because the interface is closed or its message has not yet appeared.
Inspect durable job and wake state first; `retry-wake` can safely retry recovery without adding input.
Reopen the original conversation when tools, authentication, or writer-lock ownership require its original client.
If a connection failure or backend error requires Reload Window, restore the connection or reload, then inspect the original conversation before retrying delivery.
Client-side proxy failure, extension app-server termination, and Server main-process termination recovered this way in the tested environment.
These findings do not establish recovery from recursive process-tree loss, shared Codex daemon termination, or server-side network loss.
If the original extension App Server exits while a compatible shared daemon remains, the helper can attempt a cold resume.
If Remote-SSH disconnects while the original App Server remains alive, that server may still own the thread's writer lock.
A competing resume must fail safely and remain pending; do not kill the owner or bypass its lock.
If both servers are unavailable, the helper preserves acceptance and waits within its deadline; it does not create a replacement daemon.
A loaded thread may already be consuming its queue, in which case recovery must defer to that consumer.
The helper answers server-initiated tool or approval requests with `-32601`, records encountered method names without their arguments, and never grants approval or fabricates tool results.
Requests after the helper disconnects are outside its observation window.
Client-provided tools can therefore prevent a resumed turn from completing after the client closes.
The September results establish recovery after reconnection.
In the supervised November 2026 tests, the development runtime with CLI 0.164.0, extension 0.164.0-alpha.11.1, and daemon 0.167.0 produced actual tool-verification receipts before window reopening and before remote reconnection.
The subsequent CLI 0.167.0 comparison preserved the extension and daemon versions and also verified those continuation boundaries, with initially unloaded threads and no native resume warnings in the closed-window and disconnected cases.
Those successful cases require the observed host, daemon, and tool availability; they do not remove the failure boundaries above.
See the [supervised acceptance records](testing.md#staged-supervised-acceptance-2026-11-08) and the [historical VS Code record](testing.md#recorded-vs-code-remote-ssh-acceptance) for their distinct scopes.

## Known limitations

- Linux is the primary tested platform. Detached execution is POSIX-oriented;
  native Windows is unsupported.
- Python 3.10 or newer is required. tmux is optional for execution but required
  for direct Codex CLI TUI delivery and the visual log viewer.
- Linux validates surviving process-group members against both group and
  session identity. The non-Linux fallback can validate only the original group
  leader, so cancellation after that leader exits is not guaranteed there.
- Queue delivery requires a Codex CLI that exposes `codex queue --thread` and a
  reachable local app-server daemon. The command acknowledgement does not prove
  session consumption.
- Direct TUI delivery remains a conservative compatibility transport for older
  Codex CLIs and explicitly selected TUI mode.
- The supervisor and worker survive tmux failure, not machine reboot. Jobs are
  host-local.
- A running worker survives supervisor-only loss, but there is no automatic
  supervisor replacement.
- After `SIGKILL`, the worker keeps a nonterminal record while any non-zombie
  group member remains. An uninterruptible process can therefore outlive the
  CLI cancellation timeout instead of being falsely reported as stopped.
- A queued job whose supervisor dies before it starts a worker remains stranded
  and needs manual host inspection; no live component can observe its request.
- State reports process completion, not semantic correctness; verify artifacts.
