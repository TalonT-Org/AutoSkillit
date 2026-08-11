"""Agent definition rendering: prefix projection, validation, and pipeline coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    DIRECT_PREFIX,
    MARKETPLACE_PREFIX,
    SkillExecutionRole,
    SkillSource,
    load_agent_definitions,
    load_bundled_agent_definitions,
    pkg_root,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


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
            tools=[f"{MARKETPLACE_PREFIX}submit_exploration_query"],
        )
        with pytest.raises(ValueError, match="direct-install canonical prefix"):
            _render_agent_definitions(agents_dir, DIRECT_PREFIX)

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
            _render_agent_definitions(agents_dir, DIRECT_PREFIX)

    def test_non_mcp_tools_pass_through_unchanged(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _write_agent_md(
            agents_dir / "builtin-only.md",
            name="builtin-only",
            tools=["Read", "Grep", "Glob", "Bash"],
        )
        before = (agents_dir / "builtin-only.md").read_bytes()
        _render_agent_definitions(agents_dir, MARKETPLACE_PREFIX)
        after = (agents_dir / "builtin-only.md").read_bytes()
        assert before == after, "non-MCP agent files must be byte-identical after rendering"

    def test_projection_preserves_crlf_line_endings(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

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

        _render_agent_definitions(agents_dir, MARKETPLACE_PREFIX)

        rendered = path.read_bytes()
        projected_tools_line = (
            f"tools: [{MARKETPLACE_PREFIX}submit_exploration_query]\r\n".encode()
        )
        assert projected_tools_line in rendered
        assert b"\n" not in rendered.replace(b"\r\n", b"")


class TestRenderAgentDefinitionsByteIdentity:
    """T4: rendering with DIRECT_PREFIX produces byte-identical output for source files."""

    def test_direct_prefix_is_byte_identical_to_source(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        source_agents = pkg_root() / "agents"
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        import shutil

        for path in sorted(source_agents.iterdir()):
            if path.is_file():
                shutil.copy2(path, agents_dir / path.name)

        originals = {
            path.name: path.read_bytes()
            for path in sorted(source_agents.iterdir())
            if path.is_file()
        }

        _render_agent_definitions(agents_dir, DIRECT_PREFIX)

        for name, original_bytes in originals.items():
            rendered_bytes = (agents_dir / name).read_bytes()
            assert rendered_bytes == original_bytes, (
                f"{name} differs from source after DIRECT_PREFIX rendering — "
                f"rendering must be byte-identical when the target prefix equals "
                f"the authored prefix"
            )

    def test_marketplace_render_changes_only_tools_line(self, tmp_path: Path) -> None:
        """REQ-10: rendering with a different prefix changes only the tools: line."""
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        source_agents = pkg_root() / "agents"
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        import shutil

        for path in sorted(source_agents.iterdir()):
            if path.is_file():
                shutil.copy2(path, agents_dir / path.name)

        originals = {}
        for defn in load_bundled_agent_definitions():
            if any(tool.startswith("mcp__") for tool in defn.tools):
                originals[f"{defn.name}.md"] = (agents_dir / f"{defn.name}.md").read_text()

        assert originals, "Expected at least one MCP-tool-bearing agent"
        assert (
            "tools: [mcp__autoskillit__inspect_session_logs]" in originals["session-log-reader.md"]
        )

        _render_agent_definitions(agents_dir, MARKETPLACE_PREFIX)

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

    def test_all_builtin_only_agents_rendered_byte_identical(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            _render_agent_definitions,
        )

        source_agents = pkg_root() / "agents"
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        import shutil

        for path in sorted(source_agents.iterdir()):
            if path.is_file():
                shutil.copy2(path, agents_dir / path.name)

        bundled_definitions = load_bundled_agent_definitions()
        originals = {}
        for defn in bundled_definitions:
            if not any(tool.startswith("mcp__") for tool in defn.tools):
                originals[f"{defn.name}.md"] = (agents_dir / f"{defn.name}.md").read_bytes()

        assert len(bundled_definitions) == 16, (
            "Adding session-log-reader and retiring pipeline-health-scanner must preserve "
            "the bundled-agent count"
        )
        assert len(originals) == 13, f"Expected 13 built-in-only agents, got {len(originals)}"

        _render_agent_definitions(agents_dir, MARKETPLACE_PREFIX)

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

        _render_agent_definitions(agents_dir, MARKETPLACE_PREFIX)

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
        from autoskillit.workspace.skills import (
            DefaultSkillResolver,
            EffectiveSkillCatalog,
            SkillCatalogEntry,
        )

        source_root = pkg_root()
        source_infos = tuple(
            s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED
        )
        catalog = EffectiveSkillCatalog(
            skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in source_infos),
            execution_role=SkillExecutionRole.SESSION,
        )
        destination = tmp_path / "marketplace" / "autoskillit"
        destination.parent.mkdir(parents=True)
        materialize_sanitized_plugin_root(
            source_root,
            destination,
            catalog,
            SkillProjectionContext(cwd=tmp_path, catalog=catalog),
            mcp_tool_prefix=MARKETPLACE_PREFIX,
        )

        agents_dir = destination / "agents"
        assert agents_dir.is_dir(), "marketplace root must contain agents/"
        projected_defs = load_agent_definitions(agents_dir)
        bundled_defs = load_bundled_agent_definitions()
        assert len(projected_defs) == len(bundled_defs), (
            "marketplace root must contain all bundled agent definitions"
        )

    def test_projected_artifact_contains_rendered_agents(self, tmp_path: Path) -> None:
        from autoskillit.workspace import (
            SkillProjectionContext,
            materialize_sanitized_plugin_root,
        )
        from autoskillit.workspace.skills import (
            DefaultSkillResolver,
            EffectiveSkillCatalog,
            SkillCatalogEntry,
        )

        source_root = pkg_root()
        source_infos = tuple(
            s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED
        )
        catalog = EffectiveSkillCatalog(
            skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in source_infos),
            execution_role=SkillExecutionRole.SESSION,
        )
        destination = tmp_path / "projection" / "autoskillit"
        destination.parent.mkdir(parents=True)
        materialize_sanitized_plugin_root(
            source_root,
            destination,
            catalog,
            SkillProjectionContext(cwd=tmp_path, catalog=catalog),
            mcp_tool_prefix=DIRECT_PREFIX,
        )

        agents_dir = destination / "agents"
        assert agents_dir.is_dir(), "projected artifact must contain agents/"
        projected_defs = load_agent_definitions(agents_dir)
        bundled_defs = load_bundled_agent_definitions()
        assert len(projected_defs) == len(bundled_defs), (
            "projected artifact must contain all bundled agent definitions"
        )
        for defn in projected_defs:
            for tool in defn.tools:
                if tool.startswith("mcp__"):
                    assert tool.startswith(DIRECT_PREFIX), (
                        f"projected artifact agent {defn.name!r} tool {tool!r} "
                        f"must use DIRECT_PREFIX"
                    )


class TestPerCorridorConsumptionChecks:
    """I4: both pipelines share _render_agent_definitions and produce correct prefixes."""

    def test_projected_artifact_unaffected_by_installed_plugins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The projected artifact uses DIRECT_PREFIX regardless of registry content."""
        from autoskillit.workspace import (
            SkillProjectionContext,
            materialize_sanitized_plugin_root,
        )
        from autoskillit.workspace.skills import (
            DefaultSkillResolver,
            EffectiveSkillCatalog,
            SkillCatalogEntry,
        )

        fake_registry = tmp_path / "fake_registry.json"
        fake_registry.write_text(
            '{"plugins": {"autoskillit@autoskillit-local": [{"installPath": "/fake"}]}}'
        )
        monkeypatch.setattr(
            "autoskillit.core._plugin_ids._installed_plugins_path",
            lambda home=None: fake_registry,
        )

        source_root = pkg_root()
        source_infos = tuple(
            s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED
        )
        catalog = EffectiveSkillCatalog(
            skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in source_infos),
            execution_role=SkillExecutionRole.SESSION,
        )
        destination = tmp_path / "projection" / "autoskillit"
        destination.parent.mkdir(parents=True)
        materialize_sanitized_plugin_root(
            source_root,
            destination,
            catalog,
            SkillProjectionContext(cwd=tmp_path, catalog=catalog),
            mcp_tool_prefix=DIRECT_PREFIX,
        )

        projected_defs = load_agent_definitions(destination / "agents")
        for defn in projected_defs:
            for tool in defn.tools:
                if tool.startswith("mcp__"):
                    assert tool.startswith(DIRECT_PREFIX), (
                        f"projected artifact agent {defn.name!r} tool {tool!r} "
                        f"must use DIRECT_PREFIX regardless of installed_plugins.json"
                    )
                    assert not tool.startswith(MARKETPLACE_PREFIX), (
                        "projected artifact must never use MARKETPLACE_PREFIX"
                    )


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
