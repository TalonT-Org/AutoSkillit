"""Structural tests for review-pr/SKILL.md idempotent-write guidance (1b)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]

_SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-pr"
    / "SKILL.md"
)

_IDEMPOTENT_KEYWORDS = frozenset(
    ["read it first", "already exists", "Bash redirect", "jq -n", "atomically rename"]
)

_VULNERABLE_FILES = [
    "prior_threads_{pr_number}.json",
    "diff_context_{pr_number}.json",
    "raw_findings_{pr_number}.json",
    "local_findings_{pr_number}.json",
]

_WINDOW = 600


def _all_surrounding_windows(text: str, target: str) -> list[str]:
    """Return surrounding text windows for all occurrences of target in text."""
    windows: list[str] = []
    start = 0
    while True:
        idx = text.find(target, start)
        if idx == -1:
            break
        w_start = max(0, idx - _WINDOW)
        w_end = min(len(text), idx + len(target) + _WINDOW)
        windows.append(text[w_start:w_end])
        start = idx + 1
    return windows


@pytest.mark.parametrize("filename_pattern", _VULNERABLE_FILES)
def test_write_instruction_uses_dynamic_output_dir(filename_pattern: str) -> None:
    """Write instructions for collision-risk files must use the dynamic output dir variable.

    Write paths must reference ${REVIEW_OUTPUT_DIR} rather than hardcoded
    {{AUTOSKILLIT_TEMP}}/review-pr/ so the write guard's allowed prefix is respected
    when the recipe scopes output_dir to an iteration subdirectory.
    """
    text = _SKILL_PATH.read_text()
    windows = _all_surrounding_windows(text, filename_pattern)
    assert windows, f"Pattern {filename_pattern!r} not found in review-pr/SKILL.md"
    found = any("REVIEW_OUTPUT_DIR" in window for window in windows)
    assert found, (
        f"No REVIEW_OUTPUT_DIR reference found near any occurrence of {filename_pattern!r} in "
        f"review-pr/SKILL.md. Write paths must use ${{REVIEW_OUTPUT_DIR}} so they adapt to "
        f"the recipe's output_dir at runtime. First window: {windows[0][:300]!r}"
    )


@pytest.mark.parametrize("filename_pattern", _VULNERABLE_FILES)
def test_write_instruction_includes_idempotent_guidance(filename_pattern: str) -> None:
    """Write instructions for collision-risk files must include idempotent-write guidance.

    Each file that is written on every review-loop iteration (and therefore risks
    colliding on the second pass) must have guidance near the write instruction
    explaining how to safely overwrite or skip pre-existing files.
    """
    text = _SKILL_PATH.read_text()
    windows = _all_surrounding_windows(text, filename_pattern)
    assert windows, f"Pattern {filename_pattern!r} not found in review-pr/SKILL.md"
    found = any(
        any(kw.lower() in window.lower() for kw in _IDEMPOTENT_KEYWORDS) for window in windows
    )
    assert found, (
        f"No idempotent-write guidance found near any occurrence of {filename_pattern!r} in "
        f"review-pr/SKILL.md. Expected one of {sorted(_IDEMPOTENT_KEYWORDS)!r} within "
        f"{_WINDOW} chars of at least one occurrence. "
        f"First window: {windows[0][:300]!r}"
    )


def test_publication_uses_same_directory_temporary_and_atomic_rename() -> None:
    text = _SKILL_PATH.read_text()
    step8 = text[text.index("### Step 8") :]
    assert "same-directory temporary file" in step8
    assert "atomic rename" in step8
    assert "local_findings_{pr_number}.json last" in step8
    assert "review_generation_id" in step8
    assert "annotation_generation_id" in step8
    assert step8.index("**Write Raw Findings JSON (first):**") < step8.index(
        "**Write Diff-Scoped Context Handoff"
    )


def test_runtime_threads_validation_aggregation_and_publication_results() -> None:
    text = _SKILL_PATH.read_text()
    step4 = text[text.index("### Step 4") : text.index("### Step 4.5")]
    step8 = text[text.index("### Step 8") :]

    assert "VALIDATION_RESULT = validate_experimental_auditor_outputs(" in step4
    assert 'EXPERIMENTAL_CANDIDATES = VALIDATION_RESULT["candidates"]' in step4
    assert "AGGREGATION_RESULT = aggregate_experimental_review_candidates(" in step4
    assert 'for finding in AGGREGATION_RESULT["survivors"]' in step4
    assert "FINAL_REVIEW_FINDINGS" in step4
    assert "standard_findings=STANDARD_FINDINGS" in step4
    assert "valid_diff_lines=VALID_DIFF_LINES" in step4
    assert 'snapshot=GATE_AUTHORITY["snapshot"]' in step4
    assert "review_root=REVIEW_CHECKOUT_ROOT" in step4
    assert 'if GATE_STATE == "valid_true":' in step4
    assert 'elif GATE_STATE == "valid_false":' in step4
    assert '"state": "not_required"' in step4
    assert "PUBLICATION = prepare_experimental_review_publication(" in step8
    assert "survivors=FINAL_REVIEW_FINDINGS" in step8
    assert "receipt=" in step8
    assert "PUBLICATION_RESULT = publish_experimental_review_artifacts(" in step8


def test_fixed_destinations_reject_direct_redirects() -> None:
    text = _SKILL_PATH.read_text()
    assert "jq -n ... > path" not in text
    assert "bash redirects (`> path`)" not in text
    for filename in (
        "diff_context_${pr_number}.json",
        "raw_findings_${pr_number}.json",
        "local_findings_${pr_number}.json",
    ):
        assert f'> "${{REVIEW_OUTPUT_DIR}}{filename}"' not in text
