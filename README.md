# Codex Long Jobs: Background Jobs and Automatic Session Wake-Up

[![CI](https://github.com/zycdev/codex-long-jobs/actions/workflows/ci.yml/badge.svg)](https://github.com/zycdev/codex-long-jobs/actions/workflows/ci.yml)

**Run long-running tasks in the background with no model polling, then wake the original Codex session to inspect results and continue authorized work.**

`codex-long-jobs` is a skill and local process supervisor for OpenAI Codex CLI and the Codex extension for VS Code.
It supports unattended continuation on tested Linux hosts, including Remote-SSH: start a job, let the current turn end, and receive a completion-triggered turn in the same conversation.
Native `codex queue --resume` is preferred when available; an RPC helper retains compatibility and wake-only recovery.

> **v0.5.0 beta.** Requires Python 3.10+; Linux is the primary tested platform.
> Automatic continuation depends on a reachable compatible Codex daemon and access to the original thread and required tools.
> tmux is optional. See [compatibility and requirements](#compatibility-and-requirements).

## Quick Start

### 1. Install the skill

In a Codex conversation on the host where your jobs will run, paste:

```text
Install https://github.com/zycdev/codex-long-jobs as a user Codex skill and verify the installation.
```

Restart Codex if the skill is not discovered, then confirm it appears in `/skills`.
For an installation from a local clone, see [manual installation and updates](#manual-installation-and-updates).

### 2. Run a background task

For example, in a model training project where Codex already knows the training command and configuration, ask:

```text
Run my model training in the background with $codex-long-jobs, then check the final checkpoint.
```

Codex starts the detached job, reports its name and log path, verifies the running state once, and ends the turn.
The local worker waits for completion; the resulting Codex turn checks the evidence and performs only follow-up work you have authorized.

You can continue interacting with Codex in the same conversation while training runs, including starting additional background jobs.
Each job can wake that conversation when it finishes.
With queue delivery, completion notifications wait for an active turn to finish and respect earlier queued input.

Codex must launch the job with scoped host permission because its normal tool sandbox can reap detached descendants when the tool call ends.
If automatic delivery is unavailable, the job can still record its result, but it will not wake the conversation automatically.

### 3. Inspect or cancel when needed

You can open the reported log file in any terminal or editor.
For an on-demand check, ask:

```text
Use $codex-long-jobs to show the state and log path of the job you started in this conversation.
```

To stop it explicitly:

```text
Use $codex-long-jobs to cancel the job you started in this conversation and report its final state.
```

Closing a log viewer does not cancel the job.
A successful command still needs its outputs checked before Codex reports the task as complete.

**Explore:** [Why use it?](#why-codex-long-jobs) · [Features](#key-features) · [Compatibility](#compatibility-and-requirements) · [How it works](#how-session-wake-up-works) · [Advanced usage](#advanced-usage-and-recovery) · [FAQ](#faq) · [Testing](#testing-and-contributing)

## Why codex-long-jobs?

Long builds, test suites, model training, evaluations, and data pipelines often need little attention while they run.
Keeping an agent active just to ask whether a command has finished adds progress-only model turns.
This skill delegates waiting to a local process and returns control to Codex when there is a result to inspect.

```text
Codex starts the job and ends its turn
    -> detached worker waits locally
    -> command finishes, fails, or is cancelled
    -> completion is delivered to the original thread
    -> Codex inspects results and continues authorized work
```

Ordinary background-process tools can keep commands running, but do not themselves connect completion to the owning Codex conversation.

| Capability | `nohup` / shell background job | Ordinary tmux job | `codex-long-jobs` |
| --- | --- | --- | --- |
| Keep a command running independently of the current turn | With appropriate shell and host policy | Yes | Detached worker and supervisor |
| Retain structured exit status, failure reasons, and log paths | Manual setup | Manual setup | Built in |
| Notify and wake the original Codex thread | Manual integration | Manual integration | Queue delivery and wake recovery, subject to client availability |
| Keep running after the log viewer closes | Depends on setup | Depends on session lifecycle | Yes; the viewer does not own the job |

The worker uses no model tokens while waiting.
Starting the job and handling completion remain normal Codex turns.
There is no universal token-saving claim: a single foreground tool call that blocks until exit may already avoid intermediate model turns.

## Key Features

- **Original-session wake-up.** Completion targets the recorded Codex thread, with native `queue --resume` preferred and RPC compatibility recovery retained.
- **No model polling.** Local processes supervise execution and wait for delivery readiness without progress-only model calls.
- **Durable results.** Persistent logs and structured state retain exit codes, signals, failure reasons, and delivery status on success or failure.
- **Separate delivery and wake recovery.** Accepted queue notifications are not retransmitted. `retry-wake` retries recovery without adding another message.
- **Explicit cancellation.** Stop the validated command process group with graceful termination and bounded escalation.
- **Failure handling.** A supervisor detects worker loss; log-write errors and missing optional success markers prevent a false success result.
- **Terminal-independent logs.** Read the log in a terminal or IDE, or request an optional tmux viewer.

## Compatibility and Requirements

| Component | Requirement or tested boundary |
| --- | --- |
| Host | Linux is the primary tested platform. Jobs are host-local and subject to host process policy. Native Windows is unsupported; other platforms do not have equivalent acceptance coverage. |
| Python | Python 3.10+; no third-party Python runtime dependencies. |
| Codex CLI or VS Code extension | Queue delivery needs an owner thread ID and a selected executable supporting `codex queue --thread`. Unattended continuation also needs a compatible reachable daemon, thread access, and required tools. |
| Native resume | Used when the selected executable advertises `queue --resume`. Older queue-capable CLIs can use the RPC compatibility path. |
| tmux | Optional for the log viewer and required only for legacy TUI delivery. It does not supervise the job. |
| Launch permission | Scoped host execution outside the Codex tool sandbox. |

Executable discovery checks `CODEX_LONG_JOBS_CODEX_BIN` first, then a trusted PATH CLI, then a trusted VS Code extension binary.
A standalone CLI is unnecessary when the extension fallback works.
The extension App Server and shared daemon are distinct processes and can have different versions.

To inspect the selected executable and its actual capabilities:

```bash
SKILL_ROOT="${CODEX_HOME:-$HOME/.codex}/skills/codex-long-jobs"
"$SKILL_ROOT/scripts/codex-long-jobs" doctor
```

For a standalone CLI below 0.166.0 without native resume, `doctor` can recommend an optional upgrade.
It does not install software or change the default CLI, and normal jobs do not repeat the recommendation.
Execution uses detected capabilities rather than version numbers alone.

### Verified VS Code Remote-SSH continuation

The November 2026 acceptance covered online and busy conversations, an unselected conversation, deliberate window closure, and deliberate Remote-SSH disconnection.
With CLI 0.167.0, extension binary 0.164.0-alpha.11.1, and daemon 0.167.0, actual tool verification completed **before reopening the window or reconnecting** in the closed-window and disconnected cases.
The maintainer confirmed the resulting replies in the original conversation after returning.
Earlier November tests also verified continuation with the older CLI 0.164.0 compatibility path.

These are specific tested environments, not a guarantee for arbitrary network failures or client shutdowns.
A surviving App Server may retain the thread's writer lock; missing client-provided tools, authentication, or an unavailable daemon can prevent continuation.
The helper does not replace a daemon, bypass a writer lock, or grant approval.
See the [supervised acceptance records](references/testing.md#staged-supervised-acceptance-2026-11-08) and [VS Code recovery limits](references/operations.md#vs-code-client-recovery).

## How Session Wake-Up Works

At launch, the controller records the owner `CODEX_THREAD_ID` and starts a detached supervisor, worker, and command outside tmux.
After the command reaches terminal state, it persists the result and sends a completion prompt containing state and log paths to the original thread.
Process output is treated as untrusted evidence and is not inserted into that prompt.

When supported, delivery uses `codex queue --thread OWNER --message PROMPT --resume`.
After acceptance, a detached RPC helper provides wake-only recovery through the existing local daemon, including for older queue-capable CLIs and uncertain native-resume outcomes.
Recovery preserves earlier queued input and does not interrupt active turns.
The existing supervisor can recover a failed helper launch or abnormal helper exit.

**Queue acceptance, turn startup, and completed follow-up work are separate facts:**

| Observation | What it establishes |
| --- | --- |
| `delivery.status: queued` | The CLI accepted the completion message. It does not prove the session consumed it. |
| `queue_wake.status: started` | A correlated turn started with the target input. Its work may still fail. |
| `queue_wake.status: absent` or a consumption observation | The item was not found or disappeared. This alone does not prove successful processing. |
| Verified follow-up results | The actual continuation inspected state, logs, and required artifacts. This establishes task-specific success. |

Accepted notifications are not resent, even when the interface has not displayed them.
Timeouts or interrupted queue invocations with uncertain acceptance remain pending for inspection rather than automatic retransmission.
Use `status --json` for command results and the independent `queue_wake` observation.

On older CLIs without queue support, compatibility delivery validates the original tmux-hosted TUI and waits for an idle turn and empty composer.
Local readiness checks do not invoke a model.
See [queue delivery](references/operations.md#queue-delivery-state-machine), [unattended wake recovery](references/operations.md#unattended-queue-wake), and [TUI delivery](references/operations.md#tui-delivery-state-machine) for the full contracts.

## Advanced Usage and Recovery

### Training with artifact checks

For the training task in [Quick Start](#2-run-a-background-task), the corresponding controller command can look like this.
Replace the training script, configuration, and checkpoint paths with those in your project; choose a new job name for every run.

```bash
SKILL_ROOT="${CODEX_HOME:-$HOME/.codex}/skills/codex-long-jobs"
"$SKILL_ROOT/scripts/codex-long-jobs" start \
  --name train-model-run-01 \
  --log ./logs/train-model-run-01.log \
  --success-pattern '^TRAINING_COMPLETE$' \
  --delivery auto \
  --viewer none \
  -- bash -c '
    python3 train.py \
      --config configs/train.yaml \
      --output-dir checkpoints/run-01 &&
    test -f checkpoints/run-01/final.pt &&
    printf "TRAINING_COMPLETE\n"
  '
```

The marker is emitted only after training succeeds and the checkpoint exists.
Success patterns use multiline regular-expression semantics, so `^TRAINING_COMPLETE$` matches one complete log line; omit the pattern only when exit code zero is sufficient.
Checkpoint existence is still not semantic validation of the model or its evaluation results.

### Status, logs, and cancellation

Using the installed skill path above:

```bash
# Inspect command results and independent delivery/wake evidence.
"$SKILL_ROOT/scripts/codex-long-jobs" status --name train-model-run-01 --json

# Follow the log in your own terminal.
"$SKILL_ROOT/scripts/codex-long-jobs" tail --name train-model-run-01

# Explicitly stop the recorded process group and wait for terminal state.
"$SKILL_ROOT/scripts/codex-long-jobs" cancel --name train-model-run-01

# Optional: create or restore a tmux log viewer.
"$SKILL_ROOT/scripts/codex-long-jobs" view --name train-model-run-01
```

`start` and `status` report the log path and a copyable `log_follow` command.
The default `--viewer none` opens no managed window, including inside tmux.

Cancellation sends `SIGTERM`, then escalates to `SIGKILL` after 10 seconds by default; `--grace-seconds` changes that interval.
If natural completion wins before a signal takes effect, its actual result is retained.
A zero exit from `cancel` means a durable terminal state was reached, not necessarily effective cancellation; inspect `status` and `cancel_effective_at`.
Processes that escape the recorded OS session, scheduler jobs, and containers require their own cancellation mechanisms.

### Recover an accepted notification

If a terminal job is already `queued`, inspect its state and the original conversation, then retry only the wake step when needed:

```bash
"$SKILL_ROOT/scripts/codex-long-jobs" retry-wake --name train-model-run-01
```

Successful command exit confirms helper launch, not completed continuation.
The helper waits locally within a bounded interval (24 hours by default) and does not resend the accepted message.
Installing an update does not replace code already loaded by running workers; do not restart a healthy job to update notification behavior.

For pending legacy TUI delivery, resume the same original Codex thread inside tmux and run:

```bash
"$SKILL_ROOT/scripts/codex-long-jobs" rebind --all
```

Queue-mode jobs do not require pane rebind.
For unaccepted pending delivery, `retry-delivery --name NAME` retries delivery subject to its safety checks.
Migration with `retry-delivery --name NAME --use-queue` requires the old delivery worker to have exited and durable evidence that no prompt was pasted; ambiguous records require explicit duplicate-risk acknowledgement.
Read [session recovery](references/operations.md#session-exit-and-resume) and [queue recovery](references/operations.md#unattended-queue-wake) before intervening.

### Choose a delivery mode

| Mode | Behavior |
| --- | --- |
| `auto` | Prefer queue delivery when supported and an owner is known; otherwise use the validated legacy TUI path or durable event-only state. |
| `queue` | Request thread-addressed queue delivery explicitly. |
| `tui` | Require the validated tmux-hosted Codex CLI TUI. |
| `event-only` | Record completion without starting another Codex turn. |
| `headless` | Start a separate `codex exec resume` only with explicit authorization; live TUI synchronization is not guaranteed. |

### Manual installation and updates

For users maintaining a local clone, the offline installer creates a fixed release worktree so development edits do not affect the installed skill:

```bash
git clone https://github.com/zycdev/codex-long-jobs.git \
  "$HOME/workspaces/codex-long-jobs"
cd "$HOME/workspaces/codex-long-jobs"
python3 scripts/install_skill.py install --ref v0.5.0        # Preview.
python3 scripts/install_skill.py install --ref v0.5.0 --yes  # Install the pinned release.
python3 scripts/install_skill.py verify
```

It links `${CODEX_HOME:-$HOME/.codex}/skills/codex-long-jobs` to a detached worktree under `${XDG_STATE_HOME:-$HOME/.local/state}/codex-long-jobs/release`.
Restart Codex to load the installed instructions.
Private job state defaults to `${CODEX_HOME:-$HOME/.codex}/long-jobs`.

New installations enable notification-only release checks once per week, performed on skill use when due.
Updates require an explicit request; network failures do not block job operations, and reinstallation preserves preferences.
To disable checks:

```bash
SKILL_ROOT="${CODEX_HOME:-$HOME/.codex}/skills/codex-long-jobs"
python3 "$SKILL_ROOT/scripts/check_updates.py" configure --enabled no
```

See [release checks and manual updates](references/updates.md) for intervals, initialization of copied installations, and update procedures.

## FAQ

### Does Codex auto resume work when VS Code or SSH is closed?

It can, while the remote host, compatible daemon, thread access, and required tools remain available.
The [Remote-SSH acceptance](#verified-vs-code-remote-ssh-continuation) verified tool-based continuation before reopening or reconnection in specific configurations.
Job survival and model continuation are separate: a detached job may finish even when Codex cannot resume.
This is not a guarantee for arbitrary disconnection, host reboot, or daemon loss.

### Does this eliminate all polling and model usage?

It eliminates model polling while waiting for the job.
Local supervision checks process and cancellation state; recovery may check local readiness.
Launch and completion handling use normal Codex turns, and authorized follow-up work can use further turns.

### Why is my job queued but Codex has not continued?

`queued` confirms acceptance only.
Inspect `status --json`, especially `queue_wake`, and check the original conversation.
A busy thread, earlier input, writer lock, unavailable daemon, or missing client tools can delay or prevent continuation.
Use [wake-only recovery](#recover-an-accepted-notification) rather than resending an accepted notification.

### Do I need tmux or a standalone Codex CLI?

Neither is mandatory when queue delivery and trusted extension-binary discovery work.
tmux remains an option for logs and the legacy TUI transport.
Run `doctor` to inspect your environment; see [compatibility](#compatibility-and-requirements).

### Can I use it with an active Codex Goal?

Yes, but completion notification does not manage the Goal lifecycle.
An active Goal may otherwise generate additional waiting turns.
Follow the [Goal-aware wait instructions](SKILL.md#goal-aware-waits), including explicit authorization for pausing a Goal; a queued notification alone does not change it back to `active`.

### What are the safety and recovery limits?

Jobs do not survive machine reboot or loss of both worker and supervisor.
A worker can finish after supervisor-only loss, but no replacement supervisor watches for a later worker failure.
Use a service manager or cluster scheduler when host-crash recovery is required.

State records argv and working directory, so do not put secrets in command arguments.
Completion prompts reference logs without inserting their contents; treat those logs as untrusted data.
Notifications are best effort, and process completion does not prove artifact correctness.
Full [security boundaries](references/operations.md#security-boundaries) and [known limitations](references/operations.md#known-limitations) are maintained in the operations reference.

## Testing and Contributing

From a repository checkout:

```bash
scripts/smoke_test.sh
scripts/coverage_test.sh
scripts/stress_test.sh 10
ruff check scripts/*.py tests
ruff format --check scripts/*.py tests
shellcheck scripts/codex-long-jobs scripts/*.sh
```

CI covers Python 3.10 through 3.14 and enforces an 80% coverage threshold.
The automated suite tests lifecycle failures, cancellation, queue and wake recovery, duplicate suppression, concurrency, argument and path safety, and legacy TUI behavior.
Real-CLI tests with isolated model fixtures and supervised VS Code tests provide distinct evidence; neither implies universal client-version or platform coverage.

See [testing and release acceptance](references/testing.md) for reproduction commands, Skill validation, dated results, and remaining coverage limits.
Read [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance and follow [AGENTS.md](AGENTS.md) for repository conventions.
Use [GitHub Issues](https://github.com/zycdev/codex-long-jobs/issues) for bug reports, feature requests, and discussion before changing durable schema or delivery behavior.
Consult the [changelog](CHANGELOG.md) for change history and [GitHub Releases](https://github.com/zycdev/codex-long-jobs/releases) for published versions and release notes.

## License

MIT. See [LICENSE](LICENSE).
