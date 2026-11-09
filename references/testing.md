# Testing and release acceptance

## Scope

The test program separates deterministic behavior from environmental
acceptance. Passing the automated suite means the implemented state and process
contracts behaved as specified on the tested host. It does not claim survival
of machine reboot, loss of both supervisor and worker, or arbitrary future
Codex TUI changes. Supervisor-only SIGKILL after the worker reaches `running`
has a separate real-host acceptance test.

## Staged supervised acceptance (2026-11-08)

Run the current old-CLI UI cases before upgrading.
The read-only preflight on 2026-11-08 found PATH CLI 0.164.0 with queue support and without native `--resume`.
The automated and isolated results below remain historical evidence; they do not mark any of these UI cases as passed.
Use the development runtime directly, a separate test conversation/window, a temporary workspace, and a separate long-jobs state directory.
Do not replace the installed skill or modify conversation/authentication files directly.
Discover the owner thread from the test session automatically; never target the coordinating or an unrelated working conversation.
Record the actual CLI, extension, and daemon combination before launch without changing it.

| Case | Action and evidence | Maintainer participation | Status |
| --- | --- | --- | --- |
| O1: Online baseline | A 30-second job produces an artifact. Its completion notification must trigger real model tool use that verifies the artifact and writes a separate continuation receipt. | Open the dedicated test conversation and leave it visible. | Passed for online notification and verified tool continuation; see O1 record below. |
| O2: Active turn | Complete a short job during a bounded foreground turn, then verify deferred consumption and one continuation. | No interface action. | Passed: acceptance during an active turn, no interruption, and one subsequent verified continuation. |
| O3: Unselected conversation | Complete the job while another conversation is selected; verify routing and continuation timing before selecting the original again. | Switch away, then return when instructed. | Passed under maintainer-reported unselected state; verified receipt observed before any return instruction. |
| O4: Closed test window | Close only the test project window before completion. Record the continuation artifact time and compare it with reopening. | Close and later reopen the test window. | Passed: verified continuation before reopening, followed by maintainer-confirmed display in the original conversation. |
| O5: Remote-SSH disconnected | Close the remote connection for the test window before completion. Check whether continuation precedes reconnection and whether the original conversation is intact afterward. | Disconnect and reconnect the test window. | Passed: verified continuation before reconnection and maintainer-confirmed display in the original conversation. |

Complete and verify O1 before any disruptive UI action.
For O3–O5, confirm the job is running before requesting the action; test one action at a time, and record closure and return times.
The background command may create only its own task artifact.
Only the notified model, through an actual tool call after inspecting state/log/artifact evidence, may create the continuation receipt with a machine-generated UTC timestamp and artifact hash.
A receipt created before reopening establishes continuation during disconnection; one created afterward establishes recovery after reconnection only.
Classify command completion, queue acceptance, wake observation, tool availability, verified continuation, and user-visible display separately.
Retain safe failure evidence for writer-lock or missing-tool cases; investigate before proceeding instead of resending an accepted notification or terminating its owner.
The tests do not stop shared servers or other sessions.

### O1 real online baseline (2026-11-08)

The dedicated VS Code test conversation used CLI 0.164.0 from PATH and extension binary 0.164.0-alpha.11.1 (extension 26.1021.22027).
The control socket owner was identified after continuation as a daemon binary from the 0.167.0 installation; the prelaunch snapshot did not resolve its symbolic-link socket path, so this is post-run version evidence.
No component was upgraded or replaced for this case.

The launch turn ended at 12:53:02.401 UTC.
The command completed and its message was accepted at 12:53:03 UTC, with one queue attempt and native resume disabled.
The notification triggered a new turn at 12:53:08.964 UTC; its user-message record contained the exact completion token, and the checked interval contained no additional manual user message.
The continuation performed actual tool reads and wrote its verification receipt at 12:54:27.985 UTC.
Independent inspection verified the artifact sum, SHA-256, owner identity, command result, queue attempt count, and unchanged runtime source hashes.

O1 therefore passes online notification and real tool-based continuation.
The helper first encountered an active-writer refusal and later recorded `absent`; this does not establish independent cold-resume success.
The maintainer reported that the test conversation finished; client rendering was not independently inspected.
Window closure, conversation switching, and Remote-SSH disconnection were not performed in O1.
Raw thread identifiers, transcripts, logs, and receipts remain in the private test workspace rather than the repository.

### O2 real active-turn delivery (2026-11-08)

The same dedicated test conversation used PATH CLI 0.164.0, extension binary 0.164.0-alpha.11.1, and the existing control-socket owner from daemon 0.167.0.
The foreground control operation ran from 13:10:27.104 to 13:11:12.105 UTC and exited normally after 45 seconds.
The background artifact was produced at 13:10:42.288 UTC, and one queue attempt was accepted at 13:10:42 UTC, both within that active interval.
The launching turn ended at 13:11:20.027 UTC; the next turn started at 13:11:20.036 UTC and received the matching completion notification at 13:11:20.085 UTC.
Actual tool verification produced the continuation receipt at 13:12:57.369 UTC.

Independent review verified artifact and foreground-file hashes, unchanged source hashes, owner identity, normal foreground exit, the timing bounds, and one notification token in the relevant test-thread history.
No extra manual user message or turn-abort event appeared in that interval.
O2 passes active-turn queue acceptance without interruption and subsequent verified continuation.
As in O1, the helper observed an active-writer refusal followed by `absent`; independent cold-resume success is not established by this case.

For O3–O5, use a bounded local gate before generating the task artifact.
The background worker first waits for a per-run release file; the coordinator creates that file only after the maintainer reports the specified UI action.
This ensures that completion cannot precede the action merely because preparation or model execution took longer than expected.
The gate does not create a continuation receipt, add queue messages, or change any Codex service.
A gate timeout is an invalid test sequence or a test failure, not successful unattended continuation.

