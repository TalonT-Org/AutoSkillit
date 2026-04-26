"""Contract tests verifying shared mutable state isolation guidance exists in pipeline skills."""

import pytest

from autoskillit.core.paths import pkg_root


@pytest.fixture(scope="module")
def make_plan_text() -> str:
    p = pkg_root() / "skills_extended" / "make-plan" / "SKILL.md"
    return p.read_text()


@pytest.fixture(scope="module")
def dry_walkthrough_text() -> str:
    p = pkg_root() / "skills_extended" / "dry-walkthrough" / "SKILL.md"
    return p.read_text()


@pytest.fixture(scope="module")
def resolve_failures_text() -> str:
    p = pkg_root() / "skills_extended" / "resolve-failures" / "SKILL.md"
    return p.read_text()


@pytest.fixture(scope="module")
def tests_claude_md_text() -> str:
    p = pkg_root().parent.parent / "tests" / "CLAUDE.md"
    return p.read_text()


def test_make_plan_step1_reads_isolation_patterns(make_plan_text: str) -> None:
    planning_steps_idx = make_plan_text.find("## Planning Steps")
    assert planning_steps_idx != -1, "make-plan SKILL.md must have a '## Planning Steps' section"
    step1_idx = make_plan_text.find("**Understand related systems", planning_steps_idx)
    step2_idx = make_plan_text.find("**Explore and design approaches", planning_steps_idx)
    assert step1_idx != -1 and step2_idx != -1
    step1_section = make_plan_text[step1_idx:step2_idx]
    has_isolation_read = "isolation" in step1_section.lower() and (
        "singleton" in step1_section.lower()
        or "module-level" in step1_section.lower()
        or "mutating" in step1_section.lower()
    )
    assert has_isolation_read, (
        "make-plan Step 1 must instruct reading existing test isolation patterns "
        "when the plan involves tests that call mutating methods on singletons or "
        "module-level objects"
    )


def test_make_plan_step3_isolation_contract(make_plan_text: str) -> None:
    planning_steps_idx = make_plan_text.find("## Planning Steps")
    assert planning_steps_idx != -1, "make-plan SKILL.md must have a '## Planning Steps' section"
    step3_idx = make_plan_text.find("**Design tests first", planning_steps_idx)
    step4_idx = make_plan_text.find("**Evaluate approaches", planning_steps_idx)
    assert step3_idx != -1 and step4_idx != -1
    step3_section = make_plan_text[step3_idx:step4_idx]
    has_isolation_contract = "isolation" in step3_section.lower() and (
        "cleanup" in step3_section.lower() or "reset" in step3_section.lower()
    )
    assert has_isolation_contract, (
        "make-plan Step 3 must include a test isolation contract requiring plans "
        "to specify how shared state is reset between tests when mutating shared objects"
    )


def test_make_plan_step3_incomplete_without_cleanup(make_plan_text: str) -> None:
    planning_steps_idx = make_plan_text.find("## Planning Steps")
    assert planning_steps_idx != -1, "make-plan SKILL.md must have a '## Planning Steps' section"
    step3_idx = make_plan_text.find("**Design tests first", planning_steps_idx)
    step4_idx = make_plan_text.find("**Evaluate approaches", planning_steps_idx)
    assert step3_idx != -1 and step4_idx != -1
    step3_section = make_plan_text[step3_idx:step4_idx]
    assert "incomplete" in step3_section.lower(), (
        "make-plan Step 3 must explicitly label plans that prescribe mutating shared "
        "state without specifying cleanup as 'incomplete'"
    )


def test_dry_walkthrough_step2_has_shared_state_check(dry_walkthrough_text: str) -> None:
    step2_idx = dry_walkthrough_text.find("### Step 2:")
    step3_idx = dry_walkthrough_text.find("### Step 3:")
    assert step2_idx != -1 and step3_idx != -1
    step2_section = dry_walkthrough_text[step2_idx:step3_idx]
    has_shared_state = (
        "shared" in step2_section.lower()
        and (
            "cleanup" in step2_section.lower()
            or "restore" in step2_section.lower()
            or "reset" in step2_section.lower()
        )
        and (
            "singleton" in step2_section.lower()
            or "module-scope" in step2_section.lower()
            or "mutating" in step2_section.lower()
        )
    )
    assert has_shared_state, (
        "dry-walkthrough Step 2 checklist must include a check that verifies "
        "plans specify cleanup for mutating methods on shared/module-scope objects"
    )


def test_dry_walkthrough_step2_mechanical_scan_instruction(dry_walkthrough_text: str) -> None:
    step2_idx = dry_walkthrough_text.find("### Step 2:")
    step3_idx = dry_walkthrough_text.find("### Step 3:")
    assert step2_idx != -1 and step3_idx != -1
    step2_section = dry_walkthrough_text[step2_idx:step3_idx]
    has_mechanical = "scan" in step2_section.lower() or "search" in step2_section.lower()
    assert has_mechanical, (
        "dry-walkthrough Step 2 shared-state check must include a mechanical scan "
        "instruction (e.g., 'scan the plan for method calls on module-scope objects') "
        "so the LLM has a concrete action, not just an open-ended question"
    )


def test_resolve_failures_accumulation_pattern_guidance(resolve_failures_text: str) -> None:
    constraints_idx = resolve_failures_text.find("## Critical Constraints")
    assert constraints_idx != -1, (
        "resolve-failures SKILL.md must have a '## Critical Constraints' section"
    )
    assert "accumulation-based" in resolve_failures_text[constraints_idx:].lower(), (
        "resolve-failures must describe the 'accumulation-based' pattern — shared state "
        "growing unboundedly rather than toggling — as a distinct non-determinism cause"
    )


def test_resolve_failures_inverse_method_warning(resolve_failures_text: str) -> None:
    has_inverse_warning = (
        "inverse" in resolve_failures_text.lower() or "append" in resolve_failures_text.lower()
    ) and "does not reset" in resolve_failures_text.lower()
    assert has_inverse_warning, (
        "resolve-failures must warn that calling an inverse method (e.g., disable() "
        "to undo enable()) does not reset accumulation-based state if the framework "
        "appends rather than toggles"
    )


def test_resolve_failures_full_reset_prescription(resolve_failures_text: str) -> None:
    assert "clear the collection" in resolve_failures_text.lower(), (
        "resolve-failures must prescribe clearing the collection (full reset) "
        "as the fix for accumulation-based state leakage, not inverse operations"
    )


def test_tests_claude_md_fastmcp_singleton_rule(tests_claude_md_text: str) -> None:
    assert "_transforms" in tests_claude_md_text, (
        "tests/CLAUDE.md must document the mcp._transforms accumulation behavior "
        "under the xdist compatibility section"
    )


def test_tests_claude_md_clear_restore_pattern(tests_claude_md_text: str) -> None:
    has_clear_restore = "_transforms.clear()" in tests_claude_md_text and (
        "disable" in tests_claude_md_text or "baseline" in tests_claude_md_text
    )
    assert has_clear_restore, (
        "tests/CLAUDE.md must prescribe the clear+restore pattern: "
        "call mcp._transforms.clear() then re-apply baseline state, "
        "not mcp.disable() as a teardown"
    )
