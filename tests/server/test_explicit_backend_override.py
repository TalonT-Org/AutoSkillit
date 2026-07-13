from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_recipe_step(step_name: str, skill_command: str = "/dry-walkthrough"):
    return SimpleNamespace(
        name=step_name,
        tool="run_skill",
        provider="",
        with_args={"skill_command": skill_command},
        skip_when_false="",
        backend_requirements=None,
    )


def _make_recipe_steps(*names: str):
    return {n: _make_recipe_step(n) for n in names}


def _make_backend(**kwargs):
    from autoskillit.config.settings import AgentBackendConfig

    return AgentBackendConfig(**kwargs)


def _make_resolver(skill_name: str, caps=("agent_model",)):
    cap_reg = MagicMock()
    cap_reg.get = lambda c: MagicMock(worker_routable=True) if c in caps else None
    resolved = MagicMock(uses_capabilities=frozenset(caps))
    return MagicMock(resolve=MagicMock(return_value=resolved))


class TestExplicitBackendOverrideAdmissionDispatchAgreement:
    def test_explicit_backend_override_admission_dispatch_agreement(self) -> None:
        """Both admission (compute_effective_backend_map) and dispatch agree
        on the explicit override: a codex pin must produce codex in both."""
        from autoskillit.server.tools._auto_overrides import _compute_effective_backend_map

        steps = _make_recipe_steps("dry_walkthrough")
        cfg = _make_backend(
            backend="codex",
            recipe_overrides={"remediation": {"dry_walkthrough": "codex"}},
        )
        # Admission side
        admission_map = _compute_effective_backend_map(
            cast(Any, steps), "codex", None, "remediation", config_backend=cfg
        )
        assert admission_map == {"dry_walkthrough": "codex"}

    def test_explicit_override_suppresses_capability_routing(self) -> None:
        """A step pinned to codex with a worker_routable capability must NOT
        be rerouted to claude-code by capability routing."""
        from autoskillit.server.tools._auto_overrides import (
            AGENT_BACKEND_CLAUDE_CODE,
            _compute_effective_backend_map,
        )

        steps = _make_recipe_steps("dry_walkthrough")
        cfg = _make_backend(
            backend="codex",
            recipe_overrides={"remediation": {"dry_walkthrough": "codex"}},
        )
        resolver = _make_resolver("dry-walkthrough")
        admission_map = _compute_effective_backend_map(
            cast(Any, steps),
            "codex",
            None,
            "remediation",
            skill_resolver=resolver,
            config_backend=cfg,
        )
        assert admission_map is not None
        assert admission_map["dry_walkthrough"] != AGENT_BACKEND_CLAUDE_CODE
        assert admission_map["dry_walkthrough"] == "codex"


class TestBothRoutingDirections:
    def test_codex_to_claude_explicit_override(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(
            backend="codex",
            step_overrides={"implement": "claude-code"},
        )
        assert _resolve_backend_override("implement", "any_recipe", cfg) == "claude-code"

    def test_claude_to_codex_explicit_override(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(
            backend="claude-code",
            step_overrides={"implement": "codex"},
        )
        assert _resolve_backend_override("implement", "any_recipe", cfg) == "codex"

    def test_explicit_override_with_provider_override(self) -> None:
        """Explicit backend pin takes precedence over provider ANTHROPIC_BASE_URL."""
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(
            backend="codex",
            recipe_overrides={"remediation": {"dry_walkthrough": "codex"}},
        )
        assert _resolve_backend_override("dry_walkthrough", "remediation", cfg) == "codex"

    def test_dry_walkthrough_codex_pin(self) -> None:
        """The exact scenario from issue #4242: backend=codex + recipe override to codex +
        capability worker_routable=True → resolves to codex (no reroute)."""
        from autoskillit.config.settings import ProvidersConfig
        from autoskillit.server.tools._auto_overrides import (
            AGENT_BACKEND_CLAUDE_CODE,
            _compute_effective_backend_map,
        )

        steps = _make_recipe_steps("dry_walkthrough")
        providers = ProvidersConfig()
        cfg = _make_backend(
            backend="codex",
            recipe_overrides={"remediation": {"dry_walkthrough": "codex"}},
        )
        resolver = _make_resolver("dry-walkthrough")
        admission_map = _compute_effective_backend_map(
            cast(Any, steps),
            "codex",
            providers,
            "remediation",
            skill_resolver=resolver,
            config_backend=cfg,
        )
        assert admission_map is not None
        assert admission_map["dry_walkthrough"] != AGENT_BACKEND_CLAUDE_CODE
        assert admission_map["dry_walkthrough"] == "codex"

    def test_explicit_codex_pin_skips_claude_binary_check(self, monkeypatch) -> None:
        """When explicit override pins to codex on a worker_routable step, the
        ``_skill_requires_claude`` claude binary check must NOT fire — but
        we still verify the override itself suppresses capability reroute."""
        from autoskillit.server.tools._auto_overrides import (
            AGENT_BACKEND_CLAUDE_CODE,
            _compute_effective_backend_map,
        )

        # Force shutil.which('claude') to return None — simulating absent claude binary.
        monkeypatch.setattr(
            "autoskillit.server.tools._auto_overrides.shutil.which",
            lambda name: None if name == "claude" else f"/usr/bin/{name}",
        )
        steps = _make_recipe_steps("dry_walkthrough")
        cfg = _make_backend(
            backend="codex",
            recipe_overrides={"remediation": {"dry_walkthrough": "codex"}},
        )
        resolver = _make_resolver("dry-walkthrough")
        # No exception: the explicit pin keeps the step on codex.
        admission_map = _compute_effective_backend_map(
            cast(Any, steps),
            "codex",
            None,
            "remediation",
            skill_resolver=resolver,
            config_backend=cfg,
        )
        assert admission_map is not None
        assert admission_map["dry_walkthrough"] != AGENT_BACKEND_CLAUDE_CODE
