"""Fallback specialist registration for enable_exploration's ineligible-session
paths (#4684 Fix E / AC5, Step 1.15).

Issue #4684 AC5 requires a fallback agent for when enable_exploration returns
ineligible_session_type or exploration_store_unavailable. The codebase's agent
discipline is specialist-only (agents/AGENTS.md; docs/execution/explorer-agents.md
mandates specialist terminal leaves) — registering a general-purpose.md would
contradict that discipline. The architecturally consistent fix is a third
specialist, pluginless-explorer, restricted to Read/Grep/Glob.

Deliberately does NOT add pluginless-explorer to BUNDLED_EXPLORER_ROLES: that
set drives Codex's per-child terminal-binding machinery
(execution/backends/_codex/explorer_projection.py, server/_explorer_projection.py's
strict-equality discovery check) for the two broker-bound MCP-tool explorers.
pluginless-explorer is a Claude-only, tool-restricted fallback with an
unrelated tool surface — it is not part of that binding contract.
"""

from __future__ import annotations

import pytest

from autoskillit.core import pkg_root
from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_pluginless_explorer_specialist_registered() -> None:
    agent_path = pkg_root() / "agents" / "pluginless-explorer.md"
    assert agent_path.exists(), f"pluginless-explorer specialist missing at {agent_path}"

    text = agent_path.read_text()
    assert text.startswith("---\n"), "pluginless-explorer.md must have YAML frontmatter"
    parts = text.split("---", 2)
    assert len(parts) == 3, "pluginless-explorer.md must have YAML frontmatter"
    frontmatter = load_yaml(parts[1])

    assert frontmatter["name"] == "pluginless-explorer"
    assert set(frontmatter["tools"]) == {"Read", "Grep", "Glob"}, (
        "Fallback specialist must be a restricted terminal leaf — no Write/Edit/Bash"
    )


def test_no_general_purpose_specialist_exists() -> None:
    """The codebase does not permit generic agents — no general-purpose.md."""
    agent_path = pkg_root() / "agents" / "general-purpose.md"
    assert not agent_path.exists(), (
        "general-purpose agent contradicts the specialist-only discipline "
        "(agents/AGENTS.md, docs/execution/explorer-agents.md)"
    )


def test_pluginless_explorer_is_not_a_bundled_explorer_role() -> None:
    """pluginless-explorer must stay out of Codex's broker-bound terminal-binding
    set — it has an unrelated (Read/Grep/Glob, not MCP broker) tool surface."""
    from autoskillit.core import BUNDLED_EXPLORER_ROLES

    assert "pluginless-explorer" not in BUNDLED_EXPLORER_ROLES


def test_pluginless_explorer_discovered_by_agents_directory_walk() -> None:
    """Reflective enumeration mirrors
    tests/execution/test_launch_force_inactive_call_path_reflective.py:
    discover agents by walking agents/ rather than a hand-maintained name."""
    agents_dir = pkg_root() / "agents"
    discovered = {
        p.stem for p in agents_dir.glob("*.md") if p.name not in ("CLAUDE.md", "AGENTS.md")
    }
    assert "pluginless-explorer" in discovered
