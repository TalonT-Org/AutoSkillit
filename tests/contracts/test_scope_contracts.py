"""Contract tests for the scope skill's SKILL.md template."""

from __future__ import annotations

import re

import pytest

from autoskillit.core import pkg_root

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


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
        cc_idx = content.index("## Computational Complexity")
        hyp_idx = content.index("## Hypotheses")
        section = content[cc_idx:hyp_idx]
        assert field in section, (
            f"Scope SKILL.md missing '{field}' in Computational Complexity section"
        )

    def test_section_between_domain_context_and_hypotheses(self) -> None:
        content = _read_scope_skill_md()
        dc_idx = content.index("## Domain Context")
        cc_idx = content.index("## Computational Complexity")
        hyp_idx = content.index("## Hypotheses")
        assert dc_idx < cc_idx < hyp_idx, (
            "## Computational Complexity must appear between ## Domain Context and ## Hypotheses"
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
            r"(?:Subagent|subagent).*?Complexity.*?dominant.*?operation",
            content,
            re.DOTALL | re.IGNORECASE,
        ), (
            "Step 1 subagent must instruct gathering of computational complexity "
            "fields including dominant operation (heading alone is not sufficient)"
        )


def test_scope_source_type_matches_canonical() -> None:
    """scope SKILL.md source_type enum must list all canonical direction source types."""
    from autoskillit.core import SCOPE_DIRECTION_SOURCE_TYPES

    text = _read_scope_skill_md()
    lower = text.lower()
    for st in SCOPE_DIRECTION_SOURCE_TYPES:
        assert st in lower, f"scope SKILL.md missing direction source_type '{st}'"
