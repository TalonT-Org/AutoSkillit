"""Tests for dispatch-time backend compatibility gate in run_skill."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import SkillSource
from autoskillit.server.tools.tools_execution import _is_backend_incompatible
from autoskillit.workspace.skills import SkillInfo

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestBackendCompatGate:
    def test_incompatible_skill_is_blocked(self):
        """Production gate blocks skill when backend not in requirements."""
        skill_info = SkillInfo(
            name="investigate",
            source=SkillSource.BUNDLED_EXTENDED,
            path=Path("/nonexistent-test-path"),
            backend_requirements=frozenset({"claude-code"}),
        )
        assert _is_backend_incompatible(skill_info, "codex") is True

    def test_compatible_skill_passes(self):
        """Production gate allows skill when backend is in requirements."""
        skill_info = SkillInfo(
            name="investigate",
            source=SkillSource.BUNDLED_EXTENDED,
            path=Path("/nonexistent-test-path"),
            backend_requirements=frozenset({"claude-code"}),
        )
        assert _is_backend_incompatible(skill_info, "claude-code") is False

    def test_no_requirements_passes_any_backend(self):
        """Empty backend_requirements means any backend is allowed."""
        skill_info = SkillInfo(
            name="make-req",
            source=SkillSource.BUNDLED_EXTENDED,
            path=Path("/nonexistent-test-path"),
            backend_requirements=frozenset(),
        )
        assert _is_backend_incompatible(skill_info, "codex") is False
        assert _is_backend_incompatible(skill_info, "claude-code") is False

    def test_multi_backend_requirements(self):
        """Skill with multiple backends passes only listed ones."""
        skill_info = SkillInfo(
            name="multi",
            source=SkillSource.BUNDLED_EXTENDED,
            path=Path("/nonexistent-test-path"),
            backend_requirements=frozenset({"claude-code", "codex"}),
        )
        assert _is_backend_incompatible(skill_info, "claude-code") is False
        assert _is_backend_incompatible(skill_info, "codex") is False
        assert _is_backend_incompatible(skill_info, "other") is True
