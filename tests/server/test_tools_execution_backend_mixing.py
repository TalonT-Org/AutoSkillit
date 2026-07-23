"""Integration tests for per-step backend mixing in run_skill().

Verifies that run_skill() correctly derives backend_override='claude-code'
when a Codex-primary context dispatches a step through a provider profile
containing ANTHROPIC_BASE_URL.
"""

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

    assert len(executor.calls) == 1
    assert captured.get("backend_override") == "claude-code"


@pytest.mark.anyio
async def test_anthropic_capable_backend_profile_without_base_url_no_override(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Anthropic-capable backend + profile without ANTHROPIC_BASE_URL -> no override."""
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
    from autoskillit.execution.backends.codex import CodexBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.name = "codex"
    concrete_backend = CodexBackend()
    fake_backend.capabilities = concrete_backend.capabilities
    fake_backend.conventions = concrete_backend.conventions
    fake_backend.ensure_pre_launch.return_value = []
    tool_ctx_kitchen_open.backend = fake_backend

    _install_skill_invocation(
        tool_ctx_kitchen_open,
        name="probe",
        capabilities=frozenset({"git_metadata_write"}),
    )

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
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

    _install_skill_invocation(
        tool_ctx_kitchen_open,
        name="probe",
        capabilities=frozenset({"git_metadata_write"}),
    )

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
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

    _install_skill_invocation(
        tool_ctx_kitchen_open,
        name="test-skill",
        capabilities=frozenset({"git_metadata_write"}),
    )

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
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

    _install_skill_invocation(
        tool_ctx_kitchen_open,
        name="test-skill",
        capabilities=frozenset({"git_metadata_write"}),
    )

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda name: None,
    )

    result = await run_skill("/autoskillit:test-skill", str(tmp_path))
    data = json.loads(result)
    assert data.get("subtype") == "crashed"
    assert "claude" in data.get("result", "").lower()
    mock_ssm.materialize_invocation.assert_not_called()


@pytest.mark.anyio
async def test_provider_override_threads_effective_backend_to_materialization(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """Provider overrides thread the Claude backend into invocation materialization."""
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
    assert projection_context.backend is not fake_backend, (
        "Invocation materialization must NOT receive the orchestrator backend"
    )
    assert projection_context.backend.name == "claude-code"


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

    _install_skill_invocation(
        tool_ctx_kitchen_open,
        name="test-skill",
        capabilities=frozenset({"open_kitchen"}),
    )

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
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
    from autoskillit.execution.backends.codex import CodexBackend
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor
    fake_backend = MagicMock(spec=CodingAgentBackend)
    fake_backend.name = "codex"
    concrete_backend = CodexBackend()
    fake_backend.capabilities = concrete_backend.capabilities
    fake_backend.conventions = concrete_backend.conventions
    fake_backend.ensure_pre_launch.return_value = []
    tool_ctx_kitchen_open.backend = fake_backend
    _install_skill_invocation(
        tool_ctx_kitchen_open,
        name="test-skill",
        capabilities=frozenset({"github_api_write"}),
    )

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    with structlog.testing.capture_logs() as log_list:
        result = await run_skill("/autoskillit:test-skill", str(tmp_path))

    data = json.loads(result)
    assert data.get("subtype") == "success", (
        "github_api_write skill must run successfully on Codex — "
        f"not auto-routed, not blocked: {data}"
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
    _install_skill_invocation(
        tool_ctx_kitchen_open,
        name="test-skill",
        capabilities=frozenset({"git_metadata_write"}),
    )

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
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


@pytest.mark.parametrize("agent_cap", ["agent_subagent", "agent_model", "cross_skill_ref"])
@pytest.mark.anyio
async def test_agent_capability_skill_auto_routed(
    tool_ctx_kitchen_open, tmp_path, monkeypatch, agent_cap: str
) -> None:
    """Agent capabilities (agent_subagent, agent_model, cross_skill_ref) with
    worker_routable=True must trigger backend reroute on non-Anthropic backends."""
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
    _install_skill_invocation(
        tool_ctx_kitchen_open,
        name="test-skill",
        capabilities=frozenset({agent_cap}),
    )

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    with structlog.testing.capture_logs() as log_list:
        result = await run_skill("/autoskillit:test-skill", str(tmp_path))

    json.loads(result)
    override_logs = [
        entry for entry in log_list if entry.get("event") == "backend_override_activated"
    ]
    assert len(override_logs) == 1, f"{agent_cap} (worker_routable=True) must trigger route"
    log = override_logs[0]
    assert log["reason"] == "skill_requirement"
    assert log["target_backend"] == "claude-code"
    assert log.get("routing_capabilities") == [agent_cap], (
        f"routing_capabilities must include {agent_cap}"
    )
    assert len(executor.calls) == 1
    assert executor.calls[0].backend_override == "claude-code"


@pytest.mark.anyio
async def test_routing_log_includes_capability_set(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """The backend_override_activated log must include routing_capabilities field."""
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
    _install_skill_invocation(
        tool_ctx_kitchen_open,
        name="test-skill",
        capabilities=frozenset({"git_metadata_write"}),
    )

    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
    _feat = "autoskillit.server.tools.tools_execution.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("default", {}),
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_execution.shutil.which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )

    with structlog.testing.capture_logs() as log_list:
        result = await run_skill("/autoskillit:test-skill", str(tmp_path))

    json.loads(result)
    override_logs = [
        entry for entry in log_list if entry.get("event") == "backend_override_activated"
    ]
    assert len(override_logs) == 1
    log = override_logs[0]
    assert "routing_capabilities" in log, (
        "backend_override_activated log must include routing_capabilities field"
    )
    assert log["routing_capabilities"] == ["git_metadata_write"]
