"""T8: cook exploration mode sets explorer_provisioning_eligible correctly."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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

        base_context = MagicMock()
        base_context.explorer_provisioning_eligible = None

        provider = _mock_skills_provider(base_context)
        backend = _mock_backend(session_scoped=True)
        binding = _mock_binding()
        catalog = MagicMock()

        with patch("autoskillit.cli.session._session_cook.replace") as mock_replace:
            mock_replace.return_value = MagicMock()
            _build_cook_projection_context(
                provider,
                catalog,
                Path("/fake/project"),
                backend,
                binding,
                None,
                explorer_provisioning_eligible=True,
            )
            mock_replace.assert_called_once()
            call_kwargs = mock_replace.call_args
            assert call_kwargs[1]["explorer_provisioning_eligible"] is True

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

    def test_open_kitchen_not_required(self) -> None:
        """REQ-63: cook exploration wiring does not depend on open_kitchen."""
        import inspect

        from autoskillit.cli.session._session_cook import _build_cook_projection_context

        source = inspect.getsource(_build_cook_projection_context)
        assert "open_kitchen" not in source
        assert "gate" not in source.lower() or "explorer" in source.lower()
