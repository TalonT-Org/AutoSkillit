from autoskillit.core.io import load_yaml
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
    step4_start = skill_md.find("### Step 4:")
    step5_start = skill_md.find("### Step 5:")
    assert step4_start != -1, "SKILL.md must have a '### Step 4:' section"
    decision_table = (
        skill_md[step4_start:step5_start] if step5_start != -1 else skill_md[step4_start:]
    )
    assert "transient_api" in decision_table, (
        "transient_api must appear in the Step 4 decision table"
    )
    assert "overloaded_error" in decision_table, (
        "overloaded_error must appear in the Step 4 decision table"
    )
    assert "rate_limit_error" in decision_table, (
        "rate_limit_error must appear in the Step 4 decision table"
    )


def test_troubleshoot_experiment_emits_retry_delay_token():
    """SKILL.md Step 6 must declare retry_delay output token."""
    skill_md = (
        pkg_root() / "skills_extended" / "troubleshoot-experiment" / "SKILL.md"
    ).read_text()
    step6_start = skill_md.find("### Step 6:")
    assert step6_start != -1, "SKILL.md must have a '### Step 6:' section"
    step7_start = skill_md.find("### Step 7:", step6_start)
    step6_section = (
        skill_md[step6_start:step7_start] if step7_start != -1 else skill_md[step6_start:]
    )
    assert "retry_delay" in step6_section, "retry_delay output token must be declared in Step 6"


def test_troubleshoot_experiment_contract_includes_retry_delay():
    """skill_contracts.yaml must declare retry_delay as a troubleshoot-experiment output."""
    contracts_path = pkg_root() / "recipe" / "skill_contracts.yaml"
    data = load_yaml(contracts_path)
    skill = data["skills"]["troubleshoot-experiment"]
    output_names = [o["name"] for o in skill["outputs"]]
    assert "retry_delay" in output_names
