#!/usr/bin/env bash
# Route a probe failure record to the Python canary state machine.
#
# Usage: scripts/post-probe-failure.sh BACKEND CLI_VERSION FAILURE_TYPE WORKFLOW_RUN_URL
#
# Args: $1=BACKEND  $2=CLI_VERSION  $3=FAILURE_TYPE  $4=WORKFLOW_RUN_URL
#
# Environment:
#   CANARY_STATE_FILE  — path to canary state JSON (required)
#   GITHUB_REPOSITORY  — owner/repo for issue creation (required by Python)

set -euo pipefail
export LC_ALL=C

BACKEND="${1:?Usage: $0 BACKEND CLI_VERSION FAILURE_TYPE WORKFLOW_RUN_URL}"
CLI_VERSION="${2:?Usage: $0 BACKEND CLI_VERSION FAILURE_TYPE WORKFLOW_RUN_URL}"
FAILURE_TYPE="${3:?Usage: $0 BACKEND CLI_VERSION FAILURE_TYPE WORKFLOW_RUN_URL}"
WORKFLOW_RUN_URL="${4:?Usage: $0 BACKEND CLI_VERSION FAILURE_TYPE WORKFLOW_RUN_URL}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYTHON="${PROJECT_ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
    echo "ERROR: ${PYTHON} not found. Run 'task install-worktree' first." >&2
    exit 1
fi

"${PYTHON}" -m autoskillit._probe_canary post-failure \
    --state-file "${CANARY_STATE_FILE:?CANARY_STATE_FILE is not set}" \
    --backend "${BACKEND}" \
    --cli-version "${CLI_VERSION}" \
    --failure-type "${FAILURE_TYPE}" \
    --workflow-run-url "${WORKFLOW_RUN_URL}"