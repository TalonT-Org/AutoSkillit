from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_backend(**kwargs):
    from autoskillit.config.settings import AgentBackendConfig

    return AgentBackendConfig(**kwargs)


class TestResolveBackendOverride:
    def test_exact_recipe_step_match(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(recipe_overrides={"remediation": {"dry_walkthrough": "codex"}})
        assert _resolve_backend_override("dry_walkthrough", "remediation", cfg) == "codex"

    def test_recipe_wildcard(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(recipe_overrides={"remediation": {"*": "codex"}})
        assert _resolve_backend_override("any_step", "remediation", cfg) == "codex"

    def test_exact_beats_wildcard(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(
            recipe_overrides={"remediation": {"*": "codex", "dry_walkthrough": "claude-code"}}
        )
        assert _resolve_backend_override("dry_walkthrough", "remediation", cfg) == "claude-code"

    def test_step_override_with_recipe_context(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(step_overrides={"dry_walkthrough": "codex"})
        assert _resolve_backend_override("dry_walkthrough", "remediation", cfg) == "codex"

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
        assert _resolve_backend_override("dry_walkthrough", "remediation", cfg) == "claude-code"

    def test_step_wildcard_with_recipe_context(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(step_overrides={"*": "codex"})
        assert _resolve_backend_override("anything", "any_recipe", cfg) == "codex"

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
        assert _resolve_backend_override("", "remediation", cfg) == "codex"

    def test_empty_recipe_name_skips_recipe_overrides(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(
            recipe_overrides={"remediation": {"*": "codex"}},
            step_overrides={"*": "claude-code"},
        )
        # No recipe context: all tiers are gated, so we return None.
        assert _resolve_backend_override("any_step", "", cfg) is None
