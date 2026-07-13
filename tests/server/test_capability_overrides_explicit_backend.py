from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_recipe_step(step_name: str, skill_command: str = "/foo"):
    return SimpleNamespace(
        name=step_name,
        tool="run_skill",
        provider="",
        with_args={"skill_command": skill_command},
        skip_when_false="",
        backend_requirements=None,
    )


def _make_recipe_steps(*names: str) -> dict[str, Any]:
    return {n: _make_recipe_step(n) for n in names}


def _make_backend(**kwargs):
    from autoskillit.config.settings import AgentBackendConfig

    return AgentBackendConfig(**kwargs)


def _make_resolver(caps_per_skill: dict[str, tuple[str, ...]] | None = None):
    if caps_per_skill is None:
        caps_per_skill = {}
    cap_reg = MagicMock()
    cap_reg.get = lambda c: MagicMock(worker_routable=True)
    table = {}

    def _resolve(name: str):
        caps = caps_per_skill.get(name, ())
        return MagicMock(uses_capabilities=frozenset(caps))

    table["resolve"] = _resolve
    return MagicMock(**table)


class TestExplicitClaudePinFlipsGitWriteIngredient:
    def test_explicit_claude_pin_flips_git_write_ingredient(self) -> None:
        """A step explicitly pinned to claude-code must contribute to
        flipping backend_supports_git_write=True."""
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.server.tools._auto_overrides import (
            _provider_aware_capability_overrides,
        )

        steps = _make_recipe_steps("implement")
        providers = ProvidersConfig()
        cfg = _make_backend(
            backend="codex",
            step_overrides={"implement": "claude-code"},
        )
        # Resolver says the skill uses git_metadata_write (worker_routable).
        resolver = _make_resolver({"foo": ("git_metadata_write",)})
        overrides, _detail = _provider_aware_capability_overrides(
            cast(Any, _make_mock_codex_backend()),
            "any_recipe",
            providers,
            cast(Any, steps),
            skill_resolver=resolver,
            config_backend=cfg,
        )
        assert overrides["backend_supports_git_write"] == "true"


class TestExplicitCodexPinExcludedFromAggregate:
    def test_explicit_codex_pin_single_step_does_not_flip_ingredient(self) -> None:
        """A single step explicitly pinned to codex must NOT flip the
        backend_supports_git_write ingredient (it's excluded from the aggregate)."""
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.server.tools._auto_overrides import (
            _provider_aware_capability_overrides,
        )

        steps = _make_recipe_steps("implement")
        providers = ProvidersConfig()
        cfg = _make_backend(
            backend="codex",
            step_overrides={"implement": "codex"},
        )
        resolver = _make_resolver({"foo": ("git_metadata_write",)})
        overrides, _detail = _provider_aware_capability_overrides(
            cast(Any, _make_mock_codex_backend()),
            "any_recipe",
            providers,
            cast(Any, steps),
            skill_resolver=resolver,
            config_backend=cfg,
        )
        # Single step pinned to codex is excluded — aggregate stays false.
        assert overrides["backend_supports_git_write"] == "false"

    def test_explicit_codex_pin_sibling_unpinned_step_still_flips(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two steps: A pinned to codex (excluded), B unpinned with same capability
        (contributes) → aggregate still flips (any-suffices semantics preserved)."""
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.server.tools._auto_overrides import (
            _provider_aware_capability_overrides,
        )

        # step_b's implicit capability routing requires the claude binary.
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/bin/claude" if cmd == "claude" else None
        )

        steps = {
            "step_a": _make_recipe_step("step_a", skill_command="/skill_a"),
            "step_b": _make_recipe_step("step_b", skill_command="/skill_b"),
        }
        # Pin step_a to codex, leave step_b unpinned.
        cfg = _make_backend(
            backend="codex",
            step_overrides={"step_a": "codex"},
        )
        # Both skills declare the same routable capability.
        resolver = _make_resolver(
            {"skill_a": ("git_metadata_write",), "skill_b": ("git_metadata_write",)}
        )
        providers = ProvidersConfig()
        overrides, _detail = _provider_aware_capability_overrides(
            cast(Any, _make_mock_codex_backend()),
            "any_recipe",
            providers,
            cast(Any, steps),
            skill_resolver=resolver,
            config_backend=cfg,
        )
        # step_b alone contributes → any-suffices flips the ingredient.
        assert overrides["backend_supports_git_write"] == "true"


def _make_mock_codex_backend():
    backend = MagicMock()
    backend.name = "codex"
    backend.capabilities.anthropic_provider_capable = False
    backend.capabilities.git_metadata_writable = False
    backend.capabilities.applicable_guards = frozenset()
    return backend
