"""Guard tests for compose-pr/SKILL.md template structure."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.small]

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "compose-pr"
    / "SKILL.md"
)


def test_closes_issue_url_outside_architecture_impact_block():
    """The exact closing URL must follow the Architecture Impact conditional.

    If the URL-form Closes reference is inside the Architecture Impact conditional, it gets
    omitted when no validated diagrams exist (the entire block is skipped).
    """
    SKILL_TEXT = SKILL_PATH.read_text()
    end_marker = "{## End Architecture Impact conditional}"
    closes_pattern = "Closes {source_issue_url}"
    marker_positions = [i for i in range(len(SKILL_TEXT)) if SKILL_TEXT[i:].startswith(end_marker)]
    assert marker_positions, (
        "compose-pr/SKILL.md must contain '{## End Architecture Impact conditional}' marker"
    )
    closes_positions = [
        i for i in range(len(SKILL_TEXT)) if SKILL_TEXT[i:].startswith(closes_pattern)
    ]
    assert closes_positions, (
        "compose-pr/SKILL.md must contain the canonical source_issue_url template"
    )
    for closes_pos in closes_positions:
        nearest_marker = max(
            (m for m in marker_positions if m < closes_pos),
            default=None,
        )
        assert nearest_marker is not None, (
            f"'Closes #' at position {closes_pos} has no preceding "
            "'{## End Architecture Impact conditional}' marker — "
            "it may be inside the conditional block"
        )
