"""Contract tests for the build-execution-map skill SKILL.md."""

from __future__ import annotations

import re
from pathlib import Path


def _skill_md_text() -> str:
    skill_md = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "autoskillit"
        / "skills_extended"
        / "build-execution-map"
        / "SKILL.md"
    )
    return skill_md.read_text()


def test_execution_map_schema_has_required_fields() -> None:
    """SKILL.md output schema section must document required JSON fields."""
    text = _skill_md_text()
    # Top-level fields
    assert '"groups"' in text, "Schema must document 'groups' array"
    assert '"parallel"' in text, "Schema must document 'parallel' bool per group"
    assert '"issues"' in text, "Schema must document 'issues' array per group"
    assert '"merge_order"' in text, "Schema must document top-level 'merge_order'"
    # Per-issue fields
    assert '"number"' in text, "Schema must document 'number' per issue"
    assert '"title"' in text, "Schema must document 'title' per issue"
    # Group-level field
    assert '"group"' in text, "Schema must document 'group' integer per group"
    # AI assessment fields
    assert '"pairwise_assessments"' in text, "Schema must document 'pairwise_assessments' array"
    assert '"parallel_safe"' in text, "Schema must document 'parallel_safe' per assessment"
    assert '"confidence"' in text, "Schema must document 'confidence' per assessment"
    assert '"reasoning"' in text, "Schema must document 'reasoning' per assessment"


def test_execution_map_output_tokens_declared() -> None:
    """SKILL.md must declare structured output tokens execution_map and execution_map_report."""
    text = _skill_md_text()
    # Tokens must appear as 'token_name = value' pattern (spaces around =)
    assert re.search(r"execution_map\s*=\s*\S", text), (
        "SKILL.md must declare 'execution_map = ...' output token"
    )
    assert re.search(r"execution_map_report\s*=\s*\S", text), (
        "SKILL.md must declare 'execution_map_report = ...' output token"
    )


def test_execution_map_never_block_prohibits_code_changes() -> None:
    """SKILL.md NEVER block must prohibit modifying source code."""
    text = _skill_md_text()
    never_match = re.search(r"\*\*NEVER:\*\*(.*?)(?=\n\*\*|\n##)", text, re.DOTALL)
    assert never_match is not None, "SKILL.md must have a **NEVER:** block"
    never_block = never_match.group(1).lower()
    assert "modify" in never_block and "source code" in never_block, (
        "NEVER block must prohibit modifying source code files"
    )


def test_execution_map_always_block_requires_file_output() -> None:
    """SKILL.md ALWAYS block must require writing the execution map to temp directory."""
    text = _skill_md_text()
    always_match = re.search(r"\*\*ALWAYS:\*\*(.*?)(?=\n\*\*|\n##)", text, re.DOTALL)
    assert always_match is not None, "SKILL.md must have an **ALWAYS:** block"
    always_block = always_match.group(1)
    assert "AUTOSKILLIT_TEMP" in always_block, (
        "ALWAYS block must reference AUTOSKILLIT_TEMP output directory"
    )


def test_execution_map_references_ai_assessment() -> None:
    """SKILL.md must reference AI-driven pairwise assessment for parallelism decisions."""
    text = _skill_md_text()
    lower = text.lower()
    assert "pairwise" in lower, "SKILL.md must reference pairwise assessment"
    assert "assessment" in lower or "parallel_safe" in lower, (
        "SKILL.md must reference AI-driven assessment or parallel_safe field"
    )


def test_execution_map_references_dependency_ordering() -> None:
    """SKILL.md must reference dependency-based group ordering."""
    text = _skill_md_text()
    lower = text.lower()
    assert "dependency" in lower, "SKILL.md must reference dependency-based group ordering"


def test_execution_map_review_approach_flag_declared() -> None:
    """SKILL.md Arguments section must declare --assess-review-approach flag."""
    text = _skill_md_text()
    args_match = re.search(r"## Arguments(.*?)(?=\n##)", text, re.DOTALL)
    assert args_match is not None, "SKILL.md must have an ## Arguments section"
    args_section = args_match.group(1)
    assert "--assess-review-approach" in args_section, (
        "Arguments section must declare --assess-review-approach flag"
    )


def test_execution_map_review_approach_schema_fields() -> None:
    """SKILL.md output schema must document review-approach fields when flag is active."""
    text = _skill_md_text()
    assert '"review_approach_recommended"' in text, (
        "Schema must document 'review_approach_recommended' boolean field"
    )
    assert '"review_approach_reasoning"' in text, (
        "Schema must document 'review_approach_reasoning' string field"
    )


def test_execution_map_review_approach_candidates_token() -> None:
    """SKILL.md must declare review_approach_candidates output token."""
    text = _skill_md_text()
    assert re.search(r"review_approach_candidates\s*=\s*\S", text), (
        "SKILL.md must declare 'review_approach_candidates = ...' output token"
    )


def test_execution_map_reads_review_approach_skill() -> None:
    """SKILL.md must instruct reading review-approach/SKILL.md to ground assessment."""
    text = _skill_md_text()
    assert "review-approach" in text.lower() and "SKILL.md" in text, (
        "SKILL.md must reference reading review-approach/SKILL.md for assessment grounding"
    )


def test_review_approach_candidates_contract_registered() -> None:
    """skill_contracts.yaml must register review_approach_candidates output."""
    from autoskillit.recipe.contracts import get_skill_contract, load_bundled_manifest

    manifest = load_bundled_manifest()
    contract = get_skill_contract("build-execution-map", manifest)
    assert contract is not None
    output_names = [o.name for o in contract.outputs]
    assert "review_approach_candidates" in output_names, (
        "build-execution-map contract must register review_approach_candidates output"
    )


def test_skill_declares_max_parallel_argument() -> None:
    """REQ-MAP-001: SKILL.md must document --max-parallel as an accepted input."""
    skill_md = _skill_md_text()
    assert "--max-parallel" in skill_md


def test_skill_documents_max_parallel_default() -> None:
    """REQ-MAP-002: Default of 6 must appear in the SKILL.md arguments section."""
    skill_md = _skill_md_text()
    assert "default" in skill_md.lower() and "6" in skill_md


def test_output_schema_includes_max_parallel_field() -> None:
    """REQ-OUT-001: JSON schema section must include max_parallel field."""
    skill_md = _skill_md_text()
    schema_section_start = skill_md.find("## Output JSON Schema")
    assert schema_section_start != -1
    schema_section = skill_md[schema_section_start:]
    assert '"max_parallel"' in schema_section


def test_skill_documents_group_splitting_logic() -> None:
    """REQ-MAP-003/004/005/006: Group splitting instructions must appear in SKILL.md."""
    skill_md = _skill_md_text()
    assert "split" in skill_md.lower()
    assert "sub-group" in skill_md.lower() or "subgroup" in skill_md.lower()
