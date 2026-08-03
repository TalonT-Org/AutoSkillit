"""Discipline delivery channel matrix contract tests.

Session-type x backend parametrized tests asserting that each combination's
PRIMARY delivery channel is populated via the correct builder.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from autoskillit.core import (
    CODEX_INTAKE_DISCIPLINE_DIGEST,
    SESSION_TYPE_ENV_VAR,
    SESSION_TYPE_FLEET,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
)
from autoskillit.core.paths import pkg_root
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend, _generate_agent_tomls
from tests.execution.backends._plugin_binding import plugin_binding

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _assert_interactive_primary_channel(backend, spec) -> None:
    """Assert the interactive primary delivery channel is populated."""
    if isinstance(backend, ClaudeCodeBackend):
        assert "--append-system-prompt" in spec.cmd
    else:
        assert any("developer_instructions=" in arg for arg in spec.cmd)


def _build_orchestrator_spec(backend):
    with plugin_binding(Path("/tmp")) as binding:
        return backend.build_food_truck_cmd(
            orchestrator_prompt="Run the pipeline",
            plugin_binding=binding,
            cwd="/tmp",
            completion_marker="%%DONE%%",
        )


def _assert_interactive_intake_digest(backend, spec) -> None:
    """Assert the intake digest is present for Codex, absent for Claude.

    The interactive channel delivers the digest inside a `-c developer_instructions=...`
    TOML config-override, which escapes newlines to literal `\\n` sequences — the digest's
    single-line header survives that escaping unchanged, so it is the anchor here.
    """
    header = CODEX_INTAKE_DISCIPLINE_DIGEST.splitlines()[0]
    if isinstance(backend, ClaudeCodeBackend):
        assert not any(header in arg for arg in spec.cmd)
    else:
        assert any("developer_instructions=" in arg and header in arg for arg in spec.cmd)


def _assert_headless_intake_digest(backend, spec) -> None:
    """Assert the intake digest is present in the final prompt arg for Codex, absent for Claude."""
    if isinstance(backend, ClaudeCodeBackend):
        assert CODEX_INTAKE_DISCIPLINE_DIGEST not in spec.cmd[-1]
    else:
        assert CODEX_INTAKE_DISCIPLINE_DIGEST in spec.cmd[-1]


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

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_intake_digest_delivery(self, backend) -> None:
        spec = backend.build_interactive_cmd(
            system_prompt="Fleet discipline prompt",
            env_extras={SESSION_TYPE_ENV_VAR: SESSION_TYPE_FLEET},
        )
        _assert_interactive_intake_digest(backend, spec)


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

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_intake_digest_delivery(self, backend) -> None:
        spec = backend.build_interactive_cmd(
            system_prompt="Orchestrator discipline prompt",
        )
        _assert_interactive_intake_digest(backend, spec)


class TestOrchestratorHeadless:
    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_orchestrator_prompt_non_empty(self, backend) -> None:
        spec = _build_orchestrator_spec(backend)
        assert any("Run the pipeline" in arg for arg in spec.cmd)
        assert any("%%DONE%%" in arg for arg in spec.cmd)

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_session_type_orchestrator_in_env(self, backend) -> None:
        spec = _build_orchestrator_spec(backend)
        assert spec.env.get(SESSION_TYPE_ENV_VAR) == SESSION_TYPE_ORCHESTRATOR

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_intake_digest_delivery(self, backend) -> None:
        spec = _build_orchestrator_spec(backend)
        _assert_headless_intake_digest(backend, spec)


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

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_intake_digest_delivery(self, backend) -> None:
        spec = backend.build_skill_session_cmd("/investigate foo", "/tmp")
        _assert_headless_intake_digest(backend, spec)


class TestResumeDelivery:
    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_intake_digest_delivery(self, backend) -> None:
        spec = backend.build_resume_cmd(
            resume_session_id="abc123",
            prompt="CALLER PROMPT MARKER",
        )
        _assert_headless_intake_digest(backend, spec)

    def test_intake_digest_is_prepended_before_the_caller_prompt_for_codex(self) -> None:
        backend = CodexBackend()
        spec = backend.build_resume_cmd(
            resume_session_id="abc123",
            prompt="CALLER PROMPT MARKER",
        )
        assert spec.cmd[-1].index(CODEX_INTAKE_DISCIPLINE_DIGEST) < spec.cmd[-1].index(
            "CALLER PROMPT MARKER"
        )


class TestAgentTomlDelivery:
    @pytest.mark.medium
    def test_every_bundled_agent_toml_carries_the_composed_suffix(self, tmp_path) -> None:
        from autoskillit.execution.backends._claude_prompt import codex_discipline_suffix

        agents_src = pkg_root() / "agents"
        expected_count = sum(
            1
            for md_path in agents_src.glob("*.md")
            if md_path.name not in ("AGENTS.md", "CLAUDE.md")
        )

        count = _generate_agent_tomls(tmp_path)

        assert count == expected_count
        toml_files = sorted((tmp_path / "agents").glob("*.toml"))
        assert len(toml_files) == expected_count
        suffix = codex_discipline_suffix()
        for toml_path in toml_files:
            parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            # TOML keeps the trailing newline before the closing ''' delimiter.
            assert parsed["developer_instructions"].endswith(f"{suffix}\n")


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
        assert "activate_deps:" not in sous_chef
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
        assert "activate_deps:" not in prompt

    def test_sous_chef_not_in_fleet_dispatch_prompt(self) -> None:
        from autoskillit.cli._prompts import _build_fleet_dispatch_prompt, _read_full_sous_chef

        sous_chef = _read_full_sous_chef()
        assert sous_chef, "_read_full_sous_chef must return non-empty content"
        prompt = _build_fleet_dispatch_prompt("mcp__autoskillit__")
        assert sous_chef[:80] not in prompt
        assert "name: sous-chef" not in prompt


@pytest.mark.anyio
async def test_llm_triage_prompt_uses_projected_skill_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from autoskillit._llm_triage import triage_staleness
    from autoskillit.core import SubprocessResult, TerminationReason
    from autoskillit.recipe import StaleItem

    run = AsyncMock(
        return_value=SubprocessResult(
            returncode=0,
            stdout="not-json",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=1,
        )
    )
    monkeypatch.setattr("autoskillit._llm_triage.run_managed_async", run)
    await triage_staleness(
        [
            StaleItem(
                skill="open-kitchen",
                reason="hash_mismatch",
                stored_value="old",
                current_value="new",
            )
        ],
        backend=ClaudeCodeBackend(),
    )
    prompt = run.await_args.kwargs["cmd"][2]
    assert "name: open-kitchen" in prompt
    assert "uses_capabilities:" not in prompt
    assert "execution_role:" not in prompt
    assert "activate_deps:" not in prompt
