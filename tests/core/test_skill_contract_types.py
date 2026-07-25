"""Typed skill machine-contract invariants."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    SessionType,
    SkillExecutionRole,
    SkillSource,
    session_type_for_skill_execution_role,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_skill_execution_roles_are_closed_and_map_explicitly() -> None:
    expected = {
        SkillExecutionRole.SESSION: SessionType.SKILL,
        SkillExecutionRole.ORCHESTRATOR: SessionType.ORCHESTRATOR,
        SkillExecutionRole.FLEET: SessionType.FLEET,
    }
    assert set(SkillExecutionRole) == set(expected)
    assert {
        role: session_type_for_skill_execution_role(role) for role in SkillExecutionRole
    } == expected


def test_skill_sources_cover_effective_origins_exactly() -> None:
    assert {source.value for source in SkillSource} == {
        "bundled",
        "bundled_extended",
        "project_local",
        "third_party",
    }
    assert SkillSource.PROJECT_LOCAL == "project_local"
    assert SkillSource.THIRD_PARTY == "third_party"
