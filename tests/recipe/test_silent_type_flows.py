"""Integration tests for silent-type convention flows.

Shared between Work Item 2.3 (review-design silent-type) and Work Item 4.7
(vis-lens out-of-scope traditions). Tests verify that registry, convention
doc, and SKILL.md files are aligned on advisory schema and behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.methodology_tradition_registry import (
    get_methodology_tradition_by_name,
    is_out_of_scope_tradition,
    load_all_methodology_traditions,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _REPO_ROOT / "src" / "autoskillit" / "skills_extended"
_CONVENTION_PATH = _REPO_ROOT / "docs" / "research" / "silent-type-convention.md"


class TestVisLensOutOfScopeFlow:
    """Work Item 4.7: vis-lens-methodology-norms out-of-scope tradition path."""

    def test_qualitative_tradition_is_out_of_scope(self) -> None:
        spec = get_methodology_tradition_by_name("qualitative_interpretive_tradition")
        assert spec is not None
        assert is_out_of_scope_tradition(spec) is True

    def test_qualitative_tradition_has_strongly_expected_figures(self) -> None:
        spec = get_methodology_tradition_by_name("qualitative_interpretive_tradition")
        assert spec is not None
        assert len(spec.strongly_expected_figures) >= 1
        for entry in spec.strongly_expected_figures:
            assert "figure" in entry
            assert "source" in entry

    def test_qualitative_tradition_has_reference_framework(self) -> None:
        spec = get_methodology_tradition_by_name("qualitative_interpretive_tradition")
        assert spec is not None
        assert spec.canonical_guideline["name"] == "COREQ/SRQR"

    def test_vis_lens_skill_documents_out_of_scope_advisory(self) -> None:
        skill_md = (_SKILLS_DIR / "vis-lens-methodology-norms" / "SKILL.md").read_text()
        for term in [
            "advisory_context",
            "subject_kind",
            "methodology_tradition",
            "strongly_expected_figures",
            "is_out_of_scope_tradition",
        ]:
            assert term in skill_md, f"SKILL.md missing '{term}'"

    def test_convention_doc_and_skill_agree_on_write_target(self) -> None:
        convention = _CONVENTION_PATH.read_text()
        skill_md = (_SKILLS_DIR / "vis-lens-methodology-norms" / "SKILL.md").read_text()
        target = "visualization-plan-trace.md"
        assert target in convention, f"Convention doc missing '{target}'"
        assert target in skill_md, f"SKILL.md missing '{target}'"

    def test_all_out_of_scope_traditions_have_strongly_expected_figures(self) -> None:
        traditions = load_all_methodology_traditions()
        oos = [s for s in traditions if is_out_of_scope_tradition(s)]
        assert len(oos) >= 1, "Expected at least one out-of-scope tradition"
        for spec in oos:
            assert len(spec.strongly_expected_figures) >= 1, (
                f"{spec.name}: out-of-scope tradition must have strongly_expected_figures"
            )

    def test_no_in_scope_tradition_has_empty_mandatory_figures(self) -> None:
        traditions = load_all_methodology_traditions()
        for spec in traditions:
            if not is_out_of_scope_tradition(spec):
                assert len(spec.mandatory_figures) >= 1, (
                    f"{spec.name}: in-scope tradition must have mandatory_figures"
                )
