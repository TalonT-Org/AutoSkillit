#!/usr/bin/env bash
set -euo pipefail

# Args: $1=worktree_path $2=experiment_branch $3=base_branch $4=research_dir
WORKTREE_PATH="$1"
EXPERIMENT_BRANCH="$2"
BASE_BRANCH="$3"
RESEARCH_DIR="$4"

# Prefer upstream (real GitHub URL) over origin (may be file:// in clones).
REMOTE=$(git -C "${WORKTREE_PATH}" remote get-url upstream 2>/dev/null | grep -qv "^file://" && echo upstream || echo "")
if [ -z "$REMOTE" ]; then
    REMOTE=$(git -C "${WORKTREE_PATH}" remote get-url origin 2>/dev/null | grep -qv "^file://" && echo origin || echo "")
fi
: "${REMOTE:=origin}"

SLUG=$(printf '%s' "${EXPERIMENT_BRANCH}" | sed 's/^research-//')
ARTIFACT_BRANCH="research-artifacts-${SLUG}"

ARTIFACT_DIR="$(mktemp -d)"
trap 'git -C "${WORKTREE_PATH}" worktree remove "${ARTIFACT_DIR}" --force 2>/dev/null; rm -rf "${ARTIFACT_DIR}"' EXIT

git -C "${WORKTREE_PATH}" fetch "$REMOTE" "${BASE_BRANCH}"
git -C "${WORKTREE_PATH}" branch -D "${ARTIFACT_BRANCH}" 2>/dev/null || true
git -C "${WORKTREE_PATH}" worktree add "${ARTIFACT_DIR}" -b "${ARTIFACT_BRANCH}" "${REMOTE}/${BASE_BRANCH}"

RESEARCH_SUBDIR="research/$(basename "${RESEARCH_DIR}")"
git -C "${ARTIFACT_DIR}" checkout "${EXPERIMENT_BRANCH}" -- "${RESEARCH_SUBDIR}"
cd "${ARTIFACT_DIR}"
git add research/
git commit -m "Add research artifacts: ${SLUG}"
git push -u "$REMOTE" "${ARTIFACT_BRANCH}"
cd - > /dev/null

git -C "${WORKTREE_PATH}" worktree remove "${ARTIFACT_DIR}" --force

echo "artifact_branch=${ARTIFACT_BRANCH}"
