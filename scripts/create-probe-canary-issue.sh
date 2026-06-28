#!/usr/bin/env bash
# Create a GitHub issue with the autoskillit-canary label.
#
# Usage: scripts/create-probe-canary-issue.sh TITLE BODY
#
# Args: $1=TITLE  $2=BODY
#
# Environment:
#   GITHUB_REPOSITORY  — required, owner/repo format
#   GITHUB_TOKEN       — required, GitHub authentication token

set -euo pipefail
export LC_ALL=C

TITLE="${1:?Usage: $0 TITLE BODY}"
BODY="${2:?Usage: $0 TITLE BODY}"

if [[ -z "${GITHUB_REPOSITORY:-}" ]]; then
    echo "ERROR: GITHUB_REPOSITORY is not set." >&2
    exit 1
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    echo "ERROR: GITHUB_TOKEN is not set." >&2
    exit 1
fi

gh issue create \
    --repo "${GITHUB_REPOSITORY}" \
    --title "${TITLE}" \
    --label "autoskillit-canary" \
    --body "${BODY}"