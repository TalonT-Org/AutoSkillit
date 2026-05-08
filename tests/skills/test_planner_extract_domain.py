import pytest

from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]


def test_planner_extract_domain_skill_uses_positional_args():
    """SKILL.md must reference $1 and $2, not env vars."""
    skill_dir = pkg_root() / "skills_extended" / "planner-extract-domain"
    content = (skill_dir / "SKILL.md").read_text()
    assert "$1" in content, "Must document $1 (analysis.json path)"
    assert "$2" in content, "Must document $2 (task_file path)"
    assert "PLANNER_ANALYSIS_FILE" not in content, (
        "Must not reference PLANNER_ANALYSIS_FILE env var"
    )


def test_extract_domain_skill_no_env_var_delivery():
    """SKILL.md must not reference PLANNER_TASK or PLANNER_TASK_FILE as env vars."""
    content = (pkg_root() / "skills_extended" / "planner-extract-domain" / "SKILL.md").read_text()
    assert "PLANNER_TASK" not in content, "Must not reference PLANNER_TASK env var"
