"""T11: spawn-instant visibility follows provisioning intent for Claude sessions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import (
    EXPLORATION_TOOLS,
)
from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestSessionScopedVisibility:
    """T11: Claude session-scoped exploration visibility."""

    @pytest.fixture(autouse=True)
    def _reset_mcp_visibility(self) -> None:
        import sys

        if "autoskillit.server" not in sys.modules:
            return
        from autoskillit.core import ALL_VISIBILITY_TAGS
        from autoskillit.server import mcp

        mcp._transforms.clear()
        for tag in sorted(ALL_VISIBILITY_TAGS):
            mcp.disable(tags={tag})
        yield
        mcp._transforms.clear()

    @pytest.mark.asyncio
    async def test_default_skill_session_reveals_no_exploration_tools(self) -> None:
        """Ordinary SKILL session with no provisioning intent reveals nothing."""
        from autoskillit.server import mcp

        visible = {tool.name for tool in await mcp.list_tools()}
        assert not (visible & EXPLORATION_TOOLS), (
            f"default SKILL session must not reveal exploration tools, "
            f"found: {visible & EXPLORATION_TOOLS}"
        )

    @pytest.mark.asyncio
    async def test_session_scoped_authority_reveals_exploration_tools(
        self, tmp_path: Path, exploration_snapshot_service: MagicMock
    ) -> None:
        """After bind_session_scoped + tag enable, exploration tools are visible."""
        from autoskillit.server import mcp

        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path,
            service=exploration_snapshot_service,
        )
        store.bind_session_scoped(
            owner_id="uid:1000",
            session_id="test-session",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity="interactive:test",
        )
        mcp.enable(tags={"exploration"}, components={"tool"})

        visible = {tool.name for tool in await mcp.list_tools()}
        assert EXPLORATION_TOOLS <= visible, (
            f"after session-scoped bind + tag enable, exploration tools must be visible, "
            f"missing: {EXPLORATION_TOOLS - visible}"
        )

    @pytest.mark.asyncio
    async def test_orchestrator_session_reveals_nothing_even_with_intent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ORCHESTRATOR sessions must not reveal exploration tools regardless of intent."""
        from autoskillit.server import mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "ORCHESTRATOR")
        visible = {tool.name for tool in await mcp.list_tools()}
        assert not (visible & EXPLORATION_TOOLS), (
            "ORCHESTRATOR session must not reveal exploration tools by default"
        )

    @pytest.mark.asyncio
    async def test_fleet_session_reveals_nothing_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FLEET sessions without explicit tag enable reveal no exploration tools."""
        from autoskillit.server import mcp

        monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "FLEET")
        visible = {tool.name for tool in await mcp.list_tools()}
        assert not (visible & EXPLORATION_TOOLS), (
            f"FLEET session without tag enable must not reveal exploration tools, "
            f"found: {visible & EXPLORATION_TOOLS}"
        )
