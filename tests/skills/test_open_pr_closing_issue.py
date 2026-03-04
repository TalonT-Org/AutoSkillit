"""Tests for open-pr skill SKILL.md closing_issue argument handling."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "skills" / "open-pr" / "SKILL.md"
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
