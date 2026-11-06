# Runbook

How to operate the factory and what to do when it stops. For how it works and
why, see [DESIGN.md](DESIGN.md); for setup, [INSTALL.md](INSTALL.md).

- **Owner:** the repository owner in [`.github/CODEOWNERS`](.github/CODEOWNERS).
- **Scope:** one repository and one operator; runs start by hand with
  `/factory run` in a Claude Code session.
- **Depends on:** Claude Code (the version in `.github/claude-code-tested` or
  later), `git`, `gh` with a fine-grained token, Python 3.10 or later, and a
  trusted project folder.

Run every command from the repository root. Commands that take a reason read
it from stdin:

```bash
factory/bin/job cancel 12 <<'EOF'
The author withdrew the request.
EOF
```

## Routine

| When | Do |
|---|---|
| Before a run | `/factory run` runs `factory/bin/doctor` and `factory/bin/job seal` itself. Fix every `FAIL`; read each `warn` once. |
| During a run | `factory/bin/job summary` for each job's stage, PR, cost and time; the dashboard (README, "Dashboard") for detail. |
| What waits on you | `factory/bin/job queue`: `wait` rows are PRs to review, questions waiting on authors and held dependency changes. |
| After a run | Review the `needs-human` PRs. Merge one to approve it, or give feedback with `/factory`. |
| Weekly | The `compat` workflow fails when a newer Claude Code is out (see "Claude Code upgrade"). Review Dependabot PRs. |

## Stop everything

1. Pause the factory. No job starts, and no job moves into a stage where
   agents or checks run, until you resume. People can still merge or cancel.

   ```bash
   factory/bin/job pause <<'EOF'
   Investigating an unexpected push.
   EOF
   ```

2. Stop the orchestrator session; its agents run inside it. Check that no
   `claude` process is still working in this folder.
3. Cancel each job that shouldn't resume: `factory/bin/job cancel <n>` with a
   reason. It posts the reason on the issue, closes the PR, deletes the branch
   and worktree, and takes the `factory` label off the issue.
4. `factory/bin/job resume` when it's safe to continue.

## When something stops

| You see | It means | Do |
|---|---|---|
| `control files changed since job seal` | A file under `factory/`, `.claude/`, `.codex/`, `.agents/`, git's config or hooks changed after the seal. | Stop the run. `factory/bin/job verify` lists the files; inspect them with `git status` and `git diff`. If you don't recognize the change, follow "Suspected compromise". If it's yours, merge it through review, then run `doctor` and `job seal`. Never re-seal just to get past the refusal. Approving a permission prompt with "don't ask again" changes `.claude/settings.local.json`. |
| `no guard activity` | An agent ran without its guard hook: the folder isn't trusted, or a policy disables hooks. | Stop the run and run `factory/bin/doctor`. Trust the folder (open `claude` here and accept the prompt) or get the policy changed. Check what the unguarded agent did (its branch, the job folder) before resuming. |
| `looks like a credential` | Text bound for GitHub matched the outbound filter. | Treat it as a possible leak. Open the file and line it names; rotate the credential if it's real; then fix the file and run the step again. |
| `GATE: HOLD` | The branch changes a dependency manifest, lockfile or package-manager config, so the gate ran nothing. | Review those files on the PR: new packages, install scripts, registries. If they're fine, `factory/bin/job allow <n>`. Otherwise send feedback or reject. |
| A `needs-human` job whose decision says "the last of" or "over its budget" | The job used its rounds or its budget. | Read the decision (`factory/bin/job show <n> decision.md --full`). Feedback gives it `max_rounds` more rounds and a fresh budget; or reject it. |
| A `needs-human` job whose decision says the branch conflicts, or the gate failed after syncing | The approved branch didn't survive the merge with the base. | Send feedback so the builder fixes it, or fix the branch yourself and merge by hand. |
| A `needs-human` job whose decision says CI failed, didn't finish, or a check never reported | With `auto_merge` on, the repository's CI didn't pass on the gated commit. | Open the PR's checks. Send feedback, or merge by hand once CI passes. |
| `can't have branch protection` in doctor | The repository is private on GitHub Free, which has no branch protection. | Keep `auto_merge` off and review every factory PR, or move to GitHub Pro or Team. |
| `audit log` FAIL in doctor, or `chain BROKEN` from `job audit` | An audit record was changed or removed after it was written. | Treat it as an incident: follow "Suspected compromise". Don't delete or repair the log; copy it as evidence. |
| `records waiting` from `job audit ship` | The collector at `audit.forward_url` didn't accept them. | Check the collector. Records stay queued and go with the next `job audit ship`. In the runtime container, its host must be in `factory/runtime/egress-allowlist`. |
| `PR #n was closed without merging` | Someone closed the job's PR. | `factory/bin/job cancel <n>`, or reopen the PR. |
| `waiting N days` | A question or a PR has waited past `stale_after_days`. | Nudge the person, or reject the job. |
| `issue is closed or no longer labeled factory` | The job still holds a concurrency slot. | `factory/bin/job cancel <n>` frees it. |
| `FAIL  GitHub token` in doctor | `gh` uses a token that reaches every repository. | See "GitHub credentials". |
| `warn  MCP servers configured` in doctor | The orchestrator session would have tools beyond the shell. | Start runs with `claude --strict-mcp-config` and check that `/mcp` lists none. |
| The session died mid-run | Nothing is lost: state lives in files. | Start a new session and run `/factory run`; each job resumes from its stage. |

