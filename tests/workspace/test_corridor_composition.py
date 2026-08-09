"""T6: corridor composition — explorer advertisement tested through materialize_invocation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import (
    BackendConventions,
    SkillExecutionRole,
    SkillSource,
)
from tests.fakes import adapt_test_skill_semantics

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _build_manager(tmp_path: Path) -> object:
    from autoskillit.workspace import DefaultSessionSkillManager, SkillsDirectoryProvider

    return DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=tmp_path / "ephemeral",
        persistent_roots={},
    )


def _build_mock_backend(*, terminal: bool = False, session_scoped: bool = False) -> MagicMock:
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities.mcp_config_capable = False
    backend.capabilities.terminal_explorer_capable = terminal
    backend.capabilities.session_scoped_explorer_capable = session_scoped
    backend.capabilities.session_dir_persistent = False
    backend.conventions = BackendConventions()
    backend.adapt_skill_semantics.side_effect = adapt_test_skill_semantics
    backend.exploration_dispatch_renderer = MagicMock()
    backend.setup_session_dir = MagicMock()
    backend.ensure_pre_launch = MagicMock(return_value=[])
    return backend


class TestCorridorCompositionExplorerAdvertisement:
    """T6: explorer binding forwarding through materialize_invocation."""

    def test_factory_result_forwarded_to_setup_session_dir(self, tmp_path: Path) -> None:
        """T6a: run_skill corridor — factory result reaches backend.setup_session_dir."""
        from autoskillit.workspace import SkillProjectionContext
        from autoskillit.workspace.skills import (
            DefaultSkillResolver,
        )

        manager = _build_manager(tmp_path)
        backend = _build_mock_backend(terminal=True)

        resolver = DefaultSkillResolver()
        source_infos = tuple(
            s
            for s in resolver.list_all()
            if s.source is SkillSource.BUNDLED and not s.exploration_vectors
        )[:1]
        if not source_infos:
            pytest.skip("need at least one bundled skill without exploration vectors")

        invocation = resolver.resolve_invocation(
            source_infos[0].name,
            tmp_path,
            SkillExecutionRole.SESSION,
        )
        context = SkillProjectionContext(
            cwd=tmp_path,
            invocation=invocation,
            backend=backend,
            parent_sandbox_mode="read-only",
        )

        fake_binding = {"semantic-code-navigator": {"KEY": "VALUE"}}

        manager.materialize_invocation(
            "test-session",
            invocation,
            context,
            explorer_binding_env_factory=lambda _home: fake_binding,
        )

        backend.setup_session_dir.assert_called_once()
        setup_kwargs = backend.setup_session_dir.call_args
        assert setup_kwargs.kwargs["explorer_binding_env"] == fake_binding

    def test_no_factory_no_explorer_binding(self, tmp_path: Path) -> None:
        """T6b/c: direct-dispatch and permissive cook — no factory means no binding."""
        from autoskillit.workspace import SkillProjectionContext
        from autoskillit.workspace.skills import (
            DefaultSkillResolver,
        )

        manager = _build_manager(tmp_path)
        backend = _build_mock_backend()

        resolver = DefaultSkillResolver()
        source_infos = tuple(
            s
            for s in resolver.list_all()
            if s.source is SkillSource.BUNDLED and not s.exploration_vectors
        )[:1]
        if not source_infos:
            pytest.skip("need at least one bundled skill without exploration vectors")

        invocation = resolver.resolve_invocation(
            source_infos[0].name,
            tmp_path,
            SkillExecutionRole.SESSION,
        )
        context = SkillProjectionContext(
            cwd=tmp_path,
            invocation=invocation,
            backend=backend,
        )

        manager.materialize_invocation(
            "test-session",
            invocation,
            context,
        )

        backend.setup_session_dir.assert_called_once()
        setup_kwargs = backend.setup_session_dir.call_args
        assert "explorer_binding_env" not in (setup_kwargs.kwargs or {}), (
            "without factory, no explorer_binding_env should reach setup_session_dir"
        )
