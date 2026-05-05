"""Guard: no synthetic deep-research citation markers in the codebase."""

from __future__ import annotations

import subprocess

import pytest

pytestmark = [pytest.mark.layer("docs"), pytest.mark.small]

# Pattern matches deep-research tool citation markers.
# chr() is used intentionally so the guard does not trigger itself.
SYNTHETIC_MARKER_PATTERNS = [
    chr(0x3010),  # U+3010 LEFT BLACK LENTICULAR BRACKET (unique to deep-research markers)
    r"†L\d",  # Dagger + L + digit (line reference fragment)
]


@pytest.mark.parametrize("pattern", SYNTHETIC_MARKER_PATTERNS)
def test_no_synthetic_citation_markers_in_tracked_files(pattern: str) -> None:
    """No tracked file contains synthetic deep-research citation markers."""
    result = subprocess.run(
        [
            "git",
            "grep",
            "-n",
            "-P",
            pattern,
            "--",
            "*.md",
            "*.yaml",
            "*.py",
        ],
        capture_output=True,
        text=True,
    )
    matches = result.stdout.strip()
    assert not matches, (
        f"Synthetic citation marker pattern {pattern!r} found in tracked files:\n{matches}"
    )
