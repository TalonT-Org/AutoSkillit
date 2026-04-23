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
    assert '"recipe"' in text, "Schema must document 'recipe' per issue"
    assert '"affected_files"' in text, "Schema must document 'affected_files' per issue"
    assert '"depends_on"' in text, "Schema must document 'depends_on' per issue"
    # Group-level field
    assert '"group"' in text, "Schema must document 'group' integer per group"


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
    assert "build-execution-map" in always_block or "AUTOSKILLIT_TEMP" in text, (
        "ALWAYS block or workflow must reference the .autoskillit/temp/build-execution-map/ output directory"
    )


def test_execution_map_references_overlap_algorithm() -> None:
    """SKILL.md must reference pairwise file intersection for overlap detection (REQ-MAP-003)."""
    text = _skill_md_text()
    lower = text.lower()
    assert "pairwise" in lower or "intersection" in lower, (
        "SKILL.md must reference pairwise file intersection for overlap detection (REQ-MAP-003)"
    )


def test_execution_map_references_topological_ordering() -> None:
    """SKILL.md must reference topological sort for group ordering (REQ-MAP-004)."""
    text = _skill_md_text()
    lower = text.lower()
    assert "topological" in lower, (
        "SKILL.md must reference topological sort for group ordering (REQ-MAP-004)"
    )
