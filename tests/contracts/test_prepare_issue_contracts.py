"""Contract tests for the prepare-issue SKILL.md."""

from __future__ import annotations

import re
from pathlib import Path

SKILL_MD = (
    Path(__file__).parents[2]
    / "src/autoskillit/skills/prepare-issue/SKILL.md"
)


def _lines():
    return SKILL_MD.read_text().splitlines()


def test_label_create_calls_include_force():
    """All gh label create calls in prepare-issue must include --force."""
    for line in _lines():
        if "gh label create" in line:
            assert "--force" in line, f"Missing --force in: {line}"


def test_no_batch_labels_applied():
    """prepare-issue must never apply batch:N labels."""
    batch_pattern = re.compile(r"batch:\d+")
    for line in _lines():
        if "gh issue edit" in line or "add-label" in line:
            assert not batch_pattern.search(line), (
                f"batch label found in: {line}"
            )


def test_only_known_recipe_routes_applied():
    """Only recipe:implementation and recipe:remediation are valid route labels."""
    for line in _lines():
        if "recipe:" in line and "add-label" in line:
            assert "recipe:implementation" in line or "recipe:remediation" in line, (
                f"Unknown recipe label in: {line}"
            )
