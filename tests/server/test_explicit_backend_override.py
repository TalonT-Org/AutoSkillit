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
    )


def _make_recipe_steps(*names: str):
    return {n: _make_recipe_step(n) for n in names}


def _make_backend(**kwargs):
    from autoskillit.config.settings import AgentBackendConfig

    return AgentBackendConfig(**kwargs)


def _bundled_backend():
    from autoskillit.config.settings import AgentBackendConfig
    from autoskillit.core.io import load_yaml
    from autoskillit.core.paths import pkg_root

    defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
    return AgentBackendConfig(**defaults["agent_backend"])


class TestExplicitBackendOverrideAdmissionDispatchAgreement:
    @pytest.mark.parametrize(
        ("recipe_name", "step_name", "key_path"),
        [
            (
                "implementation",
                "run_arch_lenses",
                "agent_backend.recipe_overrides.implementation.run_arch_lenses",
            ),
            (
                "implementation-groups",
                "run_arch_lenses",
                "agent_backend.recipe_overrides.implementation-groups.run_arch_lenses",
            ),
            (
                "remediation",
                "investigate",
                "agent_backend.recipe_overrides.remediation.investigate",
            ),
            (
                "remediation",
                "run_arch_lenses",
                "agent_backend.recipe_overrides.remediation.run_arch_lenses",
            ),
            (
                "research",
                "run_experiment_lenses",
                "agent_backend.recipe_overrides.research.run_experiment_lenses",
            ),
            (
                "research",
                "scope",
                "agent_backend.recipe_overrides.research.scope",
            ),
            (
                "research-design",
                "scope",
                "agent_backend.recipe_overrides.research-design.scope",
            ),
            (
                "research-review",
                "run_experiment_lenses",
                "agent_backend.recipe_overrides.research-review.run_experiment_lenses",
            ),
        ],
    )
    def test_bundled_recipe_step_pin_drives_admission_with_exact_origin(
        self, recipe_name: str, step_name: str, key_path: str
    ) -> None:
        from autoskillit.server.tools._auto_overrides import _compute_effective_backend_map

        admission_map, origin_map = _compute_effective_backend_map(
            cast(Any, _make_recipe_steps(step_name)),
            "claude-code",
            recipe_name,
            config_backend=_bundled_backend(),
        )

        assert admission_map == {step_name: "codex"}
        assert origin_map == {step_name: key_path}

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
            cast(Any, steps), "codex", "remediation", config_backend=cfg
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

        # Non-Claude backend (anthropic_provider_capable=False triggers _provider_override).
        # Keep the test backend and generated-home manager aligned: Codex session
        # projection is persistent and therefore requires a persistent root.
        from autoskillit.execution.backends.codex import CodexBackend
        from autoskillit.workspace import (
            DefaultSessionSkillManager,
            SkillsDirectoryProvider,
        )

        concrete_backend = CodexBackend()
        fake_backend = MagicMock(spec=CodingAgentBackend)
        fake_backend.name = "codex"
        fake_backend.capabilities = concrete_backend.capabilities
        fake_backend.conventions = concrete_backend.conventions
        fake_backend.ensure_pre_launch.return_value = []
        fake_backend.validate_session_layout.return_value = []
        fake_backend.session_locator.return_value.project_log_dir.return_value = None
        tool_ctx_kitchen_open.backend = fake_backend
        tool_ctx_kitchen_open.session_skill_manager = DefaultSessionSkillManager(
            SkillsDirectoryProvider(),
            ephemeral_root=tmp_path / "ephemeral-sessions",
            persistent_roots={"codex": tmp_path / "persistent-sessions"},
        )
        monkeypatch.setattr(
            tool_ctx_kitchen_open.launch_resolver,
            "backend_for_authority",
            lambda _authority: fake_backend,
        )
        monkeypatch.setattr(
            "autoskillit.server.tools.tools_execution.shutil.which",
            lambda binary: f"/test-bin/{binary}",
        )

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

        # Provider profile metadata cannot compete with explicit backend authority.
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

        # Spy on executor.run to capture the typed authority. run_skill calls
        # executor.run(resolved_command, cwd, model=..., ...) — resolved_command
        # and cwd are positional, so spy_run must accept them positionally.
        captured = {}
        original_run = executor.run

        async def spy_run(*args, **kwargs):
            captured.update(kwargs)
            return await original_run(*args, **kwargs)

        monkeypatch.setattr(executor, "run", spy_run)

        response = json.loads(
            await run_skill(
                "/autoskillit:investigate backend-routing-test",
                str(tmp_path),
                step_name="investigate",
            )
        )
        authority = captured.get("backend_authority")
        assert authority is not None, response
        assert authority.backend == "codex"
        assert authority.kind.value == "recipe"
        assert authority.key_path == "agent_backend.recipe_overrides.remediation.investigate"


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
