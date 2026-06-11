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
    """Defensive unit tests for _check_backend_compat gate.

    These call _check_backend_compat directly with parameter combinations
    that do not arise at the sole production call site in run_skill
    (where target_name is only set when skill_resolver is not None).
    They verify the function's internal fail-closed logic as a defense
    against future callers or refactors that change the calling contract.
    """

    def test_compat_check_rejects_when_skill_resolver_is_none(self):
        """_check_backend_compat rejects when skill_resolver is None and target_name is set.

        Note: in run_skill, target_name is only populated when skill_resolver
        is not None, so this combination does not arise at the current call site.
        This test validates the function's own defensive guard.
        """
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
        error_text = (parsed.get("error") or parsed.get("result") or "").lower()
        assert "resolver" in error_text or "skill resolver" in error_text, (
            f"Expected error mentioning missing resolver, got: {parsed}"
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
        error_text = (parsed.get("error") or parsed.get("result") or "").lower()
        assert "backend" in error_text, f"Expected error mentioning missing backend, got: {parsed}"

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
        assert mock_ssm.init_session.called, (
            "Expected init_session to be called after provider override rerouted codex→claude-code"
        )


class TestHookFixRequiredDispatchGate:
    """Dispatch-time gate for fix-required hook entries in HOOK_REGISTRY."""

    def test_refuses_codex_dispatch_when_fix_required_hook_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json
        from unittest.mock import MagicMock

        from autoskillit.core import AGENT_BACKEND_CODEX, CodingAgentBackend
        from autoskillit.hook_registry import HookDef
        from autoskillit.server.tools import tools_execution
        from autoskillit.server.tools.tools_execution import _check_backend_compat

        registry = [
            HookDef(
                matcher=r"Read|Write",
                scripts=["guards/test_guard.py"],
                codex_status="fix-required",
            ),
        ]
        monkeypatch.setattr(tools_execution, "HOOK_REGISTRY", registry)

        backend = MagicMock(spec=CodingAgentBackend)
        backend.name = AGENT_BACKEND_CODEX
        backend.capabilities.applicable_guards = frozenset({"write_guard"})

        skill_info = MagicMock()
        skill_info.backend_requirements = frozenset({AGENT_BACKEND_CODEX})

        result = _check_backend_compat(
            skill_command="/autoskillit:test",
            resolved_command="/autoskillit:test",
            effective_order_id="ord-1",
            target_name="test",
            skill_info=skill_info,
            effective_backend_obj=backend,
            skill_resolver=MagicMock(),
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["subtype"] == "crashed"
        error_text = parsed["result"]
        assert "Read|Write" in error_text

    def test_allows_claude_code_dispatch_even_with_fix_required_hook(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock

        from autoskillit.core import AGENT_BACKEND_CLAUDE_CODE, CodingAgentBackend
        from autoskillit.hook_registry import HookDef
        from autoskillit.server.tools import tools_execution
        from autoskillit.server.tools.tools_execution import _check_backend_compat

        registry = [
            HookDef(
                matcher=r"Read|Write",
                scripts=["guards/test_guard.py"],
                codex_status="fix-required",
            ),
        ]
        monkeypatch.setattr(tools_execution, "HOOK_REGISTRY", registry)

        backend = MagicMock(spec=CodingAgentBackend)
        backend.name = AGENT_BACKEND_CLAUDE_CODE
        backend.capabilities.applicable_guards = frozenset({"test_guard"})

        skill_info = MagicMock()
        skill_info.backend_requirements = frozenset({AGENT_BACKEND_CLAUDE_CODE})

        result = _check_backend_compat(
            skill_command="/autoskillit:test",
            resolved_command="/autoskillit:test",
            effective_order_id="",
            target_name="test",
            skill_info=skill_info,
            effective_backend_obj=backend,
            skill_resolver=MagicMock(),
        )
        assert result is None

    def test_refuses_codex_dispatch_when_fix_required_hook_has_empty_scripts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json
        from unittest.mock import MagicMock

        from autoskillit.core import AGENT_BACKEND_CODEX, CodingAgentBackend
        from autoskillit.hook_registry import HookDef
        from autoskillit.server.tools import tools_execution
        from autoskillit.server.tools.tools_execution import _check_backend_compat

        # scripts=[] means the hook has no guard scripts to check coverage against;
        # the `not h.scripts` guard treats it as always-unenforced and blocks dispatch.
        registry = [
            HookDef(
                matcher=r"Read|Write",
                scripts=[],
                codex_status="fix-required",
            ),
        ]
        monkeypatch.setattr(tools_execution, "HOOK_REGISTRY", registry)

        backend = MagicMock(spec=CodingAgentBackend)
        backend.name = AGENT_BACKEND_CODEX
        backend.capabilities.applicable_guards = frozenset({"any_guard"})

        skill_info = MagicMock()
        skill_info.backend_requirements = frozenset({AGENT_BACKEND_CODEX})

        result = _check_backend_compat(
            skill_command="/autoskillit:test",
            resolved_command="/autoskillit:test",
            effective_order_id="ord-empty",
            target_name="test",
            skill_info=skill_info,
            effective_backend_obj=backend,
            skill_resolver=MagicMock(),
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["subtype"] == "crashed"
        assert "Read|Write" in parsed["result"]

    def test_allows_codex_dispatch_when_no_fix_required_hooks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock

        from autoskillit.core import AGENT_BACKEND_CODEX, CodingAgentBackend
        from autoskillit.hook_registry import HookDef
        from autoskillit.server.tools import tools_execution
        from autoskillit.server.tools.tools_execution import _check_backend_compat

        registry = [
            HookDef(matcher=r"Read|Write", codex_status="works-as-is"),
            HookDef(matcher=r"Bash", codex_status="not-applicable"),
        ]
        monkeypatch.setattr(tools_execution, "HOOK_REGISTRY", registry)

        backend = MagicMock(spec=CodingAgentBackend)
        backend.name = AGENT_BACKEND_CODEX
        backend.capabilities.applicable_guards = frozenset({"write_guard"})

        skill_info = MagicMock()
        skill_info.backend_requirements = frozenset({AGENT_BACKEND_CODEX})

        result = _check_backend_compat(
            skill_command="/autoskillit:test",
            resolved_command="/autoskillit:test",
            effective_order_id="",
            target_name="test",
            skill_info=skill_info,
            effective_backend_obj=backend,
            skill_resolver=MagicMock(),
        )
        assert result is None

    def test_crash_message_lists_affected_matchers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import json
        from unittest.mock import MagicMock

        from autoskillit.core import AGENT_BACKEND_CODEX, CodingAgentBackend
        from autoskillit.hook_registry import HookDef
        from autoskillit.server.tools import tools_execution
        from autoskillit.server.tools.tools_execution import _check_backend_compat

        registry = [
            HookDef(
                matcher=r"Read|Write",
                scripts=["guards/guard_a.py"],
                codex_status="fix-required",
            ),
            HookDef(
                matcher=r"Bash|Grep",
                scripts=["guards/guard_b.py"],
                codex_status="fix-required",
            ),
        ]
        monkeypatch.setattr(tools_execution, "HOOK_REGISTRY", registry)

        backend = MagicMock(spec=CodingAgentBackend)
        backend.name = AGENT_BACKEND_CODEX
        backend.capabilities.applicable_guards = frozenset({"write_guard"})

        skill_info = MagicMock()
        skill_info.backend_requirements = frozenset({AGENT_BACKEND_CODEX})

        result = _check_backend_compat(
            skill_command="/autoskillit:test",
            resolved_command="/autoskillit:test",
            effective_order_id="ord-2",
            target_name="test",
            skill_info=skill_info,
            effective_backend_obj=backend,
            skill_resolver=MagicMock(),
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["subtype"] == "crashed"
        error_text = parsed["result"]
        assert "Read|Write" in error_text
        assert "Bash|Grep" in error_text
