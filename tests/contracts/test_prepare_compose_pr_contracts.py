"""Contract tests for prepare-pr and compose-pr skills."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"
PREPARE_PR = SKILLS_DIR / "prepare-pr/SKILL.md"
COMPOSE_PR = SKILLS_DIR / "compose-pr/SKILL.md"


def test_prepare_pr_skill_exists():
    assert PREPARE_PR.exists()


def test_compose_pr_skill_exists():
    assert COMPOSE_PR.exists()


def test_prepare_pr_outputs_prep_path():
    text = PREPARE_PR.read_text()
    assert "prep_path" in text


def test_prepare_pr_outputs_selected_lenses():
    text = PREPARE_PR.read_text()
    assert "selected_lenses" in text


def test_prepare_pr_outputs_lens_context_paths():
    text = PREPARE_PR.read_text()
    assert "lens_context_paths" in text


def test_prepare_pr_never_invokes_arch_lens():
    """prepare-pr must explicitly state it does NOT invoke arch-lens skills."""
    text = PREPARE_PR.read_text()
    assert "NOT invoke arch-lens" in text or "Does NOT invoke any arch-lens" in text


def test_prepare_pr_classifies_new_vs_modified():
    """prepare-pr must classify files as new (★) vs modified (●)."""
    text = PREPARE_PR.read_text()
    assert "★" in text and "●" in text


def test_compose_pr_outputs_pr_url():
    text = COMPOSE_PR.read_text()
    assert "pr_url" in text


def test_compose_pr_validates_diagrams_with_markers():
    """compose-pr must validate that diagrams contain ★ or ●."""
    text = COMPOSE_PR.read_text()
    assert "★" in text and "●" in text


def test_compose_pr_degrades_gracefully_without_diagrams():
    """compose-pr must handle empty diagram list gracefully."""
    text = COMPOSE_PR.read_text()
    assert (
        "empty" in text.lower()
        or "no diagrams" in text.lower()
        or "all_diagram_paths is empty" in text
    )


def test_compose_pr_never_invokes_sub_skills():
    text = COMPOSE_PR.read_text()
    assert "NOT invoke any sub-skills" in text or "Does NOT invoke sub-skills" in text


def test_compose_pr_gh_degrades_gracefully():
    text = COMPOSE_PR.read_text()
    assert "gh auth status" in text
    assert "empty" in text.lower() or "pr_url =" in text


RECIPES_DIR = Path(__file__).parents[2] / "src" / "autoskillit" / "recipes"


def test_compose_pr_skill_command_includes_issue_number() -> None:
    """compose_pr skill_command must structurally include context.issue_number."""
    from autoskillit.core.io import load_yaml

    data = load_yaml(RECIPES_DIR / "implementation.yaml")
    skill_command = data["steps"]["compose_pr"]["with"]["skill_command"]
    assert "${{ context.issue_number }}" in skill_command, (
        f"compose_pr skill_command must include"
        f" ${{{{ context.issue_number }}}} as a positional arg, "
        f"not rely on with: metadata. Got: {skill_command!r}"
    )


def test_prepare_pr_skill_command_includes_issue_number() -> None:
    """prepare_pr skill_command must structurally include context.issue_number."""
    from autoskillit.core.io import load_yaml

    data = load_yaml(RECIPES_DIR / "implementation.yaml")
    skill_command = data["steps"]["prepare_pr"]["with"]["skill_command"]
    assert "${{ context.issue_number }}" in skill_command, (
        f"prepare_pr skill_command must include"
        f" ${{{{ context.issue_number }}}} as a positional arg, "
        f"not rely on with: metadata. Got: {skill_command!r}"
    )


def test_prepare_pr_title_source_attribution():
    """Step 2 must explicitly prohibit using issue metadata for task_title."""
    import regex as re

    text = PREPARE_PR.read_text()
    pattern = re.compile(
        r"(?:NOT|NEVER)[\s\S]*?(?:issue\s+title|issue\s+body|issue\s+metadata|closing_issue)"
        r"[\s\S]*?(?:task_title|PR\s+title|## Title)",
        re.IGNORECASE,
    )
    assert pattern.search(text), (
        "prepare-pr/SKILL.md Step 2 must contain a proximity-anchored prohibition: "
        "'Do NOT use the issue title/body/metadata for task_title/PR title'"
    )


def test_compose_pr_title_source_attribution():
    """Step 1 must prohibit re-deriving task_title from non-prep-file sources."""
    import regex as re

    text = COMPOSE_PR.read_text()
    pattern = re.compile(
        r"(?:NOT|NEVER)[\s\S]*?(?:re-?deriv|overrid|substitut|replac)[\s\S]*?(?:task_title|title)"
        r"|task_title[\s\S]*?(?:ONLY|exclusively|solely)[\s\S]*?(?:prep\s+file|## Title)",
        re.IGNORECASE,
    )
    assert pattern.search(text), (
        "compose-pr/SKILL.md must prohibit re-deriving task_title from non-prep-file sources"
    )


def test_prepare_pr_source_pin_fields_in_manifest():
    from autoskillit.recipe.contracts import load_bundled_manifest

    manifest = load_bundled_manifest()
    skill_data = manifest["skills"]["prepare-pr"]
    assert "source_pin_fields" in skill_data, (
        "prepare-pr must declare source_pin_fields in skill_contracts.yaml"
    )
    fields = skill_data["source_pin_fields"]
    assert any(f["field"] == "task_title" for f in fields)


def test_compose_pr_source_pin_fields_in_manifest():
    from autoskillit.recipe.contracts import load_bundled_manifest

    manifest = load_bundled_manifest()
    skill_data = manifest["skills"]["compose-pr"]
    assert "source_pin_fields" in skill_data, (
        "compose-pr must declare source_pin_fields in skill_contracts.yaml"
    )
    fields = skill_data["source_pin_fields"]
    assert any(f["field"] == "task_title" for f in fields)


def test_prepare_pr_plan_summary_prohibits_nested_heading():
    """Plan Summary placeholder must prohibit including the ## Summary heading.

    Checks that a single sentence contains both the prohibition verb and the
    heading reference — a proximity anchor that prevents false passes from
    unrelated occurrences scattered across the file.
    """
    import regex as re

    text = PREPARE_PR.read_text()
    pattern = re.compile(
        r"(?:NOT include|NEVER.*include).*## Summary.*heading"
        r"|heading.*## Summary.*(?:NOT include|NEVER)",
        re.IGNORECASE,
    )
    assert pattern.search(text), (
        "prepare-pr/SKILL.md must contain a proximity-anchored prohibition: "
        "'do NOT include the ## Summary heading' or 'NEVER include the ## Summary heading'"
    )


def test_prepare_pr_no_hardcoded_lens_file_patterns():
    """Development lens selection must not enumerate specific build tool filenames."""
    text = PREPARE_PR.read_text()
    for pattern in ["pyproject.toml", "Taskfile*", "setup.cfg", "tox.ini", "noxfile.py"]:
        assert pattern not in text, (
            f"prepare-pr must not contain hardcoded file pattern '{pattern}' — "
            "lens selection should use codebase-agnostic criteria"
        )


def test_prepare_pr_codebase_agnostic_development_lens_criteria():
    """Development lens prompt must use intent-based criteria, not filenames."""
    text = PREPARE_PR.read_text().lower()
    assert "build configuration" in text
    assert "test infrastructure" in text
    assert "ci" in text
    assert "quality gate" in text
    assert "file purpose" in text or "purpose, not filename" in text


def test_prepare_pr_documents_arch_lenses_argument():
    """prepare-pr must document arch_lenses as a positional argument."""
    text = PREPARE_PR.read_text()
    args_idx = text.find("## Arguments")
    step0_idx = text.find("### Step 0")
    assert args_idx != -1 and step0_idx != -1
    args_section = text[args_idx:step0_idx]
    assert "arch_lenses" in args_section, (
        "prepare-pr Arguments section must document the arch_lenses parameter"
    )


def test_prepare_pr_skips_lens_work_when_arch_lenses_false():
    """prepare-pr must skip Steps 5-6 when arch_lenses is not true."""
    text = PREPARE_PR.read_text()
    step5_idx = text.find("### Step 5")
    step7_idx = text.find("### Step 7")
    assert step5_idx != -1 and step7_idx != -1
    step5_section = text[step5_idx:step7_idx]
    assert "arch_lenses" in step5_section, "Step 5 must reference arch_lenses for the skip gate"
    assert "none" in step5_section.lower(), "Step 5 skip path must emit 'none' for lens tokens"


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation.yaml",
        "remediation.yaml",
        "implementation-groups.yaml",
    ],
)
def test_recipe_prepare_pr_passes_arch_lenses(recipe_name: str) -> None:
    """Every recipe's prepare_pr step must pass arch_lenses and issue_number in skill_command."""
    from autoskillit.core.io import load_yaml

    data = load_yaml(RECIPES_DIR / recipe_name)
    skill_command = data["steps"]["prepare_pr"]["with"]["skill_command"]
    assert "${{ inputs.arch_lenses }}" in skill_command, (
        f"{recipe_name}: prepare_pr skill_command must include "
        "${{ inputs.arch_lenses }} as a positional argument"
    )
    assert "${{ context.issue_number }}" in skill_command, (
        f"{recipe_name}: prepare_pr skill_command must include "
        "${{ context.issue_number }} as a positional argument, "
        "not in a separate with: key (ADR-0003)"
    )


