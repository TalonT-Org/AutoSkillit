#!/usr/bin/env bash
set -euo pipefail

# Args: $1=research_dir $2=worktree_path $3=autoskillit_temp_name
RESEARCH_DIR="$1"
WORKTREE_PATH="$2"
TEMP_NAME="${3:-.autoskillit/temp}"

if [ -z "${RESEARCH_DIR}" ] || [ ! -d "${RESEARCH_DIR}" ]; then
    echo "stage_bundle: no research directory found — skipped"
    exit 0
fi

WT_TEMP="${WORKTREE_PATH}/${TEMP_NAME}"
mkdir -p "${RESEARCH_DIR}/artifacts/phase-groups"
mkdir -p "${RESEARCH_DIR}/artifacts/phase-plans"
mkdir -p "${RESEARCH_DIR}/artifacts/images"
mkdir -p "${RESEARCH_DIR}/artifacts/scripts"

for f in "${WT_TEMP}"/make-groups/*.md; do [ -f "$f" ] && cp "$f" "${RESEARCH_DIR}/artifacts/phase-groups/"; done
for f in "${WT_TEMP}"/make-groups/*.yaml; do [ -f "$f" ] && cp "$f" "${RESEARCH_DIR}/artifacts/phase-groups/"; done
for f in "${WT_TEMP}"/make-plan/*.md; do [ -f "$f" ] && cp "$f" "${RESEARCH_DIR}/artifacts/phase-plans/"; done
for f in "${WT_TEMP}"/exp-lens-*/*.md; do [ -f "$f" ] && cp "$f" "${RESEARCH_DIR}/artifacts/images/"; done

echo "stage_bundle=done"
