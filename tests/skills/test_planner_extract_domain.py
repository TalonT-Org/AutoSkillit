import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]


def test_planner_extract_domain_skill_uses_env_var():
    """SKILL.md must reference PLANNER_ANALYSIS_FILE, not $3."""
    skill_dir = pkg_root() / "skills_extended" / "planner-extract-domain"
    content = (skill_dir / "SKILL.md").read_text()
    assert "$3" not in content
    assert "PLANNER_ANALYSIS_FILE" in content
