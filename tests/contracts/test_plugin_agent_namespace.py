"""Production projection uses the Claude plugin namespace for rendered agents."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import (
    DIRECT_PREFIX,
    PluginLoadMode,
    load_agent_definitions,
    load_bundled_agent_definitions,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.workspace import project_default_plugin_authority
from tests.contracts._projection_helpers import session_catalog

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


@pytest.mark.parametrize("registered", [False, True])
def test_projection_agents_use_plugin_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: bool
) -> None:
    """The artifact manifests determine the namespace with or without a host registry."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    registry = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    if registered:
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps({"plugins": {"autoskillit@autoskillit-local": [{"installPath": "/fake"}]}}),
            encoding="utf-8",
        )
    assert registry.is_file() is registered

    authority = project_default_plugin_authority(
        cwd=tmp_path, base_branch="main", catalog=session_catalog()
    )
    with authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(), load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR
    ) as binding:
        assert binding.plugin_dir is not None
        rendered = load_agent_definitions(binding.plugin_dir / "agents")
    assert binding.closed

    prefix = "mcp__plugin_autoskillit_autoskillit__"
    projected_tools = {
        tool for definition in rendered for tool in definition.tools if tool.startswith("mcp__")
    }
    assert projected_tools
    assert all(tool.startswith(prefix) for tool in projected_tools)
    projected_short_names = {tool.removeprefix(prefix) for tool in projected_tools}
    bundled_short_names = {
        tool.removeprefix(DIRECT_PREFIX)
        for definition in load_bundled_agent_definitions()
        for tool in definition.tools
        if tool.startswith("mcp__")
    }
    assert projected_short_names == bundled_short_names
