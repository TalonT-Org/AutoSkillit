"""Discipline delivery channel matrix contract tests.

Session-type x backend parametrized tests asserting that each combination's
PRIMARY delivery channel is populated via the correct builder.

Since #4478's delivery-scoping remediation, the scope-discipline digest is a
change-authoring policy: absent by default on headless skill sessions, food
trucks, and resumes; present only when a caller opts in (skill contract
declares ``scope_discipline: true``, or the resume/skill-session builder is
called with ``include_scope_discipline=True``). Interactive TUI sessions keep
full coverage unconditionally — the task is unknown at launch.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from autoskillit.core import (
    CODEX_INTAKE_DISCIPLINE_DIGEST,
    CODEX_SCOPE_DISCIPLINE_DIGEST,
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


def _assert_interactive_scope_digest(backend, spec) -> None:
    """Assert the scope digest is present for Codex, absent for Claude.

    Interactive TUI coverage is the deliberate exception: the task is unknown at
    launch, so scope discipline is delivered unconditionally on this surface.
    """
    header = CODEX_SCOPE_DISCIPLINE_DIGEST.splitlines()[0]
    if isinstance(backend, ClaudeCodeBackend):
        assert not any(header in arg for arg in spec.cmd)
    else:
        assert any("developer_instructions=" in arg and header in arg for arg in spec.cmd)


def _assert_headless_scope_digest_absent(backend, spec) -> None:
    """Assert the scope digest is absent by default in the final prompt arg for both backends."""
    assert CODEX_SCOPE_DISCIPLINE_DIGEST not in spec.cmd[-1]


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

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_scope_digest_delivery(self, backend) -> None:
        spec = backend.build_interactive_cmd(
            system_prompt="Fleet discipline prompt",
            env_extras={SESSION_TYPE_ENV_VAR: SESSION_TYPE_FLEET},
        )
        _assert_interactive_scope_digest(backend, spec)


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

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_scope_digest_delivery(self, backend) -> None:
        spec = backend.build_interactive_cmd(
            system_prompt="Orchestrator discipline prompt",
        )
        _assert_interactive_scope_digest(backend, spec)


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

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_scope_digest_delivery(self, backend) -> None:
        """Orchestrators dispatch run_skill calls; they never author code changes."""
        spec = _build_orchestrator_spec(backend)
        _assert_headless_scope_digest_absent(backend, spec)


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

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_scope_digest_delivery(self, backend) -> None:
        """Default (no opt-in) skill sessions get no scope digest on either backend."""
        spec = backend.build_skill_session_cmd("/investigate foo", "/tmp")
        _assert_headless_scope_digest_absent(backend, spec)

    def test_scope_digest_delivered_when_contract_opts_in(self) -> None:
        from autoskillit.core import SkillSessionConfig

        codex_spec = CodexBackend().build_skill_session_cmd(
            "/implement-worktree-no-merge foo",
            "/tmp",
            include_scope_discipline=True,
        )
        assert CODEX_SCOPE_DISCIPLINE_DIGEST in codex_spec.cmd[-1]

        # Claude has no standalone kwarg for this (mirrors sandbox_mode/network_access,
        # both config-only) — the opt-in is expressed via SkillSessionConfig instead, and
        # Claude ignores it either way: prompts must stay byte-identical to today.
        claude_spec = ClaudeCodeBackend().build_skill_session_cmd(
            "/implement-worktree-no-merge foo",
            "/tmp",
            SkillSessionConfig(include_scope_discipline=True),
        )
        assert CODEX_SCOPE_DISCIPLINE_DIGEST not in "".join(claude_spec.cmd)


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

    @pytest.mark.parametrize(
        "backend",
        [ClaudeCodeBackend(), CodexBackend()],
        ids=["claude-code", "codex"],
    )
    def test_scope_digest_delivery(self, backend) -> None:
        """Default (no opt-in) resumes get no scope digest on either backend."""
        spec = backend.build_resume_cmd(
            resume_session_id="abc123",
            prompt="CALLER PROMPT MARKER",
        )
        _assert_headless_scope_digest_absent(backend, spec)

    def test_scope_digest_delivered_when_resume_opts_in(self) -> None:
        spec = CodexBackend().build_resume_cmd(
            resume_session_id="abc123",
            prompt="CALLER PROMPT MARKER",
            include_scope_discipline=True,
        )
        assert CODEX_SCOPE_DISCIPLINE_DIGEST in spec.cmd[-1]


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

    @pytest.mark.medium
    def test_no_bundled_agent_toml_carries_the_scope_digest(self, tmp_path) -> None:
        """Every bundled agent is an analyst; none authors code changes (#4478)."""
        scope_header = CODEX_SCOPE_DISCIPLINE_DIGEST.splitlines()[0]
        _generate_agent_tomls(tmp_path)
        toml_files = sorted((tmp_path / "agents").glob("*.toml"))
        assert toml_files, "expected at least one generated agent TOML"
        for toml_path in toml_files:
            parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            assert scope_header not in parsed["developer_instructions"]


class TestScopeDisciplineContractKeying:
    """The scope-discipline delivery decision is keyed off skill_contracts.yaml."""

    _EXPECTED_SCOPE_DISCIPLINE_SKILLS = frozenset(
        {
            "make-plan",
            "rectify",
            "implement-worktree",
            "implement-worktree-no-merge",
            "implement-experiment",
            "retry-worktree",
            "resolve-failures",
            "resolve-review",
            "resolve-merge-conflicts",
        }
    )

    def test_scope_discipline_skills_match_expected_set(self) -> None:
        from autoskillit.recipe import get_skill_contract, load_bundled_manifest

        manifest = load_bundled_manifest()
        got = {
            name
            for name in manifest["skills"]
            if (contract := get_skill_contract(name, manifest)) is not None
            and contract.scope_discipline
        }
        assert got == self._EXPECTED_SCOPE_DISCIPLINE_SKILLS

    def test_every_scope_verdict_producer_declares_scope_discipline(self) -> None:
        from autoskillit.recipe import get_skill_contract, load_bundled_manifest

        manifest = load_bundled_manifest()
        for name in manifest["skills"]:
            contract = get_skill_contract(name, manifest)
            if contract is None:
                continue
            produces_scope_verdict = any(out.name == "scope_verdict" for out in contract.outputs)
            if produces_scope_verdict:
                assert contract.scope_discipline, (
                    f"skill {name!r} declares a scope_verdict output but not "
                    "scope_discipline: true — it would emit a token the delivery "
                    "matrix never primes it to emit"
                )

    def test_no_skill_is_both_read_only_and_scope_discipline(self) -> None:
        from autoskillit.recipe import get_skill_contract, load_bundled_manifest

        manifest = load_bundled_manifest()
        for name in manifest["skills"]:
            contract = get_skill_contract(name, manifest)
            if contract is None:
                continue
            assert not (contract.read_only and contract.scope_discipline), (
                f"skill {name!r} is both read_only and scope_discipline — a read-only "
                "skill cannot author the code changes the scope digest governs"
            )


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
