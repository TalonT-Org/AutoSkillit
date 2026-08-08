"""T14: Claude live conformance gate for session-scoped explorer authority.

Env-gated: runs only when AUTOSKILLIT_CLAUDE_EXPLORER_LIVE_GATE=1.
"""

from __future__ import annotations

import os

import pytest

from autoskillit.core import BUNDLED_EXPLORER_ROLES, EXPLORATION_TOOLS

_LIVE_ENV = "AUTOSKILLIT_CLAUDE_EXPLORER_LIVE_GATE"
_skip_unless_live_gate = pytest.mark.skipif(
    os.environ.get(_LIVE_ENV) != "1",
    reason=f"Claude explorer live gate requires {_LIVE_ENV}=1",
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@_skip_unless_live_gate
@pytest.mark.smoke
def test_session_scoped_explorer_authority_round_trip() -> None:
    """Real Claude session-scoped authority: bind, enable, and verify broker tools.

    This test exercises the production corridor — the binding is derived from
    enable_exploration (the interactive gate tool), not passed in by the test.
    """
    from pathlib import Path
    from unittest.mock import MagicMock

    from autoskillit.core import RepositoryIdentity, RepositorySnapshot
    from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore

    project_root = Path.cwd()
    service = MagicMock()
    service.capture_snapshot.side_effect = lambda root: RepositorySnapshot(
        RepositoryIdentity("test", "rev", worktree_path=str(root.resolve())),
        tree_digest="tree",
        collector_manifest_digest="manifest",
    )
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_root,
        service=service,
    )

    capability = store.bind_session_scoped(
        owner_id=f"uid:{os.getuid()}",
        session_id="claude-live-gate-test",
        cwd=project_root,
        repository_root=project_root,
        source_identity="live-gate:claude-test",
    )

    assert capability, "bind_session_scoped must return a non-empty capability"
    assert capability.startswith("explore_")

    found = store.session_scoped_capability("claude-live-gate-test")
    assert found == capability, "session_scoped_capability must find the active capability"

    assert store.session_scoped_capability("wrong-session") is None

    store.close()
    assert store.session_scoped_capability("claude-live-gate-test") is None


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
