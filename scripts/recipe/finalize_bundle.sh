#!/usr/bin/env bash
set -euo pipefail

# Args: $1=output_mode $2=research_dir $3=worktree_path
OUTPUT_MODE="$1"
RESEARCH_DIR="$2"
WORKTREE_PATH="$3"

if [ -z "${RESEARCH_DIR}" ] || [ ! -d "${RESEARCH_DIR}" ]; then
    echo "finalize_bundle: no research directory found — artifact commit skipped"
    exit 0
fi

if [ "${OUTPUT_MODE}" != "local" ]; then
    if [ -f "${RESEARCH_DIR}/report.md" ]; then
        cp "${RESEARCH_DIR}/report.md" "${RESEARCH_DIR}/README.md"
        rm "${RESEARCH_DIR}/report.md"
    fi
fi

REPORT_FILE=$( [ "${OUTPUT_MODE}" = "local" ] && echo "report.md" || echo "README.md" )

if [ "${OUTPUT_MODE}" = "local" ]; then
    EXCLUDE_PATTERN='^(report\.html|mermaid\.min\.js|images|report\.md|artifacts\.tar\.gz)$'
else
    EXCLUDE_PATTERN='^(README\.md|artifacts\.tar\.gz)$'
fi

mapfile -t TAR_ITEMS < <(ls -1 "${RESEARCH_DIR}" | grep -vE "${EXCLUDE_PATTERN}")

if [ ${#TAR_ITEMS[@]} -gt 0 ]; then
    tar czf "${RESEARCH_DIR}/artifacts.tar.gz" -C "${RESEARCH_DIR}" "${TAR_ITEMS[@]}"
    for item in "${TAR_ITEMS[@]}"; do rm -rf "${RESEARCH_DIR}/${item}"; done
fi

if [ -f "${RESEARCH_DIR}/artifacts.tar.gz" ] && [ -f "${RESEARCH_DIR}/${REPORT_FILE}" ]; then
    MANIFEST=$(tar tzf "${RESEARCH_DIR}/artifacts.tar.gz" | sort)
    {
        echo ""
        echo "## Archive Manifest"
        echo ""
        echo "Contents of \`artifacts.tar.gz\`:"
        echo ""
        echo '```'
        echo "${MANIFEST}"
        echo '```'
    } >> "${RESEARCH_DIR}/${REPORT_FILE}"
fi

[ -f "${RESEARCH_DIR}/artifacts.tar.gz" ] || \
    { echo "ERROR: artifacts.tar.gz not created in ${RESEARCH_DIR}"; exit 1; }

if [ "${OUTPUT_MODE}" = "local" ]; then
    LEFTOVER_PATTERN='^(report\.md|report\.html|mermaid\.min\.js|images|artifacts\.tar\.gz)$'
else
    LEFTOVER_PATTERN='^(README\.md|artifacts\.tar\.gz)$'
fi

LEFTOVERS=$(ls -1 "${RESEARCH_DIR}" | grep -vE "${LEFTOVER_PATTERN}" || true)
[ -z "${LEFTOVERS}" ] || \
    { echo "ERROR: uncompressed entries still present in ${RESEARCH_DIR}: ${LEFTOVERS}"; exit 1; }

[ -f "${RESEARCH_DIR}/${REPORT_FILE}" ] || \
    { echo "ERROR: ${REPORT_FILE} not found in ${RESEARCH_DIR}"; exit 1; }

if [ "${OUTPUT_MODE}" != "local" ]; then
    cd "${WORKTREE_PATH}" && git add "${RESEARCH_DIR}" && { git diff --cached --quiet || git commit -m "Add compressed research artifacts"; }
fi

echo "report_path=${RESEARCH_DIR}/${REPORT_FILE}"
echo "report_path_after_finalize=${RESEARCH_DIR}/${REPORT_FILE}"
