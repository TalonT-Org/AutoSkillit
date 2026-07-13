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


class TestPreflightExplicitBackend:
    def test_explicit_override_to_missing_binary_excluded(self, monkeypatch) -> None:
        """An explicit override pointing to a backend whose binary is not on
        PATH excludes that step from feasibility — with a synthetic fix-required
        hook, preflight passes because the excluded step is not feasible."""
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        monkeypatch.setattr(
            "autoskillit.server.tools._preflight.shutil.which",
            lambda name: None if name in ("codex", "codex-cli") else f"/usr/bin/{name}",
        )

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
            )
        assert err is None

    def test_explicit_override_to_claude_exempts_from_fix_required(self, monkeypatch) -> None:
        """A step explicitly pinned to claude-code is exempted from the
        orchestrator-level fix_required_matchers check — with a synthetic
        fix-required hook, the claude-pinned step is skipped so preflight passes."""
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.server.tools._preflight import _check_dispatch_feasibility

        monkeypatch.setattr(
            "autoskillit.server.tools._preflight.shutil.which",
            lambda name: f"/usr/bin/{name}",
        )
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
            )
        assert err is None

    def test_explicit_override_backend_requirements_conflict(self):
        from unittest.mock import MagicMock

        from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
        from autoskillit.server.tools.tools_execution import _check_backend_compat

        fake_backend = MagicMock(spec=CodingAgentBackend)
        fake_backend.name = "codex"
        fake_backend.capabilities.applicable_guards = frozenset()

        skill_info = MagicMock()
        skill_info.backend_requirements = frozenset({"claude-code"})

        mock_resolver = MagicMock()

        result = _check_backend_compat(
            skill_command="/autoskillit:open-kitchen",
            resolved_command="/autoskillit:open-kitchen",
            effective_order_id="test-order",
            target_name="open-kitchen",
            skill_info=skill_info,
            effective_backend_obj=fake_backend,
            skill_resolver=mock_resolver,
        )
        assert result is not None
        import json

        parsed = json.loads(result)
        assert parsed["subtype"] == "crashed"
        assert "claude-code" in parsed["result"]
        assert "codex" in parsed["result"]
