"""Structural guard: resolve-review SKILL.md must contain an architectural
constraint catalog that intent validation subagents can consult before
classifying ACCEPT.
"""

import re
from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-review"
    / "SKILL.md"
)

ARCH_DIR = Path(__file__).parent.parent / "arch"


@pytest.fixture(scope="module")
def skill_text():
    if not SKILL_PATH.exists():
        pytest.fail(f"SKILL.md not found at {SKILL_PATH}")
    return SKILL_PATH.read_text()


# --- Catalog presence ---


def test_arch_constraint_catalog_section_exists(skill_text):
    """SKILL.md must contain an 'Architectural Constraint Catalog' section."""
    assert "architectural constraint catalog" in skill_text.lower(), (
        "resolve-review SKILL.md must contain an 'Architectural Constraint Catalog' "
        "section for intent validation subagents"
    )


# --- Catalog references key arch tests ---


def test_catalog_references_key_constraint_tests(skill_text):
    """The catalog must reference the most commonly violated constraint
    enforcement test filenames — from tests/arch/ and tests/recipe/."""
    key_test_files = [
        "test_regex_import.py",  # tests/arch/
        "test_ast_rules.py",  # tests/arch/
        "test_dataclass_slots.py",  # tests/arch/
        "test_layer_enforcement.py",  # tests/arch/
        "test_anti_pattern_guards.py",  # tests/recipe/
    ]
    for test_file in key_test_files:
        assert test_file in skill_text, (
            f"Architectural Constraint Catalog must reference {test_file} by name"
        )


# --- REJECT criteria include arch violation ---


def test_reject_criteria_includes_arch_violation(skill_text):
    """The REJECT classification criteria prose must include language about
    architecturally prohibited changes — not just the category enum."""
    criteria_idx = skill_text.find("Classification criteria:")
    if criteria_idx == -1:
        criteria_idx = skill_text.find("**Classification criteria:**")
    assert criteria_idx != -1, "SKILL.md must have a Classification criteria section"
    criteria_region = skill_text[criteria_idx : criteria_idx + 1200].lower()
    assert (
        "architectural constraint" in criteria_region
        or "architecturally prohibited" in criteria_region
        or "project-wide architectural constraint" in criteria_region
    ), (
        "Classification criteria prose for REJECT must include language about "
        "architecturally prohibited changes (locally correct but violating "
        "project-wide constraints enforced by tests/arch/)"
    )


# --- Sub-agent prompt references catalog ---


def test_subagent_prompt_references_constraint_catalog(skill_text):
    """Step 3.5 sub-agent prompt must instruct subagents to check the
    Architectural Constraint Catalog — not just mention 'arch' generically."""
    step35_idx = skill_text.find("### Step 3.5")
    assert step35_idx != -1
    step4_idx = skill_text.find("### Step 4", step35_idx + 10)
    step35_section = (
        skill_text[step35_idx:step4_idx]
        if step4_idx != -1
        else skill_text[step35_idx : step35_idx + 5000]
    )
    assert "architectural constraint catalog" in step35_section.lower(), (
        "Step 3.5 sub-agent instructions must reference the "
        "'Architectural Constraint Catalog' by name so subagents can check "
        "proposed changes against project-wide constraints"
    )


# --- NEVER block ---


def test_never_block_prohibits_ignoring_arch_constraints(skill_text):
    """NEVER block must prohibit accepting changes that violate
    architectural constraints."""
    never_start = skill_text.find("**NEVER:**")
    if never_start == -1:
        never_start = skill_text.find("NEVER:")
    assert never_start != -1, "SKILL.md must have a NEVER block"
    always_start = skill_text.find("**ALWAYS:**", never_start)
    if always_start == -1:
        always_start = skill_text.find("ALWAYS:", never_start)
    never_block = (
        skill_text[never_start:always_start]
        if always_start != -1
        else skill_text[never_start : never_start + 1000]
    )
    assert (
        "architectural constraint" in never_block.lower()
        or "architecturally prohibited" in never_block.lower()
    ), (
        "NEVER block must prohibit accepting/applying changes that violate "
        "project-wide architectural constraints"
    )


# --- Bidirectional staleness guard ---


def test_catalog_forward_references_valid(skill_text):
    """Every arch test file referenced in the catalog must actually exist
    in tests/arch/ or tests/recipe/."""
    catalog_idx = skill_text.lower().find("architectural constraint catalog")
    if catalog_idx == -1:
        return  # test_arch_constraint_catalog_section_exists will catch this
    catalog_section = skill_text[catalog_idx : catalog_idx + 3000]
    arch_test_files = {f.name for f in ARCH_DIR.glob("test_*.py")}
    recipe_test_dir = ARCH_DIR.parent / "recipe"
    recipe_test_files = (
        {f.name for f in recipe_test_dir.glob("test_*.py")} if recipe_test_dir.exists() else set()
    )
    all_test_files = arch_test_files | recipe_test_files
    referenced_files = set(re.findall(r"`(test_\w+\.py)`", catalog_section))
    for ref in referenced_files:
        assert ref in all_test_files, (
            f"Catalog references {ref} but it does not exist in tests/arch/ or tests/recipe/"
        )


def test_catalog_reverse_coverage(skill_text):
    """High-risk arch test files must be referenced in the catalog.
    When a new arch test is added that enforces a constraint violable by
    reviewer suggestions, this test ensures the catalog is updated."""
    high_risk_test_files = {
        "test_regex_import.py",
        "test_ast_rules.py",
        "test_dataclass_slots.py",
        "test_layer_enforcement.py",
        "test_never_raises_contracts.py",
        "test_no_backend_name_bypass.py",
        "test_subpackage_isolation.py",
        "test_anyio_migration.py",
        "test_python_no_hardcoded_temp.py",
        "test_skill_result_construction_guard.py",
        "test_anti_pattern_guards.py",
    }
    for test_file in high_risk_test_files:
        arch_path = ARCH_DIR / test_file
        recipe_path = ARCH_DIR.parent / "recipe" / test_file
        if not arch_path.exists() and not recipe_path.exists():
            continue
        assert test_file in skill_text, (
            f"High-risk arch test {test_file} exists but is not referenced "
            f"in the Architectural Constraint Catalog. Update the catalog."
        )


# --- arch_violation category ---


def test_arch_violation_category_defined(skill_text):
    """The REJECT category enum must include arch_violation."""
    assert "arch_violation" in skill_text, (
        "REJECT category enum must include 'arch_violation' for architecturally prohibited changes"
    )
