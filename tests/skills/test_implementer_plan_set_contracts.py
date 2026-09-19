"""Implementation skills consume assigned requirements without opening authority files."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.contracts import get_skill_contract, load_bundled_manifest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.small]

_SKILLS = ("implement-worktree", "implement-worktree-no-merge", "retry-worktree")
_ROOT = Path(__file__).resolve().parents[2] / "src/autoskillit/skills_extended"


@pytest.mark.parametrize("skill_name", _SKILLS)
def test_implementer_requires_verified_plan_set_checklist(skill_name: str) -> None:
    contract = get_skill_contract(skill_name, load_bundled_manifest())
    assert contract is not None
    authority_input = next(
        item for item in contract.inputs if item.name == "plan_set_authority_path"
    )
    assert authority_input.type == "file_path"
    assert not authority_input.required and authority_input.absence_value == ""
    assert contract.input_preflight == ("plan_set_coverage",)

    skill_text = (_ROOT / skill_name / "SKILL.md").read_text(encoding="utf-8")
    assert "Open plan-set authority artifacts directly or reinterpret" in skill_text
    assert "Plan-set completeness check" in skill_text
    assert "assigned_requirements" in skill_text
    assert "PART {X} ONLY" in skill_text
    if skill_name == "retry-worktree":
        assert "deviation-manifest" in skill_text
