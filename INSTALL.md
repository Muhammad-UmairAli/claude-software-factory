# Install the software factory

You are a coding agent installing this factory into the user's project. Copy
what's generic, adapt what's repo-specific, verify it, and report. Work in the
project's root.

The source is the folder the user pointed you at: a local checkout of the
factory, pinned to a release tag. Copy from that folder. Don't download the
files from the internet, and if the user gave you a URL, ask them for a local
checkout at a tag instead.

## The setup command

`./install <project>`, run from this folder, does steps 0 and 1 and the
mechanical parts of step 2: it checks the project, copies the files, drafts
`factory/checks.sh` from the project's own files, sets `base_branch`, adds the
`.gitignore` lines and the `AGENTS.md` section, prints the label commands (or
creates the labels with `--create-labels`), and runs `factory/bin/doctor
--offline`. It never overwrites a file and never commits; `--dry-run` shows
the plan first.

After it, carry on with what needs judgment: read and run the drafted
`checks.sh`, tailor the agents, `escalate_paths` and `sensitive_paths`, and
`.claude/settings.json` (step 2), then steps 3 and 4. An agent installing by
hand follows every step below.

## 0. Check the project

Stop and tell the user if any of these fail:

- It's a git repo with an `origin` on GitHub, and `gh auth status` shows a user
  who can push to it and manage its labels, through a fine-grained token
  limited to this repository (Contents, Issues, Pull requests and Commit
  statuses read and write; Checks read). `doctor` fails on a classic or OAuth token with
  the `repo` scope (what a plain `gh auth login` gives), because that token
  reaches every repository the user can access.
- Its default branch (`gh repo view --json defaultBranchRef`) exists on
  `origin`.
- `claude --version` is 2.1.257 or later.
- `python3 --version` is 3.10 or later. The scripts use only the standard
  library.
- None of the files in step 1 already exist. If one does, show the user the
  clash and ask before overwriting.

Tell the user, once, before you go on:

- The factory runs agent-written code (tests, builds, dev servers) with
  their access. Running it in its runtime container (`factory/bin/runtime`,
  needs Docker), with a fine-grained GitHub token scoped to this repo, is
  strongly recommended. If `checks.sh` installs packages from somewhere other
  than npm or PyPI, add those hosts to `factory/runtime/egress-allowlist`.
- The factory is GPLv3 (see `NOTICE`). If this is a client repository or one
  they distribute, they should confirm with legal before installing.

## 1. Copy

Copy these files to the same paths in the project, unchanged:

```
.claude/skills/factory/SKILL.md
.claude/agents/intake.md
.claude/agents/spec-writer.md
.claude/agents/builder.md
.claude/agents/reviewer-code.md
.claude/agents/reviewer-security.md
.claude/agents/reviewer-ux.md
.claude/agents/reviewer-ui.md
.claude/agents/approver.md
factory/config.json
factory/checks.sh
factory/bin/job
factory/bin/gate
factory/bin/route
factory/bin/guard
factory/bin/doctor
factory/bin/git-credential-app
factory/bin/runtime
factory/lib/github_app.py
factory/lib/audit.py
factory/runtime/Dockerfile
factory/runtime/entrypoint.sh
factory/runtime/init-firewall.sh
factory/runtime/egress-allowlist
factory/dashboard.html
```

Also copy `LICENSE` to `factory/LICENSE` and `NOTICE` to `factory/NOTICE`, so
the license and attribution travel with the files.

```bash
SRC=<the factory folder>
for f in <the paths above>; do
  mkdir -p "$(dirname "$f")" && cp "$SRC/$f" "$f"
done
cp "$SRC/LICENSE" factory/LICENSE && cp "$SRC/NOTICE" factory/NOTICE
chmod +x factory/checks.sh factory/bin/*
```

Don't copy `README.md`, `DESIGN.md`, `INSTALL.md`,
`BENCHMARK.md`, `benchmark/`, `PILOT.md`, `tests/` or `examples/`. They describe the factory, not the user's project.

## 2. Adapt

