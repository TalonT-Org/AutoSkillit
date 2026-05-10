#!/usr/bin/env bash
set -euo pipefail

# Args: $1=source_dir $2=task $3=experiment_plan $4=scope_report $5=evaluation_dashboard
#       $6=visualization_plan_path $7=report_plan_path $8=autoskillit_temp_name
#       $9=visualization_plan_trace_path
SOURCE_DIR="$1"
TASK="$2"
EXPERIMENT_PLAN="$3"
SCOPE_REPORT="${4:-}"
EVAL_DASHBOARD="${5:-}"
VISUALIZATION_PLAN="${6:-}"
REPORT_PLAN="${7:-}"
TEMP_NAME="${8:-.autoskillit/temp}"
VIS_TRACE_PATH="${9:-}"

if [ ! -e "$SOURCE_DIR/.git" ]; then
  git init -q "$SOURCE_DIR"
  git -C "$SOURCE_DIR" commit --allow-empty -m "autoskillit: init for research recipe" -q
fi

# Resolve to main worktree root — prevents nested worktrees/worktrees/ when
# SOURCE_DIR is itself a linked worktree.
MAIN_GIT_DIR="$(git -C "$SOURCE_DIR" rev-parse --path-format=absolute --git-common-dir)"
[ -n "$MAIN_GIT_DIR" ] || { echo "error: could not resolve git-common-dir" >&2; exit 1; }
MAIN_ROOT="$(dirname "$MAIN_GIT_DIR")"

BRANCH="research-$(date +%Y%m%d-%H%M%S)"
WORKTREE_DIR="${MAIN_ROOT}/../worktrees"
mkdir -p "${WORKTREE_DIR}"
WORKTREE_PATH="${WORKTREE_DIR}/${BRANCH}"
git -C "$SOURCE_DIR" worktree add -b "${BRANCH}" "${WORKTREE_PATH}"
RESOLVED="$(cd "${WORKTREE_PATH}" && pwd)"

case "${RESOLVED}" in /*) ;; *) echo "error: resolved worktree path is not absolute: ${RESOLVED}" >&2; exit 1;; esac

mkdir -p "${MAIN_ROOT}/${TEMP_NAME}/worktrees/${BRANCH}"
git -C "$SOURCE_DIR" rev-parse --abbrev-ref HEAD > "${MAIN_ROOT}/${TEMP_NAME}/worktrees/${BRANCH}/base-branch"

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

# --- Audit trail artifacts ---
AUDIT_DIR="${RESEARCH_DIR}/audit"
mkdir -p "${AUDIT_DIR}"

if [ -n "${EVAL_DASHBOARD}" ] && [ -f "${EVAL_DASHBOARD}" ]; then
    cp "${EVAL_DASHBOARD}" "${AUDIT_DIR}/design-review-dashboard.md"
fi

if [ -n "${VIS_TRACE_PATH}" ] && [ -f "${VIS_TRACE_PATH}" ]; then
    cp "${VIS_TRACE_PATH}" "${AUDIT_DIR}/visualization-plan-trace.md"
elif [ -f "${SRC_TEMP}/plan-visualization/visualization-plan-trace.md" ]; then
    cp "${SRC_TEMP}/plan-visualization/visualization-plan-trace.md" "${AUDIT_DIR}/visualization-plan-trace.md"
fi

cd "${RESOLVED}" && git add research/ && git commit -m "Add experiment plan and scope to research/"

echo "research_dir=${RESEARCH_DIR}"
echo "worktree_path=${RESOLVED}"
echo "research_dir_rel=${RESEARCH_DIR#"${RESOLVED}/"}"