### O3 real unselected-conversation continuation (2026-11-08)

The maintainer reported switching away while the gated job was still running and had no task artifact.
The coordinator released the gate at 13:25:11.073 UTC; the worker observed it at 13:25:11.148 UTC and produced the artifact at 13:25:26.209 UTC.
One queue attempt was accepted at 13:25:26 UTC, and the original test thread received the matching notification at 13:25:29.002 UTC.
Actual tool verification created the continuation receipt at 13:26:37.122 UTC.
A local file-event observer recorded that receipt at 13:26:37.124 UTC, before the coordinator issued any instruction to return to the original conversation.
The continuation turn ended at 13:26:52.501 UTC.

Independent checks verified hashes, command success, owner identity, source hashes, timing order, and exactly one matching notification with no extra manual user message or turn-abort event in the checked interval.
O3 passes verified continuation while the original conversation remained unselected according to the maintainer's action report and the agreed return procedure.
UI selection was not independently inspected by software, and rendered output after returning remains a separate observation.
The helper again observed an active-writer refusal followed by `absent`; the pass relies on the real continuation evidence, not an inference that the helper independently resumed the thread.

### O4 real window-closure continuation (2026-11-08)

An earlier O4 attempt started at 13:31:29 UTC but never received a release marker and failed at 13:41:30 UTC after its ten-minute gate timeout.
No task artifact was produced. It is excluded from window-closure acceptance, and its failure evidence remains preserved.
A new immutable job was started at 14:15:21 UTC.
After the maintainer confirmed closing the test window, the coordinator verified that this was the only running, unreleased O4 job and released it at 14:17:13.745 UTC.
The original test thread and runtime source hashes were verified before release.
The artifact was produced at 14:17:28.835 UTC and accepted by the queue once at 14:17:29 UTC, using CLI 0.164.0 without native resume.
The helper recorded `consumed-after-resume` at 14:17:32 UTC, and the notification started a turn in the original test thread at 14:17:32.780 UTC.
Real tool calls verified the artifact and created the continuation receipt at 14:18:54.218 UTC.
The coordinator's local file-event observer recorded that receipt at 14:18:54.220 UTC, before any instruction to reopen the window.
The continuation turn completed at 14:19:09.168 UTC.

Independent inspection verified the artifact hash and sum, owner identity, unchanged runtime source hashes, chronological gate/action evidence, and exactly one matching notification with no extra manual user message or turn-abort event in the inspected interval.
This establishes verified continuation during the maintainer-reported closed-window interval for the tested combination of CLI 0.164.0, extension 0.164.0-alpha.11.1, and existing daemon 0.167.0.
The conclusion uses actual tool-produced evidence in addition to the helper's disappearance observation.
After reopening, the maintainer confirmed that the original conversation displayed the latest O4 result, including exit code zero, case O4, and the verified sum of 15.
This completes the observed window-closure and post-reopen display case for the recorded version combination; no conclusion about all closed-client deployments or unavailable shared services follows from it.

### O5 real remote-disconnection continuation (2026-11-08)

The maintainer reported closing the remote connection for the dedicated test window while the only O5 job was still waiting for its release marker.
The coordinator verified the original owner and unchanged source hashes, then released the gate at 14:31:17.858 UTC.
The artifact was produced at 14:31:32.978 UTC; one queue attempt was accepted, without native resume, and the helper recorded `consumed-after-resume` at 14:31:36 UTC.
The notification started a turn in the original thread at 14:31:36.763 UTC.
Actual tool calls verified the artifact and created the continuation receipt at 14:32:45.619 UTC.
The local file-event observer recorded that receipt at 14:32:45.621 UTC before any instruction to reconnect.
The continuation turn completed at 14:32:54.190 UTC.

Independent checks verified the artifact hash and sum, owner, source hashes, action/gate timing, and exactly one matching notification with no extra manual user message or turn-abort event in the inspected interval.
O5 passes verified continuation during the maintainer-reported disconnected interval for CLI 0.164.0, extension 0.164.0-alpha.11.1, and existing daemon 0.167.0.
After reconnecting, the maintainer confirmed that the original conversation displayed the latest O5 completion and verification reply.

O4 and O5 exhibited the same relevant outcome in this environment: an accepted notification was consumed after resume, and actual tool verification completed before the user returned.
The two UI actions were recorded separately; the observations do not prove different or identical process-exit sequences.
These cases cover deliberate window closure and deliberate Close Remote Connection, not arbitrary network failure, host loss, or termination of the shared daemon.

All five supervised UI cases O1–O5 are now verified for the recorded current-version combination.
The earlier unreleased O4 attempt remains excluded because its action gate timed out before producing an artifact.
Runtime source hashes remained unchanged across the UI batch, and each successful case used one queue attempt with native resume disabled.
This completes the planned current old-CLI UI batch, not universal historical-version coverage or native `queue --resume` acceptance.

The maintainer subsequently confirmed the system CLI upgrade and requested independent testing before further UI participation.
The post-upgrade preflight found CLI 0.167.0 and an advertised `queue --resume` option.
The extension binary remained 0.164.0-alpha.11.1; other existing sessions were not restarted or migrated for testing.
The independent native acceptance is recorded below.
The maintainer subsequently repeated O1–O5 as N1–N5 on the new CLI: online continuation, busy-turn deferral, an unselected conversation, window closure, and Remote-SSH disconnection.
The dedicated test conversation and the three UI actions required maintainer participation; no test notification was sent to unrelated sessions.
Historical CLI versions not executed and the uncompleted legacy TUI matrix remain explicit coverage limits; completing O1–O5 does not imply universal old-version coverage.

### N1 native CLI online continuation (2026-11-09)

The dedicated VS Code test conversation retained extension binary 0.164.0-alpha.11.1 and the existing shared daemon from the 0.167.0 installation.
The task explicitly selected system CLI 0.167.0 through `CODEX_LONG_JOBS_CODEX_BIN`, scoped to the test command and its descendants.
The installed skill, extension settings, other sessions, and shared services were unchanged.

