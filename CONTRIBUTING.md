# Contributing

Open an issue before changing the delivery contract or durable state schema.
Keep the runtime dependency-free, preserve argv boundaries, and never treat
untrusted job output as agent instructions.

For every change:

1. Add or update a deterministic regression test.
2. Run `scripts/smoke_test.sh`.
3. Run the OpenAI Skill validator against the repository root.
4. Update `CHANGELOG.md` when behavior changes.

Use Conventional Commits where practical. Do not commit real Codex transcripts,
job logs, credentials, tokens, or machine-specific state.
