"""Reverse-direction annotation validation: annotated skills must justify their annotation."""

from __future__ import annotations

import pytest

from autoskillit.core import AGENT_BACKEND_CLAUDE_CODE
from autoskillit.core.types._type_constants_registries import SKILL_CAPABILITY_REGISTRY
from autoskillit.workspace import (
    DefaultSkillResolver,
    validate_skill_capability_authenticity,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_annotated_skills_have_justified_annotation():
    over_annotated: list[str] = []
    for skill_info in DefaultSkillResolver().list_all():
        if AGENT_BACKEND_CLAUDE_CODE not in skill_info.backend_requirements:
            continue
        errors = validate_skill_capability_authenticity(skill_info)
        required_declared = {
            capability
            for capability in skill_info.uses_capabilities
            if AGENT_BACKEND_CLAUDE_CODE in SKILL_CAPABILITY_REGISTRY[capability].required_backends
        }
        if errors or not required_declared:
            over_annotated.append(
                f"{skill_info.name}: required_capabilities={sorted(required_declared)}, "
                f"errors={list(errors)}"
            )

    assert not over_annotated, (
        f"{len(over_annotated)} skill(s) derive a claude-code backend requirement "
        f"without genuine justification:\n" + "\n".join(f"  {s}" for s in over_annotated)
    )
