"""There is one way to get a plugin source, and it never reads the registry.

Four call sites used to resolve a `PluginSource` independently. Two read
`installed_plugins.json` — a file Claude Code owns, versions, and garbage-collects
— and those two were the two that broke when the path it named stopped existing.
The other two already went through `project_default_plugin_source(pkg_root())`
and were unaffected.

F2 is the crash: `installPath` naming a deleted directory took down `cook` and
MCP server startup. The fix is not a guard around the read; it is removing the
read, so no externally-owned path can be a projection source at all.
"""

from __future__ import annotations

import json
import typing
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _seed_dangling_registry(home: Path) -> Path:
    """Point installed_plugins.json at a directory that does not exist.

    The exact production state: the registry named
    `.../autoskillit/0.10.883` after the sweeper (or Claude Code's own in-use
    sweep) had already removed it.
    """
    gone = (
        home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit" / "0.10.883"
    )
    registry = home / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {"autoskillit@autoskillit-local": {"installPath": str(gone)}},
            }
        )
    )
    assert not gone.exists()
    return gone


class TestDanglingInstallPathIsHarmless:
    """F2 reproduction. Both entrypoints raised SkillContractError before the fix."""

    def test_make_context_resolves_despite_a_dangling_install_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from autoskillit.config import AutomationConfig
        from autoskillit.core import ProjectedPluginRoot, pkg_root
        from autoskillit.server._factory import make_context

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        gone = _seed_dangling_registry(tmp_path)

        ctx = make_context(AutomationConfig(), runner=None, project_dir=tmp_path)

        assert isinstance(ctx.plugin_source, ProjectedPluginRoot)
        assert ctx.plugin_source.plugin_dir.is_dir()
        assert gone not in ctx.plugin_source.plugin_dir.parents
        assert ctx.plugin_source.plugin_dir != pkg_root()

    def test_cook_resolution_survives_a_dangling_install_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """`cook` shares make_context's authority; exercise it the way cook calls it."""
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_source
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _seed_dangling_registry(tmp_path)

        projected = project_default_plugin_source(
            cwd=tmp_path,
            backend=ClaudeCodeBackend(),
            default_base_branch="main",
            skill_catalog=session_catalog(),
        )
        assert projected.plugin_dir.is_dir()

    def test_mcp_prefix_detection_still_reads_key_presence(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Key presence remains a legitimate dependency; only the *path* read is gone.

        `installed_plugins.json` is authoritative for exactly one thing: which MCP
        tool-name prefix a spawned session will use. That answer comes from key
        presence and never dereferences installPath, so it survives a dangling
        entry unchanged.
        """
        from autoskillit.core import _plugin_ids

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _seed_dangling_registry(tmp_path)
        _plugin_ids.detect_autoskillit_mcp_prefix.cache_clear()
        try:
            from autoskillit.core import CLAUDE_CODE_CAPABILITIES

            assert (
                _plugin_ids.detect_autoskillit_mcp_prefix(CLAUDE_CODE_CAPABILITIES)
                == _plugin_ids.MARKETPLACE_PREFIX
            )
        finally:
            _plugin_ids.detect_autoskillit_mcp_prefix.cache_clear()


class TestPluginSourceHasOneVariant:
    def test_plugin_source_is_not_a_union(self) -> None:
        """F2 is unrepresentable, not merely guarded.

        `MarketplaceInstall` existed only to carry a path read out of the
        registry. With it gone there is no type that can hold one.
        """
        from autoskillit.core import PluginSource, ProjectedPluginRoot

        assert typing.get_args(PluginSource) == (), "PluginSource is a union again"
        assert PluginSource is ProjectedPluginRoot

    def test_marketplace_install_is_gone_from_the_public_surface(self) -> None:
        import autoskillit.core as core

        assert "MarketplaceInstall" not in core.__all__
        assert not hasattr(core, "MarketplaceInstall")

    def test_registry_path_extractor_is_gone(self) -> None:
        """`_get_autoskillit_install_path` had exactly two consumers: the two that broke."""
        import autoskillit.core as core

        assert not hasattr(core, "_get_autoskillit_install_path")

    def test_projected_root_refuses_the_canonical_package_root(self) -> None:
        from autoskillit.core import ProjectedPluginRoot, pkg_root

        with pytest.raises(ValueError, match="canonical package root"):
            ProjectedPluginRoot(plugin_dir=pkg_root())

    def test_projected_root_refuses_a_relative_path(self) -> None:
        from autoskillit.core import ProjectedPluginRoot

        with pytest.raises(ValueError, match="absolute"):
            ProjectedPluginRoot(plugin_dir=Path("relative/plugin"))


class TestAllEntrypointsAgree:
    """Every resolution site returns a projection under plugin-projections/."""

    def test_make_context_and_catalog_dispatch_agree(self, tmp_path: Path, monkeypatch) -> None:
        from autoskillit.config import AutomationConfig
        from autoskillit.server._factory import make_context
        from autoskillit.workspace import prepare_catalog_skill_dispatch
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ctx = make_context(AutomationConfig(), runner=None, project_dir=tmp_path)
        dispatched, _contract = prepare_catalog_skill_dispatch(
            resolved_command="/investigate",
            cwd=tmp_path,
            backend=ctx.backend,
            catalog=session_catalog(),
            default_base_branch=ctx.config.branching.default_base_branch,
        )

        projections = tmp_path / ".autoskillit" / "plugin-projections"
        for source in (ctx.plugin_source, dispatched):
            assert source.plugin_dir.is_relative_to(projections), (
                f"{source.plugin_dir} is not a projection"
            )

    def test_cook_and_make_context_derive_project_dir_identically(self) -> None:
        """Related Issue 11: the two used to disagree from a repo subdirectory."""
        import autoskillit.cli.session._session_cook as cook_mod
        import autoskillit.server._factory as factory_mod
        from autoskillit.core import resolve_project_dir

        assert cook_mod.resolve_project_dir is resolve_project_dir
        assert factory_mod.resolve_project_dir is resolve_project_dir
