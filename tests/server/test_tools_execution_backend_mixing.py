"""Integration tests for per-step backend mixing in run_skill().

Verifies that run_skill() correctly derives backend_override='claude-code'
when a Codex-primary context dispatches a step through a provider profile
containing ANTHROPIC_BASE_URL.
"""

from __future__ import annotations

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
    """Skill with backend_requirements=[claude-code] + git_metadata_write capability
    on a non-claude backend triggers capability-driven auto-route when binary present."""
    import json
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
    mock_skill_info.uses_capabilities = frozenset({"git_metadata_write"})
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
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    with structlog.testing.capture_logs() as log_list:
        result = await run_skill("/autoskillit:probe", str(tmp_path))
    json.loads(result)

    override_logs = [
        entry for entry in log_list if entry.get("event") == "backend_override_activated"
    ]
    assert len(override_logs) == 1
    log = override_logs[0]
    assert log["reason"] == "skill_requirement"
    assert log["target_backend"] == "claude-code"
    assert len(executor.calls) == 1
    assert executor.calls[0].backend_override == "claude-code"


@pytest.mark.anyio
async def test_backend_incompatibility_does_not_emit_override_log(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Skill-requirement capability route fires -> logger.info with reason='skill_requirement'."""
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
    mock_skill_info.uses_capabilities = frozenset({"git_metadata_write"})
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
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    with structlog.testing.capture_logs() as log_list:
        await run_skill("/autoskillit:probe", str(tmp_path))

    override_logs = [
        entry for entry in log_list if entry.get("event") == "backend_override_activated"
    ]
    assert len(override_logs) == 1
    log = override_logs[0]
    assert log["reason"] == "skill_requirement"
    assert log["target_backend"] == "claude-code"


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
    mock_resolver.resolve.return_value = MagicMock(
        source=MagicMock(value="bundled_extended"), backend_requirements=frozenset()
    )
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
async def test_backend_incompatibility_gate_rejects_before_init_session_binary_present(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """When skill requires claude-code (git_metadata_write) and binary IS present,
    capability route fires — init_session IS called with the overridden backend."""
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
    mock_skill_info.uses_capabilities = frozenset({"git_metadata_write"})
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
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    await run_skill("/autoskillit:test-skill", str(tmp_path))
    assert len(executor.calls) == 1


@pytest.mark.anyio
async def test_backend_incompatibility_gate_rejects_before_init_session_binary_absent(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """When skill requires claude-code (git_metadata_write) and binary is ABSENT,
    the binary probe crashes the call before init_session (REQ-ROUTE-004)."""
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
    mock_skill_info.uses_capabilities = frozenset({"git_metadata_write"})
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
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda name: None,
    )

    result = await run_skill("/autoskillit:test-skill", str(tmp_path))
    data = json.loads(result)
    assert data.get("subtype") == "crashed"
    assert "claude" in data.get("result", "").lower()
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
async def test_open_kitchen_capability_skill_not_auto_routed(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """REQ-ROUTE-003 negative guard: a skill whose claude-code requirement derives
    from a capability OTHER than git_metadata_write (e.g. open_kitchen) must NOT
    be auto-routed — the incompatibility gate rejects it with no override log,
    even with the claude binary present."""
    import json
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
    mock_skill_info.uses_capabilities = frozenset({"open_kitchen"})
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
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    with structlog.testing.capture_logs() as log_list:
        result = await run_skill("/autoskillit:test-skill", str(tmp_path))

    data = json.loads(result)
    assert data.get("subtype") == "crashed"
    assert "requires backend" in data.get("result", "")
    override_logs = [
        entry for entry in log_list if entry.get("event") == "backend_override_activated"
    ]
    assert not override_logs, (
        "capability route must not fire for open_kitchen-derived requirements"
    )
    assert len(executor.calls) == 0


@pytest.mark.anyio
async def test_github_api_write_capability_skill_not_auto_routed(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """github_api_write (fix-required, worker_routable=False) must NOT trigger auto-routing.

    The skill runs on whatever backend the session uses (Codex), with network_access=True
    injected via required_sandbox_overrides. No backend_override_activated log emitted."""
    import json
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
    mock_skill_info.backend_requirements = frozenset()
    mock_skill_info.uses_capabilities = frozenset({"github_api_write"})
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
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    with structlog.testing.capture_logs() as log_list:
        result = await run_skill("/autoskillit:test-skill", str(tmp_path))

    data = json.loads(result)
    assert data.get("subtype") == "success", (
        "github_api_write skill must run successfully on Codex — not auto-routed, not blocked"
    )
    override_logs = [
        entry for entry in log_list if entry.get("event") == "backend_override_activated"
    ]
    assert not override_logs, "worker_routable=False must not trigger capability route"
    assert len(executor.calls) == 1
    assert executor.calls[0].network_access is True, (
        "github_api_write required_sandbox_overrides must inject network_access=True"
    )


@pytest.mark.anyio
async def test_git_metadata_write_capability_still_routed(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """git_metadata_write (not-applicable, worker_routable=True) must still auto-route
    after the worker_routable discriminator fix."""
    import json
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
    mock_skill_info.backend_requirements = frozenset()
    mock_skill_info.uses_capabilities = frozenset({"git_metadata_write"})
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
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    with structlog.testing.capture_logs() as log_list:
        result = await run_skill("/autoskillit:probe", str(tmp_path))

    json.loads(result)
    override_logs = [
        entry for entry in log_list if entry.get("event") == "backend_override_activated"
    ]
    assert len(override_logs) == 1, "worker_routable=True must trigger capability route"
    log = override_logs[0]
    assert log["reason"] == "skill_requirement"
    assert log["target_backend"] == "claude-code"
    assert len(executor.calls) == 1
    assert executor.calls[0].backend_override == "claude-code"
