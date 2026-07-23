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
        backend_requirements=None,
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
        backend_requirements=frozenset(),
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


def test_real_direct_and_pack_closure_drives_every_policy_consumer(
    tmp_path,
    monkeypatch,
) -> None:
    from pathlib import Path

    from autoskillit.config.settings import AgentBackendConfig, ProvidersConfig
    from autoskillit.core import SkillExecutionRole
    from autoskillit.execution.backends import get_backend
    from autoskillit.server.tools._backend_compat import _check_backend_compat
    from autoskillit.server.tools._execution_helpers import (
        aggregate_sandbox_overrides,
        get_routing_caps,
    )
    from autoskillit.server.tools._preflight import _check_dispatch_feasibility
    from autoskillit.workspace import DefaultSkillResolver

    project_root = tmp_path / "project"
    skill_root = project_root / ".claude" / "skills"

    def write_skill(
        name: str,
        *,
        capabilities: tuple[str, ...],
        categories: tuple[str, ...] = (),
        dependencies: tuple[str, ...] = (),
    ) -> None:
        path = skill_root / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            f"name: {name}\n"
            "description: Closure policy fixture.\n"
            f"uses_capabilities: [{', '.join(capabilities)}]\n"
            f"categories: [{', '.join(categories)}]\n"
            f"activate_deps: [{', '.join(dependencies)}]\n"
            "---\nbody\n",
            encoding="utf-8",
        )

    write_skill(
        "root",
        capabilities=("git_metadata_write",),
        dependencies=("direct", "github"),
    )
    write_skill("direct", capabilities=("agent_model",))
    write_skill(
        "pack-member",
        capabilities=("github_api_write",),
        categories=("github",),
    )
    empty_bundled = tmp_path / "bundled"
    empty_extended = tmp_path / "extended"
    empty_bundled.mkdir()
    empty_extended.mkdir()
    resolver = DefaultSkillResolver()
    monkeypatch.setattr(resolver, "_dir", empty_bundled)
    monkeypatch.setattr(resolver, "_extended_dir", empty_extended)

    invocation = resolver.resolve_invocation(
        "root",
        project_root,
        SkillExecutionRole.SESSION,
    )

    assert {member.name for member in invocation.closure} == {
        "root",
        "direct",
        "pack-member",
    }
    assert invocation.capability_union == frozenset(
        {"git_metadata_write", "agent_model", "github_api_write"}
    )
    assert all(
        member.execution_role is SkillExecutionRole.SESSION for member in invocation.closure
    )
    assert aggregate_sandbox_overrides(invocation.capability_union) == frozenset(
        {"sandbox_workspace_write.network_access=true"}
    )
    assert get_routing_caps(invocation.capability_union) == [
        "agent_model",
        "git_metadata_write",
    ]

    codex = get_backend("codex")
    compatibility_error = _check_backend_compat(
        skill_command="/root",
        resolved_command="/root",
        effective_order_id="order",
        target_name="root",
        skill_info=invocation,
        effective_backend_obj=codex,
        skill_resolver=resolver,
    )
    assert compatibility_error is not None
    assert "git_metadata_write" in compatibility_error

    step = SimpleNamespace(
        name="step_a",
        tool="run_skill",
        provider="",
        with_args={},
        skill_name="root",
        skip_when_false="",
        backend_requirements=None,
    )
    preflight_error = _check_dispatch_feasibility(
        post_prune_step_names=["step_a"],
        active_recipe_steps=cast(Any, {"step_a": step}),
        backend=codex,
        config_providers=ProvidersConfig(),
        recipe_name="closure-policy",
        config_backend=AgentBackendConfig(
            backend="codex",
            step_overrides={"step_a": "codex"},
        ),
        skill_resolver=resolver,
        project_root=Path(project_root),
    )
    assert preflight_error is not None
    assert "git_metadata_write" in preflight_error