The launching turn ended at 11:10:45.598 UTC.
The producer created its artifact at 11:11:04.731 UTC and exited successfully.
Exactly one completion-token input started a subsequent turn at 11:11:07.551 UTC, before the CLI returned and durable delivery recorded acceptance at 11:11:09 UTC.
The delivery used native resume, with `queue_attempts: 1` and `queue_resume_warning: true`.
Thread-scoped server logs at 11:11:09 UTC identify the native `thread/resume` failure as an active-writer conflict.
The independent helper observed `absent`; no recovery message was resent.

The notified model executed tools to read the command state, log, and artifact, verify `3 + 5 + 7 = 15` and the artifact SHA-256, and create the continuation receipt at 11:12:07.061 UTC.
The continuation turn completed at 11:12:14.654 UTC, with no intervening manual continuation input or turn-abort event.
Coordinator checks confirmed the artifact hash, owner, source hashes, single completion input, and actual post-notification tool calls.

N1 passes online native-CLI delivery and verified tool continuation.
It also confirms safe handling of an unsuccessful native resume while an existing consumer proceeds; it does not establish successful independent native cold resume in this UI case.
The maintainer reported completion; the coordinator verified recorded output rather than independently inspecting the screen.
Subsequent UI cases are recorded separately below.

### N2 native CLI busy-turn continuation (2026-11-09)

The dedicated test conversation retained the N1 version combination and explicitly selected system CLI 0.167.0 for this task.
A single tool invocation launched a 15-second producer and immediately began a 45-second foreground operation.
The foreground interval was 11:21:42.013–11:22:27.014 UTC, with a measured duration of 45.000768 seconds and exit code zero.
The producer artifact timestamp was 11:21:57.223 UTC; queue acceptance was recorded at 11:22:00 UTC, within that foreground interval even after accounting for one-second queue timestamp precision.
Delivery used native resume and one queue attempt.

The original turn completed at 11:22:31.198 UTC, followed by the completion-notification turn at 11:22:31.208 UTC.
Exactly one completion-token input appeared, with no intervening manual continuation message or turn-abort event.
Actual tools verified the command result, artifact SHA-256, arithmetic result, and foreground timing before creating the receipt at 11:23:38.851 UTC.
The continuation turn completed at 11:23:51.348 UTC.
Coordinator checks independently confirmed these records and unchanged runtime hashes.

The native CLI reported a resume warning; the RPC helper recorded an active-writer refusal followed by `absent`.
These observations establish safe deferral to the existing consumer, not independent cold-resume success.
N2 passes busy-turn delivery without interruption and one subsequent verified continuation.
Subsequent UI cases are recorded separately below.

### N3 native CLI unselected-conversation continuation (2026-11-09)

The task used the same explicit CLI 0.167.0 and existing extension/server combination as N1 and N2.
Its launching turn ended at 11:28:35.359 UTC while the producer waited on a coordinator-controlled action gate.
After the maintainer confirmed selecting another conversation, the coordinator armed a file-event observer and released the gate at 11:30:40.422 UTC.
The producer created its artifact at 11:30:55.577 UTC.
Exactly one completion-token input started a turn at 11:30:57.574 UTC; durable queue acceptance was recorded at 11:31:00 UTC with native resume enabled and one attempt.

Actual tool calls verified the artifact, hash, command outcome, and gate timing before creating the continuation receipt at 11:32:04.161 UTC.
The file-event observer recorded that receipt at 11:32:04.164 UTC, before any instruction to return to the test conversation.
The continuation turn completed at 11:32:12.082 UTC, without a manual continuation input or turn-abort event.
Coordinator checks confirmed the receipt and artifact hashes, owner, source hashes, and turn sequence.

N3 passes verified continuation during the maintainer-reported unselected interval.
The selection state was not independently inspected through the UI; the receipt's existence before the return instruction was directly observed.
Native resume reported a warning and the helper observed `absent`, so this case does not establish independent native cold resume.
The closed-window and Remote-SSH cases are recorded below.

### N4 native CLI closed-window continuation (2026-11-09)

The dedicated test conversation retained the preceding version combination and task-scoped selection of CLI 0.167.0.
Its launching turn ended at 11:40:07.096 UTC.
After the maintainer reported closing the test window, a read-only `thread/read` at 11:41:28.079 UTC observed `notLoaded` through the existing shared daemon.
That inspection did not resume the thread or modify its queue.
The coordinator armed the file-event observer and released the producer at 11:41:28.138 UTC.

The artifact was produced at 11:41:43.297 UTC, and one queue attempt was accepted with native resume enabled and no resume warning or diagnostic output.
Exactly one completion-token input started a turn at 11:41:43.858 UTC.
The helper subsequently observed `absent`; completed follow-up work was established separately through actual tool calls and artifact verification.
The model verified the command result, artifact hash, arithmetic result, and release timing before creating its receipt at 11:43:10.600 UTC.
The file-event observer recorded the receipt at 11:43:10.603 UTC, before any reopening instruction, and the continuation turn completed at 11:43:21.544 UTC.

Coordinator checks confirmed unchanged runtime hashes, the matching owner and receipt, one notification, and no manual continuation input or turn-abort event.
N4 passes verified tool continuation during the maintainer-reported closed-window interval with an initially unloaded thread and no native resume warning.
After reopening, the maintainer confirmed the latest N4 reply showing exit code zero and the verified arithmetic result in the original conversation.
N4 therefore also passes post-reopening display confirmation; the UI actions and display were maintainer-reported rather than independently inspected.
The deliberate Remote-SSH disconnection case is recorded below.

### N5 native CLI disconnected continuation (2026-11-09)

