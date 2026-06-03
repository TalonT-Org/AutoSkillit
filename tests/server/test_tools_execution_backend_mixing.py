"""Integration tests for per-step backend mixing in run_skill().

Verifies that run_skill() correctly derives backend_override='claude-code'
when a Codex-primary context dispatches a step through a provider profile
containing ANTHROPIC_BASE_URL.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_codex_backend_minimax_profile_with_anthropic_base_url_derives_override(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Codex backend + minimax profile with ANTHROPIC_BASE_URL
    -> backend_override='claude-code'."""
    from unittest.mock import MagicMock

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.capabilities.anthropic_provider_capable = False
    tool_ctx_kitchen_open.backend = fake_backend
    tool_ctx_kitchen_open.session_skill_manager = None
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

    assert len(executor.calls) == 1
    assert captured.get("backend_override") == "claude-code"


@pytest.mark.anyio
async def test_anthropic_capable_backend_profile_without_base_url_no_override(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Anthropic-capable backend + profile without ANTHROPIC_BASE_URL -> no override."""
    from unittest.mock import MagicMock

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.capabilities.anthropic_provider_capable = True
    tool_ctx_kitchen_open.backend = fake_backend
    tool_ctx_kitchen_open.session_skill_manager = None
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
    assert "backend_override" in captured
    assert captured["backend_override"] is None


@pytest.mark.anyio
async def test_skill_backend_requirement_triggers_incompatibility_gate(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Skill with backend_requirements=[claude-code] on a non-claude backend
    -> _is_backend_incompatible gate rejects the call (no silent override)."""
    import json
    from unittest.mock import MagicMock

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.name = "codex"
    fake_backend.capabilities.anthropic_provider_capable = False
    tool_ctx_kitchen_open.backend = fake_backend
    tool_ctx_kitchen_open.session_skill_manager = None

    mock_skill_info = MagicMock()
    mock_skill_info.backend_requirements = frozenset({"claude-code"})
    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = mock_skill_info
    tool_ctx_kitchen_open.skill_resolver = mock_resolver

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.resolve_target_skill",
        lambda cmd, resolver: ("/autoskillit:test-skill", "test-skill"),
    )

    result = await run_skill("/autoskillit:probe", str(tmp_path))
    data = json.loads(result)
    assert data.get("status") == "crashed"
    assert "requires backend" in data.get("error", "")


@pytest.mark.anyio
async def test_skill_without_backend_requirement_no_override(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Skill with empty backend_requirements on a non-claude backend -> no override."""
    from unittest.mock import MagicMock

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.name = "codex"
    fake_backend.capabilities.anthropic_provider_capable = False
    tool_ctx_kitchen_open.backend = fake_backend
    tool_ctx_kitchen_open.session_skill_manager = None

    mock_skill_info = MagicMock()
    mock_skill_info.backend_requirements = frozenset()
    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = mock_skill_info
    tool_ctx_kitchen_open.skill_resolver = mock_resolver

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.resolve_target_skill",
        lambda cmd, resolver: ("/autoskillit:test-skill", "test-skill"),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("backend_override") is None


@pytest.mark.anyio
async def test_backend_incompatibility_does_not_emit_override_log(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Skill-requirement incompatibility crashes (no override log emitted)."""
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
    tool_ctx_kitchen_open.session_skill_manager = None

    mock_skill_info = MagicMock()
    mock_skill_info.backend_requirements = frozenset({"claude-code"})
    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = mock_skill_info
    tool_ctx_kitchen_open.skill_resolver = mock_resolver

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.resolve_target_skill",
        lambda cmd, resolver: ("/autoskillit:test-skill", "test-skill"),
    )

    with structlog.testing.capture_logs() as log_list:
        await run_skill("/autoskillit:probe", str(tmp_path))

    override_logs = [
        entry for entry in log_list if entry.get("event") == "backend_override_activated"
    ]
    assert len(override_logs) == 0


@pytest.mark.anyio
async def test_backend_override_emits_structured_log_provider_profile(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Provider-profile override fires -> logger.info with reason='provider_profile'."""
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
    tool_ctx_kitchen_open.session_skill_manager = None

    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = None
    tool_ctx_kitchen_open.skill_resolver = mock_resolver

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
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.resolve_target_skill",
        lambda cmd, resolver: ("/autoskillit:probe", "probe"),
    )

    with structlog.testing.capture_logs() as log_list:
        await run_skill("/autoskillit:probe", str(tmp_path))

    override_logs = [
        entry for entry in log_list if entry.get("event") == "backend_override_activated"
    ]
    assert len(override_logs) == 1
    log = override_logs[0]
    assert log["reason"] == "provider_profile"
    assert log["original_backend"] == "codex"


@pytest.mark.anyio
async def test_backend_incompatibility_gate_rejects_before_init_session(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """When skill requires claude-code and effective backend is codex,
    the incompatibility gate rejects before init_session is called."""
    import json
    from unittest.mock import MagicMock

    from autoskillit.core.types._type_protocols_backend import CodingAgentBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.name = "codex"
    fake_backend.capabilities.anthropic_provider_capable = False
    tool_ctx_kitchen_open.backend = fake_backend

    mock_ssm = MagicMock()
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm

    mock_skill_info = MagicMock()
    mock_skill_info.backend_requirements = frozenset({"claude-code"})
    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = mock_skill_info
    tool_ctx_kitchen_open.skill_resolver = mock_resolver

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.resolve_target_skill",
        lambda cmd, resolver: ("/autoskillit:test-skill", "test-skill"),
    )

    result = await run_skill("/autoskillit:test-skill", str(tmp_path))
    data = json.loads(result)
    assert data.get("status") == "crashed"
    mock_ssm.init_session.assert_not_called()


@pytest.mark.anyio
async def test_provider_override_threads_effective_backend_to_init_session(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """When provider_override triggers backend_override, init_session receives
    the overridden backend (ClaudeCodeBackend), not the orchestrator backend (Codex)."""
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
    mock_ssm.init_session.return_value = fake_validated
    tool_ctx_kitchen_open.session_skill_manager = mock_ssm

    mock_skill_info = MagicMock()
    mock_skill_info.backend_requirements = frozenset()
    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = mock_skill_info
    tool_ctx_kitchen_open.skill_resolver = mock_resolver

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
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.resolve_target_skill",
        lambda cmd, resolver: ("/autoskillit:test-skill", "test-skill"),
    )

    await run_skill("/autoskillit:test-skill", str(tmp_path))

    mock_ssm.init_session.assert_called_once()
    init_backend = mock_ssm.init_session.call_args.kwargs.get("backend")
    assert init_backend is not fake_backend, (
        "init_session must NOT receive the orchestrator backend"
    )
    assert init_backend.name == "claude-code"


@pytest.mark.anyio
async def test_no_skill_requires_claude_logic() -> None:
    source = Path("src/autoskillit/server/tools/tools_execution.py").read_text()
    assert "_skill_requires_claude" not in source
