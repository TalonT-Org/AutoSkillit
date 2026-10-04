#!/usr/bin/env bash
# Append an E2E test failure to that test's tracking issue.
#
# Usage: scripts/e2e/post-e2e-failure.sh TEST STAGE PULL_REQUEST HEAD_SHA WORKFLOW_RUN_URL
#
# Args: $1=TEST  $2=STAGE (test|redaction)  $3=PULL_REQUEST (empty for workflow_dispatch)
#       $4=HEAD_SHA  $5=WORKFLOW_RUN_URL
#
# Runs inside the AutoSkillit user image, against its uv tool installation.
#
# Environment:
#   GH_TOKEN           — token with issues: write (required by gh)
#   GITHUB_REPOSITORY  — owner/repo for the issue (required by Python)

set -euo pipefail
export LC_ALL=C

USAGE="Usage: $0 TEST STAGE PULL_REQUEST HEAD_SHA WORKFLOW_RUN_URL"
TEST="${1:?$USAGE}"
STAGE="${2:?$USAGE}"
PULL_REQUEST="${3?$USAGE}"
HEAD_SHA="${4:?$USAGE}"
WORKFLOW_RUN_URL="${5:?$USAGE}"

PYTHON="$(uv tool dir)/autoskillit/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
    echo "ERROR: ${PYTHON} not found; run this inside the AutoSkillit user image." >&2
    exit 1
fi

exec "${PYTHON}" -m autoskillit._probe_canary post-e2e-failure \
    --test "${TEST}" \
    --stage "${STAGE}" \
    --pull-request "${PULL_REQUEST}" \
    --head-sha "${HEAD_SHA}" \
    --workflow-run-url "${WORKFLOW_RUN_URL}"
