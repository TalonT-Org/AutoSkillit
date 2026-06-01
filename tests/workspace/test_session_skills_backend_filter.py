"""Tests for backend-aware skill injection filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import SkillSource
from autoskillit.workspace.session_skills import _should_inject_skill
from autoskillit.workspace.skills import SkillInfo

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _make_skill(
    name: str = "test-skill",
    source: SkillSource = SkillSource.BUNDLED_EXTENDED,
    backend_requirements: frozenset[str] = frozenset(),
) -> SkillInfo:
    return SkillInfo(
        name=name,
        source=source,
        path=Path("/fake/path"),
        backend_requirements=backend_requirements,
    )


class TestShouldInjectSkillBackendFilter:
    def test_incompatible_backend_returns_false(self):
        skill = _make_skill(backend_requirements=frozenset({"claude-code"}))
        result = _should_inject_skill(
            skill,
            overrides=frozenset(),
            effective_disabled=frozenset(),
            effective_custom_tags={},
            features={},
            backend_name="codex",
        )
        assert result is False

    def test_compatible_backend_returns_true(self):
        skill = _make_skill(backend_requirements=frozenset({"claude-code"}))
        result = _should_inject_skill(
            skill,
            overrides=frozenset(),
            effective_disabled=frozenset(),
            effective_custom_tags={},
            features={},
            backend_name="claude-code",
        )
        assert result is True

    def test_empty_requirements_compatible_with_any_backend(self):
        skill = _make_skill(backend_requirements=frozenset())
        result = _should_inject_skill(
            skill,
            overrides=frozenset(),
            effective_disabled=frozenset(),
            effective_custom_tags={},
            features={},
            backend_name="codex",
        )
        assert result is True

    def test_none_backend_skips_check(self):
        skill = _make_skill(backend_requirements=frozenset({"claude-code"}))
        result = _should_inject_skill(
            skill,
            overrides=frozenset(),
            effective_disabled=frozenset(),
            effective_custom_tags={},
            features={},
            backend_name=None,
        )
        assert result is True
