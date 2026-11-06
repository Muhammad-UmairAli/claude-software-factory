# Design

How the factory works, why it's built this way, and what protects the repo
and the machine it runs on. The source is small enough to read in one sitting:
one skill, eight agents, five scripts and a config file.

## The idea

A software factory turns a queue of requests into merged code with a fixed
pipeline. Scripts handle the deterministic stages, such as running the tests,
moving state, opening PRs and merging. Agents do the judgment in between:
whether a request is worth building, what exactly to build, the code itself,
and whether it's good. Each stage leaves a file behind, so you can see what
happened and why. Every step's file opens with a `## Summary` of three to five
bullets, so a human can skim a job on the dashboard and read the detail only
where it matters.

## Pieces

| Piece | File | Job |
|---|---|---|
| Queue | GitHub issues labeled `factory` | What to build. Only issues from authors with triage or write access run (`issue_authors`). |
| Orchestrator | `.claude/skills/factory/SKILL.md` | The session that runs `/factory`. Runs the preflight, starts jobs, spawns agents, calls the scripts. Writes nothing itself. |
| Agents | `.claude/agents/*.md` | One file per role. Each reads files, writes exactly one file, and replies with one line. |
| Guard | `factory/bin/guard` | Every agent's PreToolUse hook. Enforces what each agent may write, read and run. |
| State | `factory/jobs/<job-id>/` | `job.json`, `events.jsonl` and every agent's file. Gitignored, local to the machine running the factory. |
| Spine | `factory/bin/job`, `gate`, `route` | Deterministic. `job` is the only writer of `job.json`, refuses illegal moves, opens PRs and merges. |
| Preflight | `factory/bin/doctor`, `job seal` | Doctor validates config, agents, the GitHub token, labels, branch protection and sandbox; `job seal` then fingerprints the control files that every later step checks. |
| Checks | `factory/checks.sh` | The repo's test, lint, typecheck and build commands. The only repo-specific script. |
| Exit | the PR | Every job ends as a PR that closes its issue. Merging it is the approval. |

## Pipeline

```
intake             → ACCEPT (scope possibly changed) | NEEDS-INFO | REJECT, posted as an issue comment
route (script)     → reviewers + every agent's model from risk/surfaces via config.json
worktree           → factory/<job-id> from origin/<base>
spec-writer        → spec.md
builder            → commits; `job advance … gate` pushes and opens the draft PR "Closes #<n>"
gate (script)      → checks.sh; FAIL → builder gets the log, no reviewers spawned
reviewers (routed) → parallel, WAIT FOR ALL; CHANGES → builder → gate → reviewers (max rounds, budget)
approver           → APPROVE: `job merge`: escalate_paths → human; else sync base, gate,
                              then merge the gated commit (auto_merge) or hand off (factory:approved)
                     ESCALATE: PR ready, decision as PR comment, label factory:needs-human
```

`stage` in `job.json` is one of `intake`, `needs-info`, `spec`, `build`,
`gate`, `review`, `approve`, `merging`, `needs-human`, `merged`, `rejected`.
The legal moves are in `TRANSITIONS` at the top of `factory/bin/job`. Every
move and every refused move is appended to the job's `events.jsonl`.

## Decisions

**Issues are the queue; you trigger the run.** `/factory run` drains every open
`factory` issue. Nothing runs in GitHub Actions or on a schedule: the factory
runs on your machine, on your Claude plan, when you start it. Keeping it local
means your worktrees, dev servers and dashboard are right there.

**One orchestrator for every job.** The session running `/factory run` starts up
to `concurrency` jobs and spawns every agent as a background subagent. There
are no per-job orchestrators to coordinate. Its context stays small because
agents reply with one line and the scripts print one line; the detail lives in
files the next agent reads.

**Intake decides once, before anything is built.** It decides whether the
issue is worth building, rewrites the scope when the issue is too big or vague,
and asks the author when only they can answer. Its decision goes on the issue
as a comment; the issue body stays as written. Questions go to the author
before a spec exists, so the spec writer never stalls on one.

**Risk routes the work, a script does the routing.** Intake rates risk (`low`,
`medium`, `high`) and surfaces (`ui`, `security`). `factory/bin/route` looks
them up in `config.json`:

| | code review | security | ux | ui |
|---|---|---|---|---|
| low | Haiku | | | |
| medium | Sonnet | | | |
| high | Sonnet | Opus | | |
| + `ui` surface | | | Sonnet | Haiku |
| + `security` surface | | Opus | | |

