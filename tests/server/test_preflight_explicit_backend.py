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
    def test_explicit_override_to_missing_binary_excluded(self, tmp_path) -> None:
        """An explicit override pointing to a backend excludes that step from
        feasibility — with a synthetic fix-required hook, preflight passes
        because the excluded step is not feasible.

        Pins to the real (unpatched) CodexBackend, which is persistent — pass
        temp_dir so the #4391 persistent-root axis derives cleanly and this
        test keeps exercising its original fix-required-hook path (T6b).
        """
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.server.tools import _preflight
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
        with patch.object(_preflight, "HOOK_REGISTRY", [synthetic]):
            err = _check_dispatch_feasibility(
                post_prune_step_names=["step_a"],
                active_recipe_steps=cast(Any, steps),
                backend=backend,
                config_providers=providers,
                recipe_name="remediation",
                config_backend=cfg,
                skill_resolver=_make_skill_resolver_with_no_caps(),
                temp_dir=tmp_path,
            )
        assert err is None

    def test_explicit_override_to_claude_exempts_from_fix_required(self) -> None:
        """A step explicitly pinned to claude-code is exempted from the
        orchestrator-level fix_required_matchers check — with a synthetic
        fix-required hook, the claude-pinned step is skipped so preflight passes."""
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.server.tools import _preflight
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
        with patch.object(_preflight, "HOOK_REGISTRY", [synthetic]):
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
        from autoskillit.server.tools import _preflight
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
        with patch.object(_preflight, "HOOK_REGISTRY", [synthetic]):
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


def _join_required_step_and_resolver() -> tuple[dict[str, SimpleNamespace], MagicMock]:
    from autoskillit.core import JoinSpec, SkillSemanticPlan

    step = SimpleNamespace(
        name="step_a",
        tool="run_skill",
        provider="",
        with_args={},
        skip_when_false="",
        skill_name="join-root",
    )
    resolver = _make_skill_resolver_with_no_caps()
    resolver.resolve_invocation.return_value = SimpleNamespace(
        root=SimpleNamespace(
            semantic_plan=SkillSemanticPlan(schema_version=1, join=JoinSpec(required=True))
        )
    )
    return {"step_a": step}, resolver


class TestPreflightLaunchEvidenceDeferral:
    def test_codex_pinned_join_required_step_defers_to_launch_evidence(self, tmp_path) -> None:
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.execution.backends import get_backend
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        steps, resolver = _join_required_step_and_resolver()
        err = _check_dispatch_feasibility(
            post_prune_step_names=["step_a"],
            active_recipe_steps=cast(Any, steps),
            backend=get_backend("codex"),
            config_providers=ProvidersConfig(),
            recipe_name="remediation",
            config_backend=_make_backend(backend="codex", step_overrides={"step_a": "codex"}),
            skill_resolver=resolver,
            temp_dir=tmp_path,
        )

        assert err is None

    def test_backend_absolute_join_refusal_still_fails_preflight(self, monkeypatch) -> None:
        import json

        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.core import (
            BackendCapabilities,
            SkillSemanticAdaptationResult,
            SkillSemanticOperation,
        )
        from autoskillit.server.tools import _preflight
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        pinned = SimpleNamespace(
            name="join-less",
            capabilities=BackendCapabilities(
                fixed_set_join_capable=False,
                managed_fixed_batch_route_capable=False,
            ),
            adapt_skill_semantics=lambda _plan, _context=None: (
                SkillSemanticAdaptationResult.unsupported(
                    backend="join-less",
                    operation=SkillSemanticOperation.REQUIRED_JOIN,
                )
            ),
        )
        monkeypatch.setattr(_preflight, "get_backend", lambda _name: pinned)
        steps, resolver = _join_required_step_and_resolver()
        err = _check_dispatch_feasibility(
            post_prune_step_names=["step_a"],
            active_recipe_steps=cast(Any, steps),
            backend=pinned,
            config_providers=ProvidersConfig(),
            recipe_name="remediation",
            config_backend=_make_backend(backend="codex", step_overrides={"step_a": "join-less"}),
            skill_resolver=resolver,
        )

        assert err is not None
        payload = json.loads(err)
        assert payload["kitchen"] == "preflight_failed"
        assert "required_join" in payload["error"]
