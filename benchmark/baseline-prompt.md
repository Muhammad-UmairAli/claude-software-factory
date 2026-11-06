# Baseline prompt (arm A: single prompt)

Paste this into a fresh Claude Code session started in a new worktree on a new
branch from the ticket's frozen base commit. Fill in the ticket. Don't add
anything else, and answer questions only with information the factory's
intake would also have had (the issue text and its comments).

---

Implement this GitHub issue in this repository, as one small pull request.

Issue #<n>: <title>

<issue body, verbatim>

- Read AGENTS.md (or CLAUDE.md) first and follow it.
- Make the smallest change that fully does what the issue asks. Don't add
  extras the issue doesn't ask for.
- Add or update tests for the new behaviour.
- Run `bash factory/checks.sh` and make it pass. Don't weaken or skip tests.
- Never read .env files or secrets.
- Commit with short one-line messages, push the branch, and open a draft PR
  whose body starts with "Closes #<n>" and lists how you verified each part of
  the issue.
