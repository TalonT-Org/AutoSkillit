"""T8: cook exploration mode sets explorer_provisioning_eligible correctly."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.workspace import SkillProjectionContext

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _mock_backend(*, session_scoped: bool = False, terminal: bool = False) -> MagicMock:
    backend = MagicMock()
    backend.capabilities.session_scoped_explorer_capable = session_scoped
    backend.capabilities.terminal_explorer_capable = terminal
    backend.conventions = MagicMock()
    return backend


def _mock_binding() -> MagicMock:
    binding = MagicMock()
    binding.identity.managed_path = Path("/fake/managed")
    return binding


def _mock_skills_provider(context_result: MagicMock) -> MagicMock:
    provider = MagicMock()
    provider.catalog_projection_context.return_value = context_result
    return provider


class TestCookExplorationEligibility:
    """T8: _build_cook_projection_context sets eligible based on backend."""

    def test_claude_backend_sets_eligible_true(self) -> None:
        from autoskillit.cli.session._session_cook import _build_cook_projection_context

        catalog = MagicMock()
        base_context = SkillProjectionContext(cwd=Path("/fake/project"), catalog=catalog)
        provider = _mock_skills_provider(base_context)

        result = _build_cook_projection_context(
            provider,
            catalog,
            Path("/fake/project"),
            _mock_backend(session_scoped=True),
            _mock_binding(),
            None,
            explorer_provisioning_eligible=True,
        )

        assert result.explorer_provisioning_eligible is True

    def test_codex_backend_leaves_eligible_none(self) -> None:
        from autoskillit.cli.session._session_cook import _build_cook_projection_context

        base_context = MagicMock()
        provider = _mock_skills_provider(base_context)
        backend = _mock_backend(terminal=True)
        binding = _mock_binding()
        catalog = MagicMock()

        result = _build_cook_projection_context(
            provider,
            catalog,
            Path("/fake/project"),
            backend,
            binding,
            None,
        )
        assert result is base_context

    def test_no_binding_raises(self) -> None:
        from autoskillit.cli.session._session_cook import _build_cook_projection_context

        provider = MagicMock()
        backend = _mock_backend()
        catalog = MagicMock()

        with pytest.raises(RuntimeError, match="retained plugin artifact binding"):
            _build_cook_projection_context(
                provider,
                catalog,
                Path("/fake/project"),
                backend,
                None,
                None,
            )

    def test_codex_terminal_sets_read_only_sandbox(self) -> None:
        """REQ-56: Codex cook with terminal explorer sets read-only parent."""
        from autoskillit.cli.session._session_cook import _build_cook_projection_context

        catalog = MagicMock()
        base_context = SkillProjectionContext(cwd=Path("/fake/project"), catalog=catalog)
        provider = _mock_skills_provider(base_context)

        result = _build_cook_projection_context(
            provider,
            catalog,
            Path("/fake/project"),
            _mock_backend(terminal=True),
            _mock_binding(),
            None,
            explorer_provisioning_eligible=True,
        )

        assert result.parent_sandbox_mode == "read-only"

    def test_claude_session_scoped_preserves_sandbox(self) -> None:
        """Claude cook does NOT force read-only — session-scoped model uses enable_exploration."""
        from autoskillit.cli.session._session_cook import _build_cook_projection_context

        catalog = MagicMock()
        base_context = SkillProjectionContext(cwd=Path("/fake/project"), catalog=catalog)
        provider = _mock_skills_provider(base_context)

        result = _build_cook_projection_context(
            provider,
            catalog,
            Path("/fake/project"),
            _mock_backend(session_scoped=True),
            _mock_binding(),
            None,
            explorer_provisioning_eligible=True,
        )

        assert result.parent_sandbox_mode == "workspace-write"

    def test_ordinary_cook_stays_permissive(self) -> None:
        """REQ-47/61: ordinary cook (no exploration) is unchanged."""
        from autoskillit.cli.session._session_cook import _build_cook_projection_context

        base_context = MagicMock()
        provider = _mock_skills_provider(base_context)
        backend = _mock_backend()
        binding = _mock_binding()
        catalog = MagicMock()

        result = _build_cook_projection_context(
            provider,
            catalog,
            Path("/fake/project"),
            backend,
            binding,
            None,
        )
        assert result is base_context
