"""Contract test: sous-chef SKILL.md must contain QUOTA WAIT PROTOCOL section."""

from pathlib import Path


def test_sous_chef_contains_quota_wait_protocol():
    """sous-chef/SKILL.md contains QUOTA WAIT PROTOCOL section."""
    skill_md = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "skills"
        / "sous-chef"
        / "SKILL.md"
    )
    content = skill_md.read_text()
    assert "QUOTA WAIT PROTOCOL" in content
