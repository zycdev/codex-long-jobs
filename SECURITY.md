# Security policy

Please report vulnerabilities privately to the repository maintainer before
opening a public issue. Until a dedicated security contact is published, do not
include exploit details, credentials, transcripts, or private job output in an
issue.

The supported line is the latest tagged release. This project executes commands
with the invoking user's authority, persists argv and local paths, and uses tmux
input injection only after same-user process and pane identity checks. Review
the source before installation and keep state directories private.
