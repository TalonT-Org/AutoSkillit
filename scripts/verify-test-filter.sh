#!/usr/bin/env bash
# Anti-drift verification for test path filtering.
#
# Shadow-mode comparison: collects tests with and without the filter,
# identifies missed tests, runs them to detect false negatives, and
# reports reduction percentage.
#
# Usage: scripts/verify-test-filter.sh <base_ref>
#   base_ref: git ref for AUTOSKILLIT_TEST_BASE_REF (e.g. HEAD~5)
#
# NOTE: This script uses .venv/bin/python -m pytest directly (not task
# test-check) because it needs raw --collect-only output without the
# Taskfile harness. This is an intentional exception to the "no bare
# pytest" convention.

set -euo pipefail

# Deterministic byte-order for sort and comm — prevents locale-dependent
# collation surprises with parametrized test IDs (brackets, hyphens, etc.)
export LC_ALL=C

BASE_REF="${1:?Usage: $0 <base_ref>}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PYTEST=".venv/bin/python -m pytest"
if [[ ! -x ".venv/bin/python" ]]; then
    echo "ERROR: .venv/bin/python not found. Run 'task install-worktree' first." >&2
    exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/test-filter-audit.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

FULL_FILE="$WORK_DIR/full_selected.txt"
FILTER_FILE="$WORK_DIR/filter_selected.txt"
MISSED_FILE="$WORK_DIR/missed_tests.txt"

echo "=== Test Filter Anti-Drift Verification ==="
echo "Base ref: $BASE_REF"
echo ""

# --- Step 1: Full collection (no filter) ---
echo "--- Step 1: Collecting all tests (no filter) ---"
$PYTEST tests/ --collect-only -q --no-header --disable-warnings -o "addopts=" -m "not smoke and not canary" 2>/dev/null \
    | grep '::' \
    | sort > "$FULL_FILE"

TOTAL=$(wc -l < "$FULL_FILE")
echo "Total tests collected: $TOTAL"

if [[ "$TOTAL" -eq 0 ]]; then
    echo "ERROR: No tests collected in full run. Something is wrong." >&2
    exit 1
fi

# --- Step 2: Filtered collection (conservative) ---
echo ""
echo "--- Step 2: Collecting tests with conservative filter (base_ref=$BASE_REF) ---"
AUTOSKILLIT_TEST_BASE_REF="$BASE_REF" \
AUTOSKILLIT_TEST_FILTER=conservative \
$PYTEST tests/ --collect-only -q --no-header --disable-warnings -o "addopts=" -m "not smoke and not canary" 2>/dev/null \
    | grep '::' \
    | sort > "$FILTER_FILE"

FILTERED=$(wc -l < "$FILTER_FILE")
echo "Filtered tests collected: $FILTERED"

# --- Step 3: Find missed tests ---
echo ""
echo "--- Step 3: Computing shadow diff ---"
comm -23 "$FULL_FILE" "$FILTER_FILE" > "$MISSED_FILE"

MISSED=$(wc -l < "$MISSED_FILE")
echo "Missed tests (in full but not in filtered): $MISSED"

# --- Step 4: Report reduction ---
echo ""
echo "--- Report ---"
if [[ "$TOTAL" -gt 0 ]]; then
    REDUCTION=$(( (TOTAL - FILTERED) * 100 / TOTAL ))
    echo "Reduction: ${REDUCTION}% ($TOTAL → $FILTERED)"
else
    REDUCTION=0
    echo "Reduction: 0% (no tests collected)"
fi

echo "Total tests:    $TOTAL"
echo "Filtered tests: $FILTERED"
echo "Missed tests:   $MISSED"
echo "Reduction:      ${REDUCTION}%"

# --- Step 5: Run missed tests if any ---
if [[ "$MISSED" -gt 0 ]]; then
    echo ""
    echo "--- Step 5: Running $MISSED missed tests to detect false negatives ---"

    set +e
    $PYTEST $(cat "$MISSED_FILE" | tr '\n' ' ') \
        --tb=short -q --no-header -o "addopts=" \
        -m "not smoke and not canary" 2>&1
    MISSED_EXIT=$?
    set -e

    if [[ "$MISSED_EXIT" -ne 0 ]]; then
        echo ""
        echo "FALSE NEGATIVE DETECTED: $MISSED missed tests and some FAILED."
        echo "The test filter is dropping tests that would have caught regressions."
        exit 1
    else
        echo ""
        echo "Missed tests all passed — no false negatives detected."
        echo "These tests were excluded by the filter but would not have caught regressions."
        exit 0
    fi
else
    echo ""
    echo "No missed tests — filter is not dropping any tests for this diff range."
    exit 0
fi
