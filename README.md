# Codex Long Jobs

[![CI](https://github.com/zycdev/codex-long-jobs/actions/workflows/ci.yml/badge.svg)](https://github.com/zycdev/codex-long-jobs/actions/workflows/ci.yml)

Event-driven background jobs for OpenAI Codex CLI: durable execution,
persistent logs, abnormal-exit detection, and automatic wake-up of the owning
tmux TUI, without spending model turns polling for progress.

> Status: `v0.1.0` beta. Linux is the primary tested platform.

## Why this skill exists

Codex can launch a long training run, build, evaluation sweep, migration, or
data-processing command, but an ordinary interactive turn is a poor process
supervisor. Keeping the turn open blocks the conversation; repeatedly asking
for status spends model tokens while nothing requiring model judgment has
happened.

Codex Long Jobs separates three responsibilities:

```text
Codex turn ──starts──> detached worker ──waits for──> command
    ▲                       │                           │
    │                       ├── persistent state/logs <─┘
    │                       │
    └── safe idle wake <────┴── completion delivery

tmux viewer ──reads the log only; it never owns the command
```

The worker waits locally for process exit. Codex is invoked again only when a
terminal event needs attention.

## Advantages

- **No model polling.** The runtime makes no model calls while a job is merely
  running. This avoids repeated progress-only turns and their context/token
  cost.
- **tmux is a viewer, not a supervisor.** A tmux server crash can remove the
  live log view without killing the worker or command. Recreate the viewer at
  any time.
- **Success and failure use the same wake path.** Nonzero exits, signals,
  missing success markers, and log write failures all produce durable terminal
  state and trigger delivery.
- **Disk-full failures are explicit.** Log ENOSPC overrides a misleading command
  exit code zero, and a small reserved state file is released before final
  metadata is written.
- **Busy TUI delivery is durable.** Completion waits for an idle turn and empty
  composer before pasting. It does not rely on a fragile `Tab` queue. A missed
  first `Enter` retries the key without pasting a duplicate prompt.
- **Session restart recovery.** Resume the same Codex thread in any tmux pane
  and explicitly rebind pending jobs; thread identity prevents accidental
  delivery to another conversation.
- **No runtime dependencies beyond Python.** Python 3.10+ is required; tmux is
  optional unless direct TUI delivery or the visual viewer is desired.

## Token cost claim, precisely

This skill removes the need for **LLM status polling**. If a workflow
would otherwise run several progress-only Codex turns, it should avoid those
turns and their token usage. The detached worker itself uses no model tokens.

It does not make every workload cheaper. Starting a job and handling its
completion are normal Codex turns. If the alternative was one foreground tool
call that blocked until exit without additional model turns, this skill may add
small fixed orchestration overhead. No fixed percentage saving is claimed
without a matched benchmark on the target Codex version and workload.

Token comparisons should separate detachment overhead from optional result
inspection and count the same desired outcome in every workflow.

## Installation

Clone the repository into the Codex skills directory:

```bash
mkdir -p "$HOME/.agents/skills"
git clone https://github.com/zycdev/codex-long-jobs.git \
  "$HOME/.agents/skills/codex-long-jobs"
```

Restart Codex, then confirm the skill appears in `/skills`. The runtime stores
private job state under `${CODEX_HOME:-$HOME/.codex}/long-jobs`.

## Quick start

From a Codex CLI TUI running inside tmux:

```bash
SKILL_ROOT="$HOME/.agents/skills/codex-long-jobs"

"$SKILL_ROOT/scripts/codex-long-jobs" start \
  --name build-release \
  --log ./logs/build-release.log \
  --success-pattern '^BUILD_COMPLETE$' \
  --delivery auto \
  --viewer auto \
  -- bash -c 'make release && printf "BUILD_COMPLETE\n"'
```

Codex must run `start` with scoped host permission. Its normal tool sandbox
reaps detached children when the tool call ends; the controller detects that
condition and refuses to pretend the job detached successfully.

The command prints the worker PID, log path, durable state path, delivery mode,
and optional viewer attach command. After a one-time running-state check, let
the Codex turn end. The worker initiates completion delivery.

## Commands

```bash
# Durable state, on demand
scripts/codex-long-jobs status --name build-release

# Follow the log in the current terminal
scripts/codex-long-jobs tail --name build-release

# Recreate the disposable tmux viewer
scripts/codex-long-jobs view --name build-release

# After exiting and resuming the same Codex thread
scripts/codex-long-jobs rebind --all

# Retry a terminal job whose delivery remains pending
scripts/codex-long-jobs retry-delivery --name build-release

# Runtime and TUI binding diagnostics
scripts/codex-long-jobs doctor
```

Use `--delivery event-only` for durable state without a continuation. Use
`--delivery headless` only when a separate `codex exec resume` turn has been
explicitly authorized; it cannot guarantee live repainting of an open TUI.

## What happens if the original Codex session exits?

The worker and command continue. Automatic TUI delivery cannot safely guess
whether a new Codex PID in the same pane or another pane is showing the same
thread. Resume the original thread and run:

```bash
scripts/codex-long-jobs rebind --all
```

Rebind requires the current `CODEX_THREAD_ID` to match each job owner. It may
be done before or after job completion. Without rebind, completion remains
durably pending.

## Codex compatibility

The project targets the gap described in OpenAI Codex
[#29922](https://github.com/openai/codex/issues/29922): wake on background
events without repeatedly running full model turns. A native Codex mechanism
would ultimately be preferable to terminal injection.

## Safety and limitations

- Job output is untrusted data. Completion prompts point to logs but never
  inject log contents into the TUI.
- State stores argv and working directory. Do not put secrets in argv.
- Direct TUI wake-up uses tmux input injection because Codex CLI has no stable
  public arbitrary-local-process wake API. Changes in Codex TUI rendering may
  require compatibility updates.
- Rebind is mandatory after the Codex process exits or the displayed thread is
  changed. Simply returning to the old pane is insufficient.
- The worker survives tmux failure, not machine reboot or an uncatchable kill of
  the worker itself. Use systemd, a cluster scheduler, or another service
  manager when host-level restart recovery is required.
- Native Windows is not currently supported.

See [references/operations.md](references/operations.md) for the full failure
model and recovery rules.

## Development

```bash
scripts/smoke_test.sh
python3 /path/to/skill-creator/scripts/quick_validate.py .
```

The smoke suite covers successful and failed commands, simulated disk-full log
writes, a disposable tmux viewer crash, busy-TUI deferral, missed-Enter retry,
and explicit session rebind.

## License

MIT. See [LICENSE](LICENSE).