The test used CLI 0.167.0, extension binary 0.164.0-alpha.11.1, and the existing shared daemon from the 0.167.0 installation.
The launching turn ended at 14:02:46.570 UTC while the producer waited for the coordinator's gate.
After the maintainer reported closing the test window's Remote-SSH connection, a read-only inspection at 14:03:45.779 UTC confirmed that the shared daemon remained reachable and the target thread was `notLoaded`.
The coordinator released the task at 14:03:45.849 UTC after arming the file-event observer.

The producer created the artifact at 14:04:01.009 UTC; one queue attempt was accepted with native resume enabled and no resume warning or diagnostic output.
Exactly one completion-token input started the continuation turn at 14:04:01.562 UTC.
Actual tools verified the command result, artifact hash, arithmetic result, and gate timing before writing the receipt at 14:05:23.558 UTC.
The file-event observer recorded the receipt at 14:05:23.561 UTC, before any reconnection instruction; the turn completed at 14:05:34.457 UTC.
Coordinator checks confirmed the matching owner and hashes, unchanged runtime, one notification, and no manual continuation input or turn-abort event.

N5 passes verified tool continuation during the maintainer-reported disconnected interval with an initially unloaded thread.
The helper observed `absent`; follow-up success was established from the actual tools and verified artifact, not from queue disappearance.
After reconnecting, the maintainer confirmed that the original conversation displayed the latest N5 verification reply with exit code zero and the expected arithmetic result.
N5 therefore also passes post-reconnection display confirmation; the disconnection, reconnection, and display were maintainer-reported rather than independently inspected.
N4 and N5 cover deliberate window closure and deliberate Close Remote Connection as separate user actions; they do not establish distinct process-exit sequences, arbitrary network-failure recovery, host-reboot recovery, or recovery after shared-daemon termination.

All five native-CLI UI cases N1–N5 are now complete for the recorded version combination.
Each case used one queue attempt and produced a verified tool-continuation receipt.
N1–N3 retained native resume warnings while an existing consumer handled the notification; they are not independent native cold-resume evidence.
N4 and N5 began with an observed `notLoaded` thread, reported no native resume warning, and produced verified receipts before the coordinator permitted reopening or reconnection.

### Native CLI and mixed-version isolated acceptance (2026-11-08)

`tests/accept_native_queue_wake.py` exercises the actual selected CLI and App Server against a local Responses model fixture.
Each invocation creates its own Codex home, state directory, synthetic threads, control socket, and disposable server processes.
It neither copies credentials nor uses the installed skill, hosted models, production conversations, or the shared daemon.
`--server-codex` selects only the temporary server executable; `--legacy-codex` selects only a subsequent compatibility test subprocess.
Neither option changes an installation or the default CLI.
An additional direct `queue --resume` case deliberately starts no RPC wake helper and independently verifies native recovery and FIFO consumption on both server versions.

The following combinations passed cold-thread recovery with earlier input, native resume warnings caused by a separate private writer, wake-only recovery after that writer exits, and repeated recovery without duplicate delivery:

| Queue CLI | Private App Server | Legacy CLI used afterward | Result |
| --- | --- | --- | --- |
| 0.167.0 | 0.167.0 | Extension binary 0.164.0-alpha.11.1 | Passed. |
| 0.167.0 | Extension binary 0.164.0-alpha.11.1 | Extension binary 0.164.0-alpha.11.1 | Passed. |

The writer-conflict cases confirmed exit code zero, `queue_resume: true`, `queue_resume_warning: true`, one retained queue item, and a separate pending wake observation.
Once the private owner exited, the helper resumed the thread and the completion token appeared in exactly one completed turn.
Both `retry-wake` and `retry-delivery` left `queue_attempts` at one.
In the normal native case, the helper observed `absent`; success was established separately from the actual ordered turn records, not inferred from absence.
The owner shutdown can produce a broken-pipe diagnostic from the local model fixture's interrupted response; the resumed target turn must still complete and pass all assertions.
The full legacy harness also passed with extension CLI 0.164.0-alpha.11.1 connected to a private 0.167.0 server, including temporary server unavailability followed by recovery of the existing notification with one queue attempt.

Both combinations also passed three concurrent native-delivery jobs with six recovery callers while a turn was active.
The queued inputs remained unchanged during that turn, then completed once in FIFO order after its release.
All three producer artifacts were verified and the recorded wake processes exited.

The existing isolated client-tool test also passed with server 0.167.0.
The RPC client rejected `item/tool/call`, and the server recorded a failed dynamic-tool call before the model fixture completed its turn.
This again separates terminal turn state from successful tool execution; no approval or tool success was fabricated.
Runtime source hashes match the preceding O1–O5 UI acceptance; this native acceptance phase required no further runtime changes.
The shared acceptance fixture now uses the bounded thread-materialization wait consistently and accepts an independently selected private server executable.

Host-side diagnostic checks selected CLI 0.167.0 from PATH, discovered extension 0.164.0-alpha.11.1 when only a child process's PATH excluded the standalone CLI, and honored an explicit old-binary override.
Only the explicit old-CLI diagnostic produced optional upgrade advice; extension-only discovery and the new CLI produced none.
A tool sandbox that remapped root ownership to an unmapped user correctly failed conservative path discovery; the host-side check established the actual selection without weakening ownership checks.

Reproduce with explicitly chosen installed executables and a new evidence directory:

```bash
python3 tests/accept_native_queue_wake.py \
  --codex /absolute/path/to/new-codex \
  --server-codex /absolute/path/to/server-codex \
  --legacy-codex /absolute/path/to/old-codex \
  --evidence-dir /tmp/clj-native-unique

python3 tests/accept_queue_wake.py \
  --codex /absolute/path/to/old-codex \
  --server-codex /absolute/path/to/new-codex \
  --evidence-dir /tmp/clj-legacy-new-server-unique
```

