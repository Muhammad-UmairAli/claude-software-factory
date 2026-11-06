#!/usr/bin/env bash
# The repo's checks. Exit 0 only when the branch is fit to review.
#
# The gate runs this script from the main checkout with the job's worktree as
# the working directory. Keep it fast, deterministic and non-interactive: no
# watch modes, no prompts, nothing that rewrites tracked files.
#
# It runs with a scrubbed environment: only the variables in config.json
# `checks.env_allowlist` pass through, plus CI=true. Add a variable there only
# if the checks can't run without it, and never a production credential.
# It is killed after `checks.timeout_sec`.
#
# Agents can't read or write .env files. If the checks need one, create it from
# the committed example here, e.g.:
#
#   [ -f .env ] || cp .env.example .env
#
# INSTALL.md replaces the lines below with this project's real commands, e.g.:
#
#   npm ci --prefer-offline
#   npm run lint
#   npm run typecheck
#   npm test -- --run
#   npm run build
set -euo pipefail

echo "factory/checks.sh has no checks yet. Add this repo's test, lint, typecheck and build commands." >&2
exit 1
