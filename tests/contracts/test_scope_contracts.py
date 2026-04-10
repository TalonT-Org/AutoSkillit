"""Contract tests for the scope skill's SKILL.md template."""

from __future__ import annotations

import re

import pytest

from autoskillit.core import pkg_root


def _read_scope_skill_md() -> str:
    return (pkg_root() / "skills_extended" / "scope" / "SKILL.md").read_text()


class TestComputationalComplexitySection:
    """Validate the Computational Complexity section in scope SKILL.md."""

    def test_section_exists(self) -> None:
        content = _read_scope_skill_md()
        assert "## Computational Complexity" in content

    @pytest.mark.parametrize(
        "field",
        [
            "Dominant operation",
            "Scaling behavior",
            "Known bottlenecks",
            "Gotchas",
        ],
    )
    def test_field_present(self, field: str) -> None:
        content = _read_scope_skill_md()
        assert field in content, (
            f"Scope SKILL.md missing '{field}' in Computational Complexity section"
        )

    def test_section_between_technical_context_and_hypotheses(self) -> None:
        content = _read_scope_skill_md()
        tc_idx = content.index("## Technical Context")
        cc_idx = content.index("## Computational Complexity")
        hyp_idx = content.index("## Hypotheses")
        assert tc_idx < cc_idx < hyp_idx, (
            "## Computational Complexity must appear between "
            "## Technical Context and ## Hypotheses"
        )

    def test_baseline_computation_instruction(self) -> None:
        content = _read_scope_skill_md()
        assert re.search(r"baseline.*computation", content, re.IGNORECASE), (
            "Known bottlenecks field must include instruction about "
            "baseline/reference computation costs"
        )

    def test_complexity_subagent_instruction(self) -> None:
        content = _read_scope_skill_md()
        assert re.search(
            r"(?:Subagent|subagent).*(?:Complexity|complexity|dominant.*operation)",
            content,
        ), (
            "Step 1 must include a subagent instruction for gathering "
            "computational complexity information"
        )
