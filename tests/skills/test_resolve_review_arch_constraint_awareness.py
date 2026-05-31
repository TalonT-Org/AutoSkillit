"""Structural guard: resolve-review SKILL.md must contain an architectural
constraint catalog that intent validation subagents can consult before
classifying ACCEPT.
"""

import re
from pathlib import Path

import pytest

from tests._arch_constraint_discovery import discover_constraint_tests

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-review"
    / "SKILL.md"
)

ARCH_DIR = Path(__file__).parent.parent / "arch"

# Tests whose constraints cannot be violated by reviewer suggestions
# (they guard test infrastructure, CI config, or test file structure).
_CATALOG_EXCLUSIONS: frozenset[str] = frozenset(
    {
        # Test-infrastructure guards (guard test file splits, not production code):
        "test_headless_split.py",
        "test_doctor_split.py",
        "test_update_checks_split.py",
        "test_tools_kitchen_gate_split.py",
        "test_tools_dispatch_split.py",
        "test_tools_git_split.py",
        "test_api_split_integrity.py",
        # CI/infra guards (not violable by code review suggestions):
        "test_ci_dev_config.py",
        "test_session_type_exemption_enforcement.py",
        "test_session_scope_enforcement.py",
        "test_skill_exemption_enforcement.py",
        # The catalog guard itself:
        "test_resolve_review_arch_constraint_awareness.py",
        # SKILL.md content guards (guard skill prose structure, not production code;
        # reviewer suggestions don't change SKILL.md structure):
        "test_audit_impl_diff_discipline.py",
        "test_conflict_resolution_guards.py",
        "test_deletion_regression_guards.py",
        "test_open_research_pr_decomposition.py",
        "test_resolve_design_review_contracts.py",
        "test_resolve_review_intent_validation.py",
        "test_resolve_review_token_optimizations.py",
        "test_review_pr_inline_comment_guards.py",
        # Execution/server test-infrastructure guards:
        "test_conftest_import_guard.py",
        "test_flush_completeness_guard.py",
        "test_smoke_recipe_scope_guard.py",
        # Internal structural guards (guard conftest/env patterns, not production code):
        "test_conftest_env_coverage.py",
        "test_make_context_env_boundary.py",
        "test_command_guard_completeness.py",
        "test_interactive_subprocess_contracts.py",
    }
)


@pytest.fixture(scope="module")
def skill_text():
    if not SKILL_PATH.exists():
        pytest.fail(f"SKILL.md not found at {SKILL_PATH}")
    return SKILL_PATH.read_text()


# --- Discovery utility self-test ---


def test_discovery_finds_known_constraints():
    """Constraint discovery must find tests from multiple directories."""
    discovered = discover_constraint_tests()
    # These three are in different directories (arch/, workspace/, recipe/)
    assert "test_regex_import.py" in discovered, "test_regex_import.py not found"
    assert "test_clone_timeouts.py" in discovered, "test_clone_timeouts.py not found"
    assert "test_anti_pattern_guards.py" in discovered, "test_anti_pattern_guards.py not found"


# --- Catalog presence ---


def test_arch_constraint_catalog_section_exists(skill_text):
    """SKILL.md must contain an 'Architectural Constraint Catalog' section."""
    assert "architectural constraint catalog" in skill_text.lower(), (
        "resolve-review SKILL.md must contain an 'Architectural Constraint Catalog' "
        "section for intent validation subagents"
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
        "project-wide constraints enforced by tests)"
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
    """Every test file cited in the catalog must actually exist in tests/."""
    catalog_idx = skill_text.lower().find(
        "architectural constraint catalog — consult before classifying accept"
    )
    if catalog_idx == -1:
        pytest.fail("Architectural Constraint Catalog section not found in SKILL.md")
    catalog_section = skill_text[catalog_idx : catalog_idx + 6000]
    # Build a set of ALL test filenames across the entire test tree
    all_test_files = {f.name for f in Path(__file__).parent.parent.rglob("test_*.py")}
    referenced_files = set(re.findall(r"`(test_\w+\.py)`", catalog_section))
    for ref in referenced_files:
        assert ref in all_test_files, (
            f"Catalog references {ref} but it does not exist anywhere in tests/"
        )


def test_catalog_reverse_coverage(skill_text):
    """Every discoverable constraint test (minus exclusions) must be
    referenced in the Architectural Constraint Catalog."""
    all_constraints = discover_constraint_tests()
    catalogable = {name for name in all_constraints if name not in _CATALOG_EXCLUSIONS}
    catalog_idx = skill_text.lower().find(
        "architectural constraint catalog — consult before classifying accept"
    )
    assert catalog_idx != -1
    catalog_section = skill_text[catalog_idx : catalog_idx + 6000]
    missing = {name for name in catalogable if name not in catalog_section}
    assert missing == set(), (
        f"Constraint tests not in catalog: {sorted(missing)}. "
        "Add them to the Architectural Constraint Catalog in "
        "resolve-review/SKILL.md or add to _CATALOG_EXCLUSIONS with a reason."
    )


# --- arch_violation category ---


def test_arch_violation_category_defined(skill_text):
    """The REJECT category enum must include arch_violation."""
    assert "arch_violation" in skill_text, (
        "REJECT category enum must include 'arch_violation' for architecturally prohibited changes"
    )
