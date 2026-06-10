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


class TestBackendCompatGateFailClosed:
    """Fail-closed unit tests for _check_backend_compat gate.

    These test _check_backend_compat directly to verify fail-closed behavior
    when skill_resolver or effective_backend_obj is None.
    """

    def test_compat_check_rejects_when_skill_resolver_is_none(self):
        """Gate rejects when skill_resolver is None and a skill is referenced."""
        import json

        from autoskillit.server.tools.tools_execution import _check_backend_compat

        result = _check_backend_compat(
            skill_command="/autoskillit:investigate",
            resolved_command="/autoskillit:investigate",
            effective_order_id="",
            target_name="investigate",
            skill_info=None,
            effective_backend_obj=None,
            skill_resolver=None,
        )
        assert result is not None, "Expected crash JSON when skill_resolver is None"
        parsed = json.loads(result)
        assert parsed.get("subtype") == "crashed", (
            f"Expected crashed when skill_resolver is None and a skill is referenced, "
            f"got subtype={parsed.get('subtype')}"
        )
        error = parsed.get("error", "").lower()
        assert "resolver" in error or "skill resolver" in error, (
            f"Expected error mentioning missing resolver, got: {parsed.get('error')}"
        )

    def test_compat_check_rejects_when_backend_is_none(self):
        """Gate rejects when effective_backend_obj is None and a skill is referenced."""
        import json
        from unittest.mock import MagicMock

        from autoskillit.server.tools.tools_execution import _check_backend_compat

        result = _check_backend_compat(
            skill_command="/autoskillit:investigate",
            resolved_command="/autoskillit:investigate",
            effective_order_id="",
            target_name="investigate",
            skill_info=MagicMock(),
            effective_backend_obj=None,
            skill_resolver=MagicMock(),
        )
        assert result is not None, "Expected crash JSON when backend is None"
        parsed = json.loads(result)
        assert parsed.get("subtype") == "crashed", (
            f"Expected crashed when backend is None and a skill is referenced, "
            f"got subtype={parsed.get('subtype')}"
        )
        assert "backend" in parsed.get("error", "").lower(), (
            f"Expected error mentioning missing backend, got: {parsed.get('error')}"
        )

    @pytest.mark.anyio
    async def test_provider_override_allows_skill_requiring_claude_code(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        """Provider override fires before compat check: a codex backend with
        ANTHROPIC_BASE_URL provider_extras is rerouted to claude-code, so a
        claude-code-only skill passes the compat gate.
        """
        import json
        from unittest.mock import MagicMock

        from autoskillit.core import ValidatedAddDir
        from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
        from autoskillit.server.tools.tools_execution import run_skill
        from tests.fakes import InMemoryHeadlessExecutor

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor

        fake_backend = MagicMock(spec=CodingAgentBackend)
        fake_backend.name = "codex"
        fake_backend.capabilities.anthropic_provider_capable = False
        tool_ctx_kitchen_open.backend = fake_backend

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        skill_md = session_dir / ".claude" / "skills" / "investigate" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.write_text("name: investigate\n")

        fake_validated = ValidatedAddDir(path=str(session_dir))
        mock_ssm = MagicMock()
        mock_ssm.init_session.return_value = fake_validated
        tool_ctx_kitchen_open.session_skill_manager = mock_ssm

        mock_skill_info = MagicMock()
        mock_skill_info.backend_requirements = frozenset({"claude-code"})
        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = mock_skill_info
        tool_ctx_kitchen_open.skill_resolver = mock_resolver

        monkeypatch.setattr(
            "autoskillit.server.tools.tools_execution.is_feature_enabled",
            lambda *a, **kw: True,
        )
        monkeypatch.setattr(
            "autoskillit.server._guards._resolve_provider_profile",
            lambda *a, **kw: (
                "minimax",
                {
                    "ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1/anthropic",
                    "ANTHROPIC_API_KEY": "minimax-key-placeholder",
                },
            ),
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_execution.resolve_target_skill",
            lambda cmd, resolver: ("/autoskillit:investigate", "investigate"),
        )

        result = json.loads(await run_skill("/autoskillit:investigate", str(tmp_path)))
        assert (
            result.get("subtype") != "crashed"
            or "incompatible" not in result.get("error", "").lower()
        ), f"Provider override should have rerouted codex→claude-code; got: {result}"
