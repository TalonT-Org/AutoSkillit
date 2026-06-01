#!/usr/bin/env bash
set -euo pipefail

# Args: $1=output_mode $2=worktree_path
OUTPUT_MODE="$1"
WORKTREE_PATH="$2"

if [ "${OUTPUT_MODE}" = "local" ]; then
    echo "push_branch: skipped in local mode"
    exit 0
fi

cd "${WORKTREE_PATH}"

# Prefer upstream (real GitHub URL) over origin (may be file:// in clones).
REMOTE=$(git remote get-url upstream 2>/dev/null | grep -qv "^file://" && echo upstream || echo "")
if [ -z "$REMOTE" ]; then
    REMOTE=$(git remote get-url origin 2>/dev/null | grep -qv "^file://" && echo origin || echo "")
fi
: "${REMOTE:=origin}"

git push -u "$REMOTE" HEAD
