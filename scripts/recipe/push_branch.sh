#!/usr/bin/env bash
set -euo pipefail

# Args: $1=output_mode $2=worktree_path
OUTPUT_MODE="$1"
WORKTREE_PATH="$2"

if [ "${OUTPUT_MODE}" = "local" ]; then
    echo "push_branch: skipped in local mode"
    exit 0
fi

cd "${WORKTREE_PATH}" && git push -u origin HEAD