def test_prepare_pr_contract_includes_arch_lenses_input() -> None:
    """prepare-pr contract must declare arch_lenses as an optional input."""
    from autoskillit.recipe.contracts import load_bundled_manifest

    manifest = load_bundled_manifest()
    inputs = manifest["skills"]["prepare-pr"]["inputs"]
    assert any(i["name"] == "arch_lenses" for i in inputs), (
        "prepare-pr must declare arch_lenses in skill_contracts.yaml inputs"
    )


def test_compose_pr_never_fabricate_diagrams():
    """compose-pr NEVER block must contain unconditional diagram fabrication prohibition."""
    text = COMPOSE_PR.read_text()
    never_idx = text.find("**NEVER:**")
    always_idx = text.find("**ALWAYS:**")
    assert never_idx != -1 and always_idx != -1
    never_block = text[never_idx:always_idx]
    assert "fabricat" in never_block.lower(), (
        "NEVER block must contain an unconditional prohibition against fabricating diagrams"
    )


def test_compose_pr_unconditional_diagram_gate_before_step3():
    """compose-pr must have an unconditional no-fabrication rule between Step 2 and Step 3."""
    text = COMPOSE_PR.read_text()
    step2_idx = text.find("### Step 2")
    step3_idx = text.find("### Step 3")
    assert step2_idx != -1 and step3_idx != -1
    between = text[step2_idx:step3_idx]
    lower = between.lower()
    assert "do not" in lower, "Must have 'do not' instruction between Step 2 and Step 3"
    assert "generat" in lower, "Must prohibit 'generate' between Step 2 and Step 3"
    assert "fabricat" in lower, "Must prohibit 'fabricate' between Step 2 and Step 3"
    assert "creat" in lower, "Must prohibit 'create' between Step 2 and Step 3"