A surface never downgrades a reviewer the risk level already chose. Intake
judges the issue, not the code, so the route is checked again against the
diff when the gate passes: a branch that touches `sensitive_paths` (access
control, identity, payments, secrets) gets the security reviewer and the
full profile, and a `high` risk floor keeps it from merging unattended unless
`auto_merge.max_risk` is `high`. Route also
records the models for intake, spec-writer, builder and approver from
`models`, and the orchestrator passes every model to the Agent tool, which
overrides the agent file's frontmatter. Every cost decision is in one file and
changes without touching a prompt.

**Lite for small work, full for the rest.** A pipeline's ceremony pays off on
work that grows and gets maintained, and costs more than it returns on small,
contained changes. `profiles` maps risk to a profile. Lite drops the two
agents whose output a script can derive: the spec (intake's scope and
acceptance criteria, which intake must write as numbered, checkable items)
and the approval (the gate passed and every routed review passed). It keeps
everything that checks the code. Because nothing re-checks the builder's
evidence in lite, lite PRs never auto-merge.

**Size is budgeted before and measured after.** Agents do worse as a change
grows past what one session can hold. Intake works to `sizing` in config.json
(files and changed lines, one concern per job), rescopes bigger issues to a
first slice and lists the rest as follow-ups. The gate records the branch's
real size (`SIZE:` in checks.md, `size` in job.json) and notes when it's over
budget; the approver escalates an oversized change that build.md doesn't
justify.

**Measure before you trust it.** Whether the factory beats a single prompt
depends on the project and the ticket mix. `BENCHMARK.md` describes a blind
comparison on real tickets, and `job metrics` exports the factory side of it.

**Scripts decide whether it works.** The gate runs `checks.sh` before any
reviewer is spawned, and again after syncing with the base before the merge.
A failing build goes straight back to the builder with the log. The gate runs
the main checkout's copy of `checks.sh`, so a branch can't edit the checks it
is judged by, and it fails any branch that adds, modifies or deletes a file
under `protected_paths`. Tests aren't protected, since features have to
update them; the code reviewer fails any test that was weakened, skipped or
special-cased to pass. The builder runs the same checks before it finishes,
as its instructions say; the gate's run is the one that counts, and a failure
there costs the job a round. (The builder no longer has a stop hook that ran
them a third time.)

**Agents judge what scripts can't.** Reviewers don't re-run the test suite;
they read the diff for correctness, scope, security, UX and visual fit.

**A PR per issue, pushed, opened and merged by a script.** The builder only
commits. `factory/bin/job` pushes the job's branch, so no agent needs to reach
GitHub, opens the draft PR (title from the spec, body from the
build summary) with list arguments, never through a shell, and merges with
`gh pr merge --match-head-commit`, so branch protection, required checks and
the PR history all apply, only the exact commit the gate passed can merge,
and your main checkout is never touched. Before merging, the branch is synced
with the base and gated again, because another job may have merged since it
was tested.

**A human merges by default.** With `auto_merge.enabled` false, an approved PR
is marked ready, commented and labeled `factory:approved`; merging it is the
approval. When you enable auto-merge, `auto_merge.max_risk` caps it (only
`low` by default), and anything touching `escalate_paths` (CI, infrastructure,
dependency manifests, migrations, agent instructions) still goes to a human.
An escalated PR is labeled `factory:needs-human` with a specific question.
Merge it to approve, or tell the factory what to change with
`/factory <anything>`: your words go to the builder as `feedback-<k>.md` and
the job runs another round with a fresh round and cost budget.

**State is files, so runs resume.** Every fact the orchestrator needs is in
`factory/jobs/`. If the session dies or compacts, a fresh `/factory run` reads
the queue and resumes every job from its stage. One lock over the jobs folder
serializes the scripts, so two commands can never deadlock or interleave a
state change.

**Stopping is always possible, and retries are safe.** `job cancel` stops one
job in any stage and cleans up its PR, branch and worktree; `job pause` stops
all new work and every move into a stage where agents or checks run, and
works even when the seal is broken. Within a move, local steps that can fail
(creating the worktree) come before anything is posted on GitHub, and each
posted comment is recorded per job, so retrying a refused move doesn't post it
twice. `job queue` flags waits longer than `stale_after_days` (default 7) and
PRs closed without merging.

**Cost is measured and capped per job.** Claude Code writes each subagent's
transcript, with model and token counts per message, to
`~/.claude/projects/<project>/<session>/subagents/`. Every factory prompt starts
with the job's folder, so `factory/bin/job` sums the transcripts that name it
and prices them with `prices_usd_per_mtok`, including Haiku's long-prompt
tier. When a job's cost passes `max_cost_usd_per_job`, it goes to a human
instead of another round. The figure is API-equivalent; on a subscription it
shows relative spend, not your bill. The transcript format isn't a documented
interface, so unreadable lines are skipped and unknown models are reported
rather than allowed to block a state change.

## Security model

