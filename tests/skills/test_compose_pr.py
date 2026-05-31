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


def test_closes_issue_outside_architecture_impact_block():
    """Closes #{closing_issue} must appear AFTER {## End Architecture Impact conditional}.

    If Closes # is inside the Architecture Impact conditional block, it gets
    omitted when no validated diagrams exist (the entire block is skipped).
    """
    SKILL_TEXT = SKILL_PATH.read_text()
    end_marker = "{## End Architecture Impact conditional}"
    closes_pattern = "Closes #{closing_issue}"
    marker_positions = [i for i in range(len(SKILL_TEXT)) if SKILL_TEXT[i:].startswith(end_marker)]
    assert marker_positions, (
        "compose-pr/SKILL.md must contain '{## End Architecture Impact conditional}' marker"
    )
    closes_positions = [
        i for i in range(len(SKILL_TEXT)) if SKILL_TEXT[i:].startswith(closes_pattern)
    ]
    assert closes_positions, (
        "compose-pr/SKILL.md must contain 'Closes #{closing_issue}' template variable"
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
