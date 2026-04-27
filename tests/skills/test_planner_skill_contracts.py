import pytest
import yaml

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.io import load_recipe

SKILLS_ROOT = pkg_root() / "skills_extended"
RECIPES_ROOT = pkg_root() / "recipes"

PLANNER_FINALIZATION_SKILLS = [
    "planner-reconcile-deps",
    "planner-refine",
]

ALL_PLANNER_SKILLS = [
    "planner-analyze",
    "planner-extract-domain",
    "planner-generate-phases",
    "planner-elaborate-phase",
    "planner-elaborate-assignment",
    "planner-elaborate-assignments",
    "planner-elaborate-wp",
    "planner-elaborate-wps",
    "planner-reconcile-deps",
    "planner-refine",
    "planner-refine-assignments",
]


@pytest.mark.parametrize("skill_name", PLANNER_FINALIZATION_SKILLS)
def test_skill_directory_exists(skill_name: str) -> None:
    assert (SKILLS_ROOT / skill_name).is_dir()


@pytest.mark.parametrize("skill_name", PLANNER_FINALIZATION_SKILLS)
def test_skill_md_exists(skill_name: str) -> None:
    assert (SKILLS_ROOT / skill_name / "SKILL.md").is_file()


@pytest.mark.parametrize("skill_name", PLANNER_FINALIZATION_SKILLS)
def test_skill_has_planner_category(skill_name: str) -> None:
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
    assert "sub-agent" not in content.lower(), (
        "planner-reconcile-deps must be a single session — no sub-agents"
    )
    assert "subagent" not in content.lower(), (
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
    defaults = yaml.safe_load((pkg_root() / "config" / "defaults.yaml").read_text())
    tier2 = defaults["skills"]["tier2"]
    assert skill_name in tier2, f"{skill_name} must appear in defaults.yaml skills.tier2"


def test_all_planner_recipe_skills_registered_in_contract_card() -> None:
    """T1a: every run_skill step in planner.yaml must appear in the planner contract card."""
    recipe = load_recipe(RECIPES_ROOT / "planner.yaml")
    recipe_skills = {
        step.with_args.get("skill_command", "").split(":")[1].split()[0]
        for step in recipe.steps.values()
        if step.tool == "run_skill" and "/autoskillit:" in step.with_args.get("skill_command", "")
    }

    card_path = RECIPES_ROOT / "contracts" / "planner.yaml"
    assert card_path.is_file(), "planner contract card not found — run generate_recipe_card"
    card = yaml.safe_load(card_path.read_text())
    registered = set(card.get("skills", {}).keys())

    missing = recipe_skills - registered
    assert not missing, (
        f"Planner skills used in recipe but missing from contract card: {sorted(missing)}"
    )


def test_planner_contract_card_records_positional_args_for_generate_phases() -> None:
    """T1b: the generate_phases dataflow entry must record positional_args > 0."""
    card_path = RECIPES_ROOT / "contracts" / "planner.yaml"
    assert card_path.is_file(), "planner contract card not found — run generate_recipe_card"
    card = yaml.safe_load(card_path.read_text())

    generate_phases_entry = next(
        (e for e in card.get("dataflow", []) if e.get("step") == "generate_phases"),
        None,
    )
    assert generate_phases_entry is not None, (
        "generate_phases step not found in planner contract card dataflow"
    )
    assert generate_phases_entry.get("positional_args", 0) > 0, (
        "generate_phases step should record positional_args > 0 in the contract card"
    )


@pytest.mark.parametrize("skill_name", ALL_PLANNER_SKILLS)
def test_all_planner_skills_in_master_manifest(skill_name: str) -> None:
    """All 8 planner skills must be registered in skill_contracts.yaml."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    manifest = load_bundled_manifest()
    assert skill_name in manifest.get("skills", {}), (
        f"Planner skill '{skill_name}' is not registered in skill_contracts.yaml"
    )


def test_elaborate_phase_contract_declares_elab_result_path() -> None:
    """planner-elaborate-phase must declare elab_result_path for capture_list (Issue 08)."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    contracts = load_bundled_manifest()
    contract = contracts["skills"]["planner-elaborate-phase"]
    output_names = [o["name"] for o in contract.get("outputs", [])]
    assert "elab_result_path" in output_names, (
        "planner-elaborate-phase must declare elab_result_path output for "
        "capture_list: elab_result_path in the parallel recipe step (Issue 08)"
    )


def test_elaborate_phase_contract_has_output_pattern() -> None:
    """elab_result_path must appear in expected_output_patterns for run_skill validation."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    contracts = load_bundled_manifest()
    contract = contracts["skills"]["planner-elaborate-phase"]
    patterns = contract.get("expected_output_patterns", [])
    assert any("elab_result_path" in p for p in patterns), (
        "planner-elaborate-phase must have an expected_output_pattern matching elab_result_path"
    )


def test_elaborate_phase_contract_write_behavior_always() -> None:
    """planner-elaborate-phase must declare write_behavior: always."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    contracts = load_bundled_manifest()
    contract = contracts["skills"]["planner-elaborate-phase"]
    assert contract.get("write_behavior") == "always"


def test_elaborate_assignments_contract_declares_phase_result_dir() -> None:
    """planner-elaborate-assignments must declare phase_assignments_result_dir output."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    contracts = load_bundled_manifest()
    contract = contracts["skills"]["planner-elaborate-assignments"]
    output_names = [o["name"] for o in contract.get("outputs", [])]
    assert "phase_assignments_result_dir" in output_names, (
        "planner-elaborate-assignments must declare phase_assignments_result_dir output"
    )


def test_elaborate_assignments_contract_has_output_pattern() -> None:
    """phase_assignments_result_dir must appear in expected_output_patterns."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    contracts = load_bundled_manifest()
    contract = contracts["skills"]["planner-elaborate-assignments"]
    patterns = contract.get("expected_output_patterns", [])
    assert any("phase_assignments_result_dir" in p for p in patterns), (
        "planner-elaborate-assignments must have an expected_output_pattern"
        " matching phase_assignments_result_dir"
    )


def test_elaborate_assignments_contract_write_behavior_always() -> None:
    """planner-elaborate-assignments must declare write_behavior: always."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    contracts = load_bundled_manifest()
    contract = contracts["skills"]["planner-elaborate-assignments"]
    assert contract.get("write_behavior") == "always"


def test_elaborate_wps_contract_declares_phase_wps_result_dir() -> None:
    """planner-elaborate-wps must declare phase_wps_result_dir output."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    contracts = load_bundled_manifest()
    contract = contracts["skills"]["planner-elaborate-wps"]
    output_names = [o["name"] for o in contract.get("outputs", [])]
    assert "phase_wps_result_dir" in output_names, (
        "planner-elaborate-wps must declare phase_wps_result_dir output"
    )


def test_elaborate_wps_contract_has_output_pattern() -> None:
    """phase_wps_result_dir must appear in expected_output_patterns."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    contracts = load_bundled_manifest()
    contract = contracts["skills"]["planner-elaborate-wps"]
    patterns = contract.get("expected_output_patterns", [])
    assert any("phase_wps_result_dir" in p for p in patterns), (
        "planner-elaborate-wps must have an expected_output_pattern matching phase_wps_result_dir"
    )


def test_elaborate_wps_contract_write_behavior_always() -> None:
    """planner-elaborate-wps must declare write_behavior: always."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    contracts = load_bundled_manifest()
    contract = contracts["skills"]["planner-elaborate-wps"]
    assert contract.get("write_behavior") == "always"


def test_elaborate_wps_contract_two_inputs() -> None:
    """planner-elaborate-wps must declare two inputs: context_file and planner_dir."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    contracts = load_bundled_manifest()
    contract = contracts["skills"]["planner-elaborate-wps"]
    inputs = contract.get("inputs", [])
    assert len(inputs) == 2, (
        f"Expected 2 inputs, got {len(inputs)}: {[i.get('name') for i in inputs]}"
    )
    input_names = {i.get("name") for i in inputs}
    assert input_names == {"context_file", "planner_dir"}