The factory reads text that people outside the core team can influence (issue
bodies and comments), has agents write code from it, and then runs that code
on your machine and pushes it to GitHub. The controls below are layered; none
is enough alone.

### Trust boundaries

| Input | Who controls it | Reaches |
|---|---|---|
| Issue body, title and author's comments | The issue author | `issue.md`, quoted as data, read by intake, spec-writer, approver |
| Spec, build notes, reviews | Agents (influenced by the issue) | Later agents; PR title and body via `job`, as arguments, never a shell |
| Branch code | The builder | `checks.sh` (runs it), reviewers, the base branch on merge |
| `factory/`, `.claude/` in the main checkout | You | Every script and agent; protected from jobs |

### Controls

1. **Who can file work.** Only labeled issues run, and by default only from
   authors with triage or write access, because an author keeps editing the
   body and commenting after a maintainer labels it.
2. **Prompt injection containment.** Issue text is quoted data in a file,
   never part of a prompt. Intake, the spec-writer, the code and security
   reviewers and the approver get a read-only shell (search and
   `git log`/`show`/`diff` inside the repository, enforced by an allowlist):
   they judge code but never run it. The ux and ui reviewers keep a full
   shell to start the dev server.
3. **Per-role tool limits (`guard`).** Each agent writes only its own factory
   file, and only for a job in its own stage and round; the builder can also
   edit that job's worktree, except protected paths and git internals. The
   guard reads each shell command the way bash does (quotes, `cd`, `$(...)`,
   `bash -c`, heredocs, inline interpreter code) and blocks common forms of:
   reading `.env` files, keys, credential folders and shell history; printing
   the environment or token variables; GitHub write commands (PRs, issues,
   labels, API writes); running the factory's own scripts; any `git push` (the
   factory pushes the job's branch itself); changing git remotes or config;
   state-changing git in the main checkout; shell writes into the main checkout
   or home-folder dotfiles; piping downloads into a shell; `sudo`. It fails
   closed. It is still a pattern check: code an agent writes to a file and then
   runs is invisible to it. Controls 4 and 9 and an isolated machine cover
   that. `tests/test_guard_probes.py` holds the bypasses found so far.
4. **Control-file seal.** After `doctor` passes, `job seal` fingerprints
   `factory/` (not its jobs), `.claude/` (ignored files too), git's config and
   hooks, and the global git config. It seals only reviewed files: nothing
   uncommitted, and the same as `origin/<base>`. Every state-changing job
   command and the gate refuse after any change, so tampering with
   `checks.sh`, the merge policy or a git hook stops the run whatever made it.
5. **Deterministic gate.** Checks come from the main checkout; protected paths
   include the factory's own files and agent settings, added or changed; the
   environment is scrubbed to `checks.env_allowlist`; a time limit applies.
   When the branch changes a file in `hold_paths` (dependency manifests,
   lockfiles, package-manager configs), the gate runs nothing and returns
   `GATE: HOLD`: installing those files would run code nobody has reviewed. A
   human approves them with `job allow`, which records each file's git object
   id, so a later change is held again. `job` posts every verdict on the
   commit it judged as the `factory/gate` status, so the PR shows it.
6. **Merge policy in code.** `escalate_paths` and `auto_merge` are enforced by
   `job merge`, not by the approver's judgment. Only the gated commit can
   merge. With the defaults, nothing merges without a human. When
   `auto_merge` is on, only full-profile PRs at or below `max_risk` qualify;
   lite PRs always wait for a human, and `doctor` names the risks a setting
   covers. Before an unattended merge, `job merge` also waits for the
   repository's own CI on the gated commit (`ci` in config.json) and sends
   the job to a human when a check fails, stays pending or never reports.
7. **Fail closed when the guard is off.** Claude Code silently skips project
   agent hooks in an untrusted folder or when a policy disables hooks.
   `doctor` checks trust and policy before a run, and `job advance` refuses to
   leave an agent's stage unless the guard logged that agent's tool calls
   (`factory/jobs/guard.jsonl`) since the stage began. Found in the live smoke
   test, where an untrusted folder let agents run unguarded.
8. **Audit trail.** `events.jsonl` per job records every transition and
   refusal; `guard.jsonl` records every guard decision (commands only for
   blocks, tokens masked); issue and PR comments carry each decision. All of
   it also goes into one hash-chained log (`factory/jobs/audit/`): each record
   carries the previous record's hash, so an edited or deleted record breaks
   the chain, and `job audit` and `doctor` say where. It rotates by size and
   keeps rotated files `audit.keep_days`; each deletion is itself a record.
   Records cut off the end can only be seen against a copy elsewhere, so
   `job audit ship` sends new records to a collector (`audit.forward_url`).
