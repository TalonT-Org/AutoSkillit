"""Discipline delivery channel matrix contract tests.

Session-type x backend parametrized tests asserting that each combination's
PRIMARY delivery channel is populated via the correct builder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    SESSION_TYPE_ENV_VAR,
    SESSION_TYPE_FLEET,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
    DirectInstall,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _assert_interactive_primary_channel(backend, spec) -> None:
    """Assert the interactive primary delivery channel is populated."""
    if isinstance(backend, ClaudeCodeBackend):
        assert "--append-system-prompt" in spec.cmd
    else:
        assert any("developer_instructions=" in arg for arg in spec.cmd)


class TestFleetInteractive:
    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_primary_channel_populated(self, backend) -> None:
        spec = backend.build_interactive_cmd(
            system_prompt="Fleet discipline prompt",
            env_extras={SESSION_TYPE_ENV_VAR: SESSION_TYPE_FLEET},
        )
        _assert_interactive_primary_channel(backend, spec)

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_session_type_fleet_in_env(self, backend) -> None:
        spec = backend.build_interactive_cmd(
            system_prompt="Fleet discipline prompt",
            env_extras={SESSION_TYPE_ENV_VAR: SESSION_TYPE_FLEET},
        )
        assert spec.env.get(SESSION_TYPE_ENV_VAR) == SESSION_TYPE_FLEET


class TestOrchestratorInteractive:
    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_primary_channel_populated(self, backend) -> None:
        spec = backend.build_interactive_cmd(
            system_prompt="Orchestrator discipline prompt",
        )
        _assert_interactive_primary_channel(backend, spec)

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_no_session_type_assertion(self, backend) -> None:
        spec = backend.build_interactive_cmd(
            system_prompt="Orchestrator discipline prompt",
        )
        assert not spec.env.get(SESSION_TYPE_ENV_VAR, "")


class TestOrchestratorHeadless:
    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_orchestrator_prompt_non_empty(self, backend) -> None:
        spec = backend.build_food_truck_cmd(
            orchestrator_prompt="Run the pipeline",
            plugin_source=DirectInstall(plugin_dir=Path("/tmp")),
            cwd="/tmp",
            completion_marker="%%DONE%%",
        )
        assert any("Run the pipeline" in arg for arg in spec.cmd)
        assert any("%%DONE%%" in arg for arg in spec.cmd)

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_session_type_orchestrator_in_env(self, backend) -> None:
        spec = backend.build_food_truck_cmd(
            orchestrator_prompt="Run the pipeline",
            plugin_source=DirectInstall(plugin_dir=Path("/tmp")),
            cwd="/tmp",
            completion_marker="%%DONE%%",
        )
        assert spec.env.get(SESSION_TYPE_ENV_VAR) == SESSION_TYPE_ORCHESTRATOR


class TestSkillSession:
    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_positional_prompt_non_empty(self, backend) -> None:
        spec = backend.build_skill_session_cmd("/investigate foo", "/tmp")
        assert any("investigate" in arg for arg in spec.cmd)

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_session_type_skill_in_env(self, backend) -> None:
        spec = backend.build_skill_session_cmd("/investigate foo", "/tmp")
        assert spec.env.get(SESSION_TYPE_ENV_VAR) == SESSION_TYPE_SKILL


class TestSousChefDelivery:
    def test_sous_chef_in_orchestrator_prompt(self) -> None:
        from autoskillit.cli._prompts import _build_orchestrator_prompt, _read_full_sous_chef
        from autoskillit.execution import codex_recipe_delivery_calling_contract

        sous_chef = _read_full_sous_chef()
        assert sous_chef, "_read_full_sous_chef must return non-empty content"
        prompt = _build_orchestrator_prompt("test-recipe", "mcp__autoskillit__")
        assert sous_chef[:80] in prompt
        assert "uses_capabilities:" not in sous_chef
        assert "execution_role:" not in sous_chef
        assert "backend_requirements:" not in sous_chef
        calling_contract = codex_recipe_delivery_calling_contract(mcp_prefix="mcp__autoskillit__")
        assert prompt.count(calling_contract) == 1

    def test_sous_chef_in_open_kitchen_prompt(self) -> None:
        from autoskillit.cli._prompts import _build_open_kitchen_prompt, _read_full_sous_chef

        sous_chef = _read_full_sous_chef()
        assert sous_chef, "_read_full_sous_chef must return non-empty content"
        prompt = _build_open_kitchen_prompt("mcp__autoskillit__")
        assert sous_chef[:80] in prompt
        assert "uses_capabilities:" not in prompt
        assert "execution_role:" not in prompt
        assert "backend_requirements:" not in prompt

    def test_sous_chef_not_in_fleet_dispatch_prompt(self) -> None:
        from autoskillit.cli._prompts import _build_fleet_dispatch_prompt, _read_full_sous_chef

        sous_chef = _read_full_sous_chef()
        assert sous_chef, "_read_full_sous_chef must return non-empty content"
        prompt = _build_fleet_dispatch_prompt("mcp__autoskillit__")
        assert sous_chef[:80] not in prompt
        assert "name: sous-chef" not in prompt
