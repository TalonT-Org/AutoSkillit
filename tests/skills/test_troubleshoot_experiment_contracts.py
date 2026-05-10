import yaml

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


def test_troubleshoot_experiment_has_transient_api_classification():
    """Decision table must classify transient API errors as is_fixable=true."""
    skill_md = (
        pkg_root() / "skills_extended" / "troubleshoot-experiment" / "SKILL.md"
    ).read_text()
    assert "transient_api" in skill_md
    assert "overloaded_error" in skill_md
    assert "rate_limit_error" in skill_md


def test_troubleshoot_experiment_emits_retry_delay_token():
    """SKILL.md Step 6 must declare retry_delay output token."""
    skill_md = (
        pkg_root() / "skills_extended" / "troubleshoot-experiment" / "SKILL.md"
    ).read_text()
    assert "retry_delay" in skill_md


def test_troubleshoot_experiment_contract_includes_retry_delay():
    """skill_contracts.yaml must declare retry_delay as a troubleshoot-experiment output."""
    contracts_path = pkg_root() / "recipe" / "skill_contracts.yaml"
    data = yaml.safe_load(contracts_path.read_text())
    skill = data["skills"]["troubleshoot-experiment"]
    output_names = [o["name"] for o in skill["outputs"]]
    assert "retry_delay" in output_names