**`factory/checks.sh`.** Replace the placeholder with the project's real
checks: install dependencies if a fresh worktree needs them, create any env
file the checks need from its committed example (agents can't touch `.env`
files), then lint, typecheck, tests and build, whichever the project has. Find
them in `AGENTS.md`, `CLAUDE.md`, `package.json` scripts, `Makefile`,
`pyproject.toml` and CI config. The script runs with a job's worktree as its
working directory, a scrubbed environment and a time limit. It must be
non-interactive, must not rewrite tracked files (no `--fix`, no
`--update-snapshots`), and must exit non-zero on any failure. Keep
`set -euo pipefail`.

**`factory/config.json`.**

- `base_branch`: the default branch.
- `merge_method`: `merge`, `squash` or `rebase`, one the repo allows
  (`gh api repos/{owner}/{repo} --jq '{allow_merge_commit, allow_squash_merge, allow_rebase_merge}'`).
- `checks.env_allowlist`: add a variable only if `checks.sh` can't run without
  it (a toolchain path, a local test database URL). Never add tokens or
  production credentials. Raise `checks.timeout_sec` if the suite is slow.
- `protected_paths`: keep the defaults and don't add the project's tests.
  Features legitimately change existing tests, and a protected test blocks the
  job with no way through. The code reviewer catches weakened or special-cased
  tests instead. Add a path only when no job should ever change it.
- `escalate_paths`: keep the defaults, remove patterns for ecosystems the
  project doesn't use, and add the project's own sensitive paths (auth,
  payments, data access rules, deployment config). They are fnmatch patterns,
  so `*` also matches `/`.
- `hold_paths`: keep the defaults and add any other file that makes
  `checks.sh` install or fetch code (a vendored package list, a custom
  registry config). The gate holds a branch that changes one until a human
  runs `factory/bin/job allow <n>`.
- `sensitive_paths`: add the project's own names for access control,
  identity, payments and secrets (a `guards/` folder, a `tenancy` module).
  Matching ignores case. A branch that touches one gets a security review
  and can't merge unattended below `high` risk.
- `ports`: move `base` if the project already uses ports 5100–5199.
- Leave `concurrency`, `max_rounds`, `max_cost_usd_per_job`, `auto_merge`,
  `issue_authors`, `profiles`, `sizing`, `models`, `routing`,
  `surface_reviewers` and `prices_usd_per_mtok` as they are unless the user
  asks. Suggest they run `BENCHMARK.md` before changing `profiles`.

**Agents.** The checklists are general on purpose. Tailor them to the stack
without changing their structure, file names, frontmatter (especially the
`hooks:` block), first-line formats or one-line replies:

- `reviewer-security.md`: this project's hot spots (its auth, its data access
  rules, its payment and webhook code, where user input meets queries or HTML).
- `reviewer-ui.md`: where the design tokens, theme and shared components live.
- `reviewer-ux.md`: the kinds of users the app has.
- `approver.md`: what a human must always see in this project, added to the
  ESCALATE list.
- `intake.md`: one line under **Decide** saying what the project is for, so
  intake can tell what belongs.

**`AGENTS.md`** (or `CLAUDE.md`, whichever the project uses; create `AGENTS.md`
if neither exists). Every agent reads it. Make sure it says:

- how to set up a fresh checkout (install dependencies);
- how to start the dev server on a given port, if the project has one;
- the validation commands, matching `factory/checks.sh`.

Then add a short section:

```markdown
## Software factory

`/factory run` builds every open GitHub issue labeled `factory` through
intake → spec → build → gate → review → approve, one PR per issue; a human
merges unless `auto_merge` allows it. State is in `factory/jobs/`
(gitignored); `factory/bin/job` is its only writer. Checks:
`factory/checks.sh`. Preflight: `factory/bin/doctor`. Dashboard:
`cd factory && python3 -m http.server --bind 127.0.0.1 8000`, then open
http://127.0.0.1:8000/dashboard.html.

Each job works in its own git worktree, a full checkout of the repo at
`.worktrees/<job-id>` (gitignored). A tool run from the repo root that doesn't
read `.gitignore` sees those copies too; exclude `.worktrees/` in its config.
```

**`.gitignore`.** Append these two lines:

```gitignore
/factory/jobs/
/.worktrees/
```

