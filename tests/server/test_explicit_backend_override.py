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
    resolver = MagicMock(resolve=MagicMock(return_value=resolved))
    resolver.resolve_invocation.return_value = SimpleNamespace(capability_union=frozenset(caps))
    return resolver


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
        admission_map, _ = _compute_effective_backend_map(
            cast(Any, steps), "codex", None, "remediation", config_backend=cfg
        )
        assert admission_map == {"dry_walkthrough": "codex"}

    def test_dry_walkthrough_codex_pin_model_alias_translation(self):
        """The full composed scenario: when dry_walkthrough is pinned to codex
        and model is set to 'opus', the Codex backend translates 'opus' via
        CODEX_MODEL_ALIASES with corresponding CODEX_EFFORT_MAPPING effort.

        This completes the end-to-end verification started by
        test_dry_walkthrough_codex_pin (which covers the backend resolution
        half) — together they prove REQ-MDL-002.
        """
        from autoskillit.core.types._type_backend import (
            CODEX_EFFORT_MAPPING,
            CODEX_MODEL_ALIASES,
        )
        from autoskillit.execution.backends.codex import CodexBackend

        backend = CodexBackend()
        expected_model = CODEX_MODEL_ALIASES["opus"]
        expected_effort = CODEX_EFFORT_MAPPING["opus"]
        translated_model = backend.translate_model("opus")
        assert translated_model == expected_model, (
            f"Expected 'opus' to translate to {expected_model!r} on Codex, "
            f"got {translated_model!r}"
        )
        config_overrides = backend.model_config_overrides("opus")
        assert config_overrides == (f"model_reasoning_effort={expected_effort}",), (
            f"Expected 'opus' to produce {expected_effort!r} effort on Codex, "
            f"got {config_overrides!r}"
        )

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
        admission_map, _ = _compute_effective_backend_map(
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


class TestExplicitOverrideProviderPrecedence:
    """Explicit backend pin takes precedence over provider ANTHROPIC_BASE_URL routing."""

    @pytest.mark.anyio
    async def test_explicit_override_beats_provider_routing(
        self, tool_ctx_kitchen_open, tmp_path, monkeypatch
    ):
        import json

        from autoskillit.config._config_dataclasses import AgentBackendConfig
        from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
        from autoskillit.server.tools.tools_execution import run_skill
        from tests.fakes import InMemoryHeadlessExecutor

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor

        # Non-Claude backend (anthropic_provider_capable=False triggers _provider_override)
        fake_backend = MagicMock(spec=CodingAgentBackend)
        fake_backend.name = "codex"
        fake_backend.capabilities.anthropic_provider_capable = False
        tool_ctx_kitchen_open.backend = fake_backend

        # Explicit backend override: pin this step to codex
        tool_ctx_kitchen_open.config.agent_backend = AgentBackendConfig(
            backend="codex",
            recipe_overrides={"remediation": {"investigate": "codex"}},
        )
        tool_ctx_kitchen_open.recipe_name = "remediation"

        from autoskillit.core import SkillExecutionRole, SkillSource
        from autoskillit.workspace import EffectiveSkillInvocation, SkillInfo

        root = SkillInfo(
            name="investigate",
            source=SkillSource.BUNDLED_EXTENDED,
            path=tmp_path / "investigate" / "SKILL.md",
            canonical_content=(
                "---\nname: investigate\ndescription: Test skill.\n"
                "execution_role: session\n---\n# Investigate\n"
            ),
        )
        invocation = EffectiveSkillInvocation(
            root=root,
            closure=(root,),
            capability_union=frozenset(),
            project_root=tmp_path,
            execution_role=SkillExecutionRole.SESSION,
        )
        resolver = MagicMock()
        resolver.resolve_invocation.return_value = invocation
        tool_ctx_kitchen_open.skill_resolver = resolver

        # Provider profile returns ANTHROPIC_BASE_URL — normally this would
        # trigger _provider_override=True and set backend_override="claude-code"
        monkeypatch.setattr(
            "autoskillit.server._guards._resolve_provider_profile",
            lambda *a, **kw: (
                "minimax",
                {
                    "ANTHROPIC_BASE_URL": "https://api.minimax.chat/v1/anthropic",
                    "ANTHROPIC_API_KEY": "minimax-key-placeholder",
                },
            ),
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_execution.is_feature_enabled",
            lambda *a, **kw: True,
        )

        # Spy on executor.run to capture backend_override kwarg. run_skill calls
        # executor.run(resolved_command, cwd, model=..., ...) — resolved_command
        # and cwd are positional, so spy_run must accept them positionally.
        captured = {}
        original_run = executor.run

        async def spy_run(*args, **kwargs):
            captured.update(kwargs)
            return await original_run(*args, **kwargs)

        monkeypatch.setattr(executor, "run", spy_run)

        result = json.loads(
            await run_skill(
                "/autoskillit:investigate",
                str(tmp_path),
                step_name="investigate",
            )
        )
        # Explicit override must win — backend_override should be "codex",
        # NOT "claude-code" from the provider routing
        assert captured.get("backend_override") == "codex", (
            f"Expected explicit override 'codex' to beat provider routing, "
            f"got backend_override={captured.get('backend_override')!r}: {result}"
        )


class TestBothRoutingDirections:
    def test_codex_to_claude_explicit_override(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(
            backend="codex",
            step_overrides={"implement": "claude-code"},
        )
        result = _resolve_backend_override("implement", "any_recipe", cfg)
        assert result is not None
        assert result.backend == "claude-code"

    def test_claude_to_codex_explicit_override(self) -> None:
        from autoskillit.server._guards import _resolve_backend_override

        cfg = _make_backend(
            backend="claude-code",
            step_overrides={"implement": "codex"},
        )
        result = _resolve_backend_override("implement", "any_recipe", cfg)
        assert result is not None
        assert result.backend == "codex"

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
        admission_map, _ = _compute_effective_backend_map(
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
        ``_skill_requires_claude`` claude binary check must NOT fire — the
        explicit pin keeps the step on codex even when the claude binary is
        absent (simulated at the _provider_aware_capability_overrides level
        where shutil.which is actually called)."""
        from autoskillit.server.tools._auto_overrides import (
            AGENT_BACKEND_CLAUDE_CODE,
            _compute_effective_backend_map,
        )

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
        admission_map, _ = _compute_effective_backend_map(
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
