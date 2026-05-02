#!/usr/bin/env bash
set -euo pipefail

# Args: $1=worktree_path $2=experiment_branch $3=artifact_branch $4=pr_url $5=base_branch $6=research_dir
WORKTREE_PATH="$1"
EXPERIMENT_BRANCH="$2"
ARTIFACT_BRANCH="$3"
ORIG_PR="$4"
BASE_BRANCH="$5"
RESEARCH_DIR="$6"

RESEARCH_SUBDIR="research/$(basename "${RESEARCH_DIR}")"
ARTIFACT_DIR="${WORKTREE_PATH}"

if [ -f "${ARTIFACT_DIR}/${RESEARCH_SUBDIR}/artifacts.tar.gz" ]; then
    STRUCT_DETAIL=$'- `artifacts.tar.gz` \xe2\x80\x94 compressed artifact tree\n\nExtract:\n```bash\ntar xzf artifacts.tar.gz\n```'
else
    STRUCT_DETAIL='- `artifacts/` — uncompressed artifact tree'
fi

PR_BODY=$(printf '## Research Artifacts\n\nFrom experiment branch `%s`.\n\nOriginal experiment PR: %s\n\n### Structure\n\n- **README.md** — Research conclusions (browsable on GitHub)\n%s\n\nContains only files under `research/` — zero production code changes.' "${EXPERIMENT_BRANCH}" "${ORIG_PR}" "${STRUCT_DETAIL}")

PR_URL=$(gh pr create \
    --head "${ARTIFACT_BRANCH}" \
    --base "${BASE_BRANCH}" \
    --title "Research artifacts: ${EXPERIMENT_BRANCH}" \
    --body "${PR_BODY}")

echo "artifact_pr_url=${PR_URL}"