These results establish real CLI, protocol, queue, and process behavior with a synthetic model.
The isolated fixtures alone do not establish new-version VS Code tool-dependent continuation or visible results during actual window closure or disconnection.
The separate N1–N5 records above establish those exercised continuation cases, with maintainer-confirmed display after reopening and reconnection.

The post-upgrade regression run passed all 152 tests on Python 3.10.12, 3.11.15, 3.12.13, 3.13.12, and 3.14.4.
Ten consecutive full-suite stress rounds each passed all 152 tests, extending the earlier 144-test stress snapshot.
Subprocess-aware combined statement/branch coverage was 85.74%, above the 80% gate.
Ruff lint and formatting, ShellCheck, the OpenAI Skill validator, and `git diff --check` passed.
The review patch was applied to an archived base commit, compared byte-for-byte with all 17 changed or added files, and checked for reverse application without modifying the Git index.
At that validation checkpoint, `feat/queue-wake-hardening` retained uncommitted changes, with `VERSION` unchanged at 0.4.2 and no publication or installed-skill synchronization.
Independent native acceptance and the supervised N1–N5 UI comparison are complete.

### Final regression check (2026-11-09)

After the native UI batch, `scripts/smoke_test.sh` and `scripts/coverage_test.sh` each passed all 152 tests on Python 3.14.4.
Combined statement/branch coverage remained 85.74%.
Ruff lint and formatting, ShellCheck, the OpenAI Skill validator, and `git diff --check` passed.
The Python 3.10–3.14 matrix and ten full-suite stress repetitions recorded above apply to the same runtime source hashes; no runtime change was needed after native CLI or supervised UI acceptance.
Two documentation links were corrected after the staged-acceptance heading was renamed, and all 16 local documentation links, including heading targets, were checked.
Historical CLI versions 0.157.1, 0.160.0, and 0.165.0 remain source-audit evidence rather than newly executed binaries; the complete legacy TUI manual matrix was not repeated.
The tested Linux host and available daemon/tools remain part of the acceptance conditions.
No production process was stopped, no installed skill was synchronized, and no version, tag, release, or remote GitHub state was changed during acceptance.
The original contributor and merge commits remain in the development branch's ancestry.
The implementation and exercised local acceptance are ready for review, subject to the coverage limits above.
The maintainer subsequently authorized local commit preparation, as recorded below.
Remote CI had not run at the local acceptance checkpoint because the branch had not yet been pushed.
The subsequently authorized remote submission is tracked in [PR #5](https://github.com/zycdev/codex-long-jobs/pull/5), including its current CI results; merging remains a maintainer decision.

### Limited fallback check and local handoff (2026-11-09)

The maintainer explicitly retained tmux as a secondary transport and limited further compatibility work rather than requiring the complete historical manual matrix.
Five focused regressions passed: automatic fallback without queue support, busy-turn deferral with missed-Enter retry, expiry without pasting into a busy TUI, rejection of a wrong-thread rebind, and explicit rebind after the original process exits.
Running these tests independently exposed a test-import ordering dependency: `test_runtime.py` needed to add the script directory itself before loading the runtime module.
The import-path correction changes only the test harness; no runtime or TUI recognition logic changed.

A real tmux check used extension CLI 0.164.0-alpha.11.1, a private Codex home, a disposable tmux socket with user configuration disabled, and the local model fixture.
An actual background job completed, its explicit TUI notification was submitted once, and a repeated delivery invocation produced no second input or turn.
The endpoint test hook selected the actual discovered private TUI process; normal pane, TTY, process identity, and ancestry validation remained enabled.
The first fixture launch reached a working-directory selection dialog and safely retained a pending notification; after aligning the private pane's working directory and waiting for the resumed history, the bounded retry passed.
This confirms limited fallback delivery and duplicate suppression, not the complete old-version TUI matrix or graceful `/exit` coverage.

After the test-import correction, the complete 152-test smoke suite, Ruff lint and formatting, ShellCheck, the Skill validator, and `git diff --check` passed again.
Runtime source hashes still match the native and legacy UI acceptance above.
The local commit contains only the 17 source, test, and documentation files; private receipts and logs remain outside version control.
At the local handoff, `VERSION` remained 0.4.2 and release and installed-skill synchronization were deferred.
Remote submission was authorized separately after this local handoff.

### v0.5.0 release preparation (2026-11-09)

The maintainer subsequently authorized merging PR #5 and publishing v0.5.0.
The minor version identifies the native-resume preference, independent wake recovery, executable discovery, and status diagnostics added since v0.4.2.
The release preparation synchronizes `VERSION`, the runtime version constant, README status, and Changelog while retaining durable schema version 1.
The runtime code change at this stage is limited to its version constant; the earlier acceptance records retain their original version and source-hash context.
Publication uses an annotated tag on the tested merge commit and preserves Vandermode's original contribution history.
Updating the installed skill remains outside the authorized release operation, so existing installations and running jobs are preserved.

## Unattended queue wake acceptance

On 2026-11-01 the local patch passed 111 tests, Ruff lint and formatting,
and the skill validator. The added tests cover the Unix WebSocket transport,
partial frames, masking and length encodings, ping/fragmentation, unloaded
owners, busy owners, prior queued input, queue-consumption races, detached
launch, private wake evidence, and repeat-call idempotence.

Real acceptance ran against a Codex 0.166.0 daemon with Python 3.12:

1. A tiny background job completed with one accepted completion message and
   one completed follow-up turn while the test client was disconnected. The
   native queue consumer had already consumed this item when the helper ran;
   the helper correctly recorded `absent` and did not duplicate it.
2. A separate direct queue submission was verified to remain queued while its
   owner was `notLoaded`. After closing the submitting client, the detached
   helper resumed the owner, recorded `consumed-after-resume`, and exactly one
   follow-up turn completed. No desktop click or selected thread was involved.

Private receipts and logs remain outside the skill. Existing production jobs
and other threads were not modified. These tests cover a live host and daemon;
they do not establish host-reboot recovery or availability of desktop-provided
tools after the desktop is disconnected. This does not replace the historical TUI acceptance below.

## Phase-one queue-wake hardening validation (2026-11-04)

The `feat/queue-wake-hardening` development branch, based on merge commit `1af7e1b`, preserves PR #3 and does not change `VERSION`, the installed skill, system CLI selection, the extension, or the shared daemon.
At the end of phase one, the change comprised these 16 files; phase three additionally introduced `tests/accept_native_queue_wake.py`.

| Area | Files |
| --- | --- |
| Runtime | `scripts/codex_long_jobs.py`, `scripts/queue_wake.py`, `scripts/codex_environment.py` |
| Documentation | `SKILL.md`, `README.md`, `references/operations.md`, `references/testing.md`, `CHANGELOG.md` |
| Tests and fixtures | `tests/fake_codex.py`, `tests/test_helpers.py`, `tests/test_queue_wake.py`, `tests/test_runtime.py`, `tests/test_codex_environment.py`, `tests/test_wake_recovery.py`, `tests/test_wake_processes.py`, `tests/accept_queue_wake.py` |

The system CLI was 0.164.0; the extension binary was 0.164.0-alpha.11.1.
Both advertised `queue` without `--resume`.
Read-only inspection found a separate shared daemon from a 0.166.0 installation, but that binary was not used for acceptance or substituted for the old CLI.
The tool-process ancestry reached that shared daemon; a tmux environment variable alone did not identify the backend process version.

The initial phase-one implementation passed the complete 144-test suite under Python 3.10.12, 3.11.15, 3.12.13, 3.13.12, and 3.14.4.
Subprocess-aware combined branch coverage was 85.30%, above the unchanged 80% gate.
That implementation also passed all ten consecutive `scripts/stress_test.sh 10` rounds, each running all 144 tests.
Ruff 0.16.0 lint and formatting, ShellCheck 0.11.0, the OpenAI Skill validator, and `git diff --check` passed.
Validation dependencies and the otherwise unavailable Python 3.13 interpreter were installed only in a temporary test directory; system defaults were not changed.
The initial full smoke run passed 135 tests, and subsequent recovery and concurrency regressions expanded that suite without removing or skipping existing tests.

The opt-in harness uses a fresh `CODEX_HOME`, newly created threads, disposable App Server process groups, and a loopback Responses-protocol stub.
It does not read credentials, call a hosted model, or inject messages into production conversations.
This is real CLI and RPC integration against a synthetic model service, not evidence of real model task quality or the full VS Code UI lifecycle.
The harness closes only the processes it creates.

| Isolated real test | PATH CLI 0.164.0 | Extension binary 0.164.0-alpha.11.1 |
| --- | --- | --- |
| Real queue acceptance with an unloaded thread and two inputs | Passed | Passed |
| Resume with preceding user input; verify actual turn-input order | Passed | Passed |
| Repeat wake; verify no additional queue item or turn | Passed | Passed |
| Competing App Server holds writer lock; queue remains intact | Passed | Passed |
| Server unavailable, then restarted; durable helper recovers without resending | Passed | Passed |
| Six synthetic turns completed with the expected inputs | Passed | Passed |
| Automatic extension fallback with system CLI absent from the test PATH | Not applicable | Passed |

The initial phase-one runtime also passed a complete isolated 0.164.0 background-job pipeline: a real worker produced its artifact and success marker, automatic queue delivery made one attempt, the helper recorded `consumed-after-resume`, and the server completed exactly one notification turn.
Both `retry-wake` and `retry-delivery --allow-possible-duplicate` preserved that single queue attempt and did not create another turn.
The accepted Codex home and the separately exposed wake observation were verified in status output.

A separate real tmux-hosted 0.164.0 TUI passed idle queue consumption, deferral during an active synthetic turn, and continuation after closing its disposable pane while the isolated App Server remained available.
The typed `/exit` fixture did not establish graceful process exit; the verified lifecycle action was `tmux kill-pane` against only the disposable test socket.
The compatibility TUI input-injection path was unchanged and its existing deterministic regression cases remain enabled; the full historical manual compatibility matrix was not repeated.

A separate real 0.164.0 protocol test registered a synthetic client tool and made the local model stub request it.
The helper returned `-32601`; the server recorded a failed dynamic-tool call.
The stub subsequently produced a final response and the turn reached `completed`, demonstrating why even a completed turn does not prove successful tool-dependent work.
The deterministic suite additionally covers approval-request rejection without granting permission.

A cold-resume test exposed the expected race where `thread/read` briefly reported idle while the native queue consumer already held a pending turn.
The server rejected the competing start, retained FIFO order, and completed both inputs once.
The helper now classifies that exact server response as `waiting-for-idle`.

Reproduce against an explicitly selected old CLI, using a new evidence directory each time:

```bash
python3 tests/accept_queue_wake.py --codex /absolute/path/to/codex --evidence-dir /tmp/clj-acceptance-unique
python3 tests/accept_queue_wake.py --tools-only --codex /absolute/path/to/codex --evidence-dir /tmp/clj-tools-unique
```

The standard mode refuses binaries advertising native `--resume`, keeping newer-version acceptance separate.
To test extension-only discovery, remove the system CLI directory from a child process's PATH, call `codex_environment.discover_codex()`, and pass the selected extension executable to the harness.
Do not change the parent shell PATH or the system installation.
Keep raw receipts, process identifiers, synthetic conversation history, and host paths outside the repository.

Native `--resume` is covered by fake CLI tests for option detection, successful acceptance, resume warnings with a successful exit, single queue submission, and wake-only retries.
A combined fake-CLI/RPC regression verifies that successful acceptance with a native resume warning proceeds to RPC recovery after the CLI returns, while repeated dispatch never sends another message.
These were simulations at the end of phase one. The post-upgrade section above separately records actual CLI 0.167.0 acceptance.
CLI versions 0.157.1, 0.160.0, and 0.165.0 were not executed against this development change; their historical or upstream protocol availability is not a substitute for current acceptance.
At the end of the 2026-11-04 phase, full VS Code window closure, Remote-SSH disconnection, user-visible continuation, and unavailable real client tools remained separate acceptance cases requiring the maintainer's participation.
The supervised 2026-11-08 records above document the subsequently exercised cases.
No such user action is required for the isolated tests above.

Goal-aware waiting was reviewed against asynchronous delivery and explicit pause authorization.
Its existing guidance does not create a Goal, pause without permission, claim that notification changes Goal state, or authorize a separate headless continuation; no change to that independent workflow was needed.

### Independent follow-up review

An additional review reproduced three failure modes without using production services:

- A sequence of partial handshake headers or frame payloads could keep each individual socket read below its timeout while exceeding the complete operation's deadline.
- After sleeping until the wake waiting deadline, the helper could initiate one more RPC attempt before recording timeout.
- Structurally invalid `queue-wake.json` data could raise an exception in `status`, hiding an otherwise valid command result.

Regression tests first demonstrated these failures, then passed after the fixes.
Handshake reads share an absolute deadline, and RPC writes, reads, ping replies, and unsupported-request errors use the same per-call deadline.
The retry loop checks its waiting deadline before initiating another attempt.
Wake evidence is validated independently; malformed evidence is reported as `wake-evidence-unreadable` without rewriting either evidence or command state during inspection.
The two-job test now verifies both FIFO starts, deferral during the first active turn, and duplicate suppression after both notifications disappear.
After these additions, all 148 tests passed on Python 3.10.12, 3.11.15, 3.12.13, 3.13.12, and 3.14.4.
Subprocess-aware combined branch coverage was 85.70%.
The 20-test protocol suite and 25-test recovery suite each passed 20 consecutive repetitions; Ruff, formatting, ShellCheck, and the Skill validator passed again.
The isolated queue/recovery acceptance passed again with both CLI 0.164.0 and extension binary 0.164.0-alpha.11.1 after the transport changes.
The real 0.164.0 client-tool rejection test also passed again.
The acceptance harness now tolerates only the observed temporary `list_turns is not supported yet` response while a new thread is being materialized, within the existing deadline; unrelated errors still fail, and the tool fixture now requires terminal turn state before reporting success.
These follow-up checks did not replace native `--resume` or full VS Code UI lifecycle acceptance; subsequent dated records above distinguish those results.

### Process ownership and concurrent completion acceptance

Four process-level regressions use actual child processes, OS locks, pipes, and process signals, with a fake CLI/RPC boundary:

- Killing only the helper releases its lock and lets the existing supervisor recover, preserving the original deadline and accepted delivery bytes.
- Killing only the waiting supervisor leaves the active helper able to finish.
- A concurrent explicit helper retry exits without resetting or duplicating an active attempt.
- A worker exits immediately after a CLI subprocess accepts the message, before launching a helper; the real supervisor path recovers without another queue invocation.

The expanded 152-test suite passed on Python 3.10.12, 3.11.15, 3.12.13, 3.13.12, and 3.14.4, with 85.70% subprocess-aware combined branch coverage.
All four process-level regressions passed 20 consecutive repetitions; Ruff, formatting, ShellCheck, and the Skill validator also passed.

Only child process handles created by the test are signalled.
Readiness uses pipe events, and cleanup is limited to the private fixture processes.
The tests do not terminate production jobs or services.

A separate real 0.164.0 acceptance started three background jobs against one busy isolated thread, then invoked six recovery commands concurrently.
All three artifacts were correct, each job retained exactly one queue attempt, recovery left the ordered queue unchanged, and all three completion inputs were consumed once in FIFO order after the original turn finished.
All wake processes exited afterward.
The supervisor acquired wake ownership in this run, which is permitted by the shared per-job lock.
This is CLI/RPC acceptance with a loopback model stub, not a claim of real model task quality.

Reproduce the concurrency case with an explicitly selected old CLI and a new evidence directory:

```bash
python3 tests/accept_queue_wake.py --concurrent-jobs --codex /absolute/path/to/codex --evidence-dir /tmp/clj-concurrent-unique
```

### Older protocol source verification

The official source tags below define the request fields used by the helper: queue listing with optional `cursor` and `limit`, queue starting with an optional `queuedSubmissionId`, thread reading with `includeTurns`, and thread resumption with `excludeTurns`.
Each tag also contains the upstream tests `cold_thread_resume_dispatches_a_persisted_queued_submission`, `queue_start_without_id_starts_the_head_when_idle`, and `queue_start_while_active_returns_busy_and_preserves_the_queue`.
The source declarations are consistent with the helper's request shape and FIFO strategy.
This is static source verification; these three binaries were neither installed nor executed, and this does not establish their actual compatibility with every deployed daemon.

| Source tag | Protocol declarations | Upstream queue tests |
| --- | --- | --- |
| 0.157.1 | [thread.rs](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/app-server-protocol/src/protocol/v2/thread.rs) | [thread_queue.rs](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/app-server/tests/suite/v2/thread_queue.rs) |
| 0.160.0 | [thread.rs](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server-protocol/src/protocol/v2/thread.rs) | [thread_queue.rs](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server/tests/suite/v2/thread_queue.rs) |
| 0.165.0 | [thread.rs](https://github.com/openai/codex/blob/rust-v0.165.0/codex-rs/app-server-protocol/src/protocol/v2/thread.rs) | [thread_queue.rs](https://github.com/openai/codex/blob/rust-v0.165.0/codex-rs/app-server/tests/suite/v2/thread_queue.rs) |

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

## Recorded VS Code Remote-SSH acceptance

On 2026-09-30, skill v0.4.1 was exercised in the Codex VS Code extension (`openai.chatgpt` version `26.917.62051`) on a Linux Remote-SSH host.
The extension used its bundled `codex app-server`; the skill resolved system `codex-cli 0.157.1` through PATH.
The resumed tool environment identified `codex_vscode` and the same owner thread, with an extension-host process ancestry.
All test jobs used CPU-only commands, `--delivery auto`, and `--viewer none`.
No production job was modified.

| Scenario | Verified outcome |
| --- | --- |
| Idle session | Successful artifact and exit; completion message triggered the original thread; user confirmed message and reply display. |
| Busy session | Artifact completion and queue acceptance occurred during a bounded active command; the completion message triggered a subsequent turn without interrupting the original turn. |
| Two near-simultaneous completions | Both artifacts were correct; each notification had one queue attempt and each triggered a continuation. |
| Switch to another conversation | Original thread received the notification; user confirmed no misrouting and the reply was visible on return. |
| Close and reopen chat panel | Successful artifact, one queue attempt, and continuation; user confirmed the panel was closed during completion and messages were visible on reopening. |
| Close and reopen project window | Job completed and queue acceptance preceded reopening; the user observed `steer`, then conversation history and continuation on reopening. |
| Close Remote Connection and reconnect | Job completed and queue acceptance preceded reconnection; the user observed `steer` and continuation on reconnecting. |
| Unexpected client connection failure | An incorrect PC proxy interrupted Remote-SSH before job completion. Automatic reconnection failed and requested Reload Window. After reload, the user saw the completion message and verification reply; all 12 job heartbeats and the artifact were intact. |
| Extension app-server termination | A process-identity-checked SIGKILL terminated only the current extension backend. The job produced 12 subsequent heartbeats and a valid artifact. One queue attempt was accepted, and the original conversation continued after reopening with a new backend. |
| VS Code Server main-process termination | A process-identity-checked SIGKILL terminated the Server main process. The detached job completed with 12 post-signal heartbeats. After the requested window reload, a new Server and extension backend consumed the single queued notification in the original thread. |
| Intentional failure | Exit code 7 and `failed` state matched the artifact; one queue attempt triggered a failure continuation. |
| Explicit cancellation | Controller cancellation became effective with SIGTERM; the test child exited, the natural-completion artifact was absent, and one queue attempt triggered a cancellation continuation. |

For the project-window test, completion and queue acceptance occurred at 05:12:31 UTC; the user reported reopening at approximately 05:15 UTC.
For the Close Remote Connection test, completion and queue acceptance occurred at 05:45:05 UTC; the user reported the `steer` transition on reconnecting, and the continuation verified artifacts at 05:51:04 UTC.
These observations establish recovery after reconnection but do not establish model execution while the window or connection was closed.
The transient `steer` display did not prevent consumption in either test; no stronger interpretation of that UI label was established.

Two preliminary panel-close attempts lacked the required user action or confirmation and were excluded from panel-close acceptance.
A separate attempted disconnect occurred before any job was launched and was also excluded.
The final panel-close and Close Remote Connection tests included confirmed running jobs and explicit user observations.

In the unexpected client-connection test, the PC proxy was deliberately misconfigured before 08:51 UTC while server networking remained unchanged.
The job completed and the queue command succeeded at 08:51:26 UTC.
VS Code reported `Cannot reconnect. Please reload the window.`; the user reloaded and confirmed the completion and reply in conversation history.
This covers a client-side transport failure requiring window reload, not a server-side network failure.

In the extension-backend test, SIGKILL was sent at approximately 09:09:19 UTC, and job completion and queue acceptance occurred at 09:11:19 UTC.
The interface displayed `ChatGPT hit a snag`.
The user reopened the conversation at approximately 09:17 UTC and observed the completion message in the pending-input area with a `Steer` control before continuation.
The subsequent verification ran in the original thread under a new extension backend.
The exact UI action used to restart the backend was not established; the screenshots alone do not prove when consumption began.
Shared VS Code Server and Codex daemon processes were not terminated in this test.

In the VS Code Server test, SIGKILL was sent only to the verified Server main process at approximately 09:24:04 UTC.
Job completion and queue acceptance occurred at 09:26:04 UTC.
The user reported a reload prompt and reloaded the window at approximately 09:33 UTC, then observed a brief `steer` notification followed by reply generation.
The old Server process identity was absent, and the continuation ran under a new Server and extension backend.
No recursive process-tree termination was performed; tmux sessions and the shared Codex daemon were not targeted.

### Reproduction and limits

Use unique disposable jobs with explicit success markers and artifacts, and address only an authorized test conversation.
Record the installed skill, PATH CLI, extension version, and owner thread before testing.
For busy-session tests, compare recorded command, artifact, and queue timestamps; do not infer queue timing solely from rendered chat history.
For concurrent completions, verify each artifact and each actual completion turn separately.
For UI lifecycle tests, confirm `running` before asking the user to switch, close, or disconnect, and record the exact action and approximate reconnection time.
Keep project-window closure and Close Remote Connection as separate scenarios.
Verify terminal state, log, artifact, queue attempts, actual consumption, and user-visible results before declaring acceptance.
Use a nonzero exit for failure and cancel only the disposable test through the controller for cancellation.
For service-failure tests, first identify process ownership and other connected windows, and confirm that no foreground task requires preservation.
Capture and revalidate the target executable and process start time, then use a process handle to signal only the selected process.
Record signal evidence and post-signal job output; do not substitute an entire process-group kill for a single-process test.

The observations apply to this version combination and host setup, not every VS Code installation.
Server-side network outages, recursive loss of the Server process tree, shared Codex daemon termination, host reboot, and native Windows execution were not covered.
A CLI success code remains queue acceptance only; the consumption and display findings above depend on the additional observed completion turns and user confirmation.
Keep raw logs, conversation identifiers, and host-specific job state outside the public repository.
