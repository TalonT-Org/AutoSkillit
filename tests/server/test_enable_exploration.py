"""Test coverage for the enable_exploration gate tool (REQ-93)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autoskillit.core import RepositoryIdentity, RepositorySnapshot, SessionType
from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _snapshot_service() -> MagicMock:
    service = MagicMock()
    service.capture_snapshot.side_effect = lambda root: RepositorySnapshot(
        RepositoryIdentity("test-repo", "test-rev", worktree_path=str(root.resolve())),
        tree_digest="test-tree",
        collector_manifest_digest="test-manifest",
    )
    return service


@pytest.mark.asyncio
async def test_enable_exploration_rejects_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ORCHESTRATOR sessions cannot establish exploration authority."""
    from autoskillit.server.tools.tools_exploration import enable_exploration

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.ORCHESTRATOR,
    )
    result = json.loads(await enable_exploration())
    assert result["status"] == "error"
    assert result["code"] == "session_type_ineligible"


@pytest.mark.asyncio
async def test_enable_exploration_rejects_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FLEET sessions cannot establish exploration authority."""
    from autoskillit.server.tools.tools_exploration import enable_exploration

    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.FLEET,
    )
    result = json.loads(await enable_exploration())
    assert result["status"] == "error"
    assert result["code"] == "session_type_ineligible"


@pytest.mark.asyncio
async def test_enable_exploration_succeeds_for_skill_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """SKILL session with a valid store can establish exploration authority."""
    from autoskillit.server.tools.tools_exploration import enable_exploration

    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tmp_path,
        service=_snapshot_service(),
    )
    ctx = SimpleNamespace(
        exploration_context_store=store,
        session_id="test-session",
        gate=MagicMock(),
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._resolve_session_type",
        lambda: SessionType.SKILL,
    )
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_exploration._get_ctx",
        lambda: ctx,
    )
    monkeypatch.chdir(tmp_path)

    result = json.loads(await enable_exploration())
    assert result["status"] == "ok"
    assert result["exploration_enabled"] is True
