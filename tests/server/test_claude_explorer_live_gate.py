"""T14: Claude live conformance gate for session-scoped explorer authority.

Env-gated: runs only when AUTOSKILLIT_CLAUDE_EXPLORER_LIVE_GATE=1.
The non-env-gated tests exercise the production corridor (enable_exploration
gate tool) without requiring a real Claude subprocess.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autoskillit.core import BUNDLED_EXPLORER_ROLES, EXPLORATION_TOOLS

_LIVE_ENV = "AUTOSKILLIT_CLAUDE_EXPLORER_LIVE_GATE"
_skip_unless_live_gate = pytest.mark.skipif(
    os.environ.get(_LIVE_ENV) != "1",
    reason=f"Claude explorer live gate requires {_LIVE_ENV}=1",
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _snapshot_service() -> MagicMock:
    from autoskillit.core import RepositoryIdentity, RepositorySnapshot

    service = MagicMock()
    service.capture_snapshot.side_effect = lambda root: RepositorySnapshot(
        RepositoryIdentity("test", "rev", worktree_path=str(root.resolve())),
        tree_digest="tree",
        collector_manifest_digest="manifest",
    )
    return service


class TestClaudeExplorerProductionCorridor:
    """T14: exercise the enable_exploration gate tool — the production corridor."""

    @pytest.mark.asyncio
    async def test_enable_exploration_establishes_authority(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """The gate tool mints session-scoped authority and enables the tag."""
        from autoskillit.core import SessionType
        from autoskillit.pipeline.exploration_context import (
            OwnerBoundExplorationContextStore,
        )
        from autoskillit.server.tools.tools_exploration import enable_exploration

        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path,
            service=_snapshot_service(),
        )
        ctx = SimpleNamespace(
            exploration_context_store=store,
            session_id="claude-gate-test",
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

        capability = store.session_scoped_capability("claude-gate-test")
        assert capability is not None, "enable_exploration must mint a session-scoped capability"
        assert capability.startswith("explore_")

    @pytest.mark.asyncio
    async def test_enable_exploration_rejects_orchestrator(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ORCHESTRATOR sessions cannot establish authority — topology guard."""
        from autoskillit.core import SessionType
        from autoskillit.server.tools.tools_exploration import enable_exploration

        monkeypatch.setattr(
            "autoskillit.server.tools.tools_exploration._resolve_session_type",
            lambda: SessionType.ORCHESTRATOR,
        )
        result = json.loads(await enable_exploration())
        assert result["code"] == "session_type_ineligible"

    @pytest.mark.asyncio
    async def test_enable_exploration_rejects_fleet(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FLEET sessions cannot establish authority — topology guard."""
        from autoskillit.core import SessionType
        from autoskillit.server.tools.tools_exploration import enable_exploration

        monkeypatch.setattr(
            "autoskillit.server.tools.tools_exploration._resolve_session_type",
            lambda: SessionType.FLEET,
        )
        result = json.loads(await enable_exploration())
        assert result["code"] == "session_type_ineligible"


@_skip_unless_live_gate
@pytest.mark.smoke
def test_conformance_preamble_present_in_both_roles() -> None:
    """Both explorer role bodies carry the self-check preamble."""
    from autoskillit.core import load_agent_definition, pkg_root

    for role in sorted(BUNDLED_EXPLORER_ROLES):
        definition = load_agent_definition(pkg_root() / "agents" / f"{role}.md")
        assert "CONTRACT VIOLATION" in definition.body, (
            f"role {role} must carry the conformance preamble"
        )
        for tool in definition.tools:
            if tool.startswith("mcp__"):
                assert tool in definition.body, f"preamble must reference frontmatter tool {tool}"


@_skip_unless_live_gate
@pytest.mark.smoke
def test_explorer_tool_surface_exact() -> None:
    """The effective tool surface must be exactly the three broker tools."""
    from autoskillit.core import load_bundled_agent_definitions

    for definition in load_bundled_agent_definitions():
        if definition.name not in BUNDLED_EXPLORER_ROLES:
            continue
        tool_short_names = frozenset(
            tool.split("__")[-1] for tool in definition.tools if tool.startswith("mcp__")
        )
        assert tool_short_names == EXPLORATION_TOOLS, (
            f"role {definition.name} must declare exactly the three broker tools, "
            f"got {tool_short_names}"
        )
