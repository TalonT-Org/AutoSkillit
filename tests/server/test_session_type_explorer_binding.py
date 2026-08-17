"""Tests for terminal-explorer (terminal MCP allowlist) visibility behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import EXPLORATION_TOOLS

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.mark.parametrize(
    "role",
    (
        "shared-explorer-session",
        "semantic-code-navigator",
        "repository-impact-profiler",
    ),
)
@pytest.mark.anyio
async def test_unverified_explorer_environment_reveals_no_broker_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    role: str,
) -> None:
    from autoskillit.server import _apply_session_type_visibility, mcp

    monkeypatch.setenv("AUTOSKILLIT_EXPLORATION_CAPABILITY", "explore_test_capability")
    monkeypatch.setenv("AUTOSKILLIT_EXPLORATION_ROLE", role)
    monkeypatch.setenv("AUTOSKILLIT_EXPLORATION_SESSION_ID", "headless-test")
    monkeypatch.setenv(
        "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH",
        str(tmp_path / ".autoskillit-exploration-authority.json"),
    )
    _apply_session_type_visibility()

    visible = {tool.name for tool in await mcp.list_tools()}
    assert not (visible & EXPLORATION_TOOLS)


@pytest.mark.anyio
async def test_verified_explorer_authority_reveals_only_broker_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from autoskillit.core import RepositoryIdentity, RepositorySnapshot, SessionType
    from autoskillit.pipeline import OwnerBoundExplorationContextStore
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server import _lifespan, mcp

    project = tmp_path / "project"
    cwd = project / "worktree"
    authority_home = tmp_path / "session"
    for path in (cwd, authority_home):
        path.mkdir(parents=True)
    service = MagicMock()
    service.capture_snapshot.return_value = RepositorySnapshot(
        RepositoryIdentity("test-repository", "test-revision"),
        tree_digest="test-tree",
        collector_manifest_digest="test-manifest",
    )
    parent: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project,
        service=service,
    )
    binding = parent.bind_launch(
        owner_id="uid:1000",
        role="semantic-code-navigator",
        session_id="session-a",
        cwd=cwd,
        repository_root=project,
        source_identity="bundled:semantic-code-navigator:digest",
        authority_home=authority_home,
    )
    for key, value in binding.provider_extras().items():
        monkeypatch.setenv(key, value)
    gate = DefaultGateState()
    child: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project
    )
    monkeypatch.chdir(cwd)
    ordinary_boot = AsyncMock()
    monkeypatch.setitem(_lifespan._LIFESPAN_BOOT_REGISTRY, SessionType.SKILL, ordinary_boot)
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    boot_ctx = SimpleNamespace(exploration_context_store=child, gate=gate, backend=None)
    monkeypatch.setattr(_lifespan, "_get_ctx_or_none", lambda: boot_ctx)
    monkeypatch.setattr(_lifespan, "run_startup_fix_required_coverage_check", lambda: None)
    monkeypatch.setattr(_lifespan, "write_readiness_sentinel", lambda: None)
    monkeypatch.setattr(_lifespan, "cleanup_readiness_sentinel", lambda: None)
    monkeypatch.setattr(_lifespan, "_finalize_recorder", lambda: None)

    def _discard_background(coroutine, *, label):
        del label
        # Close the coroutine so its frame is released without ever being driven,
        # then return an already-completed future so the test never owns a live
        # task that could resurface `Task was destroyed but it is pending` warnings.
        coroutine.close()
        future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
        future.set_result(None)
        return future

    monkeypatch.setattr(_lifespan, "create_background_task", _discard_background)

    async with _lifespan._autoskillit_lifespan(SimpleNamespace()):
        ordinary_boot.assert_not_awaited()
        assert gate.enabled is True
        assert {tool.name for tool in await mcp.list_tools()} == EXPLORATION_TOOLS
        assert list(await mcp.list_resources()) == []
        assert list(await mcp.list_resource_templates()) == []


@pytest.mark.anyio
async def test_missing_explorer_authority_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from autoskillit.core import SessionType
    from autoskillit.pipeline import OwnerBoundExplorationContextStore
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server import mcp
    from autoskillit.server._lifespan import (
        _LIFESPAN_BOOT_REGISTRY,
        _run_lifespan_session_boot,
    )

    for name, value in {
        "AUTOSKILLIT_EXPLORATION_CAPABILITY": "not-a-capability",
        "AUTOSKILLIT_EXPLORATION_ROLE": "semantic-code-navigator",
        "AUTOSKILLIT_EXPLORATION_SESSION_ID": "session-a",
        "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH": str(tmp_path / "missing-authority.json"),
    }.items():
        monkeypatch.setenv(name, value)
    gate = DefaultGateState()
    ordinary_boot = AsyncMock()
    monkeypatch.setitem(_LIFESPAN_BOOT_REGISTRY, SessionType.SKILL, ordinary_boot)
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")

    await _run_lifespan_session_boot(
        SimpleNamespace(
            exploration_context_store=OwnerBoundExplorationContextStore[object](),
            gate=gate,
        )
    )

    ordinary_boot.assert_awaited_once()
    assert gate.enabled is False
    assert not ({tool.name for tool in await mcp.list_tools()} & EXPLORATION_TOOLS)


@pytest.mark.anyio
async def test_parent_without_explorer_binding_keeps_free_range_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core import FREE_RANGE_TOOLS
    from autoskillit.server import _apply_session_type_visibility, mcp

    for name in (
        "AUTOSKILLIT_EXPLORATION_CAPABILITY",
        "AUTOSKILLIT_EXPLORATION_ROLE",
        "AUTOSKILLIT_EXPLORATION_SESSION_ID",
        "AUTOSKILLIT_EXPLORATION_AUTHORITY_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    _apply_session_type_visibility()

    assert FREE_RANGE_TOOLS <= {tool.name for tool in await mcp.list_tools()}