9. **Outbound filter.** Every comment, PR title and PR body the factory posts
   passes one filter: text that looks like a credential is refused (the
   refusal names the kind and line, never the value), images are removed
   because GitHub fetches them and their URLs can carry data out, and the
   length is capped. A secret an agent read can't leave through GitHub.
10. **Narrow GitHub identity.** `doctor` fails on a classic or OAuth token
    with the `repo` scope, read from the `X-OAuth-Scopes` header without
    reading the token. Optionally, with `github_app` set, the scripts don't use your login
    at all: each command mints a one-hour installation token limited to the
    permissions the factory needs and to this repository, and passes it only
    to its own `gh` and `git` calls. If GitHub refuses a token, the command
    stops instead of falling back to your login.
11. **Dashboard.** Served on `127.0.0.1` only; job text is HTML-escaped and a
    Content-Security-Policy blocks any request to another origin.

### Residual risks and the deployment that covers them

- **The guard is a tripwire, not a sandbox.** Code an agent writes and then
  runs, and `checks.sh` and dev servers running branch code, have your user's
  file and network access. The seal catches changes to the factory's files at
  the next step and the outbound filter covers GitHub, but nothing stops such
  code reading your home folder or sending data over the network. Run the
  factory in its runtime container (`factory/bin/runtime`): no host
  credentials, egress limited to GitHub and an allowlist, and an unprivileged
  user that can't loosen either. Inside it, such code can still use the
  container's one GitHub credential (a fine-grained token or the App's key)
  and reach the allowlisted hosts. Claude Code's Bash sandbox (`sandbox.enabled` in
  `.claude/settings.json`) would restrict writes and egress, but it currently
  breaks `gh` and `git` (on macOS, `gh` fails certificate checks and SSH is
  refused).
- **CI runs pushed branches.** The factory pushes each `factory/*` branch
  before any human looks at it, and a workflow that runs on `push` or
  `pull_request` runs that branch's code with whatever secrets it uses.
  `doctor` warns about each such workflow; exclude `factory/**` branches or
  keep the secrets in an environment that requires a reviewer.
- **More than one job at a time.** The guard ties writes to a job in the
  agent's stage. With `concurrency` above 1, two jobs can share a stage, so an
  agent could write into the other job's files.
- **The GitHub token is the blast radius.** Use a fine-grained token scoped to
  the one repository with Contents, Issues, Pull requests, Commit statuses
  and Checks permissions, not
  a classic token or your personal login. `doctor` enforces the `repo`-scope
  part of this.
- **Branch protection is the last line.** Require PR reviews and status checks
  on the base branch. `doctor` warns when it's missing and fails when
  `auto_merge` is on without it. A private repository needs GitHub Pro or
  Team for it; on GitHub Free nothing stops a direct push, so keep
  `auto_merge` off there.
- **Hooks need a trusted workspace and a policy that allows them.** Agent
  frontmatter hooks in a project only run after the folder is trusted in
  Claude Code, and not at all when an organization policy sets
  `allowManagedHooksOnly` or `disableAllHooks`. `doctor` fails in that case.
- **The guard only blocks; it never approves.** It exits without a decision
  for allowed calls, so the normal permission flow, including an
  organization's managed rules, still applies. Don't change it to return
  `allow`: that would sidestep the permission policy.
- **The orchestrator isn't guarded.** The session that runs `/factory` is
  yours, with your permissions, and no hook checks its tool calls. It reads
  agents' files, which can carry text from an issue, and that text could
  steer it. The factory keeps what it reads small and inert: `job queue`
  prints no issue titles, and the skill reads the job folder only through
  `job show`, which prints the verdict lines and the summary (or `job.json`
  without the title), strips hidden characters and frames the text as data.
  MCP servers and connectors in that session are tools it could be steered
  into using: `doctor` warns when any is configured, and
  `claude --strict-mcp-config` starts a session that loads none from your
  config (check `/mcp`). A guard for the orchestrator itself is not built.
- **Third-party tools.** `agent-browser` and the project's own dependencies
  run with the same access as everything else; vet them under your dependency
  policy.

## Guardrails

- Only issues labeled `factory`, from trusted authors, run.
- Issue text reaches agents as quoted data in `issue.md`, with comments only
  from the issue's author and the factory's operator. It is never part of a
  prompt.
- Agents never push. Only `factory/bin/job` pushes `factory/*` branches and
  opens, readies and merges PRs. The factory never deploys.
- One PR per issue; at most `max_rounds` build rounds and
  `max_cost_usd_per_job` per job before a human is asked.

## Changes from upstream

See [`NOTICE`](NOTICE) for the list, and [`CHANGELOG.md`](CHANGELOG.md) for
the changes by release.
