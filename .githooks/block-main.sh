#!/usr/bin/env bash
# Blocks commits, merges and pushes on the main branch. Work on a feature branch and merge through a pull request.
# Enabled with: git config core.hooksPath .githooks
set -euo pipefail
hook=${GIT_HOOK_NAME:-$(basename "$0")}
if [ "$hook" = "pre-push" ]; then
  while read -r _ _ remote_ref _; do
    if [ "$remote_ref" = "refs/heads/main" ]; then
      echo "Blocked: pushing to main is not allowed. Push a feature branch and open a pull request." >&2
      exit 1
    fi
  done
  exit 0
fi
branch=$(git symbolic-ref --short -q HEAD || true)
if [ "$branch" = "main" ]; then
  echo "Blocked: commits on main are not allowed. Create a feature branch (git switch -c feature/YYYYMMDD-topic) first." >&2
  exit 1
fi
