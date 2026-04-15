from autoskillit.core.paths import pkg_root
from autoskillit.workspace.skills import DefaultSkillResolver


def test_troubleshoot_experiment_skill_is_discoverable():
    """troubleshoot-experiment must be discoverable via SkillResolver."""
    resolver = DefaultSkillResolver()
    skills = resolver.list_all()
    skill_names = [s.name for s in skills]
    assert "troubleshoot-experiment" in skill_names


def test_troubleshoot_experiment_skill_has_skill_md():
    """troubleshoot-experiment directory must contain SKILL.md."""
    skill_path = pkg_root() / "skills_extended" / "troubleshoot-experiment" / "SKILL.md"
    assert skill_path.exists(), f"SKILL.md not found at {skill_path}"
