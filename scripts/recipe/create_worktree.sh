#!/usr/bin/env bash
set -euo pipefail

# Args: $1=source_dir $2=task $3=experiment_plan $4=scope_report $5=evaluation_dashboard
#       $6=visualization_plan_path $7=report_plan_path $8=autoskillit_temp_name
SOURCE_DIR="$1"
TASK="$2"
EXPERIMENT_PLAN="$3"
SCOPE_REPORT="${4:-}"
EVAL_DASHBOARD="${5:-}"
VISUALIZATION_PLAN="${6:-}"
REPORT_PLAN="${7:-}"
TEMP_NAME="${8:-.autoskillit/temp}"

if [ ! -d "$SOURCE_DIR/.git" ]; then
  git init -q "$SOURCE_DIR"
  git -C "$SOURCE_DIR" commit --allow-empty -m "autoskillit: init for research recipe" -q
fi

BRANCH="research-$(date +%Y%m%d-%H%M%S)"
WORKTREE_PATH="../worktrees/${BRANCH}"
git -C "$SOURCE_DIR" worktree add -b "${BRANCH}" "${WORKTREE_PATH}"
RESOLVED="$(cd "${SOURCE_DIR}/${WORKTREE_PATH}" && pwd)"

case "${RESOLVED}" in /*) ;; *) echo "error: resolved worktree path is not absolute: ${RESOLVED}" >&2; exit 1;; esac

mkdir -p "${SOURCE_DIR}/${TEMP_NAME}/worktrees/${BRANCH}"
git -C "$SOURCE_DIR" rev-parse --abbrev-ref HEAD > "${SOURCE_DIR}/${TEMP_NAME}/worktrees/${BRANCH}/base-branch"

mkdir -p "${RESOLVED}/${TEMP_NAME}"
cp "${EXPERIMENT_PLAN}" "${RESOLVED}/${TEMP_NAME}/experiment-plan.md"

SLUG=$(printf '%s' "${TASK}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/--*/-/g;s/^-//;s/-$//' | cut -c1-30)
RESEARCH_DIR="${RESOLVED}/research/$(date +%Y-%m-%d)-${SLUG:-experiment}"
mkdir -p "${RESEARCH_DIR}/artifacts"
cp "${EXPERIMENT_PLAN}" "${RESEARCH_DIR}/experiment-plan.md"

if [ -n "${SCOPE_REPORT}" ] && [ -f "${SCOPE_REPORT}" ]; then
    cp "${SCOPE_REPORT}" "${RESEARCH_DIR}/artifacts/scope-report.md"
fi
if [ -n "${EVAL_DASHBOARD}" ] && [ -f "${EVAL_DASHBOARD}" ]; then
    cp "${EVAL_DASHBOARD}" "${RESEARCH_DIR}/artifacts/design-evaluation.md"
fi
if [ -n "${VISUALIZATION_PLAN}" ] && [ -f "${VISUALIZATION_PLAN}" ]; then
    cp "${VISUALIZATION_PLAN}" "${RESEARCH_DIR}/visualization-plan.md"
fi
if [ -n "${REPORT_PLAN}" ] && [ -f "${REPORT_PLAN}" ]; then
    cp "${REPORT_PLAN}" "${RESEARCH_DIR}/report-plan.md"
fi

SRC_TEMP="${SOURCE_DIR}/${TEMP_NAME}"
mkdir -p "${RESEARCH_DIR}/artifacts/review-cycles"
mkdir -p "${RESEARCH_DIR}/artifacts/plan-versions"
for f in "${SRC_TEMP}"/review-design/evaluation_dashboard_*.md; do [ -f "$f" ] && cp "$f" "${RESEARCH_DIR}/artifacts/review-cycles/"; done
for f in "${SRC_TEMP}"/review-design/revision_guidance_*.md; do [ -f "$f" ] && cp "$f" "${RESEARCH_DIR}/artifacts/review-cycles/"; done
for f in "${SRC_TEMP}"/plan-experiment/experiment_plan_*.md; do [ -f "$f" ] && cp "$f" "${RESEARCH_DIR}/artifacts/plan-versions/"; done
for f in "${SRC_TEMP}"/resolve-design-review/*.md; do [ -f "$f" ] && cp "$f" "${RESEARCH_DIR}/artifacts/review-cycles/"; done

cd "${RESOLVED}" && git add research/ && git commit -m "Add experiment plan and scope to research/"

echo "research_dir=${RESEARCH_DIR}"
echo "worktree_path=${RESOLVED}"
