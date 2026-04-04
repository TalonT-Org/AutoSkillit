"""Contract tests: plan-experiment YAML frontmatter schema and revision_guidance argument."""

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


def _read_skill_md(skill_name: str) -> str:
    for rel_dir in ("skills_extended", "skills"):
        path = _repo_root() / "src/autoskillit" / rel_dir / skill_name / "SKILL.md"
        if path.exists():
            return path.read_text()
    raise FileNotFoundError(f"SKILL.md not found for skill: {skill_name}")


def test_plan_experiment_accepts_revision_guidance_arg():
    """plan-experiment/SKILL.md documents revision_guidance as second optional positional arg."""
    content = _read_skill_md("plan-experiment")
    assert "[{revision_guidance}]" in content, (
        "plan-experiment/SKILL.md must document 'revision_guidance' as an optional second "
        "positional arg in usage line '[{revision_guidance}]', with path-scanning semantics"
    )


def test_plan_experiment_documents_frontmatter_delimiters():
    """plan-experiment/SKILL.md must use the word 'frontmatter' to describe the output format."""
    content = _read_skill_md("plan-experiment")
    assert "frontmatter" in content.lower(), (
        "plan-experiment/SKILL.md must reference 'frontmatter' to describe the YAML "
        "block written before '# Experiment Plan:' — checking for '---' alone is "
        "non-discriminating because the skill's own YAML metadata header already "
        "contains '---' delimiters"
    )


def test_plan_experiment_documents_all_required_frontmatter_fields():
    """plan-experiment/SKILL.md must reference all required schema fields."""
    content = _read_skill_md("plan-experiment")
    required_fields = [
        "experiment_type",
        "hypothesis_h0",
        "hypothesis_h1",
        "metrics",
        "statistical_plan",
        "success_criteria",
        "environment",
        "baselines",
    ]
    for field in required_fields:
        assert field in content, (
            f"plan-experiment/SKILL.md missing required frontmatter field: {field!r}"
        )


def test_plan_experiment_defines_all_validation_rules():
    """plan-experiment/SKILL.md must define all 8 validation rules V1–V8 as labeled entries."""
    content = _read_skill_md("plan-experiment")
    for rule_id in ["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8"]:
        assert f"{rule_id}:" in content, (
            f"plan-experiment/SKILL.md missing validation rule label {rule_id!r} "
            f"(expected '{rule_id}:' definition format)"
        )


def test_plan_experiment_frontmatter_before_heading():
    """plan-experiment/SKILL.md must specify frontmatter goes before # Experiment Plan: heading."""
    content = _read_skill_md("plan-experiment")
    assert "BEFORE the # Experiment Plan" in content, (
        "plan-experiment/SKILL.md must contain the explicit ordering instruction "
        "'BEFORE the # Experiment Plan' to specify frontmatter precedes the heading"
    )


def test_research_recipe_passes_revision_guidance_to_plan_experiment():
    """research.yaml plan_experiment step must pass context.revision_guidance."""
    recipe_path = _repo_root() / "src/autoskillit/recipes/research.yaml"
    content = recipe_path.read_text()
    assert "revision_guidance" in content, (
        "research.yaml must pass context.revision_guidance in the plan_experiment step "
        "skill_command so revision passes receive the feedback file path"
    )


def test_skill_contracts_plan_experiment_has_revision_guidance_input():
    """skill_contracts.yaml must register revision_guidance as optional (required: false) input."""
    import yaml

    contracts_path = _repo_root() / "src/autoskillit/recipe/skill_contracts.yaml"
    raw = yaml.safe_load(contracts_path.read_text())
    pe = raw.get("skills", {}).get("plan-experiment", {})
    inputs = {inp["name"]: inp for inp in pe.get("inputs", [])}
    assert "revision_guidance" in inputs, (
        "skill_contracts.yaml plan-experiment must declare 'revision_guidance' input"
    )
    assert inputs["revision_guidance"].get("required") is False, (
        "skill_contracts.yaml plan-experiment 'revision_guidance' input must have required: false"
    )
