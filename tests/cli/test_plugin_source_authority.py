"""There is one authority for launch plugin artifacts, and it never reads the registry.

Four call sites used to resolve a plugin path independently. Two read
`installed_plugins.json` — a file Claude Code owns, versions, and garbage-collects
— and those two were the two that broke when the path it named stopped existing.
The other two already projected from ``pkg_root()``
and were unaffected.

F2 is the crash: `installPath` naming a deleted directory took down `cook` and
MCP server startup. The fix is not a guard around the read; it is removing the
read, so no externally-owned path can be a projection source at all.
"""

from __future__ import annotations

import json
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
        from autoskillit.core import PluginArtifactAuthority, PluginLoadMode, pkg_root
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.server._factory import make_context

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        gone = _seed_dangling_registry(tmp_path)

        ctx = make_context(AutomationConfig(), runner=None, project_dir=tmp_path)

        assert isinstance(ctx.plugin_authority, PluginArtifactAuthority)
        with ctx.plugin_authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            assert binding.plugin_dir.is_dir()
            assert gone not in binding.plugin_dir.parents
            assert binding.plugin_dir != pkg_root()
        assert binding.closed

    def test_cook_resolution_survives_a_dangling_install_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """`cook` shares make_context's authority; exercise it the way cook calls it."""
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _seed_dangling_registry(tmp_path)

        authority = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=session_catalog(),
        )
        with authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            assert binding.plugin_dir.is_dir()
        assert binding.closed

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


class TestPluginArtifactAuthoritySurface:
    def test_legacy_plugin_source_types_are_gone(self) -> None:
        """Callers cannot persist a bare path and mistake it for launch authority."""
        import autoskillit.core as core

        for name in ("PluginSource", "ProjectedPluginRoot"):
            assert name not in core.__all__
            assert not hasattr(core, name)

    def test_marketplace_install_is_gone_from_the_public_surface(self) -> None:
        import autoskillit.core as core

        assert "MarketplaceInstall" not in core.__all__
        assert not hasattr(core, "MarketplaceInstall")

    def test_registry_path_extractor_is_gone(self) -> None:
        """`_get_autoskillit_install_path` had exactly two consumers: the two that broke."""
        import autoskillit.core as core

        assert not hasattr(core, "_get_autoskillit_install_path")

    def test_binding_refuses_a_relative_path(self) -> None:
        from autoskillit.core import (
            PluginArtifactIdentity,
            PluginLaunchBinding,
            PluginLoadMode,
        )
        from tests.execution.backends._plugin_binding import _TestLease

        absolute = Path("/plugin")
        with pytest.raises(ValueError, match="absolute"):
            PluginLaunchBinding(
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
                plugin_dir=Path("relative/plugin"),
                identity=PluginArtifactIdentity(
                    semantic_key="test",
                    incarnation_id="one",
                    manifest_schema_version=1,
                    artifact_digest="digest",
                    managed_path=absolute,
                    manifest_path=Path("/plugin.json"),
                ),
                inherited_fds=(),
                _lease=_TestLease(),
            )


class TestAllEntrypointsAgree:
    """Every resolution site lazily acquires under plugin-projections/."""

    def test_make_context_and_catalog_dispatch_agree(self, tmp_path: Path, monkeypatch) -> None:
        from autoskillit.config import AutomationConfig
        from autoskillit.core import PluginLoadMode
        from autoskillit.server._factory import make_context
        from autoskillit.workspace import prepare_catalog_skill_dispatch
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ctx = make_context(AutomationConfig(), runner=None, project_dir=tmp_path)
        dispatched_authority, _preparation = prepare_catalog_skill_dispatch(
            resolved_command="/investigate",
            cwd=tmp_path,
            catalog=session_catalog(),
            default_base_branch=ctx.config.branching.default_base_branch,
        )

        projections = tmp_path / ".autoskillit" / "plugin-projections"
        with (
            ctx.plugin_authority.acquire_launch_binding(
                backend=ctx.backend,
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            ) as context_binding,
            dispatched_authority.acquire_launch_binding(
                backend=ctx.backend,
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            ) as dispatch_binding,
        ):
            for binding in (context_binding, dispatch_binding):
                assert binding.plugin_dir is not None
                assert binding.plugin_dir.is_relative_to(projections), (
                    f"{binding.plugin_dir} is not a projection"
                )
        assert context_binding.closed
        assert dispatch_binding.closed

    def test_cook_and_make_context_derive_project_dir_identically(self) -> None:
        """Related Issue 11: the two used to disagree from a repo subdirectory."""
        import autoskillit.cli.session._session_cook as cook_mod
        import autoskillit.server._factory as factory_mod
        from autoskillit.core import resolve_project_dir

        assert cook_mod.resolve_project_dir is resolve_project_dir
        assert factory_mod.resolve_project_dir is resolve_project_dir


