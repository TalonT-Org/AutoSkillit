#!/usr/bin/env bash
set -euo pipefail

# Args: $1=worktree_name $2=autoskillit_temp_dir
# Stdout contract: only shell variable assignments (KEY='VALUE') go to stdout.
# All diagnostics, warnings, and git messages go to stderr or /dev/null.
# The SKILL.md invokes this via: eval "$(bash script.sh ...)"

if [ $# -lt 2 ]; then
    echo "ERROR: Usage: $0 <worktree_name> <autoskillit_temp_dir>" >&2
    exit 1
fi

WORKTREE_NAME="$1"
AUTOSKILLIT_TEMP="$2"

MAIN_GIT_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
[ -n "$MAIN_GIT_DIR" ] || { echo "ERROR: could not resolve git-common-dir" >&2; exit 1; }
MAIN_ROOT="$(dirname "$MAIN_GIT_DIR")"
WORKTREE_DIR="${MAIN_ROOT}/../worktrees"
WORKTREE_PATH="${WORKTREE_DIR}/${WORKTREE_NAME}"

mkdir -p "$WORKTREE_DIR"

# Placement assertion: verify worktree lands outside the main repo clone.
# Resolve from WORKTREE_DIR (exists after mkdir) to handle fresh worktree creation.
CANONICAL_WORKTREE="$(cd "$WORKTREE_DIR" && pwd -P)/${WORKTREE_NAME}"
CANONICAL_MAIN="$(cd "$MAIN_ROOT" 2>/dev/null && pwd -P)" || CANONICAL_MAIN=""

if [ -n "$CANONICAL_MAIN" ]; then
    case "$CANONICAL_WORKTREE" in
        "${CANONICAL_MAIN}"/*)
            echo "ERROR: worktree path ${CANONICAL_WORKTREE} is nested inside main repo ${CANONICAL_MAIN}" >&2
            echo "ERROR: The worktree must be created outside the clone directory (e.g., ../worktrees/)" >&2
            exit 1
            ;;
    esac
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git worktree add -b "$WORKTREE_NAME" "$WORKTREE_PATH" >&2
WORKTREE_PATH="$(cd "$WORKTREE_PATH" && pwd)"

# Write sidecar atomically via python3 tempfile pattern.
python3 -c "
import sys
from pathlib import Path
import tempfile, os

project_root = Path('${MAIN_ROOT}')
sidecar_dir = project_root / '${AUTOSKILLIT_TEMP}' / 'worktrees' / '${WORKTREE_NAME}'
sidecar_dir.mkdir(parents=True, exist_ok=True)
sidecar_file = sidecar_dir / 'base-branch'
fd, tmp = tempfile.mkstemp(dir=str(sidecar_dir))
os.write(fd, ('${CURRENT_BRANCH}' + '\n').encode())
os.close(fd)
os.replace(tmp, str(sidecar_file))
" >/dev/null 2>&1

# Detect remote and set upstream tracking (non-fatal).
REMOTE=$(git remote get-url origin >/dev/null 2>&1 && echo origin || echo "")
if [ -z "$REMOTE" ]; then
    REMOTE=$(git remote get-url upstream >/dev/null 2>&1 && echo upstream || echo "")
fi

if [ -n "$REMOTE" ]; then
    if ! git fetch "$REMOTE" "${CURRENT_BRANCH}" >&2 2>&1; then
        :  # non-fatal
    fi
    git -C "$WORKTREE_PATH" branch --set-upstream-to="${REMOTE}/${CURRENT_BRANCH}" "${WORKTREE_NAME}" >/dev/null 2>&1 || true
fi

# ONLY stdout from this point on — these are the eval-able variable assignments.
echo "WORKTREE_PATH='${WORKTREE_PATH}'"
echo "BRANCH_NAME='${WORKTREE_NAME}'"
echo "BASE_BRANCH='${CURRENT_BRANCH}'"