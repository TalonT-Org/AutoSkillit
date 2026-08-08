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

        originals = {}
        for defn in load_bundled_agent_definitions():
            if not any(tool.startswith("mcp__") for tool in defn.tools):
                originals[f"{defn.name}.md"] = (agents_dir / f"{defn.name}.md").read_bytes()

        assert len(originals) >= 13, (
            f"Expected at least 13 built-in-only agents, got {len(originals)}"
        )

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
