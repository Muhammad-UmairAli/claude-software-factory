# Changelog

Notable changes to this version of the factory. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Action IDs (A1 to
A27) refer to the backlog of a November 2026 security review of this version.
Changes from the upstream project are listed in [NOTICE](NOTICE).

## [Unreleased]

### Changed

- The README's diagrams are SVG images instead of Mermaid, in a light and a
  dark version that follow the reader's GitHub theme. GitHub showed the
  Mermaid diagrams in a small pan-and-zoom box. `docs/images/diagrams.py`
  draws both versions from one layout.

## [0.3.0] - 2026-11-07

Phase 2 of the review roadmap, and the first public release
([claude-software-factory](https://github.com/Muhammad-UmairAli/claude-software-factory)).

### Added

- `install`, the setup command: `./install <project>` checks the project,
  copies the factory in, drafts `factory/checks.sh` from the project's own
  toolchain files, sets the base branch, `.gitignore` and the `AGENTS.md`
  section, prints or creates the labels, and runs `doctor`. It never
  overwrites a file or commits; `--dry-run` shows the plan.
- `job` posts every gate verdict on the commit it judged as the
  `factory/gate` status, and an unattended merge waits for the repository's
  own CI on the gated commit (`ci` in config.json): a failing check, one still
  pending after `ci.wait_minutes`, or a required check that never reports
  sends the job to a human (A19).
- `doctor` explains when the GitHub plan offers no branch protection, and
  warns when auto-merge runs without `ci.required_checks` in a repository
  with workflows (A19).
- The route is checked again against the diff when the gate passes: a branch
  that touches `sensitive_paths` gets the security reviewer, the full profile
  and a `high` risk floor (A20).
- Optional GitHub App identity: with `github_app` set, `job` and `doctor` mint a
  one-hour installation token per command (signed with openssl; no new
  Python package), limited to the permissions the factory needs and to this
  repository, and pass it only to their own `gh` and `git` calls. A refused
  token stops the command. `doctor` checks the key's location and
  permissions and the token GitHub grants (A17).

- PILOT.md and `benchmark/pilot-log.csv`: the internal pilot's setup
  checklist, a ticket mix that walks every control path, a safety record,
  stop rules and proposed exit criteria (A18; the pilot itself is yours to
  run).
- A hash-chained audit log for every job event and guard decision
  (`factory/jobs/audit/`), with size rotation and retention that the chain
  itself records. `job audit` verifies it and shows a daily trend, `doctor`
  fails on a broken chain, and `job audit ship` sends new records to an
  optional collector over HTTPS (A21).
- The runtime container (`factory/runtime`, `factory/bin/runtime`):
  Claude Code, git, gh, Python and openssl with none of the host's
  credentials, an egress firewall that allows only GitHub and an allowlist,
  and an unprivileged user that can't change it. A CI workflow builds the
  image and checks all three (A16).

### Changed

- The README is rewritten around how the factory works and whether it's safe
  to use, with diagrams of the job flow, the architecture and the security
  layers. The docs no longer depend on the internal review notes.
- The runtime image lets Claude Code's own install script run; newer npm
  skips install scripts unless a package is named.
- The runtime image is back on Node 24, an LTS line. Node 25, which
  Dependabot had proposed, was never LTS and reached end of life on
  2026-06-01 (Node.js release schedule). Dependabot no longer proposes major
  Node moves; make them by hand, between LTS lines.
- Agents never push: `job advance <id> gate` pushes the job's branch, and
  the guard blocks every `git push` (A16).
- The fine-grained token also needs Commit statuses read and write, and
  Checks read (A19).

## [0.2.0] - 2026-11-06

Phase 1 of the review roadmap.

### Added

- CI on every pull request: the tests on Python 3.10 and 3.12 (Ubuntu) and
  3.12 (macOS), pyflakes and ShellCheck. A weekly check flags Claude Code
  releases newer than the tested one. Dependabot for actions and pip (A8).
- The gate holds changes to dependency manifests, lockfiles and
  package-manager configs (`hold_paths`) for a human before any check runs
  (`GATE: HOLD`); `job allow` approves the exact file versions (A11).
- `doctor` warns when a CI workflow could give secrets to `factory/*`
  branches (A12) and when MCP servers are configured (A14).
- `job cancel`, `job pause` and `job resume`. `job queue` flags long waits and
  PRs closed without merging (A10).
- `job show`: a job file's verdict lines and summary for the orchestrator,
  with hidden characters removed and framed as data (A14).
- Tests for every legal and illegal job move and the loops around them (A9).
- RUNBOOK.md, SECURITY.md and this changelog (A15).

### Changed

- The code and security reviewers and the approver get the read-only,
  allowlisted shell intake and the spec writer already had (A13).
- A retried step doesn't post the same comment twice, and the worktree exists
  before intake's comment is posted (A10).
- `job queue` prints no issue titles, and the orchestrator reads the job
  folder only through `job show` (A14).
- More developer-tool configs always go to a human (`.mcp.json`, `.vscode/`,
  `.devcontainer/`, pre-commit and git-hook managers, package-manager configs),
  and the Codex and `.agents/` files are protected paths (A11).
- Job commands read agent transcripts once per process for every job, and
  cache each transcript's cost in `factory/jobs/.cost-cache.json`, so later
  commands read only new or changed transcripts (A24).

### Removed

- The builder's stop hook (`gate --hook`). It ran the checks a second time
  every round: the builder already runs them before it finishes, and the gate
  decides (A24).

## [0.1.0] - 2026-11-06

Phase 0 of the review roadmap.

### Security

- The guard reads shell commands the way bash does and ties every write to the
  agent's job, stage and round. The 18 bypasses the review found are now
  regression tests (A2).
- Control-file seal: `job seal` and `job verify`. State-changing commands and
  the gate refuse after any change to the factory's files, git's config or
  its hooks (A1).
- An outbound filter on everything the factory posts to GitHub: text that
  looks like a credential is refused, images are removed, length is capped
  (A3).
- `doctor` fails on a GitHub token with the `repo` scope (A4) and says which
  risks an `auto_merge` setting covers (A7).

### Changed

- Documentation no longer overstates the controls, and CODEOWNERS names the
  owner (A5, A6).
