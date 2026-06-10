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
    """Fail-closed integration tests for _check_backend_compat gate.

    These call run_skill() through the full dispatch path; the compat gate is
    reached only after _require_enabled() passes (kitchen open).
    """

    @pytest.mark.anyio
    async def test_compat_check_rejects_when_skill_resolver_is_none(self, tool_ctx_kitchen_open):
        """Gate rejects when skill_resolver is None and a skill is referenced."""
        import json

        from autoskillit.server.tools.tools_execution import run_skill

        result = json.loads(await run_skill("/autoskillit:investigate", "/tmp"))
        assert result["subtype"] == "crashed", (
            f"Expected crashed when skill_resolver is None and a skill is referenced, "
            f"got subtype={result.get('subtype')}"
        )
        assert (
            "skill resolver" in result.get("error", "").lower()
            or "resolver" in result.get("error", "").lower()
        ), f"Expected error mentioning missing resolver, got: {result.get('error')}"

    @pytest.mark.anyio
    async def test_compat_check_rejects_when_backend_is_none(self, tool_ctx_kitchen_open):
        """Gate rejects when tool_ctx.backend is None and a skill is referenced."""
        import json

        from autoskillit.server.tools.tools_execution import run_skill

        tool_ctx_kitchen_open.backend = None
        result = json.loads(await run_skill("/autoskillit:investigate", "/tmp"))
        assert result["subtype"] == "crashed", (
            f"Expected crashed when backend is None and a skill is referenced, "
            f"got subtype={result.get('subtype')}"
        )
        assert "backend" in result.get("error", "").lower(), (
            f"Expected error mentioning missing backend, got: {result.get('error')}"
        )

    @pytest.mark.anyio
    async def test_provider_override_allows_skill_requiring_claude_code(
        self, tool_ctx_kitchen_open
    ):
        """Provider override fires before compat check: a codex backend with
        ANTHROPIC_BASE_URL provider_extras is rerouted to claude-code, so a
        claude-code-only skill passes the compat gate.
        """
        import json
        from unittest.mock import AsyncMock

        # Backend that does NOT support anthropic_provider_capable (e.g. codex)
        from autoskillit.core.types import AGENT_BACKEND_CODEX
        from autoskillit.server.tools.tools_execution import run_skill

        # Switch to codex backend
        tool_ctx_kitchen_open.backend = tool_ctx_kitchen_open.backend.__class__.from_name(
            AGENT_BACKEND_CODEX
        )
        # Mock the executor so the test does not actually spawn a headless session
        tool_ctx_kitchen_open.executor = AsyncMock()
        tool_ctx_kitchen_open.executor.run = AsyncMock(
            return_value=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "done",
                    "session_id": "s1",
                }
            )
        )

        provider_extras = {"ANTHROPIC_BASE_URL": "https://example.invalid"}
        result = json.loads(
            await run_skill("/autoskillit:investigate", "/tmp", provider_extras=provider_extras)
        )
        # Should NOT be crashed — provider override reroutes to claude-code
        assert (
            result.get("subtype") != "crashed"
            or "incompatible" not in result.get("error", "").lower()
        ), f"Provider override should have rerouted codex→claude-code; got: {result}"
