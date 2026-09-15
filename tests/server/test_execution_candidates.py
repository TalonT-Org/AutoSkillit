"""Runner-boundary coverage for configured execution candidates."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import autoskillit.server as server
from autoskillit.core import SkillExecutionRole, SkillSource
from autoskillit.server.tools.tools_execution import run_skill
from autoskillit.workspace.skills import EffectiveSkillInvocation, SkillInfo

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _install_invocation(
    tool_ctx: Any,
    *,
    tmp_path: Path,
    capabilities: frozenset[str],
) -> None:
    root = SkillInfo(
        name="candidate-probe",
        source=SkillSource.BUNDLED_EXTENDED,
        path=tmp_path / "candidate-probe" / "SKILL.md",
        uses_capabilities=capabilities,
        canonical_content=(
            "---\nname: candidate-probe\ndescription: Candidate probe.\n"
            f"uses_capabilities: {sorted(capabilities)!r}\n"
            "execution_role: session\n---\n# Candidate probe\n"
        ),
    )
    invocation = EffectiveSkillInvocation(
        root=root,
        closure=(root,),
        capability_union=capabilities,
        project_root=Path(tool_ctx.project_dir).resolve(),
        execution_role=SkillExecutionRole.SESSION,
    )
    resolver = MagicMock()
    resolver.resolve.return_value = root
    resolver.resolve_invocation.return_value = invocation
    tool_ctx.skill_resolver = resolver


def _configure_candidate_backends(tool_ctx: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from autoskillit.execution.backends import get_backend
    from autoskillit.workspace import DefaultSessionSkillManager, SkillsDirectoryProvider

    parent_backend = get_backend("codex")
    tool_ctx.backend = parent_backend
    tool_ctx.session_skill_manager = DefaultSessionSkillManager(
        SkillsDirectoryProvider(),
        ephemeral_root=Path(tool_ctx.temp_dir) / "ephemeral-sessions",
        persistent_roots={"codex": Path(tool_ctx.temp_dir) / "persistent-sessions"},
    )
    monkeypatch.setattr(
        tool_ctx.launch_resolver,
        "backend_for_authority",
        lambda authority: get_backend(authority.backend),
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution._run_skill_prepare.shutil.which",
        lambda binary: f"/test-bin/{binary}",
    )


@pytest.mark.anyio
async def test_codex_parent_skips_incompatible_primary_before_runner_and_launches_claude_candidate(
    tool_ctx_kitchen_open,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidate compatibility is evaluated against the worker backend, not its parent."""
    from autoskillit.config import AgentBackendConfig, ExecutionCandidateSpec
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.config.agent_backend = AgentBackendConfig(backend="codex")
    tool_ctx_kitchen_open.config.features["providers"] = True
    tool_ctx_kitchen_open.config.providers.execution_candidates = [
        ExecutionCandidateSpec(backend="claude-code"),
    ]
    _configure_candidate_backends(tool_ctx_kitchen_open, monkeypatch)
    _install_invocation(
        tool_ctx_kitchen_open,
        tmp_path=tmp_path,
        capabilities=frozenset({"open_kitchen"}),
    )
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)

    response = json.loads(await run_skill("/autoskillit:candidate-probe", str(tmp_path)))

    assert response["success"] is True
    assert len(executor.calls) == 1
    assert executor.calls[0].backend_authority is not None
    assert executor.calls[0].backend_authority.backend == "claude-code"


@pytest.mark.anyio
async def test_all_incompatible_candidates_exhaust_before_runner_with_ordered_reasons(
    tool_ctx_kitchen_open,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-spawn rejections are ordered evidence, never fake child executions."""
    from autoskillit.config import AgentBackendConfig, ExecutionCandidateSpec
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.config.agent_backend = AgentBackendConfig(backend="codex")
    tool_ctx_kitchen_open.config.features["providers"] = True
    tool_ctx_kitchen_open.config.providers.execution_candidates = [
        ExecutionCandidateSpec(backend="codex"),
    ]
    _configure_candidate_backends(tool_ctx_kitchen_open, monkeypatch)
    _install_invocation(
        tool_ctx_kitchen_open,
        tmp_path=tmp_path,
        capabilities=frozenset({"open_kitchen"}),
    )
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)

    response = await run_skill("/autoskillit:candidate-probe", str(tmp_path))

    assert executor.calls == []
    payload = json.loads(response)
    assert payload["candidate_exhausted"] is True
    attempts = payload["execution_selection"]["attempts"]
    assert [attempt["ordinal"] for attempt in attempts] == [0, 1]
    assert all("open_kitchen" in attempt["rejection_reason"] for attempt in attempts)


@pytest.mark.anyio
async def test_codex_parent_rejects_the_rerouted_claude_worker_before_it_starts(
    tool_ctx_kitchen_open,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Claude quota window governs the Claude candidate, even under Codex."""
    from autoskillit.config import AgentBackendConfig, ExecutionCandidateSpec
    from autoskillit.core import RateLimitWindow
    from autoskillit.execution.quota._admission import QuotaAdmission
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.config.agent_backend = AgentBackendConfig(backend="codex")
    tool_ctx_kitchen_open.config.features["providers"] = True
    tool_ctx_kitchen_open.config.quota_guard.enabled = True
    tool_ctx_kitchen_open.config.providers.execution_candidates = [
        ExecutionCandidateSpec(backend="claude-code"),
    ]
    _configure_candidate_backends(tool_ctx_kitchen_open, monkeypatch)
    _install_invocation(
        tool_ctx_kitchen_open,
        tmp_path=tmp_path,
        capabilities=frozenset({"open_kitchen"}),
    )
    reset_epoch = 1_800_000_000
    admissions: list[dict[str, object]] = []

    async def quota_exhausted(**kwargs):
        admissions.append(kwargs)
        return QuotaAdmission(
            False,
            "quota_exhausted",
            rate_limit=RateLimitWindow(
                status="rejected",
                limit_type="seven_day",
                resets_at_epoch=reset_epoch,
            ),
        )

    original_run = executor.run

    async def reject_before_start(*args, **kwargs):
        await original_run(*args, **kwargs)
        return await kwargs["pre_spawn_admission"](
            SimpleNamespace(
                quota_identity={
                    "mode": "anthropic-oauth",
                    "provider": "anthropic",
                    "credential_scope": "anthropic-oauth:fixture",
                }
            )
        )

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution._run_skill_finalize.admit_quota",
        quota_exhausted,
    )
    monkeypatch.setattr(executor, "run", reject_before_start)
    monkeypatch.setattr(server, "_ctx", tool_ctx_kitchen_open)

    payload = json.loads(await run_skill("/autoskillit:candidate-probe", str(tmp_path)))

    assert payload["candidate_exhausted"] is True
    assert admissions[0]["provider"] == "anthropic"
    attempts = payload["execution_selection"]["attempts"]
    assert attempts[0]["admission_status"] == "incompatible"
    assert attempts[0]["rejection_reason"]
    assert attempts[1]["effective_backend"] == "claude-code"
    assert attempts[1]["parent_backend"] == "codex"
    assert attempts[1]["rejection_reason"] == "quota_exhausted"
    assert attempts[1]["rate_limit_resets_at_epoch"] == reset_epoch
