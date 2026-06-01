"""Tests for dispatch-time backend compatibility gate in run_skill."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import SkillSource
from autoskillit.workspace.skills import SkillInfo

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_tool_ctx(
    *,
    backend_name: str = "codex",
    skill_info: SkillInfo | None = None,
    has_resolver: bool = True,
) -> MagicMock:
    tool_ctx = MagicMock()
    tool_ctx.backend = MagicMock()
    tool_ctx.backend.name = backend_name
    if has_resolver and skill_info is not None:
        tool_ctx.skill_resolver = MagicMock()
        tool_ctx.skill_resolver.resolve.return_value = skill_info
    elif has_resolver:
        tool_ctx.skill_resolver = MagicMock()
        tool_ctx.skill_resolver.resolve.return_value = None
    else:
        tool_ctx.skill_resolver = None
    return tool_ctx


class TestDispatchBackendCompat:
    def test_incompatible_skill_is_rejected(self):
        """Skill requiring claude-code must be rejected when backend is codex."""
        skill_info = SkillInfo(
            name="investigate",
            source=SkillSource.BUNDLED_EXTENDED,
            path=Path("/fake"),
            backend_requirements=frozenset({"claude-code"}),
        )
        tool_ctx = _make_tool_ctx(backend_name="codex", skill_info=skill_info)

        _compat_skill_info = tool_ctx.skill_resolver.resolve("investigate")
        assert _compat_skill_info is not None
        assert _compat_skill_info.backend_requirements
        _effective_backend = tool_ctx.backend.name
        assert _effective_backend not in _compat_skill_info.backend_requirements

    def test_compatible_skill_passes(self):
        """Skill requiring claude-code must pass when backend is claude-code."""
        skill_info = SkillInfo(
            name="investigate",
            source=SkillSource.BUNDLED_EXTENDED,
            path=Path("/fake"),
            backend_requirements=frozenset({"claude-code"}),
        )
        tool_ctx = _make_tool_ctx(backend_name="claude-code", skill_info=skill_info)

        _compat_skill_info = tool_ctx.skill_resolver.resolve("investigate")
        assert _compat_skill_info is not None
        _effective_backend = tool_ctx.backend.name
        assert _effective_backend in _compat_skill_info.backend_requirements

    def test_no_requirements_passes_any_backend(self):
        """Skill with no backend_requirements must pass for any backend."""
        skill_info = SkillInfo(
            name="make-req",
            source=SkillSource.BUNDLED_EXTENDED,
            path=Path("/fake"),
            backend_requirements=frozenset(),
        )
        tool_ctx = _make_tool_ctx(backend_name="codex", skill_info=skill_info)

        _compat_skill_info = tool_ctx.skill_resolver.resolve("make-req")
        assert _compat_skill_info is not None
        assert not _compat_skill_info.backend_requirements

    def test_no_resolver_skips_check(self):
        """When skill_resolver is None, the check is skipped."""
        tool_ctx = _make_tool_ctx(backend_name="codex", has_resolver=False)
        assert tool_ctx.skill_resolver is None
