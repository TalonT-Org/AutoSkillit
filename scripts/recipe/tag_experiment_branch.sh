#!/usr/bin/env bash
set -euo pipefail

# Args: $1=worktree_path $2=experiment_branch $3=artifact_pr_url
WORKTREE_PATH="$1"
EXPERIMENT_BRANCH="$2"
ARTIFACT_PR_URL="$3"

TAG="archive/research/${EXPERIMENT_BRANCH}"

git -C "${WORKTREE_PATH}" tag -a "${TAG}" "${EXPERIMENT_BRANCH}" \
    -m "Research experiment: ${EXPERIMENT_BRANCH}. Report merged via artifact PR ${ARTIFACT_PR_URL}. Variant code preserved here for reference."
git -C "${WORKTREE_PATH}" push origin "${TAG}"

echo "archive_tag=${TAG}"