## Suspected compromise or prompt injection

Signs: a seal refusal you can't explain, guard blocks you didn't expect in
`factory/jobs/guard.jsonl`, commits, pushes or comments nobody asked for, or
agent text addressed to the orchestrator.

1. Pause the factory and stop the session (see "Stop everything").
2. Keep the evidence: copy `factory/jobs/` (each job's `events.jsonl`, the
   round folders, `guard.jsonl`) somewhere outside the repository before
   deleting anything.
3. Find what changed: `factory/bin/job verify`, `git status`, recent commits
   on every branch, and the repository's branches, PRs and comments on GitHub.
4. Rotate the GitHub credentials (the App's private key, or the token). If
   code from a job branch ran on this machine, rotate any other credential it
   could reach: the guard is a tripwire, not a sandbox.
5. Cancel the affected jobs and delete their branches.
6. Write down what happened, without secrets. If the guard missed something,
   add the command to `tests/test_guard_probes.py` with the fix.
7. Resume only once you know the cause: `doctor`, `job seal`, `job resume`.

## GitHub credentials

The scripts need a GitHub login to push branches, open and merge PRs, and
comment. Never let that be a token that reaches every repository you can
access (a classic token, or a plain `gh auth login`): code the agents write
runs where it could use it. `doctor` fails on one.

### A fine-grained token (the default)

1. Create a fine-grained token for the project's repository only, with
   Contents, Issues, Pull requests and Commit statuses read and write, and
   Checks read. Give it an expiry date and put a reminder in your calendar.
2. Log `gh` in with it, then revoke the old token in your GitHub settings.
3. `factory/bin/doctor` must print `ok    GitHub token`.

### Optional: a GitHub App

An App replaces the token with one-hour tokens and a bot identity that
doesn't depend on your account. It's worth it for client repositories or a
shared factory; the pilot doesn't need it. With `github_app` set in
`factory/config.json`, the scripts mint a token for each command, limited to
the permissions below and to this repository, and stop if GitHub refuses one.

1. In GitHub, open Settings, Developer settings, GitHub Apps, New GitHub App.
   Give it a name, use the repository URL as its homepage, and clear
   "Active" under Webhook: the factory needs no webhooks.
2. Repository permissions: Contents, Issues, Pull requests and Commit
   statuses read and write; Checks read-only; Metadata read-only. Nothing
   else: no Workflows, no Administration. Install it only on this account.
3. Create it, note the App ID, and generate a private key. Move the
   downloaded `.pem` to `~/.config/software-factory/github-app.pem` and run
   `chmod 600` on it. Never put it inside the repository.
4. Install the App on this repository only. The installation ID is the
   number at the end of the installation's settings page URL.
5. Set `github_app` in `factory/config.json` (`app_id`, `installation_id`,
   and `private_key` if the key lives elsewhere), merge that through a pull
   request, then run `factory/bin/doctor` and `factory/bin/job seal`.
   `doctor` mints a token and prints the permissions GitHub granted, never
   the token.

To rotate the key, generate a new one in the App's settings, replace the
file, then delete the old key there.

## Claude Code upgrade

The guard works only while Claude Code runs project agent hooks.

1. The weekly `compat` workflow fails when a release is newer than
   `.github/claude-code-tested`.
2. After upgrading, run the test suite, then one `/factory run` on a
   throwaway issue in a test repository. Check that `factory/jobs/guard.jsonl`
   has entries for every agent that ran.
3. Update `.github/claude-code-tested` to the new version in a pull request.

## Clean up

- A rejected or cancelled job keeps its folder. Delete `factory/jobs/<id>` to
  run the issue again.
- `git worktree prune` drops git's records of worktree folders that are gone.

## Where to look

| What | Where |
|---|---|
| A job's state | `factory/bin/job show <n> job.json` |
| A job's audit trail | `factory/bin/job history <n>` (`factory/jobs/<id>/events.jsonl`) |
| Every guard decision | `factory/jobs/guard.jsonl` |
| Both, hash-chained | `factory/bin/job audit` (`factory/jobs/audit/`) |
| Gate results | `factory/jobs/<id>/round-<N>/checks.md` and `checks.log` |
| Agent transcripts and cost | Claude Code's `projects/` folder (`~/.claude/`, or `CLAUDE_CONFIG_DIR`) |
