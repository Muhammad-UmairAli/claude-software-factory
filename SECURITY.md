# Security policy

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting: open the
repository's Security tab and choose "Report a vulnerability". If that option
isn't there, contact the owner listed in
[`.github/CODEOWNERS`](.github/CODEOWNERS) privately. Don't open an issue or a
pull request, and don't post details in a comment: everyone can see them, and
issues are the factory's input.

Include what's needed to reproduce it: the command or input, the factory
commit, the Claude Code version, and what happened. Never include a real
token, key or password; describe it instead.

## In scope

- A way past a control that [DESIGN.md, "Security model"](DESIGN.md#security-model)
  documents: the guard, the control-file seal, the gate and its holds, the
  merge policy, the outbound filter, the token check, or the dashboard's
  protections.
- Issue or comment text that changes what the factory does beyond the job it
  asks for.
- Secrets reaching logs, job files or GitHub.

The residual risks DESIGN.md lists are known limits, not vulnerabilities,
unless you find a way past a promise the docs make. Each guard bypass becomes
a case in `tests/test_guard_probes.py`.

## Supported versions

Fixes go to the latest commit on `main` only.
