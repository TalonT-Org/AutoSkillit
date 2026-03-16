"""Tests for open-pr skill SKILL.md content assertions."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "open-pr"
    / "SKILL.md"
)


def test_closing_issue_arg_documented():
    """SKILL.md must document closing_issue as the 5th positional argument."""
    content = SKILL_PATH.read_text()
    assert "closing_issue" in content


def test_closes_n_footer_documented():
    """SKILL.md must document insertion of 'Closes #' in the PR body."""
    content = SKILL_PATH.read_text()
    assert "Closes #" in content


def test_closing_issue_is_optional():
    """SKILL.md must describe closing_issue as optional."""
    content = SKILL_PATH.read_text()
    # Both the argument signature and the optional description
    assert "optional" in content.lower() or "[closing_issue]" in content


def test_step5_marker_check_present():
    """Step 5 must instruct the agent to check for ★ or ● in each mermaid block."""
    content = SKILL_PATH.read_text()
    assert "validated_diagrams" in content
    # Both marker characters must be mentioned in the check instruction
    assert "★" in content
    assert "●" in content


def test_step5_discard_path_documented():
    """Step 5 must explicitly state that diagrams with no markers are discarded."""
    content = SKILL_PATH.read_text()
    assert "discard" in content.lower()


def test_step6_conditional_section_present():
    """Step 6 must gate the Architecture Impact section on validated_diagrams being non-empty."""
    content = SKILL_PATH.read_text()
    # The conditional gate language must appear in Step 6
    assert "validated_diagrams is non-empty" in content or (
        "validated_diagrams" in content and "non-empty" in content
    )
    assert "omit" in content.lower()


def test_step4_lens_count_constraint_preserved():
    """Step 4 must still instruct the subagent to return 1–3 lenses."""
    content = SKILL_PATH.read_text()
    assert "1–3" in content or "1-3" in content
