from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

SKILL_MD = Path(__file__).parents[2] / "src/autoskillit/skills_extended/audit-arch/SKILL.md"
LOCAL_SKILL_MD = Path(__file__).parents[2] / ".claude/skills/audit-arch/SKILL.md"


def test_preflight_checklist_section_exists():
    """T-AA-001: Pre-Flight Verification Checklist section exists in SKILL.md."""
    text = SKILL_MD.read_text()
    assert "Pre-Flight Verification Checklist" in text, (
        "audit-arch SKILL.md must contain a 'Pre-Flight Verification Checklist' "
        "section in the Audit Workflow (IMP-001 through IMP-005 requirement)"
    )


def test_preflight_checklist_precedes_launch_subagents():
    """T-AA-002: Checklist step appears before the worker assignment."""
    text = SKILL_MD.read_text()
    checklist_idx = text.index("Pre-Flight Verification Checklist")
    launch_idx = text.index("2. **Assign every principle to the declared single worker.**")
    assert checklist_idx < launch_idx, (
        "Pre-Flight Verification Checklist must appear BEFORE the worker assignment "
        "in the Audit Workflow"
    )


def test_imp001_init_py_and_all_check():
    """T-AA-003: IMP-001 gate requires reading __init__.py and checking __all__."""
    text = SKILL_MD.read_text()
    assert "__init__.py" in text, (
        "Pre-flight checklist must require reading '__init__.py' before reporting "
        "a missing export (IMP-001)"
    )
    assert "__all__" in text, (
        "Pre-flight checklist must require checking '__all__' before reporting "
        "a missing export (IMP-001)"
    )


def test_imp002_decorator_check():
    """T-AA-004: IMP-002 gate requires reading the class definition for decorators."""
    text = SKILL_MD.read_text()
    assert "decorator" in text, (
        "Pre-flight checklist must require checking for decorators before reporting "
        "a missing decorator (IMP-002)"
    )


def test_imp003_tests_grep_instruction():
    """T-AA-005: IMP-003 gate requires grepping tests/ for the symbol name."""
    text = SKILL_MD.read_text()
    assert "tests/" in text, (
        "Pre-flight checklist must require searching 'tests/' before reporting "
        "an enforcement gap (IMP-003)"
    )


def test_imp004_full_body_comparison():
    """T-AA-006: IMP-004 gate requires comparing full function bodies."""
    text = SKILL_MD.read_text()
    text_lower = text.lower()
    assert "full body" in text_lower, (
        "Pre-flight checklist must require comparing full function bodies before "
        "reporting code duplication (IMP-004)"
    )


def test_imp005_git_log_instruction():
    """T-AA-007: IMP-005 gate requires running git log before reporting a misplaced file."""
    text = SKILL_MD.read_text()
    assert "git log" in text, (
        "Pre-flight checklist must require running 'git log' before reporting "
        "a misplaced file or incorrect import path (IMP-005)"
    )


def test_concrete_read_tool_call():
    """T-AA-008: Checklist references the Read tool as a concrete required action."""
    text = SKILL_MD.read_text()
    assert "Read tool" in text, (
        "Pre-flight checklist must name the Read tool explicitly as the required "
        "action for IMP-001, IMP-002, and IMP-004"
    )


def test_concrete_grep_tool_call():
    """T-AA-009: Checklist references the Grep tool as a concrete required action."""
    text = SKILL_MD.read_text()
    assert "Grep tool" in text, (
        "Pre-flight checklist must name the Grep tool explicitly as the required "
        "action for IMP-003"
    )


def test_concrete_bash_tool_call():
    """T-AA-010: Checklist references the Bash tool as a concrete required action."""
    text = SKILL_MD.read_text()
    assert "Bash tool" in text, (
        "Pre-flight checklist must name the Bash tool explicitly for the git log "
        "invocation (IMP-005)"
    )


def test_local_skill_md_dispatches_in_bounded_batches() -> None:
    """Local variant bounds principle work to <=6 auditors per parallel batch
    and joins each batch before starting the next.
    """
    text = LOCAL_SKILL_MD.read_text()
    never_block = text.split("**NEVER:**", maxsplit=1)[1].split("**ALWAYS:**", maxsplit=1)[0]
    workflow = text.split("## Audit Workflow", maxsplit=1)[1]

    assert "Launch more than 6 principle auditors in one parallel batch" in never_block
    assert "Process every principle in sequential batches of at most 6." in workflow
    assert "Start all independent child delegations before awaiting any result" not in text
    assert "join each batch before starting the next" in text


def test_source_skill_md_dispatches_to_single_worker() -> None:
    """Source variant assigns every principle to a single declared worker
    (count: 1 frontmatter, no concurrency field) and joins before synthesis.
    """
    text = SKILL_MD.read_text()
    never_block = text.split("**NEVER:**", maxsplit=1)[1].split("**ALWAYS:**", maxsplit=1)[0]
    workflow = text.split("## Audit Workflow", maxsplit=1)[1]

    assert "Launch more than 6 principle auditors in one parallel batch" in never_block
    assert "Assign every principle to the declared single worker." in workflow
    assert "Start all independent child delegations before awaiting any result" not in text

    frontmatter = text.split("---", maxsplit=2)[1]
    assert "count: 1" in frontmatter
    assert "concurrency:" not in frontmatter
    assert "Join that worker before synthesis." in text
