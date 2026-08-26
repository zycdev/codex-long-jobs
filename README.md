# Codex Long Jobs: Background Jobs for OpenAI Codex CLI

[![CI](https://github.com/zycdev/codex-long-jobs/actions/workflows/ci.yml/badge.svg)](https://github.com/zycdev/codex-long-jobs/actions/workflows/ci.yml)

**Run long-running background jobs from Codex, end the current turn, and wake
the original Codex session when the process finishes.**

`codex-long-jobs` is a Codex skill and local process supervisor for OpenAI
Codex CLI. It runs long-running background jobs and other background processes
as detached OS processes. The current turn can end while a local worker waits
for process completion with no LLM polling. When the command exits, the worker
can wake the original Codex session by safely delivering a completion prompt to
its tmux-hosted Codex TUI.

- Detached and async job execution on Linux servers and over SSH, independent
  of the optional tmux viewer.
- No LLM/model polling while a command is running.
- Event-driven completion notification on success, failure, or abnormal exit.
- Safe tmux-based wake-up of the owning Codex TUI, with same-thread resume and
  rebind.
- Durable job state, logs, exit code, signal, and delivery status.
- An optional tmux log viewer that never owns the supervised process.

> Status: `v0.3.0` beta. Linux is the primary tested platform. Python 3.10 or
> newer is required.

## Why codex-long-jobs? Run long-running processes without model polling

A traditional agent workflow for a long build, training run, test suite, or
benchmark often looks like this:

```text
start process
-> periodically check status
-> another model turn
-> sleep
-> check again
```

Those progress-only turns spend tokens and add noise to the conversation even
though nothing yet requires model judgment. `codex-long-jobs` changes the flow:

```text
start detached job
-> Codex turn ends
-> worker waits locally
-> process exits
-> completion event
-> original Codex TUI is safely awakened
```

The model does not need to periodically poll the process. A multi-hour build,
training run, benchmark, test suite, data processing job, or other long-running
command can continue without consuming model turns just to check whether it has
finished. This provides asynchronous process completion handling without
keeping the agent turn open.

## How wake-up works for the original Codex TUI

At launch, the controller records the owner `CODEX_THREAD_ID` and a validated
endpoint for the owning tmux pane and Codex process. It then starts a detached
supervisor, worker, and child command outside the tmux viewer.

```text
Original Codex thread and owning tmux pane
                    |
                    | start and capture identity
                    v
       detached supervisor -> detached worker -> child process
                    |                 |                |
                    |                 |         runs for minutes or hours
                    |                 |                |
                    |                 +---- observe exit or cancel request
                    |                                  |
                    +---- detects worker failure       v
                                      durable state, log, exit code or signal
                                                       |
                                                       v
                                    validate same thread, pane, and Codex PID
                                                       |
                                   wait for TUI idle and composer empty
                                                       |
                                                       v
                                   tmux paste-buffer plus safe Enter retry
                                                       |
                                                       v
                                      new normal turn in the owning Codex TUI
```

The safe delivery boundary prevents a completion message from being pasted into
a busy turn or a nonempty composer. A unique token, process identities, pane
identity, and a cross-job delivery lock reduce stale-session and duplicate
submission risks. The completion prompt contains state and log paths, not
untrusted process output.

Codex CLI currently lacks a stable public API for arbitrary local processes to
wake an existing interactive TUI session, so `codex-long-jobs` uses safe tmux
input injection for live TUI delivery. The prompt is loaded through a named
tmux paste buffer, verified as visible, and submitted only at the safe boundary.

This is event-driven from the model's perspective: the model is not invoked
until the background process has actually completed and a completion message is
delivered. The delivery worker can perform lightweight local TUI and tmux
readiness checks. Those local checks do not invoke Codex and are not LLM/model
polling.

## Background job and detached process capabilities

- **Durable execution.** The supervisor, worker, and command use independent OS
  sessions with closed interactive stdin, so an ordinary Codex turn or tmux log
  viewer does not own the job.
- **Completion on success and failure.** Nonzero exits, signals, missing success
  markers, log write failures, and unexpected worker exit all become terminal
  state and use the same completion delivery path.
- **Worker crash detection.** A separate supervisor detects worker failure,
  stops the validated orphaned process group, records the failure, and triggers
  delivery.
- **Storage failure handling.** Partial writes and fsync failures are detected.
  A small reserved state file improves the chance of recording failure when the
  state filesystem is nearly full.
- **Safe concurrent delivery.** Completion waits for an idle TUI and empty
  composer. A global lock serializes jobs that finish at the same time, and a
  missed first `Enter` is retried without pasting another prompt.
- **Session recovery.** A pending job can be rebound after the original Codex
  thread is resumed in the same or a different tmux pane.
- **Persistent inspection.** State, logs, exit information, and delivery status
  remain available through `status`, `tail`, and an optional disposable tmux
  viewer.

## Use cases for Codex background jobs on Linux and SSH

Run long builds, training jobs, tests, benchmarks, and data pipelines from
Codex without polling. Typical uses include:

- long compilation jobs, release builds, and CUDA builds;
- model training, fine-tuning, evaluation, and benchmark suites;
- unit tests, integration tests, end-to-end tests, and large test suites;
- data preprocessing, downloads, conversion, indexing, and batch processing;
- simulations, migrations, package installation, and environment setup;
- large code generation, compilation, packaging, and deployment pipelines.

The primary environment is a Linux server reached over SSH, with Codex CLI
running in tmux when automatic live TUI wake-up is wanted.

## Install the Codex skill

Python 3.10 or newer is required. tmux is required for live wake-up of the
original Codex CLI TUI and for the optional log viewer. Install tmux with one
of these methods:

```bash
# Ubuntu or Debian
sudo apt update && sudo apt install tmux

# Conda
conda install conda-forge::tmux

# Homebrew on macOS or Linux
brew install tmux

# Pixi, installed globally from conda-forge
pixi global install --channel conda-forge tmux
```

Verify the installation, start a tmux session, and launch Codex inside it:

```bash
tmux -V
tmux new-session -s codex
```

Then run `codex` from the shell inside the new tmux session.

Without tmux, detached execution, durable state, and non-TUI delivery modes
remain available, but the skill cannot wake an already-open Codex TUI.

From an existing Codex session, a new user can ask Codex to perform the
installation:

```text
Install https://github.com/zycdev/codex-long-jobs as a user Codex skill and
tell me when it is ready.
```

After Codex completes the installation, the new skill is available on the next
turn. Contributors and other users who keep a working clone should install a
fixed release worktree instead of linking Codex directly to the active checkout:

```bash
git clone https://github.com/zycdev/codex-long-jobs.git \
  "$HOME/workspaces/codex-long-jobs"
cd "$HOME/workspaces/codex-long-jobs"
python scripts/install_skill.py install             # dry run
python scripts/install_skill.py install --yes       # link the local main commit
python scripts/install_skill.py verify
```

The installer is offline. It creates a detached worktree under
`${XDG_STATE_HOME:-$HOME/.local/state}/codex-long-jobs/release`, records the
resolved commit, and links `${CODEX_HOME:-$HOME/.codex}/skills/codex-long-jobs`
to it. Development changes do not affect the installed skill until the installer
is run again. Use `--ref TAG_OR_COMMIT` to pin or roll back explicitly.

Restart Codex, then confirm the skill appears in `/skills`.
Private job state defaults to `${CODEX_HOME:-$HOME/.codex}/long-jobs`.

## Codex CLI example: run deep learning model training in the background

For example, ask a tmux-hosted OpenAI Codex CLI session to launch a multi-hour
deep learning training run:

```text
Run my model training with $codex-long-jobs without model polling. Wake this
session when it finishes and verify the checkpoint.
```

The corresponding controller command can look like this:

```bash
SKILL_ROOT="${CODEX_HOME:-$HOME/.codex}/skills/codex-long-jobs"

"$SKILL_ROOT/scripts/codex-long-jobs" start \
  --name train-model-run-01 \
  --log ./logs/train-model-run-01.log \
  --success-pattern '^TRAINING_COMPLETE$' \
  --delivery auto \
  --viewer auto \
  -- bash -c '
    python train.py \
      --config configs/train.yaml \
      --output-dir checkpoints/run-01 &&
    test -f checkpoints/run-01/final.pt &&
    printf "TRAINING_COMPLETE\n"
  '
```

Codex must run `start` with scoped host permission. Its normal tool sandbox
reaps detached descendants when the tool call ends, so the controller detects
that condition and refuses to report a false detached launch.

The command prints the supervisor PID, log path, durable state path, delivery
mode, and optional viewer attach command. After a one-time running-state check,
let the Codex turn end. The local worker handles process completion. Success
patterns use multiline regular-expression semantics, so `^TRAINING_COMPLETE$`
matches one complete log line after any earlier output. In this example, the
marker is emitted only after training exits successfully and the expected final
checkpoint exists.

## Job lifecycle commands: status, cancel, logs, delivery, and rebind

```bash
# Show durable state, exit code, and delivery status
scripts/codex-long-jobs status --name train-model-run-01

# Follow the persistent log in the current terminal
scripts/codex-long-jobs tail --name train-model-run-01

# Request cancellation and wait for durable terminal state
scripts/codex-long-jobs cancel --name train-model-run-01

# Create or restore the disposable tmux log viewer
scripts/codex-long-jobs view --name train-model-run-01

# Rebind jobs after resuming the same original Codex thread
scripts/codex-long-jobs rebind --all

# Retry a terminal job whose TUI delivery remains pending
scripts/codex-long-jobs retry-delivery --name train-model-run-01

# Inspect runtime and TUI binding prerequisites
scripts/codex-long-jobs doctor
```

For a shorter user-facing workflow, ask Codex:

```text
Cancel train-model-run-01 with $codex-long-jobs and report its final state.
```

`cancel` records a durable request, waits for the worker to stop the complete
recorded process group, and returns only after the job reaches a terminal
state. The worker sends `SIGTERM`, waits up to 10 seconds by default, and then
uses `SIGKILL` if any member of the process group remains. An effective request
ends as `cancelled` and follows the normal completion delivery path, so the
owning Codex session can wake and inspect the result. If the command has already
finished before any cancellation signal takes effect, its actual `succeeded`
or `failed` result wins.

Use `--grace-seconds SECONDS` to change the graceful-stop interval. Cancellation
is process-group scoped; a child that deliberately escapes into another OS
session, a scheduler allocation, or a container needs its own cancellation
mechanism. Do not reuse a job name. Choose a new unique name so its durable
history remains available.

Lifecycle inspection is exposed through `status`, including stable JSON with
`--json`, plus the persistent state and log files. There is no separate
`result` command because terminal results are already part of the same durable
job record.

Exit status zero from `cancel` means the request reached a durable terminal
state, not necessarily that a signal stopped the command. Read the printed
`status` and `cancel_effective_at` fields, or use `status --json`, to distinguish
effective cancellation from an already-completed job whose actual result won.

## How codex-long-jobs differs from nohup, tmux, and command &

`nohup command &`, a shell background process, or an ordinary tmux session can
keep a command running. By themselves, they do not notify and wake the owning
Codex conversation when the process completes.

| Capability | `nohup` or `command &` | Ordinary tmux job | `codex-long-jobs` |
| --- | --- | --- | --- |
| Keep a command outside the current Codex turn | Yes, with correct shell handling | Yes | Yes, with a detached worker and supervisor |
| Durable lifecycle metadata, exit status, and failure reasons | Manual | Manual | Built in |
| Persistent combined log and optional success marker | Manual | Manual | Built in |
| Track the owning Codex thread and TUI endpoint | No | No | Yes |
| Wake or resume the original Codex session on completion | No | No | Yes, for a validated tmux-hosted Codex TUI |
| Protect against stale panes and support explicit rebind | No | No | Yes |
| Keep the command alive if the optional viewer tmux server fails | Not applicable | Usually no | Yes |

tmux remains important for direct Codex TUI wake-up, but it is a transport and
optional log viewer, not the process supervisor.

## How codex-long-jobs differs from model polling

Model polling repeatedly invokes Codex to discover that a process is still
running:

```text
Codex -> sleep -> status -> Codex -> sleep -> status
```

This project waits in a local process instead:

```text
Codex -> start
worker -> wait locally for exit or an explicit cancel request
process exits or is cancelled
worker -> deliver completion
Codex wakes
```

The worker also checks durable cancellation requests while the process is
running. These checks are lightweight local process supervision, not model
turns.

The detached worker itself uses no model tokens. Starting the job and handling
its completion are normal Codex turns, but progress-only model turns are not
needed. Delivery may use lightweight local readiness polling to wait for an
idle TUI and empty composer. This is not LLM/model polling and does not consume
model turns.

No universal percentage token saving is claimed. If the alternative is one
foreground tool call that blocks until exit without intermediate model turns,
this skill can add small fixed orchestration overhead. Comparisons should count
the same final result inspection and authorized follow-up work.

## Resume and rebind Codex sessions after process completion

The worker and command continue if the original Codex process exits. The old
TUI binding then becomes invalid because its Codex process identity changed.
After resuming the same original thread in any tmux pane, send Codex this
prompt:

```text
I resumed the original Codex thread in this tmux pane. Use $codex-long-jobs to
rebind its pending jobs to this TUI without polling them.
```

The skill performs the equivalent controller action:

```bash
scripts/codex-long-jobs rebind --all
```

Rebind requires the current `CODEX_THREAD_ID` to match the job owner. It can be
done before or after process completion. Without rebind, the completion event
remains durably pending. Returning to the old pane alone is not sufficient, and
a different Codex thread cannot claim the job.

## Completion delivery modes

- `auto` selects direct TUI delivery when an owner thread is available;
  otherwise it records durable event-only state.
- `tui` targets the validated tmux-hosted Codex CLI TUI.
- `event-only` records completion without starting another Codex turn.
- `headless` starts a separate `codex exec resume` only when explicitly
  authorized. It does not guarantee live repainting of an already open TUI.

## FAQ: Codex background jobs and session wake-up

### How can I run a long-running command in Codex without polling it?

Ask Codex to use this skill, or invoke `codex-long-jobs start` with a unique job
name and command. After confirming the detached job reached `running`, end the
turn. The local worker waits for completion without using model turns to check
progress.

### Does codex-long-jobs use LLM polling?

No LLM/model polling occurs while the job is running. After completion, the
delivery worker may check local tmux and TUI readiness until the owning TUI is
idle and its composer is empty. These checks are ordinary local process work
and do not invoke a model.

### How can Codex automatically continue when a background process finishes?

For a validated tmux-hosted Codex CLI session, the worker submits a completion
prompt after the process reaches terminal state. That prompt starts a normal
Codex turn in the owning conversation. The completion message authorizes only
inspection and follow-up work already authorized by that conversation.

### Can a background process wake the original Codex CLI session?

Yes, when the original Codex TUI is running in the recorded tmux endpoint and
still shows the owning thread. If Codex restarted, resume the same thread and
run `rebind`; a different thread is rejected.

### Why does codex-long-jobs use tmux?

Codex CLI has no stable public API for an arbitrary local process to wake an
existing interactive TUI. tmux provides a verifiable pane identity and a
controlled terminal input injection path. Job execution itself is independent
of the optional tmux viewer.

### Does the job survive closing Codex?

Under normal host process policy, yes. The detached supervisor, worker, and
command continue after the Codex process exits. Live delivery waits until the
same original thread is resumed in tmux and explicitly rebound. This is not a
machine reboot guarantee.

### What happens if the original Codex session is restarted?

Resume the same thread, which preserves its `CODEX_THREAD_ID`, then run
`rebind --name NAME` or `rebind --all` from the new tmux-hosted TUI. Rebind can
target the original pane or a different pane.

### Is this the same as nohup or command &?

No. Those tools can detach a process, but they do not add Codex owner identity,
durable lifecycle state, abnormal-exit classification, safe completion
delivery, original-session wake-up, or stale-session rebind protection.

### Can Codex cancel a long-running background job?

Yes. Ask Codex to cancel the named job, or run
`codex-long-jobs cancel --name NAME`. The worker terminates the recorded process
group, escalates from `SIGTERM` to `SIGKILL` after the grace period when needed,
persists `cancelled`, and sends the usual completion notification. Cancellation
must be explicit; the skill does not stop a healthy job merely because Codex or
the tmux viewer exits.

### How do I run background jobs from Codex CLI on a Linux server over SSH?

Run Codex CLI inside tmux, install the skill, and start the job with scoped host
permission. The detached process is designed to continue across an ordinary
SSH disconnect, subject to the server's process and login-session policy. Keep
or restore a tmux-hosted Codex TUI for live wake-up.

### How can Codex wait for a multi-hour job without using model turns to check progress?

The detached worker waits locally for process exit and checks only durable local
cancellation state while Codex is inactive. Only actual process completion and
successful prompt delivery start the next normal Codex turn.

### Does it work with the Codex VS Code extension?

No live wake support is claimed for the Codex VS Code extension. The current
automatic TUI wake path specifically targets OpenAI Codex CLI running inside
tmux. Event-only job execution is separate from extension UI synchronization.

## Safety and limitations for detached Codex jobs

- Job output is untrusted data. Completion prompts point to logs but never
  inject log contents into the TUI.
- State stores argv and working directory. Do not put secrets in argv.
- Cancellation targets the validated process group created for the command,
  not processes that deliberately escape that OS session.
- Direct TUI wake-up depends on current Codex TUI rendering and tmux input
  behavior, so meaningful Codex CLI changes require acceptance retesting.
- Rebind is mandatory after the Codex process exits or the displayed thread
  changes. Simply returning to the old pane is insufficient.
- The worker can finish and deliver after the supervisor alone is killed once
  the job is running. No replacement supervisor is created, so a later worker
  failure would no longer be detected.
- Jobs do not survive machine reboot or loss of both supervisor and worker. Use
  systemd, a cluster scheduler, or another service manager when host-crash
  recovery is required.
- Linux is the primary tested platform. Native Windows is unsupported.
- Process completion is not semantic artifact validation. The completion turn
  must inspect state, logs, and the requested outputs before claiming success.

The project addresses the event-driven wake use case discussed in OpenAI Codex
[#29922](https://github.com/openai/codex/issues/29922). A native Codex wake
mechanism would be preferable if a stable public interface becomes available.

See [operations and failure modes](references/operations.md) for recovery and
security details. See [testing and release acceptance](references/testing.md)
for the verified test matrix and environmental boundaries.

## Testing and development for the Codex skill

```bash
scripts/smoke_test.sh
scripts/stress_test.sh 10
scripts/coverage_test.sh
python3 /path/to/skill-creator/scripts/quick_validate.py .
```

The automated suite covers lifecycle, kernel and simulated write failures,
signals, worker death, concurrent starts and deliveries, tmux viewer failure,
busy-TUI deferral, missed-Enter retry, headless dispatch, notification safety,
argument boundaries, path safety, and explicit session rebind. CI runs every
supported Python minor version from 3.10 through 3.14.

## License

MIT. See [LICENSE](LICENSE).
