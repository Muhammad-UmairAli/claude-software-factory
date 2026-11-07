# Benchmark: factory vs a single prompt

Does the factory produce better software than asking Claude Code directly, and
at what cost? Run this protocol on a real project before rolling the factory
out to it, and again after any big change to the pipeline.

| | |
|---|---|
| **Purpose** | Decide, with evidence, which approach to use for which kind of ticket. |
| **Arms** | A: one Claude Code prompt. B: factory, lite profile. C: factory, full profile. |
| **Unit** | One real ticket, run once per arm from the same base commit. |
| **Output** | `benchmark/results.csv` and a one-page decision note. |
| **Owner** | Whoever owns the factory install for the project. |

## Why this design

A pipeline adds cost and wall time on every ticket. It earns that back only if
it cuts rework, catches defects earlier, or saves human review time. A
single-prompt run is the honest baseline: it's what an engineer would do
without the factory. Comparing on tickets you already know the answer to
favors loose approaches, so the scoring uses acceptance tests written before
any arm runs, and a reviewer who doesn't know which arm produced the PR.

## 1. Pick the tickets

- 8 to 12 tickets from the project's real backlog or recent history, not toy
  tasks. Mix sizes and risk: roughly half low risk, a third medium, a few high.
- Each fits the factory's size budget (`sizing` in config.json). Tickets that
  don't fit measure intake's rescoping, not the pipeline; record them
  separately.
- For each, write **hidden acceptance tests** before any arm runs: automated
  tests or a short manual checklist that a correct change must pass. Keep them
  out of the repo the arms see.
- Freeze the base commit for each ticket.

## 2. Run the arms

Run each arm in a fresh session with no memory of the others, from the frozen
base commit, with the same models where the arm lets you choose.

**A. Single prompt.** In a fresh worktree on a new branch, start Claude Code
and paste `benchmark/baseline-prompt.md` with the ticket text filled in. Let
it run to a PR. Record its cost and time from `/cost` or the session
transcript, and every human message you had to send.

**B. Factory, lite.** Label the issue `factory`, set its risk routing so it
lands on the lite profile (or use a low-risk ticket), and run
`/factory run <n>`.

**C. Factory, full.** Same ticket on a fresh issue, with `profiles` set so its
risk lands on full.

For B and C, collect the numbers with:

```bash
factory/bin/job metrics > benchmark/factory-metrics.csv
```

Each row has the profile, risk, rounds, gate failures, review rounds that
asked for changes, human feedback rounds, API-equivalent cost, minutes, and
the change size.

## 3. Score

For each PR, blind to the arm (strip branch names and factory comments, or
have someone else collect the diffs):

| Metric | How |
|---|---|
| **Correct** | Hidden acceptance tests: pass / fail, and how many criteria met. |
| **Merge-ready** | Would you merge it as is? yes / after small edits / no. |
| **Human edits** | Lines a human had to change before merging (`git diff` of the fixup). |
| **Human minutes** | Time spent reviewing, answering questions and fixing, per ticket. |
| **Defects** | Bugs found in review or within two weeks after merge. |
| **Scope** | Did it change anything the ticket didn't ask for? |
| **Cost** | API-equivalent USD (factory: `cost_usd` plus `orchestrator_usd`; single prompt: session cost). |
| **Wall time** | Minutes from start to PR ready. |

Fill one row per ticket and arm in `benchmark/results-template.csv`, saved as
`benchmark/results.csv`.

## 4. Decide

Look per risk level, not only overall. Write down thresholds before you look
at the numbers, for example:

- Use **lite** for low risk if it matches the single prompt on correctness and
  merge-readiness and cuts human minutes, at no more than 2x the cost.
- Use **full** for medium and high risk if it beats lite or the single prompt
  on correctness or defects; otherwise drop to lite for those too.
- Keep the **single prompt** for ticket types where both factory arms lose on
  human minutes, typically tiny one-file changes in small projects.

Record the decision, the thresholds and the table in a short note, and set
`profiles` in `factory/config.json` to match.

## Threats to validity

- **Small samples.** Ten tickets show direction, not precision. Run each arm
  twice on a few tickets to see how much results vary between runs.
- **Ticket choice.** Tickets picked because they look agent-friendly flatter
  every arm. Pick from the backlog in order, skipping only those outside the
  size budget.
- **Learning effects.** The person running the arms gets better at prompting.
  Rotate the order of arms per ticket.
- **Hidden-test leakage.** If the tests sit in the repo, every arm can read
  them. Keep them outside until scoring.
- **Cost accounting.** A job's `cost_usd` is its agents. Add `orchestrator_usd`,
  its even share of the sessions that ran it, for the factory's full cost per
  ticket, and run the factory in a session of its own so other work doesn't
  count. If `job summary` says a cost may be low, check it against `/cost`.
