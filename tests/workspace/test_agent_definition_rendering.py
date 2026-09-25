"""Agent definition rendering: prefix projection, validation, and pipeline coverage."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from autoskillit.core import (
    DIRECT_PREFIX,
    SkillContractError,
    SkillExecutionRole,
    SkillSource,
    load_agent_definitions,
    load_bundled_agent_definitions,
    pkg_root,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

_PLUGIN_NAMESPACE = "mcp__plugin_autoskillit_autoskillit__"


def _write_plugin_manifests(
    plugin_root: Path, *, plugin_name: str = "autoskillit", server_key: str = "autoskillit"
) -> None:
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": plugin_name}), encoding="utf-8"
    )
    (plugin_root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {server_key: {}}}), encoding="utf-8"
    )


def _copy_packaged_plugin(plugin_root: Path) -> Path:
    """Copy the packaged agents and namespace manifests into *plugin_root*."""
    agents_dir = plugin_root / "agents"
    agents_dir.mkdir(parents=True)
    for path in sorted((pkg_root() / "agents").iterdir()):
        if path.is_file():
            shutil.copy2(path, agents_dir / path.name)
    (plugin_root / ".claude-plugin").mkdir()
    shutil.copy2(
        pkg_root() / ".claude-plugin" / "plugin.json",
        plugin_root / ".claude-plugin" / "plugin.json",
    )
    shutil.copy2(pkg_root() / ".mcp.json", plugin_root / ".mcp.json")
    return agents_dir


def _session_catalog():
    from autoskillit.workspace.skills import (
        DefaultSkillResolver,
        EffectiveSkillCatalog,
        SkillCatalogEntry,
    )

    source_infos = tuple(
        s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED
    )
    return EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in source_infos),
        execution_role=SkillExecutionRole.SESSION,
    )


def _mcp_short_names(definitions, prefix: str) -> set[str]:
    return {
        tool.removeprefix(prefix)
        for definition in definitions
        for tool in definition.tools
        if tool.startswith("mcp__")
    }


def _assert_production_projection_carries_plugin_namespace(cwd: Path) -> None:
    from autoskillit.core import PluginLoadMode
    from autoskillit.execution.backends.claude import ClaudeCodeBackend
    from autoskillit.workspace import project_default_plugin_authority

    authority = project_default_plugin_authority(
        cwd=cwd, base_branch="main", catalog=_session_catalog()
    )
    with authority.acquire_launch_binding(
        backend=ClaudeCodeBackend(),
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    ) as binding:
        assert binding.plugin_dir is not None
        rendered = load_agent_definitions(binding.plugin_dir / "agents")
    assert binding.closed

    for definition in rendered:
        for tool in definition.tools:
            if tool.startswith("mcp__"):
                assert tool.startswith("mcp__plugin_autoskillit_autoskillit__"), (
                    f"projected agent {definition.name!r} tool {tool!r} does not carry "
                    "the plugin namespace Claude Code registers under --plugin-dir"
                )
    assert _mcp_short_names(rendered, "mcp__plugin_autoskillit_autoskillit__") == (
        _mcp_short_names(load_bundled_agent_definitions(), DIRECT_PREFIX)
    )


def _write_agent_md(path: Path, *, name: str, tools: list[str], body: str = "") -> None:
    tools_str = "[" + ", ".join(tools) + "]"
    content = (
        f"---\n"
        f"name: {name}\n"
        f'description: "Test agent."\n'
        f"tools: {tools_str}\n"
        f"model: sonnet\n"
        f"maxTurns: 5\n"
        f"---\n"
        f"\n{body or 'Test agent body.'}\n"
    )
    path.write_text(content, encoding="utf-8")


class TestRenderAgentDefinitionsValidation:
    """T4: rendering rejects non-canonical MCP tools and passes through non-MCP tools."""

    def test_non_direct_prefix_raises(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(
            agents_dir / "bad-prefix.md",
            name="bad-prefix",
            tools=[f"{_PLUGIN_NAMESPACE}submit_exploration_query"],
        )
        with pytest.raises(ValueError, match="direct-install canonical prefix"):
            _render_agent_definitions(tmp_path)

    def test_unknown_short_name_raises(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(
            agents_dir / "bad-tool.md",
            name="bad-tool",
            tools=[f"{DIRECT_PREFIX}nonexistent_tool"],
        )
        with pytest.raises(ValueError, match="not a registered exploration tool"):
            _render_agent_definitions(tmp_path)

    def test_non_mcp_tools_pass_through_unchanged(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        _write_plugin_manifests(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(
            agents_dir / "builtin-only.md",
            name="builtin-only",
            tools=["Read", "Grep", "Glob", "Bash"],
        )
        before = (agents_dir / "builtin-only.md").read_bytes()
        _render_agent_definitions(tmp_path)
        after = (agents_dir / "builtin-only.md").read_bytes()
        assert before == after, "non-MCP agent files must be byte-identical after rendering"

    def test_prefix_is_derived_from_plugin_manifests(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        _write_plugin_manifests(tmp_path, plugin_name="my.plugin", server_key="db-tools")
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(
            agents_dir / "probe.md",
            name="probe",
            tools=[f"{DIRECT_PREFIX}submit_exploration_query"],
        )

        _render_agent_definitions(tmp_path)

        (definition,) = load_agent_definitions(agents_dir)
        assert definition.tools == ("mcp__plugin_my_plugin_db-tools__submit_exploration_query",)

    def test_mcp_agent_without_derivable_namespace_fails_closed(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(
            agents_dir / "probe.md",
            name="probe",
            tools=[f"{DIRECT_PREFIX}submit_exploration_query"],
        )
        with pytest.raises(SkillContractError, match="plugin MCP tool namespace"):
            _render_agent_definitions(tmp_path)

    def test_projection_preserves_crlf_line_endings(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        _write_plugin_manifests(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        path = agents_dir / "crlf-agent.md"
        path.write_bytes(
            (
                "---\r\n"
                "name: crlf-agent\r\n"
                'description: "Test agent."\r\n'
                f"tools: [{DIRECT_PREFIX}submit_exploration_query]\r\n"
                "model: sonnet\r\n"
                "maxTurns: 5\r\n"
                "---\r\n"
                "\r\n"
                "Test agent body.\r\n"
            ).encode()
        )

        _render_agent_definitions(tmp_path)

        rendered = path.read_bytes()
        projected_tools_line = f"tools: [{_PLUGIN_NAMESPACE}submit_exploration_query]\r\n".encode()
        assert projected_tools_line in rendered
        assert b"\n" not in rendered.replace(b"\r\n", b"")


class TestRenderAgentDefinitionsByteIdentity:
    """T4: rendering touches only the tools: line of MCP-tool-bearing definitions."""

    def test_plugin_render_changes_only_tools_line(self, tmp_path: Path) -> None:
        """REQ-10: rendering to the plugin namespace changes only the tools: line."""
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        agents_dir = _copy_packaged_plugin(tmp_path)

        originals = {}
        for defn in load_bundled_agent_definitions():
            if any(tool.startswith("mcp__") for tool in defn.tools):
                originals[f"{defn.name}.md"] = (agents_dir / f"{defn.name}.md").read_text()

        assert originals, "Expected at least one MCP-tool-bearing agent"
        assert (
            "tools: [mcp__autoskillit__inspect_session_logs]" in originals["session-log-reader.md"]
        )

        _render_agent_definitions(tmp_path)

        for name, original_text in originals.items():
            rendered_text = (agents_dir / name).read_text()
            original_lines = original_text.splitlines()
            rendered_lines = rendered_text.splitlines()
            assert len(original_lines) == len(rendered_lines), (
                f"{name}: line count changed after rendering"
            )
            differing_indices = [
                i for i, (a, b) in enumerate(zip(original_lines, rendered_lines)) if a != b
            ]
            assert len(differing_indices) == 1, (
                f"{name}: expected exactly 1 line to differ (the tools: line), "
                f"but {len(differing_indices)} lines differ at indices {differing_indices}"
            )
            assert rendered_lines[differing_indices[0]].lstrip().startswith("tools:"), (
                f"{name}: the only differing line must be the tools: line"
            )

        reader = next(
            definition
            for definition in load_agent_definitions(agents_dir)
            if definition.name == "session-log-reader"
        )
        assert reader.tools == ("mcp__plugin_autoskillit_autoskillit__inspect_session_logs",)

        pr_source_reader = next(
            definition
            for definition in load_agent_definitions(agents_dir)
            if definition.name == "pr-source-reader"
        )
        assert pr_source_reader.tools == ("Read",)
        assert pr_source_reader.reader_tools == (
            "mcp__autoskillit__get_authorized_artifact_page",
            "mcp__autoskillit__read_authorized_artifact",
        )

    def test_all_builtin_only_agents_rendered_byte_identical(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        agents_dir = _copy_packaged_plugin(tmp_path)

        bundled_definitions = load_bundled_agent_definitions()
        originals = {}
        for defn in bundled_definitions:
            if not any(tool.startswith("mcp__") for tool in defn.tools):
                originals[f"{defn.name}.md"] = (agents_dir / f"{defn.name}.md").read_bytes()

        assert len(bundled_definitions) == 23, (
            "The bundled catalog includes the six skill-specific child roles"
        )
        assert len(originals) == 20, f"Expected 20 built-in-only agents, got {len(originals)}"

        _render_agent_definitions(tmp_path)

        for name, original_bytes in originals.items():
            rendered_bytes = (agents_dir / name).read_bytes()
            assert rendered_bytes == original_bytes, (
                f"{name} changed after rendering — built-in-only agents must be byte-identical"
            )

    def test_documentation_files_copied_verbatim(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        for doc_name in ("AGENTS.md", "CLAUDE.md"):
            doc = agents_dir / doc_name
            doc.write_text(f"# {doc_name}\nDocumentation content.\n")
        originals = {name: (agents_dir / name).read_bytes() for name in ("AGENTS.md", "CLAUDE.md")}

        _render_agent_definitions(tmp_path)

        for name, original_bytes in originals.items():
            assert (agents_dir / name).read_bytes() == original_bytes, (
                f"{name} was modified by rendering — documentation files must be verbatim"
            )


class TestBothPipelinesRenderAgents:
    """T4: both production staging pipelines render agent definitions."""

    def test_marketplace_root_contains_rendered_agents(self, tmp_path: Path) -> None:
        from autoskillit.workspace import (
            SkillProjectionContext,
            materialize_sanitized_plugin_root,
        )

        catalog = _session_catalog()
        destination = tmp_path / "marketplace" / "autoskillit"
        destination.parent.mkdir(parents=True)
        materialize_sanitized_plugin_root(
            pkg_root(),
            destination,
            catalog,
            SkillProjectionContext(cwd=tmp_path, catalog=catalog),
        )

        agents_dir = destination / "agents"
        assert agents_dir.is_dir(), "marketplace root must contain agents/"
        projected_defs = load_agent_definitions(agents_dir)
        bundled_defs = load_bundled_agent_definitions()
        assert len(projected_defs) == len(bundled_defs), (
            "marketplace root must contain all bundled agent definitions"
        )

    def test_production_projection_agents_carry_plugin_namespace(self, tmp_path: Path) -> None:
        _assert_production_projection_carries_plugin_namespace(tmp_path)


class TestPerCorridorConsumptionChecks:
    """I4: the rendered namespace follows the artifact's manifests, never host state."""

    @pytest.mark.parametrize("registered", [False, True])
    def test_production_projection_namespace_uses_plugin_manifests(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: bool
    ) -> None:
        """The rendered namespace derives from the artifact's manifests, not the host registry.

        The projection hardcodes PluginLoadMode.EXPLICIT_PLUGIN_DIR and derives the
        MCP tool namespace from the artifact's own .claude-plugin/plugin.json and
        .mcp.json via read_claude_plugin_tool_prefix(); it never consults
        is_marketplace_plugin_registered(). This parametrize exists so the
        projection is smoke-tested with both an absent and a present registry
        file (scoped to tmp_path via monkeypatch so xdist is not polluted).
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        registry = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
        if registered:
            registry.parent.mkdir(parents=True, exist_ok=True)
            registry.write_text(
                json.dumps(
                    {"plugins": {"autoskillit@autoskillit-local": [{"installPath": "/fake"}]}}
                ),
                encoding="utf-8",
            )
        assert registry.is_file() is registered

        _assert_production_projection_carries_plugin_namespace(tmp_path)


class TestRenderCoherence:
    """I4: both pipelines share _render_agent_definitions — no third unrendered path."""

    def test_both_pipelines_share_one_renderer(self) -> None:
        """The two production staging pipelines must use the same render function."""
        import ast
        import inspect

        from autoskillit.workspace._projected_artifact import authority, materialization

        mat_source = inspect.getsource(materialization.materialize_sanitized_plugin_root)
        mat_tree = ast.parse(mat_source)
        mat_calls = {
            node.func.id
            for node in ast.walk(mat_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_render_agent_definitions"
        }

        auth_source = inspect.getsource(authority._stage_projected_plugin_artifact)
        auth_tree = ast.parse(auth_source)
        auth_calls = {
            node.func.id
            for node in ast.walk(auth_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_render_agent_definitions"
        }

        assert mat_calls, "materialize_sanitized_plugin_root must call _render_agent_definitions"
        assert auth_calls, "_stage_projected_plugin_artifact must call _render_agent_definitions"
        assert authority._render_agent_definitions is materialization._render_agent_definitions


def test_render_agent_definitions_skips_a_definition_that_vanishes_mid_glob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.workspace._projected_artifact.materialization import (
        _render_agent_definitions,
    )

    _write_plugin_manifests(tmp_path)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    vanished = agents_dir / "a-vanished.md"
    rendered = agents_dir / "z-rendered.md"
    _write_agent_md(
        vanished,
        name="vanished",
        tools=[f"{DIRECT_PREFIX}submit_exploration_query"],
    )
    _write_agent_md(
        rendered,
        name="rendered",
        tools=[f"{DIRECT_PREFIX}submit_exploration_query"],
    )
    original_read_bytes = Path.read_bytes
    did_vanish = False

    def vanish_before_read(path: Path) -> bytes:
        nonlocal did_vanish
        if path == vanished and not did_vanish:
            did_vanish = True
            vanished.unlink()
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", vanish_before_read)

    _render_agent_definitions(tmp_path)

    assert did_vanish
    assert not vanished.exists()
    assert f"tools: [{_PLUGIN_NAMESPACE}submit_exploration_query]" in rendered.read_text(
        encoding="utf-8"
    )


def test_render_agent_definitions_propagates_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.workspace._projected_artifact.materialization import (
        _render_agent_definitions,
    )

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    definition = agents_dir / "permission-denied.md"
    _write_agent_md(
        definition,
        name="permission-denied",
        tools=[f"{DIRECT_PREFIX}submit_exploration_query"],
    )
    original_read_bytes = Path.read_bytes

    def deny_definition_read(path: Path) -> bytes:
        if path == definition:
            raise PermissionError("injected definition read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_definition_read)

    with pytest.raises(PermissionError, match="injected definition read failure"):
        _render_agent_definitions(tmp_path)
