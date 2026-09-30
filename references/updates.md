# Release update checks

Release checks are enabled by default, with a seven-day interval.
The installer initializes the settings without accessing the network and reports how to disable checks or change their interval.
For an installation copied by another installer, run the initialization command below when installing the skill.
Existing preferences are preserved across reinstalls.

```bash
python3 "$SKILL_ROOT/scripts/check_updates.py" init
python3 "$SKILL_ROOT/scripts/check_updates.py" status
python3 "$SKILL_ROOT/scripts/check_updates.py" configure --enabled no
python3 "$SKILL_ROOT/scripts/check_updates.py" configure --enabled yes --interval 2w
```

Intervals accept positive integer hours (`12h`), days (`7d`), or weeks (`2w`).
Changing the interval does not reset the last attempt time; re-enabling also preserves it.
The settings and attempt timestamp are stored in `${CODEX_HOME:-$HOME/.codex}/long-jobs/update-check.json`, independently of the installed skill and job records.

On skill use, the agent invokes:

```bash
python3 "$SKILL_ROOT/scripts/check_updates.py" check
```

The first automatic query becomes due one interval after initialization.
Later queries become due one interval after the last attempt, including failed attempts.
An unused skill does not perform scheduled checks; the next use performs a due check.
Concurrent checks use a nonblocking local lock, and the timestamp is saved before querying GitHub.
A malformed configuration is preserved and reported as unavailable rather than silently re-enabling checks.
If the system clock moves backwards, checks wait until the stored deadline is reached.

The checker queries the public [GitHub latest-release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release) with a three-second socket timeout and no credentials.
It accepts only published, non-prerelease tags in `vMAJOR.MINOR.PATCH` or `MAJOR.MINOR.PATCH` form and compares numeric version components.
It does not fetch release assets, execute release text, modify the installed skill, or change running jobs.
A network failure does not prevent normal skill use, and recorded failed checks are not retried before the next interval.
The command requires permission to write its configuration and, when due, access GitHub; if scoped host permission is unavailable, the agent continues the requested job operation.
When a due check finds a newer version, the agent reports its version and release link.
There is no reminder on every invocation between checks.

## Explicitly requested updates

Only install an update after the user explicitly requests it.
Back up the existing installation and preserve private job state and logs.
For a managed installation from a local clone, fetch tags and select the requested release:

```bash
git fetch origin --tags
python3 scripts/install_skill.py install --ref vX.Y.Z
python3 scripts/install_skill.py install --ref vX.Y.Z --yes
python3 scripts/install_skill.py verify
```

Replace `vX.Y.Z` with the selected release tag.
Restart Codex to load the updated skill instructions.
Existing supervisors and notification workers retain their loaded code; do not restart healthy jobs merely to update the skill.
