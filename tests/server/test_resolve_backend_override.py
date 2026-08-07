from __future__ import annotations

import pytest

from tests.server._helpers import (
    BUNDLED_RECIPE_STEP_BACKEND_PIN_CASES,
    _bundled_backend,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_backend(**kwargs):
    from autoskillit.config.settings import AgentBackendConfig

    return AgentBackendConfig(**kwargs)


def _backend(result):
    """Extract .backend from a BackendPinResolution, or return None."""
    return result.backend if result is not None else None


class TestResolveBackendOverride:
    @pytest.mark.parametrize(
        ("recipe_name", "step_name", "key_path"),
        BUNDLED_RECIPE_STEP_BACKEND_PIN_CASES,
    )
    def test_bundled_recipe_step_pin_has_exact_authority(
        self, recipe_name: str, step_name: str, key_path: str
    ) -> None:
        from autoskillit.core import BackendAuthorityKind
        from autoskillit.server._guards import _resolve_backend_override

        result = _resolve_backend_override(step_name, recipe_name, _bundled_backend())

        assert result is not None
        assert result.backend == "codex"
        assert result.kind is BackendAuthorityKind.RECIPE
        assert result.tier == "recipe_step"
        assert result.key_path == key_path

    def test_exact_recipe_step_match(self) -> None:
        from autoskillit.core import BackendAuthorityKind
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(recipe_overrides={"remediation": {"dry_walkthrough": "codex"}})
        result = _resolve_backend_override("dry_walkthrough", "remediation", cfg)
        assert _backend(result) == "codex"
        assert result is not None
        assert result.kind is BackendAuthorityKind.RECIPE

    def test_recipe_wildcard(self) -> None:
        from autoskillit.core import BackendAuthorityKind
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(recipe_overrides={"remediation": {"*": "codex"}})
        result = _resolve_backend_override("any_step", "remediation", cfg)
        assert result is not None
        assert result.backend == "codex"
        assert result.kind is BackendAuthorityKind.RECIPE
        assert result.tier == "recipe_wildcard"
        assert result.key_path == "agent_backend.recipe_overrides.remediation.*"

    def test_exact_beats_wildcard(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(
            recipe_overrides={"remediation": {"*": "codex", "dry_walkthrough": "claude-code"}}
        )
        assert (
            _backend(_resolve_backend_override("dry_walkthrough", "remediation", cfg))
            == "claude-code"
        )

    def test_step_override_with_recipe_context(self) -> None:
        from autoskillit.core import BackendAuthorityKind
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(step_overrides={"dry_walkthrough": "codex"})
        result = _resolve_backend_override("dry_walkthrough", "remediation", cfg)
        assert _backend(result) == "codex"
        assert result is not None
        assert result.kind is BackendAuthorityKind.STEP

    def test_step_override_requires_recipe_context(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(step_overrides={"dry_walkthrough": "codex"})
        assert _resolve_backend_override("dry_walkthrough", "", cfg) is None

    def test_recipe_override_beats_step_override(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(
            step_overrides={"dry_walkthrough": "codex"},
            recipe_overrides={"remediation": {"dry_walkthrough": "claude-code"}},
        )
        assert (
            _backend(_resolve_backend_override("dry_walkthrough", "remediation", cfg))
            == "claude-code"
        )

    def test_step_wildcard_with_recipe_context(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(step_overrides={"*": "codex"})
        assert _backend(_resolve_backend_override("anything", "any_recipe", cfg)) == "codex"

    def test_step_wildcard_requires_recipe_context(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(step_overrides={"*": "codex"})
        assert _resolve_backend_override("anything", "", cfg) is None

    def test_no_match_returns_none(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend()
        assert _resolve_backend_override("nothing", "any_recipe", cfg) is None

    def test_empty_step_name_skips_recipe_lookup(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(recipe_overrides={"remediation": {"*": "codex"}})
        assert _backend(_resolve_backend_override("", "remediation", cfg)) == "codex"

    def test_empty_recipe_name_skips_recipe_overrides(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(
            recipe_overrides={"remediation": {"*": "codex"}},
            step_overrides={"*": "claude-code"},
        )
        # No recipe context: all tiers are gated, so we return None.
        assert _resolve_backend_override("any_step", "", cfg) is None
