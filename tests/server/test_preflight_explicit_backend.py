from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_fix_required_hook():
    from autoskillit.hook_registry import HookDef

    return HookDef(
        matcher=r"Read|Write|Edit",
        scripts=["guards/synthetic_test_hook.py"],
        codex_status="fix-required",
        mechanism="deny",
    )


def _make_step(step_name: str, tool: str = "run_skill", provider: str = ""):
    return SimpleNamespace(
        name=step_name,
        tool=tool,
        provider=provider,
        with_args={},
        skip_when_false="",
    )


def _make_backend(**kwargs):
    from autoskillit.config.settings import AgentBackendConfig

    return AgentBackendConfig(**kwargs)


def _make_skill_resolver_with_no_caps() -> MagicMock:
    """Return a MagicMock skill_resolver whose .resolve() returns a stub
    with empty uses_capabilities — preserves existing tests' err is None behavior
    because check_hard_capability_feasibility returns None for caps with no
    required_backend_property.
    """
    resolver = MagicMock()
    resolver.resolve.return_value = SimpleNamespace(
        uses_capabilities=frozenset(),
    )
    return resolver


class TestPreflightExplicitBackend:
    def test_explicit_override_to_missing_binary_excluded(self) -> None:
        """An explicit override pointing to a backend excludes that step from
        feasibility — with a synthetic fix-required hook, preflight passes
        because the excluded step is not feasible."""
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        steps = {"step_a": _make_step("step_a")}
        providers = ProvidersConfig()
        cfg = _make_backend(
            backend="codex",
            step_overrides={"step_a": "codex"},
        )
        backend = MagicMock()
        backend.name = "codex"
        backend.capabilities.anthropic_provider_capable = False
        backend.capabilities.applicable_guards = frozenset()
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]):
            err = _check_dispatch_feasibility(
                post_prune_step_names=["step_a"],
                active_recipe_steps=cast(Any, steps),
                backend=backend,
                config_providers=providers,
                recipe_name="remediation",
                config_backend=cfg,
                skill_resolver=_make_skill_resolver_with_no_caps(),
            )
        assert err is None

    def test_explicit_override_to_claude_exempts_from_fix_required(self) -> None:
        """A step explicitly pinned to claude-code is exempted from the
        orchestrator-level fix_required_matchers check — with a synthetic
        fix-required hook, the claude-pinned step is skipped so preflight passes."""
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        steps = {"step_a": _make_step("step_a")}
        providers = ProvidersConfig()
        cfg = _make_backend(
            backend="codex",
            step_overrides={"step_a": "claude-code"},
        )
        backend = MagicMock()
        backend.name = "codex"
        backend.capabilities.anthropic_provider_capable = False
        backend.capabilities.applicable_guards = frozenset({"some_guard"})
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]):
            err = _check_dispatch_feasibility(
                post_prune_step_names=["step_a"],
                active_recipe_steps=cast(Any, steps),
                backend=backend,
                config_providers=providers,
                recipe_name="remediation",
                config_backend=cfg,
                skill_resolver=_make_skill_resolver_with_no_caps(),
            )
        assert err is None

    def test_explicit_override_invalid_backend_name_excluded(self) -> None:
        """A typo in the override backend name (unregistered) must not crash
        preflight — the step is silently excluded from feasibility."""
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        steps = {"step_a": _make_step("step_a")}
        providers = ProvidersConfig()
        cfg = _make_backend(
            backend="codex",
            step_overrides={"step_a": "codexx_typo"},
        )
        backend = MagicMock()
        backend.name = "codex"
        backend.capabilities.anthropic_provider_capable = False
        backend.capabilities.applicable_guards = frozenset()
        synthetic = _make_fix_required_hook()
        with patch("autoskillit.server.tools._preflight.HOOK_REGISTRY", [synthetic]):
            err = _check_dispatch_feasibility(
                post_prune_step_names=["step_a"],
                active_recipe_steps=cast(Any, steps),
                backend=backend,
                config_providers=providers,
                recipe_name="remediation",
                config_backend=cfg,
                skill_resolver=_make_skill_resolver_with_no_caps(),
            )
        assert err is None