`factory/jobs/` is runtime state (job metadata, specs, reviews, check logs)
local to the machine running the factory. Each job's worktree is a full
checkout in `.worktrees/<job-id>`. Neither belongs in the repo. Everything you
copied in step 1 does, so ignore only these two paths, never `.claude/` or
`factory/`.

**`.claude/settings.json`.** Propose this to the user and add it only with
their OK, merged into any existing settings. Show them the result:

```json
{
  "sandbox": { "enabled": true },
  "permissions": {
    "deny": [
      "Read(./.env)",
      "Read(./.env.*)",
      "Read(~/.ssh/**)",
      "Read(~/.aws/**)"
    ]
  }
}
```

The sandbox limits what agents' shell commands can write and reach on the
network. Deny rules apply in every permission mode and to subagents. If the
organization's managed policy sets `allowManagedPermissionRulesOnly`, the
`permissions` block here is ignored and the managed rules apply instead;
`factory/bin/doctor` reports this. If the policy sets `allowManagedHooksOnly`
or `disableAllHooks`, the agents' guard can't run: stop and tell the user,
since the factory must not run without it. Don't deny reads of `~/.config/gh`:
the sandbox enforces read rules too, and `gh` (which `factory/bin/job` runs)
then can't start. If the
sandbox breaks a needed tool (a dev server, a package registry), adjust its
`sandbox.network.allowedDomains` or `sandbox.excludedCommands` with the user
rather than turning it off. Check the rules with `/permissions` in an
interactive session.

**Labels.** Create them on GitHub:

```bash
gh label create factory --color 5319E7 --description "Build this with the software factory"
gh label create factory:needs-info --color FBCA04 --description "The factory asked the author a question"
gh label create factory:needs-human --color D93F0B --description "The factory needs a human decision on the PR"
gh label create factory:approved --color 0E8A16 --description "The factory approved this PR; a human merges it"
```

**Branch protection.** Tell the user whether the base branch requires PR
reviews and status checks (`gh api repos/{owner}/{repo}/branches/<base>/protection`).
If it doesn't, recommend turning it on before any `auto_merge`. Don't change
it yourself.

**CI secrets.** Factory branches are pushed before anyone reviews them. Show
the user any `CI:` warning from `factory/bin/doctor`: each names a workflow
that would give secrets to a factory branch's code. Fixing it changes CI, so
the user decides; don't edit workflows yourself.

## 3. Verify

1. In the project root, on the current branch, `bash factory/checks.sh` exits 0.
   If it fails there, the factory can never pass a job: fix the script (not
   the project) or tell the user what's broken.
2. `factory/bin/doctor` prints no `FAIL`. Show the user any `warn` lines.
3. `factory/bin/job summary` runs without an error. (Don't run `job queue`
   yet: job commands need the seal, and `job seal` only seals control files
   that are committed and on `origin/<base>`.)
4. `git check-ignore factory/jobs/x .worktrees/x` prints both paths, and
   `git status --porcelain --untracked-files=all` lists every file from step 1
   as committable.

## 4. Hand over

Stay on the branch the user is on: don't create or check out another branch.
Commit the install there with a one-line message and show the user the diff.
Ask before pushing: the factory branches every job from `origin/<base_branch>`,
so the install must be on that branch there before the first run, and it gets
there through the project's normal review, not a direct push.

Then tell the user, in a few lines:

- Label one or two small issues `factory`, then run `/factory run` in Claude
  Code from the project root. Accept the workspace trust prompt: the agents'
  guard hooks only run in a trusted folder, headless (`claude -p`) runs never
  ask, and the factory stops a job whose agents ran unguarded.
- Every job ends as a PR. By default the approver's approved PRs wait labeled
  `factory:approved`; escalations wait labeled `factory:needs-human`. Approve
  one by merging it on GitHub, or tell the factory what to change with
  `/factory <anything>`.
- Each job is capped at `max_cost_usd_per_job` of API-equivalent spend and
  `max_rounds` rounds.
- Keep `concurrency` at 1 and `auto_merge` off until they've read enough PRs
  to trust it.
- Once the install is merged into `origin/<base>`, each `/factory run` runs
  `doctor` and then `factory/bin/job seal`. If a step later refuses because
  the control files changed, something changed the factory mid-run: review
  the change before sealing again.