class TestInstalledPluginArtifactAuthority:
    def test_publication_does_not_wrap_control_flow_exceptions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli import _plugin_artifact

        def interrupt(_root: Path) -> Path:
            raise KeyboardInterrupt

        monkeypatch.setattr(_plugin_artifact, "_canonical_installed_root", interrupt)

        with pytest.raises(KeyboardInterrupt):
            _plugin_artifact.publish_installed_plugin_artifact(
                tmp_path,
                semantic_key="autoskillit@autoskillit-local:1.2.3",
            )

    def test_binding_acquisition_does_not_wrap_control_flow_exceptions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.cli import _plugin_artifact
        from autoskillit.core import PluginLoadMode

        root = tmp_path.resolve()

        def interrupt(*_args, **_kwargs):
            raise SystemExit("stop")

        monkeypatch.setattr(
            _plugin_artifact.ArtifactLease,
            "acquire_shared",
            interrupt,
        )

        with pytest.raises(SystemExit, match="stop"):
            _plugin_artifact.InstalledPluginArtifactAuthority(
                root,
                semantic_key="autoskillit@autoskillit-local:1.2.3",
            ).acquire_launch_binding(
                backend=object(),
                load_mode=PluginLoadMode.IMPLICIT_INSTALLED,
            )

    def test_publication_round_trips_exact_external_incarnation(self, tmp_path: Path) -> None:
        from autoskillit.cli._plugin_artifact import (
            InstalledPluginArtifactAuthority,
            installed_artifact_manifest_path,
            publish_installed_plugin_artifact,
        )
        from autoskillit.core import PluginLoadMode

        root = (tmp_path / "cache" / "autoskillit" / "1.2.3").resolve()
        root.mkdir(parents=True)
        (root / "plugin.json").write_text('{"name":"autoskillit"}')
        semantic_key = "autoskillit@autoskillit-local:1.2.3"

        published = publish_installed_plugin_artifact(root, semantic_key=semantic_key)
        manifest_path = installed_artifact_manifest_path(root)
        assert published.manifest_path == manifest_path
        assert manifest_path.parent == root.parent
        assert not manifest_path.is_relative_to(root)

        binding = InstalledPluginArtifactAuthority(
            root,
            semantic_key=semantic_key,
        ).acquire_launch_binding(
            backend=object(),
            load_mode=PluginLoadMode.IMPLICIT_INSTALLED,
        )
        try:
            assert binding.plugin_dir is None
            assert binding.identity == published
            assert len(binding.inherited_fds) == 1
            assert not binding.closed
        finally:
            binding.close()
        assert binding.closed

    def test_content_change_after_publication_fails_closed(self, tmp_path: Path) -> None:
        from autoskillit.cli._plugin_artifact import (
            InstalledPluginArtifactAuthority,
            publish_installed_plugin_artifact,
        )
        from autoskillit.core import PluginArtifactValidationError, PluginLoadMode

        root = (tmp_path / "installed" / "1.2.3").resolve()
        root.mkdir(parents=True)
        content = root / "plugin.json"
        content.write_text("before")
        semantic_key = "autoskillit@autoskillit-local:1.2.3"
        publish_installed_plugin_artifact(root, semantic_key=semantic_key)
        content.write_text("after")

        with pytest.raises(PluginArtifactValidationError, match="digest mismatch"):
            InstalledPluginArtifactAuthority(
                root,
                semantic_key=semantic_key,
            ).acquire_launch_binding(
                backend=object(),
                load_mode=PluginLoadMode.IMPLICIT_INSTALLED,
            )

    def test_generated_home_codex_does_not_construct_projection_authority(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from autoskillit.cli._plugin_artifact import interactive_plugin_authority
        from autoskillit.core import PluginLoadMode

        backend = SimpleNamespace(
            name="codex",
            capabilities=SimpleNamespace(
                skill_injection_capable=True,
                plugin_install_capable=False,
            ),
        )
        monkeypatch.setattr(
            "autoskillit.workspace.project_default_plugin_authority",
            lambda **_kwargs: pytest.fail("generated-home Codex projected an unused plugin"),
        )

        authority, load_mode = interactive_plugin_authority(
            backend=backend,
            project_dir=tmp_path,
            default_base_branch="main",
            skill_catalog=None,
            generated_home=tmp_path / "generated-home",
        )

        assert authority is None
        assert load_mode is PluginLoadMode.GENERATED_HOME

    def test_implicit_binding_rejects_wrong_transaction_identity(self, tmp_path: Path) -> None:
        from autoskillit.cli._plugin_artifact import (
            InstalledPluginArtifactAuthority,
            publish_installed_plugin_artifact,
        )
        from autoskillit.core import PluginArtifactValidationError, PluginLoadMode

        root = (tmp_path / "installed" / "1.2.3").resolve()
        root.mkdir(parents=True)
        (root / "plugin.json").write_text("content")
        publish_installed_plugin_artifact(root, semantic_key="plugin:old")

        with pytest.raises(PluginArtifactValidationError, match="semantic identity"):
            InstalledPluginArtifactAuthority(
                root,
                semantic_key="plugin:new",
            ).acquire_launch_binding(
                backend=object(),
                load_mode=PluginLoadMode.IMPLICIT_INSTALLED,
            )
