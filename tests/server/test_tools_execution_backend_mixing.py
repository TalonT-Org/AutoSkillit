"""Integration tests for explicit backend authority in ``run_skill()``."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from autoskillit.core import SkillExecutionRole, SkillSource
from autoskillit.server.tools.tools_execution import run_skill
from autoskillit.workspace.skills import EffectiveSkillInvocation, SkillInfo

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _install_skill_invocation(
    tool_ctx: Any,
    *,
    name: str,
    capabilities: frozenset[str] = frozenset(),
) -> MagicMock:
    """Install a closure-aware resolver double for one effective skill."""
    project_root = Path(tool_ctx.project_dir).resolve()
    skill_path = project_root / ".test-skills" / name / "SKILL.md"
    root = SkillInfo(
        name=name,
        source=SkillSource.BUNDLED_EXTENDED,
        path=skill_path,
        uses_capabilities=capabilities,
        canonical_content=(
            f"---\nname: {name}\ndescription: Test skill\n"
            f"uses_capabilities: {sorted(capabilities)!r}\n---\n# Test skill\n"
        ),
    )
    invocation = EffectiveSkillInvocation(
        root=root,
        closure=(root,),
        capability_union=capabilities,
        project_root=project_root,
        execution_role=SkillExecutionRole.SESSION,
    )
    resolver = MagicMock()
    resolver.resolve.return_value = root
    resolver.resolve_invocation.return_value = invocation
    tool_ctx.skill_resolver = resolver
    return resolver


@pytest.mark.anyio
async def test_provider_profile_cannot_override_global_backend_authority(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Provider metadata cannot change a Codex global backend authority."""
    from unittest.mock import MagicMock

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.name = "codex"
    fake_backend.capabilities.anthropic_provider_capable = False
    tool_ctx_kitchen_open.backend = fake_backend
    _install_skill_invocation(tool_ctx_kitchen_open, name="probe")
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
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

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert not executor.calls
    assert "backend_override" not in captured


@pytest.mark.anyio
async def test_global_backend_authority_is_explicit_without_provider_override(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """The configured global backend is carried as typed launch authority."""
    from autoskillit.execution.backends import get_backend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    tool_ctx_kitchen_open.backend = get_backend("claude-code")
    _install_skill_invocation(tool_ctx_kitchen_open, name="probe")
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: (
            "minimax",
            {"BASE_URL": "https://api.minimax.chat/v1"},
        ),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert len(executor.calls) == 1
    authority = captured["backend_authority"]
    assert authority.backend == "claude-code"
    assert authority.kind.value == "global"


@pytest.mark.anyio
async def test_provider_profile_does_not_emit_backend_authority_log(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Provider selection is not a backend authority source."""
    from unittest.mock import MagicMock

    import structlog

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.name = "codex"
    fake_backend.capabilities.anthropic_provider_capable = False
    tool_ctx_kitchen_open.backend = fake_backend
    _install_skill_invocation(tool_ctx_kitchen_open, name="probe")

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
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

    with structlog.testing.capture_logs() as log_list:
        await run_skill("/autoskillit:probe", str(tmp_path))

    override_logs = [
        entry for entry in log_list if entry.get("event") == "backend_override_activated"
    ]
    assert not override_logs
    assert not executor.calls


@pytest.mark.anyio
async def test_provider_profile_preserves_authoritative_backend_for_materialization(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Provider metadata cannot alter the materialization backend."""
    from unittest.mock import MagicMock

    from autoskillit.core import ValidatedAddDir
    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.name = "codex"
    fake_backend.capabilities.anthropic_provider_capable = False
    tool_ctx_kitchen_open.backend = fake_backend

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    skill_md = session_dir / ".claude" / "skills" / "test-skill" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("name: test-skill\n")

    fake_validated = ValidatedAddDir(path=str(session_dir))
    mock_ssm = MagicMock()
    mock_ssm.materialize_invocation.return_value = fake_validated
    mock_ssm.validate_session_exists.return_value = True
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm

    _install_skill_invocation(tool_ctx_kitchen_open, name="test-skill")

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
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

    await run_skill("/autoskillit:test-skill", str(tmp_path))

    mock_ssm.materialize_invocation.assert_called_once()
    projection_context = mock_ssm.materialize_invocation.call_args.args[2]
    assert projection_context.backend is not fake_backend
    assert projection_context.backend.name == "codex"
