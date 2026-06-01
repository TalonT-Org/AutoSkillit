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

_IDEMPOTENT_KEYWORDS = frozenset(["read it first", "already exists", "Bash redirect", "jq -n"])

_VULNERABLE_FILES = [
    "prior_threads_{pr_number}.json",
    "diff_context_{pr_number}.json",
    "raw_findings_{pr_number}.json",
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
