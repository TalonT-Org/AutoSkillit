import pytest

from autoskillit.core.paths import pkg_root

SKILLS_ROOT = pkg_root() / "skills_extended"

PLANNER_FINALIZATION_SKILLS = [
    "planner-reconcile-deps",
    "planner-refine",
]


@pytest.mark.parametrize("skill_name", PLANNER_FINALIZATION_SKILLS)
def test_skill_directory_exists(skill_name: str) -> None:
    assert (SKILLS_ROOT / skill_name).is_dir()


@pytest.mark.parametrize("skill_name", PLANNER_FINALIZATION_SKILLS)
def test_skill_md_exists(skill_name: str) -> None:
    assert (SKILLS_ROOT / skill_name / "SKILL.md").is_file()


@pytest.mark.parametrize("skill_name", PLANNER_FINALIZATION_SKILLS)
def test_skill_has_planner_category(skill_name: str) -> None:
    import yaml

    content = (SKILLS_ROOT / skill_name / "SKILL.md").read_text()
    assert content.startswith("---"), f"{skill_name}: must start with YAML frontmatter"
    parts = content.split("---", 2)
    assert len(parts) >= 3
    data = yaml.safe_load(parts[1]) or {}
    assert "planner" in (data.get("categories") or []), (
        f"{skill_name}: must declare 'categories: [planner]'"
    )


def test_reconcile_deps_output_token() -> None:
    content = (SKILLS_ROOT / "planner-reconcile-deps" / "SKILL.md").read_text()
    assert "dep_graph_path" in content, (
        "planner-reconcile-deps must document dep_graph_path output token"
    )


def test_refine_output_tokens() -> None:
    content = (SKILLS_ROOT / "planner-refine" / "SKILL.md").read_text()
    assert "refinement_complete" in content, (
        "planner-refine must document refinement_complete output token"
    )
    assert "issues_fixed" in content, "planner-refine must document issues_fixed output token"


def test_reconcile_deps_reads_wp_index_only() -> None:
    content = (SKILLS_ROOT / "planner-reconcile-deps" / "SKILL.md").read_text()
    assert "wp_index.json" in content
    assert "sub-agent" not in content.lower() and "subagent" not in content.lower(), (
        "planner-reconcile-deps must be a single session — no sub-agents"
    )


def test_refine_handles_all_finding_types() -> None:
    content = (SKILLS_ROOT / "planner-refine" / "SKILL.md").read_text()
    for finding_type in ["failed", "sizing", "duplicate", "dep", "missing"]:
        assert finding_type in content.lower(), (
            f"planner-refine must document handling of '{finding_type}' finding type"
        )


@pytest.mark.parametrize("skill_name", PLANNER_FINALIZATION_SKILLS)
def test_skill_in_defaults_yaml_tier2(skill_name: str) -> None:
    import yaml

    defaults = yaml.safe_load((pkg_root() / "config" / "defaults.yaml").read_text())
    tier2 = defaults["skills"]["tier2"]
    assert skill_name in tier2, f"{skill_name} must appear in defaults.yaml skills.tier2"
