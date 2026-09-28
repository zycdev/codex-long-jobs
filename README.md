# Codex Long Jobs: Background Jobs for OpenAI Codex CLI

[![CI](https://github.com/zycdev/codex-long-jobs/actions/workflows/ci.yml/badge.svg)](https://github.com/zycdev/codex-long-jobs/actions/workflows/ci.yml)

**Run long-running background jobs from Codex, end the current turn, and wake
the original Codex session when the process finishes.**

`codex-long-jobs` is a Codex skill and local process supervisor for OpenAI
Codex CLI. It runs long-running background jobs and other background processes
as detached OS processes. The current turn can end while a local worker waits
for process completion with no LLM polling. When the command exits, the worker
can wake the original Codex session through `codex queue`, with conservative
tmux-hosted TUI delivery retained for older Codex CLI versions.

- Detached and async job execution on Linux servers and over SSH.
- No LLM/model polling while a command is running.
- Event-driven completion notification on success, failure, or abnormal exit.
- Thread-addressed wake-up through `codex queue`, independent of tmux pane
  identity when the installed CLI supports it.
- Safe tmux-based compatibility delivery, with same-thread resume and rebind.
- Durable job state, logs, exit code, signal, and delivery status.
- Persistent log files and a log-following command for use in any terminal or IDE.

> Status: `v0.4.0` beta. Linux is the primary tested platform. Python 3.10 or
> newer is required.

tmux is not required for job execution, log inspection, or completion delivery through `codex queue`.
It is an optional dependency for the legacy TUI notification transport and the convenience tmux viewer.

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
-> original Codex session is queued or safely awakened
```

The model does not need to periodically poll the process. A multi-hour build,
training run, benchmark, test suite, data processing job, or other long-running
command can continue without consuming model turns just to check whether it has
finished. This provides asynchronous process completion handling without
keeping the agent turn open.

## How wake-up works for the original Codex session

At launch, the controller records the owner `CODEX_THREAD_ID`. When the
installed CLI supports `codex queue --thread`, automatic delivery needs no tmux
endpoint. Older CLIs retain the validated tmux pane and Codex process path. The
supervisor, worker, and child command always run outside the viewer.

```text
Original Codex thread
                    |
                    | start and record owner UUID
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
                                    codex queue --thread OWNER --message PROMPT
                                                       |
                                                       v
                                      queued normal turn in the owner thread

Older CLI compatibility branch:
validate same thread, pane, and Codex PID
-> wait for TUI idle and composer empty
-> tmux paste-buffer plus safe Enter retry
```

Queue delivery records status `queued` only after the CLI returns success. This
means the command accepted the message. It does not prove that the owner session
consumed or completed the new turn. Per-job locking suppresses normal duplicate
dispatch. A timeout or interrupted invocation remains pending with uncertain
acceptance instead of being resent automatically.

A terminal legacy TUI notification can be migrated with
`retry-delivery --use-queue` only after its old delivery worker exits and its
durable reason proves that no prompt was pasted. Ambiguous records require
explicit duplicate-risk acknowledgement.

The compatibility TUI boundary prevents a completion message from being pasted
into a busy turn or a nonempty composer. A unique token, process identities,
pane identity, and a cross-job delivery lock reduce stale-session and duplicate
submission risks. The completion prompt contains state and log paths, not
untrusted process output.

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
- **Safe concurrent delivery.** Queue delivery is serialized per job and treats
  accepted delivery as final. Compatibility TUI delivery waits for an idle TUI
  and empty composer, serializes jobs globally, and retries a missed first
  `Enter` without pasting another prompt.
- **Session recovery.** Queue delivery continues by owner thread UUID. A pending
  compatibility TUI job can be rebound after the original thread is resumed in
  the same or a different tmux pane.
- **Persistent inspection.** State, exit information, delivery status, and log paths remain available through `status`.
  Follow logs with the skill's `tail` command or open the log file in any terminal or IDE.

## Use cases for Codex background jobs on Linux and SSH

Run long builds, training jobs, tests, benchmarks, and data pipelines from
Codex without polling. Typical uses include:

- long compilation jobs, release builds, and CUDA builds;
- model training, fine-tuning, evaluation, and benchmark suites;
- unit tests, integration tests, end-to-end tests, and large test suites;
- data preprocessing, downloads, conversion, indexing, and batch processing;
- simulations, migrations, package installation, and environment setup;
- large code generation, compilation, packaging, and deployment pipelines.

The primary environment is a Linux server reached over SSH. Supported Codex CLI versions can wake the owner thread through the local app-server daemon without tmux.
Log inspection is independent of the notification transport and the window used to view the file.

## Install the Codex skill

Python 3.10 or newer is required.
Automatic queue delivery also requires a supported Codex CLI, an owner thread ID, and access to the corresponding app-server daemon.
Use `--viewer none` to run without a managed log-viewing window; log files and the `tail` command remain available.
The default viewer is `none`, including inside tmux. Explicit `--viewer tmux` creates a viewer; explicit `--viewer auto` retains the previous environment-based selection.

<details>
<summary>Optional tmux setup for legacy TUI delivery or the convenience viewer</summary>

Install tmux only if you want either of these optional features:

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

For legacy TUI delivery, verify the installation, start a tmux session, and launch Codex inside it:

```bash
tmux -V
tmux new-session -s codex
```

Then run `codex` from the shell inside the new tmux session.

</details>

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

For example, ask an OpenAI Codex CLI session to launch a multi-hour deep learning training run:

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
  --viewer none \
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

The command prints the supervisor PID, log path, log-following command, durable state path, and delivery mode.
An attach command is included only when an explicitly selected viewer starts successfully.
After a one-time running-state check,
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

# Optional: create or restore the convenience tmux log viewer
scripts/codex-long-jobs view --name train-model-run-01

# Rebind jobs after resuming the same original Codex thread
scripts/codex-long-jobs rebind --all

# Retry a terminal job whose queue or TUI delivery remains pending
scripts/codex-long-jobs retry-delivery --name train-model-run-01

# Safely migrate a legacy pending TUI notification to queue delivery
scripts/codex-long-jobs retry-delivery \
  --name train-model-run-01 \
  --use-queue

# Inspect runtime and TUI binding prerequisites
scripts/codex-long-jobs doctor
```

The launcher and `status` print the log file path; `status --json` exposes it in the `log` field.
Text output also provides a shell-quoted `log_follow` command that can be copied into any terminal on the job host.
Open that file in your preferred terminal or IDE, or inspect the example job directly:

```bash
tail -n 200 -F ./logs/train-model-run-01.log
less ./logs/train-model-run-01.log
```

These commands require no tmux session.
The optional `view` command creates a tmux window for convenience; closing a log viewer does not stop the job or remove its log.

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
| Track the owning Codex thread and optional TUI endpoint | No | No | Yes |
| Wake or resume the original Codex session on completion | No | No | Yes, through `codex queue` or a validated compatibility TUI |
| Protect against stale panes and support explicit rebind | No | No | Yes |
| Keep the command alive if the optional viewer tmux server fails | Not applicable | Usually no | Yes |

tmux remains available as a compatibility transport and optional log viewer. It
is not the process supervisor.

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
needed. Compatibility TUI delivery may use lightweight local readiness polling
to wait for an idle TUI and empty composer. This is not LLM/model polling and
does not consume model turns.

No universal percentage token saving is claimed. If the alternative is one
foreground tool call that blocks until exit without intermediate model turns,
this skill can add small fixed orchestration overhead. Comparisons should count
the same final result inspection and authorized follow-up work.

## Resume and rebind Codex sessions after process completion

The worker and command continue if the original Codex process exits. Queue-mode
jobs continue to address the recorded thread UUID and require no pane rebind.
For a pending compatibility TUI job, the old binding becomes invalid because
its Codex process identity changed. After resuming the same original thread in
any tmux pane, send Codex this prompt:

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

- `auto` selects queue delivery when the owner thread and `codex queue` are
  available, falls back to TUI delivery on older CLIs, and otherwise records
  durable event-only state.
- `queue` explicitly requests thread-addressed queue delivery.
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

No LLM/model polling occurs while the job is running. Queue delivery invokes one
completion turn after the process exits. The compatibility delivery worker may
check local tmux and TUI readiness until the owning TUI is idle and its composer
is empty. These checks are ordinary local process work and do not invoke a
model.

### How can Codex automatically continue when a background process finishes?

The worker queues a completion prompt to the recorded owner thread after the
process reaches terminal state. On older CLIs, it uses the validated tmux-hosted
TUI instead. The prompt starts a normal Codex turn and authorizes only inspection
and follow-up work already authorized by that conversation.

### Can a background process wake the original Codex CLI session?

Yes. Current CLIs accept a queued message addressed by owner thread UUID, and
host acceptance confirms delivery after the TUI exits while the app-server
daemon remains available. Older TUI-mode jobs require the original thread to be
resumed and rebound; a different thread is rejected.

### Why does codex-long-jobs still support tmux delivery?

Older Codex CLI versions do not provide `codex queue`. tmux supplies a
verifiable pane identity and controlled terminal input path for that
compatibility case. Job execution and current queue delivery are independent of
the optional tmux viewer.

### Does the job survive closing Codex?

Under normal host process policy, yes. The detached supervisor, worker, and
command continue after the Codex process exits. Queue delivery can address the
same owner thread through the persistent daemon. Compatibility TUI delivery
waits until the thread is resumed in tmux and rebound. This is not a machine
reboot guarantee.

### What happens if the original Codex session is restarted?

Queue-mode completion still targets the recorded thread UUID. For TUI-mode
delivery, resume the same thread, which preserves its `CODEX_THREAD_ID`, then
run `rebind --name NAME` or `rebind --all` from the new tmux-hosted TUI. Rebind
can target the original pane or a different pane.

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

Install the skill and start the job with scoped host permission. The detached
process is designed to continue across an ordinary SSH disconnect, subject to
the server's process and login-session policy. Current CLIs can deliver through
the app-server daemon. Keep or restore a tmux-hosted Codex TUI only when using
the compatibility transport.

### How can Codex wait for a multi-hour job without using model turns to check progress?

The detached worker waits locally for process exit and checks only durable local
cancellation state while Codex is inactive. Only actual process completion and
successful prompt delivery start the next normal Codex turn.

### Does it work with the Codex desktop client or VS Code extension?

Desktop-client and VS Code extension compatibility has not been validated.
Removing the tmux requirement does not by itself establish support for these clients.
Validation must confirm that a queued message reaches the owning session, starts the expected continuation, and appears in the client, including behavior after the client closes.
Queue acceptance alone does not establish these results.

## Safety and limitations for detached Codex jobs

- Job output is untrusted data. Completion prompts point to logs but never
  inject log contents into the TUI.
- State stores argv and working directory. Do not put secrets in argv.
- Cancellation targets the validated process group created for the command,
  not processes that deliberately escape that OS session.
- Queue command success proves acceptance, not consumption. Timeout and process
  interruption therefore remain pending when acceptance is uncertain.
- Compatibility TUI wake-up depends on Codex rendering and tmux input behavior,
  so meaningful CLI changes require acceptance retesting.
- Rebind is mandatory for TUI-mode delivery after the Codex process exits or
  the displayed thread changes. Queue-mode delivery does not use pane identity.
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
signals, worker death, queue capability and failure paths, acceptance
uncertainty, duplicate suppression, guarded migration, concurrent starts and
deliveries, tmux viewer failure, busy-TUI deferral, missed-Enter retry,
headless dispatch, notification safety, argument boundaries, path safety, and
explicit session rebind. CI runs every supported Python minor version from
3.10 through 3.14.

## License

MIT. See [LICENSE](LICENSE).
